"""离线生成并签名发版清单；清单字节由本脚本一次产出、落盘、回读后再签名。

签名对象是 manifest.json 的原始字节，本脚本绝不重新序列化已存在的清单文件。
私钥只在进程内存中短暂存在：Python 的 str/bytes 不可变，无法清零，`del` 仅缩短引用寿命，
运行纪律（离线机、不联网、不录屏）见 docs/_report_sign_1021.md「运行纪律」节。
"""
import argparse
import base64
import ctypes
from ctypes import wintypes
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
from urllib.parse import parse_qsl, urlsplit
import warnings

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

import updater

# 与 account_keeper.py 运行期算出的架构一致；不开 CLI 参数口子，避免误传 win64 造出必被拒的清单。
ARCH = "x64"
EXE_NAME = "AccountKeeper.exe"
HELPER_NAME = "update_helper.exe"
MANIFEST_NAME = "manifest.json"
SIG_NAME = "manifest.sig"
_CHUNK = 1 << 20
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_HEX64 = re.compile(r"[0-9a-fA-F]{64}")
_HOSTS = {"gitee.com", "foruda.gitee.com"}
# 已知测试公钥：命中即拒签，避免"源码、exe、种子三者一致"却全是测试钥的盲区。
_TEST_KEYS = {"27ddbc844141850e7b744205c52d232e0f24b0c4d072cc924495470e62f08b4f"}
# PE 机器类型到客户端 arch 字面量的映射；客户端只按运行期 platform.machine() 取值。
_PE_MACHINE = {0x8664: "x64", 0xAA64: "arm64", 0x014C: "x86"}
# 查询串里出现这些词即视为凭证，宁可误拒也不能把 token 写进公开清单。
_SENSITIVE = ("token", "secret", "password", "passwd", "credential", "signature", "auth", "apikey",
              "api_key", "key")
# 短参数名只能精确匹配，否则 "t" 会命中所有含 t 的参数名。
_SENSITIVE_EXACT = {"t", "p", "k", "s", "pw", "pwd", "tk", "sk", "ak", "sig"}


class SignError(Exception):
    """拒绝任何不满足发版字节要求或安全要求的输入。"""


def _require_tty():
    # 无回显输入必须有真实终端；IDE 内置终端与重定向都会让私钥落到可读日志里。
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise SignError("需要交互终端：请在 cmd 或 PowerShell 窗口运行，IDE 内置终端不可用")


def _read_seed():
    _require_tty()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        text = getpass.getpass("私钥（64 位十六进制，输入不回显）：")
    # getpass 在无法关闭回显时会告警并回退明文读取，此时输入已上屏，必须拒绝而不是继续。
    if any(issubclass(item.category, getpass.GetPassWarning) for item in caught):
        raise SignError("检测到无回显失败（输入可能已上屏），请清屏后重试")
    if not _HEX64.fullmatch(text):
        # 不做 strip、去 0x、去引号等清洗：格式错误一律让用户重新复制。
        raise SignError("私钥格式错误：必须是 64 位十六进制，且不含空格或前缀")
    seed = bytes.fromhex(text.lower())
    del text  # 立即丢弃明文字符串引用；str 不可变，del 只缩短寿命，不等于清零。
    return seed


def _scan_exe(path):
    return _scan_asset(path, EXE_NAME)


def _scan_asset(path, name):
    path = Path(path)
    # 文件名同时决定附件名与客户端 _target 校验，改名即失效。
    if path.name != name:
        raise SignError(f"文件名必须是 {name}")
    if not path.is_file():
        raise SignError(f"找不到 exe：{path}")
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK):  # 分块读取，避免整包进内存。
            size += len(chunk)
            digest.update(chunk)
    if not 1 <= size <= 256 * 1024 * 1024:
        raise SignError("附件大小必须为1..256 MiB")  # 签名端与客户端使用同一接收上限。
    return digest.hexdigest(), size


def _exe_public_key(exe):
    """取出 exe 里真正内置的公钥；取不到就拒绝签名，不回退为只比源码。"""
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError as error:  # 签名只在构建环境执行，缺 PyInstaller 不允许降级。
        raise SignError("缺少 PyInstaller，无法校验 exe 内公钥") from error
    try:
        archive = CArchiveReader(str(exe))
        pyz = next((name for name in archive.toc if name.lower().endswith(".pyz")), None)
        if pyz is None:
            raise SignError("exe 内没有 PYZ 归档，无法提取公钥")
        module = archive.open_embedded_archive(pyz).extract("updater")
    except SignError:
        raise
    except Exception as error:  # 归档格式或 PyInstaller 版本变动一律拒绝，不静默降级。
        raise SignError(f"无法从 exe 提取 updater：{error}") from error
    # 只认模块顶层常量：PUBLIC_KEY 是顶层赋值，嵌套函数里的十六进制串不算公钥。
    found = [item for item in module.co_consts if isinstance(item, str) and _HEX64.fullmatch(item)]
    if len(found) > 1:
        found = [item for item in found if item not in _TEST_KEYS]  # 黑名单字面量也在顶层，不能把它误当第二把发布钥。
    if len(found) != 1:
        raise SignError(f"exe 内公钥候选 {len(found)} 个，无法唯一确定")
    return bytes.fromhex(found[0])


def _fingerprint(public):
    """公钥指纹：SHA-256 前 16 位按 4 组打印，供人眼与 gen_signing_key.py 输出核对。"""
    text = hashlib.sha256(public).hexdigest()[:16].upper()
    return "-".join(text[index:index + 4] for index in range(0, 16, 4))


def _pe_arch(exe):
    """读 PE 头 Machine 字段，架构由产物决定而不是由常量声明。"""
    with Path(exe).open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise SignError("不是 PE 文件：缺少 MZ 头")
        stream.seek(0x3C)
        offset = struct.unpack("<I", stream.read(4))[0]
        stream.seek(offset)
        if stream.read(4) != b"PE\0\0":
            raise SignError("不是 PE 文件：缺少 PE 签名")
        machine = struct.unpack("<H", stream.read(2))[0]
    arch = _PE_MACHINE.get(machine)
    if arch is None:
        raise SignError(f"未知的 PE 机器类型 0x{machine:04X}")
    return arch


def _exe_version(exe):
    """只认产物自带的版本资源：它与 build_release 写入的 APP_VERSION 同源，不受 --version 影响。"""
    api = ctypes.windll.version
    api.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    api.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    api.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    api.GetFileVersionInfoW.restype = wintypes.BOOL
    api.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                   ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    api.VerQueryValueW.restype = wintypes.BOOL
    size = api.GetFileVersionInfoSizeW(str(exe), None)
    if not size:
        raise SignError("exe 没有版本资源，无法绑定版本号；请用 build_release.py 重新构建")
    buffer = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(str(exe), 0, size, buffer):
        raise SignError("读取 exe 版本资源失败")
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if not api.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)) or length.value < 16:
        raise SignError("exe 版本资源缺少固定版本信息")
    # VS_FIXEDFILEINFO 的 dwFileVersionMS/LS 在偏移 8；构建号恒为 0，与 write_version_resource 一致。
    ms, ls = struct.unpack_from("<II", ctypes.string_at(pointer.value, 16), 8)
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}"


def _check_version(text):
    # 与 updater._version 同口径：三段稳定号、无前导零、各段不超 Windows WORD。
    if not isinstance(text, str) or not _VERSION.fullmatch(text) or max(map(int, text.split("."))) > 65535:
        raise SignError("版本必须是 主.次.补丁 三段稳定号，各段不超过 65535")
    return text


def _check_url(text):
    if not isinstance(text, str) or len(text) > 8192 or any(ord(c) <= 32 or ord(c) == 127 for c in text):
        raise SignError("下载 URL 含空白、控制字符或超长")
    try:
        parts = urlsplit(text)
        valid = (parts.scheme == "https" and parts.hostname in _HOSTS and parts.port in (None, 443)
                 and parts.username is None and parts.password is None and not parts.fragment)
    except ValueError as error:  # 端口解析失败也归为非法来源。
        raise SignError("下载 URL 不是合法的 gitee.com 附件地址") from error
    if not valid:
        raise SignError("下载 URL 必须是 gitee.com 的 HTTPS 附件地址")
    for name, _ in parse_qsl(parts.query, keep_blank_values=True):
        # 清单是公开文件，任何凭证都不能随它一起发布；短名精确匹配，长词子串匹配。
        key = name.lower()
        if key in _SENSITIVE_EXACT or any(word in key for word in _SENSITIVE):
            raise SignError("URL 查询串含敏感参数名，禁止写入公开清单")
    return text  # 原样返回：清单 url 必须与 API 值逐字节相等，禁止任何规范化。


def _build_manifest(version, size, sha256, url, arch, helper_sha256, helper_size, helper_url):
    fields = {"schema": 3, "app": "AccountKeeper", "version": version, "channel": "stable",
              "arch": arch, "size": size, "sha256": sha256, "url": url,
              "helper_size": helper_size, "helper_sha256": helper_sha256, "helper_url": helper_url}
    # 固定插入顺序 + 紧凑分隔符 + ASCII 转义：任一处变化都会改变被签名字节。
    return json.dumps(fields, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _create(path, data):
    with path.open("xb") as stream:  # 独占创建：已存在即失败，杜绝悄悄覆盖上一版产物。
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())  # 先落盘再回读，回读结果才代表真实字节。


def _read_back(path, expected):
    raw = path.read_bytes()
    if raw != expected:
        raise SignError(f"{path.name} 落盘字节与内存不一致（可能被文本模式转换）")
    return raw


def _copy_asset(source, destination, expected_sha, expected_size):
    # 独占创建四附件中的可执行文件，复制后再核对，防止签名与发布字节分叉。
    dst = destination.open("xb")
    try:
        with dst, Path(source).open("rb") as src:
            while chunk := src.read(_CHUNK):
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        if _scan_asset(destination, destination.name) != (expected_sha, expected_size):
            raise SignError("发布副本与扫描字节不符")
    except BaseException:  # 已取得独占创建权才清理，绝不能删除别人的既有附件。
        destination.unlink(missing_ok=True)
        raise


def _sign(raw, seed):
    return base64.b64encode(SigningKey(seed).sign(raw).signature)


def _self_check(raw, signature, seed, exe_key):
    derived = bytes(SigningKey(seed).verify_key)
    # 先比 exe：改了公钥却没重新打包时，源码与产物分叉，客户端会全部验签失败。
    if derived != exe_key:
        raise SignError("私钥与 exe 内公钥不匹配：改过 PUBLIC_KEYS 后必须重新构建")
    if exe_key not in updater.PUBLIC_KEYS.values():
        raise SignError("exe 内公钥不在客户端信任列表 PUBLIC_KEYS 中：先加入列表并重新构建")
    # 客户端要求 88 字符标准 base64 单行，内部换行会被严格解码拒绝。
    if len(signature) != 88 or b"\r" in signature or b"\n" in signature:
        raise SignError("签名编码异常：必须是 88 字节单行 base64")
    try:
        VerifyKey(exe_key).verify(raw, base64.b64decode(signature, validate=True))
    except (BadSignatureError, ValueError) as error:
        raise SignError("签名自检失败") from error


def main(argv=None):
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="生成并签名离线发版清单")
    parser.add_argument("--exe", type=Path, default=root / "dist" / EXE_NAME)
    parser.add_argument("--version", default=None, help="缺省取 exe 版本资源，给出时必须与之一致")
    parser.add_argument("--url", required=True, help="Gitee 附件的 browser_download_url")
    parser.add_argument("--helper", type=Path, required=True, help="本次发布的独立 update_helper.exe")
    parser.add_argument("--helper-url", required=True, help="同一Release的helper附件 browser_download_url")
    parser.add_argument("--out", type=Path, default=None, help="缺省 release/v<版本>")
    parser.add_argument("--assume-pubkey", default=None, help="破窗：人工确认的 exe 公钥（64 位 hex）")
    parser.add_argument("--exe-sha256", default=None, help="破窗：exe 实测 SHA-256")
    parser.add_argument("--assume-helper-sha256", default=None, help="破窗：必须与独立helper实测SHA-256一致")
    args = parser.parse_args(argv)
    created = []
    try:
        url = _check_url(args.url)
        helper_url = _check_url(args.helper_url)
        if url == helper_url:
            raise SignError("主包与helper不能指向同一附件")
        sha256, size = _scan_exe(args.exe)
        helper_sha256, helper_size = _scan_asset(args.helper, HELPER_NAME)
        exe_version = _exe_version(args.exe)  # 版本以产物为准，--version 只能确认不能改写。
        version = _check_version(args.version or exe_version)
        if version != exe_version:
            raise SignError(f"--version {version} 与 exe 实际版本 {exe_version} 不符")
        arch = _pe_arch(args.exe)  # 架构同样以产物为准，避免把 arm64／x86 包按 x64 发出去。
        if arch != ARCH:
            raise SignError(f"exe 架构为 {arch}，只允许发布 {ARCH} 产物")
        if _pe_arch(args.helper) != arch:
            raise SignError("helper架构与主包不符")  # 独立附件也必须能在目标架构执行。
        if args.assume_pubkey or args.exe_sha256 or args.assume_helper_sha256:
            # 破窗：只在 PyInstaller 私有 API 变动导致解包失败时使用，且必须绑定产物哈希。
            if not (args.assume_pubkey and args.exe_sha256 and args.assume_helper_sha256):
                raise SignError("破窗参数必须齐备：--assume-pubkey、--exe-sha256、--assume-helper-sha256")
            if args.exe_sha256 != sha256:
                raise SignError("--exe-sha256 与实测哈希不符，拒绝破窗")
            if not _HEX64.fullmatch(args.assume_pubkey) or not _HEX64.fullmatch(args.assume_helper_sha256):
                raise SignError("破窗参数必须是 64 位十六进制")
            exe_key = bytes.fromhex(args.assume_pubkey)
            if args.assume_helper_sha256.lower() != helper_sha256:
                raise SignError("--assume-helper-sha256 与独立helper实测哈希不符")
            print("！破窗模式：跳过归档公钥提取；主包和独立helper仍按实测字节绑定")
        else:
            exe_key = _exe_public_key(args.exe)  # 先取产物公钥，避免写盘后才发现信任根错位。
            if _exe_public_key(args.helper) != exe_key:
                raise SignError("独立helper与主包的内置信任根不一致")
        if exe_key.hex() in _TEST_KEYS:
            raise SignError("exe 内仍是测试公钥，禁止用于真实发行")
        print(f"exe 公钥指纹 {_fingerprint(exe_key)}（应与 gen_signing_key.py 打印的一致）")
        out = args.out or root / "release" / f"v{version}"
        leftovers = [name for name in (MANIFEST_NAME, SIG_NAME, EXE_NAME, HELPER_NAME) if (out / name).exists()]
        if leftovers:
            raise SignError(f"上次残留：{'、'.join(leftovers)}；确认无用后删除 {out} 再重跑")
        out.mkdir(parents=True, exist_ok=True)
        manifest_path, sig_path = out / MANIFEST_NAME, out / SIG_NAME
        raw = _build_manifest(version, size, sha256, url, arch, helper_sha256, helper_size, helper_url)
        _create(manifest_path, raw)
        created.append(manifest_path)
        raw = _read_back(manifest_path, raw)  # 之后只签这份回读字节。
        seed = _read_seed()
        signature = _sign(raw, seed)
        _self_check(raw, signature, seed, exe_key)
        _create(sig_path, signature)
        created.append(sig_path)
        for source, name, expected_sha, expected_size in (
                (args.exe, EXE_NAME, sha256, size), (args.helper, HELPER_NAME, helper_sha256, helper_size)):
            destination = out / name
            _copy_asset(source, destination, expected_sha, expected_size)
            created.append(destination)
        del seed  # 签名与自检已结束，主动缩短私钥引用寿命（不清零）。
    except BaseException as error:
        for path in created:  # 只清理本次创建的文件，不动目录内既有内容。
            try:
                path.unlink()
            except OSError:
                pass  # 清理失败不掩盖原始错误，异常信息已说明失败原因。
        # 中断与已知错误给单行提示；SystemExit 等其余异常原样抛出，不吞掉退出码。
        if isinstance(error, KeyboardInterrupt):
            print("已取消：本次产物已清理", file=sys.stderr)
            return 1
        if isinstance(error, (SignError, OSError, ValueError)):
            print(f"签名失败：{error}", file=sys.stderr)
            return 1
        raise
    print(f"版本 {version}／架构 {arch}／{size} 字节／sha256 {sha256}")
    print(f"helper {helper_size} 字节／sha256 {helper_sha256}／{helper_url}")
    print(f"下载地址 {url}")
    print(f"公钥指纹 {_fingerprint(exe_key)}")
    print(f"清单 {manifest_path}（{len(raw)} 字节）")
    print(f"签名 {sig_path}（{len(signature)} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
