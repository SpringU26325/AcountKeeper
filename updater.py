"""更新候选准备与验签；当前公钥仅供测试，禁止用于真实发行信任。"""
import base64
import binascii
from dataclasses import dataclass, replace
from email.utils import parsedate_to_datetime
import hashlib
import http.client
import json
import os
from pathlib import Path
import queue
import re
import secrets
import socket
import ssl
import sys
import threading
import time
from urllib.parse import urljoin, urlsplit
from nacl.bindings import crypto_core_ed25519_is_valid_point
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey
from update_helper import (journal_path, state_path, transaction_root, payload_path,
                           transaction_entries, remove_transaction_dirs)

# 信任列表：键为公钥指纹（SHA-256 前 8 位，形如 "B52A-0FD5"）。
PUBLIC_KEYS = {
    "0762-4AAB": bytes.fromhex("6dfe1c8ffb59ec70dcde49df7377dbcaea376187526460cd782bf701088cca8e"),
}


# 已知测试公钥：打包产物一旦命中即拒绝启动；源码态放行，否则当前测试钥阶段连探针都无法导入。
_TEST_KEYS = {"27ddbc844141850e7b744205c52d232e0f24b0c4d072cc924495470e62f08b4f"}


def key_id(public):
    """公钥指纹：与签名脚本同算法，供人眼核对，并作为 ready.json 的验签留痕。"""
    text = hashlib.sha256(public).hexdigest()[:8].upper()
    return f"{text[:4]}-{text[4:]}"


def _check_public_key():
    # 种子与公钥同为 32 字节，无法确定判定；点校验能挡住约 94% 的误粘
    # （实测随机 32 字节仅 11/200 通过），剩余漏网由签名脚本的三方比对兜住。
    # 测试钥拦截放在验签期（见 verify_offer）：启动期拒绝会让依赖测试钥的夹具无法运行。
    if not PUBLIC_KEYS:
        raise RuntimeError("PUBLIC_KEYS 为空，任何更新都无法验证")
    for name, public in PUBLIC_KEYS.items():
        if name != key_id(public):
            raise RuntimeError(f"PUBLIC_KEYS 的键 {name} 与公钥指纹不符")
        if len(public) != 32 or not crypto_core_ed25519_is_valid_point(public):
            raise RuntimeError(f"PUBLIC_KEYS[{name}] 不是合法的 Ed25519 公钥，疑似误粘私钥")


_check_public_key()  # 导入即拒绝：宁可启动失败，也不能带着错误信任根发版。
_API = "https://gitee.com/api/v5/repos/SpringU26325/account-keeper/releases"
_HOSTS = {"gitee.com", "foruda.gitee.com"}
_FILES = {"candidate.exe.part", "candidate.exe", "manifest.json", "manifest.sig",
          "ready.json.tmp", "ready.json", "owner.json", "update_helper.exe", "update_helper.exe.part",
          "pending.json", "stop.json", "healthy.json", "failure.json", "commit.json"}


class UpdateError(Exception):
    """拒绝不可信或不完整的更新候选。"""


class Cancelled(UpdateError):
    """取消不能被当作可重试的网络故障。"""


class _Transient(UpdateError):
    """可重试的传输中断：断流或截断，不改变信任判断。"""


class _Restart(Exception):
    """服务端忽略 Range，调用方必须截断已下载部分后从头重下。"""


class RateLimited(UpdateError):
    def __init__(self, retry_at):
        super().__init__("更新源限流，请稍后重试")
        self.retry_at = retry_at


@dataclass(frozen=True)
class Offer:
    release_id: int
    version: str
    assets: tuple[str, str, str, str]
    manifest: bytes
    signature: bytes
    attempt: dict | None = None

def _version(text):
    if not isinstance(text, str) or not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", text):
        raise UpdateError("非法稳定版本")
    result = tuple(map(int, text.split(".")))
    if max(result) > 65535:
        raise UpdateError("版本字段越界")
    return result

def _url(text):
    # 解析后的精确主机名才是边界；拒绝凭据、片段及控制字符。
    if not isinstance(text, str) or len(text) > 8192 or any(ord(c) <= 32 or ord(c) == 127 for c in text):
        raise UpdateError("非法下载URL")
    try:
        p = urlsplit(text)
        valid = p.scheme == "https" and p.hostname in _HOSTS and p.port in (None, 443)
        if not valid or p.username is not None or p.password is not None or p.fragment:
            raise ValueError()
    except ValueError as error:  # 端口解析也可能失败，一律拒绝非法来源。
        raise UpdateError("URL不属于HTTPS来源白名单") from error
    return p

def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise UpdateError("JSON含重复键")
        result[key] = value
    return result

def _json(raw):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except (UnicodeError, ValueError) as error:  # 不接受无法按UTF-8严格解释的材料。
        raise UpdateError("非法JSON") from error

def verify_offer(offer, current_version, arch):
    if not isinstance(offer, Offer) or type(offer.release_id) is not int or offer.release_id < 1:
        raise UpdateError("候选快照非法")
    if not isinstance(offer.assets, tuple) or len(offer.assets) != 4 or len(set(offer.assets)) != 4:
        raise UpdateError("附件快照不完整")
    for url in offer.assets:
        _url(url)
    if not isinstance(offer.manifest, bytes) or len(offer.manifest) > 65536 or not isinstance(offer.signature, bytes) or len(offer.signature) > 1024:
        raise UpdateError("清单或签名越界")
    try:
        signature = base64.b64decode(offer.signature.rstrip(b"\r\n"), validate=True)
        if len(signature) != 64:
            raise ValueError("签名长度错误")
        # 遍历信任列表：轮换期间新旧公钥并存，任一把验过即接受，未知钥自然被拒。
        matched = None
        for public in PUBLIC_KEYS.values():
            try:
                VerifyKey(public).verify(offer.manifest, signature)
                matched = public
                break
            except BadSignatureError:
                continue
        if matched is None:
            raise ValueError("信任列表内所有公钥均验签失败")
        if matched.hex() in _TEST_KEYS:
            # 验签期拦截：正式产物可以正常启动，但绝不接受测试钥签署的清单。
            raise UpdateError("清单由内置测试公钥签署，正式产物拒绝该更新")
    except (BadSignatureError, ValueError, TypeError, binascii.Error) as error:  # 密码学失败绝不降级为哈希验证。
        raise UpdateError("Ed25519验签失败") from error
    m = _json(offer.manifest)
    fields = {"schema", "app", "version", "channel", "arch", "size", "sha256", "url",
              "helper_size", "helper_sha256", "helper_url"}
    # 字段集合严格相等：清单里多出任何字段（例如自带公钥）都会被整份拒绝。
    if not isinstance(m, dict) or set(m) != fields or type(m["schema"]) is not int or m["schema"] != 3:
        raise UpdateError("清单格式不受支持")
    if m["app"] != "AccountKeeper" or m["channel"] != "stable" or not isinstance(arch, str) or m["arch"] != arch:
        raise UpdateError("应用／渠道／架构不符")
    if _version(m["version"]) <= _version(current_version) or m["version"] != offer.version:
        raise UpdateError("版本不符或不是升级")
    if type(m["size"]) is not int or not 1 <= m["size"] <= 256 * 1024 * 1024:
        raise UpdateError("包大小非法")
    if not isinstance(m["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", m["sha256"]) or m["url"] != offer.assets[2]:
        raise UpdateError("包哈希／同Release附件绑定不符")
    if not isinstance(m["helper_sha256"], str) or not re.fullmatch("[0-9a-f]{64}", m["helper_sha256"]):
        raise UpdateError("helper哈希不符")
    if (type(m["helper_size"]) is not int or not 1 <= m["helper_size"] <= 256 * 1024 * 1024
            or m["helper_url"] != offer.assets[3]):
        raise UpdateError("helper大小／同Release附件绑定不符")  # 独立附件必须同时受签名与Release快照约束。
    return m


class _Budget:
    def __init__(self, cancel):
        self.cancel = cancel if cancel is not None else threading.Event()
        self.end, self.done, self.sock = time.monotonic() + 300, threading.Event(), None

    def check(self):
        if self.cancel.is_set():
            raise Cancelled("下载已取消")
        if time.monotonic() >= self.end:
            raise TimeoutError("更新操作超过总期限")

    def timeout(self):
        self.check()
        return min(10, self.end - time.monotonic())

    def abort(self):
        if (sock := self.sock) is not None:  # 固定对象引用，避免关闭线程清空字段的竞态。
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:  # 关闭竞态不影响取消结果；禁止以此判请求成功。
                pass  # 对象可能已关闭；这里只唤醒本次I/O，不伪造请求成功。

    def __enter__(self):
        def watch():
            while not self.done.wait(0.02):
                if self.cancel.is_set() or time.monotonic() >= self.end:
                    self.abort()
                    return
        self.thread = threading.Thread(target=watch, daemon=True)
        self.thread.start()
        self.check()
        return self

    def __exit__(self, *args):
        self.done.set()
        self.abort()
        self.thread.join(1)

    def resolve(self, host):
        # 解析线程只返回地址；迟到结果永远不能连接、写盘或阻止取消返回。
        result = queue.Queue()
        def lookup():
            try:
                result.put((True, socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
            except OSError as error:  # 向等待方传递解析失败，不在后台发请求。
                result.put((False, error))
        threading.Thread(target=lookup, daemon=True).start()
        while True:
            self.check()
            try:
                ok, value = result.get(timeout=min(0.05, self.timeout()))
                self.check()
                if not ok:
                    raise value
                return value
            except queue.Empty:  # 短轮询保证DNS挂起时仍及时响应取消。
                continue

    def connect(self, host):
        for family, kind, proto, _, address in self.resolve(host):
            self.check()
            raw = socket.socket(family, kind, proto)
            self.sock = raw
            try:
                raw.settimeout(self.timeout())
                raw.connect(address)
                self.check()
                # 先登记SSL对象再握手，监督任务才能中断握手／响应头／慢速读取。
                self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host, do_handshake_on_connect=False)
                self.sock.settimeout(self.timeout())
                self.sock.do_handshake()
                self.check()
                return self.sock
            except OSError as error:  # 换地址前关闭旧对象，证书错误保持硬拒绝。
                if self.sock is not None:
                    self.sock.close()
                raw.close()
                if isinstance(error, ssl.SSLCertVerificationError):
                    raise  # 证书失败不能重试或降级；已关闭登记的SSL对象。
                last = error
                self.check()
        raise last if "last" in locals() else UpdateError("DNS无可用地址")

def _stream(url, limit, budget, sink, expected=None, progress=None, start=0, digest=None):
    for hop in range(4):
        p = _url(url)
        connection = http.client.HTTPSConnection(p.hostname, 443, timeout=budget.timeout())
        response = None
        try:
            connection.sock = budget.connect(p.hostname)
            headers = {"User-Agent": "AccountKeeper", "Accept-Encoding": "identity"}
            if start:
                headers["Range"] = f"bytes={start}-"  # 续传只取剩余部分；服务端不回206即视为不支持。
            connection.request("GET", (p.path or "/") + ("?" + p.query if p.query else ""), headers=headers)
            response = connection.getresponse()
            budget.check()
            if response.status in (301, 302, 303, 307, 308):
                if hop == 3 or not response.getheader("Location"):
                    raise UpdateError("重定向超过3跳或缺少Location")
                url = urljoin(url, response.getheader("Location"))
                _url(url)  # 必须在下一次DNS／请求前拦截，不事后检查最终URL。
                continue
            if response.status == 429:
                value = response.getheader("Retry-After", "")
                try:
                    if value.isascii() and value.isdigit():
                        digits = value.lstrip("0") or "0"
                        delay = 86400 if len(digits) > 5 else int(digits)  # 超长数字不转大整数，避免位数限制及浮点溢出。
                    else:
                        delay = parsedate_to_datetime(value).timestamp() - time.time()
                except (ValueError, TypeError, OverflowError, OSError):  # Windows不支持的日期也按无效冷却处理。
                    delay = 60
                raise RateLimited(time.time() + min(86400, max(0, delay)))  # 外部元数据最多使检查冷却24小时。
            if 500 <= response.status <= 599:
                raise OSError("更新源暂时不可用")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise UpdateError("HTTP状态或编码不受支持")
            if response.status == 206 and start:
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)",
                                     (response.getheader("Content-Range") or "").strip())
                # 续传必须精确衔接：起点等于已有字节数，终点是总长末字节，总长等于清单声明。
                if (not match or int(match.group(1)) != start or int(match.group(3)) != expected
                        or int(match.group(2)) != expected - 1):
                    raise UpdateError("续传范围不符")
                declared_expected = expected - start
            elif response.status == 200:
                if start:
                    raise _Restart("服务端忽略 Range")
                declared_expected = expected
            else:
                raise UpdateError("HTTP状态或编码不受支持")
            declared = response.getheader("Content-Length")
            if declared is not None and (not declared.isascii() or not declared.isdigit() or int(declared) > limit
                                         or (declared_expected is not None and int(declared) != declared_expected)):
                raise UpdateError("声明长度不符")
            received, digest = 0, digest or hashlib.sha256()
            while True:
                budget.check()
                if budget.sock is not None:
                    budget.sock.settimeout(budget.timeout())
                chunk = response.read1(65536)
                budget.check()
                if not chunk:
                    break
                received += len(chunk)
                if start + received > limit:
                    raise UpdateError("接收字节数越界")
                sink(chunk)
                digest.update(chunk)
                if progress:
                    progress(start + received, expected)
            if (declared is not None and received != int(declared)) or (expected is not None and start + received != expected):
                raise _Transient("下载提前中断")
            return digest.hexdigest()
        except (OSError, http.client.HTTPException):
            budget.check()  # 将监督任务唤醒的错误归为真正的超时／取消。
            raise
        finally:
            if response is not None:
                response.close()
            connection.close()
            if budget.sock is not None:
                budget.sock.close()  # 连接尚未交给HTTPConnection时也必须释放。
            budget.sock = None

def _fetch(url, limit, budget):
    for attempt in range(3):
        chunks = []
        try:
            _stream(url, limit, budget, chunks.append)
            return b"".join(chunks)
        except ssl.SSLCertVerificationError:  # 证书失败不消耗瞬态重试额度。
            raise
        except OSError:  # 仅对瞬态I/O故障执行有界退避，不重试校验失败。
            budget.check()
            if attempt == 2:
                raise
            until = time.monotonic() + 2 ** attempt + secrets.randbelow(1000) / 1000
            while time.monotonic() < until:
                budget.check()
                budget.cancel.wait(min(0.05, budget.timeout()))

def check_updates(current_version, arch, cancel=None, *, target_exe=None, manual=False):
    from update_helper import begin_attempt, settle_attempt
    if target_exe is None:
        raise UpdateError("检查需要明确的目标更新上下文")
    try:
        cleanup_stale(target_exe, current_version)  # 启动即自愈：上次崩溃留下的暂存材料在此回收。
    except (OSError, UpdateError):
        pass  # 清理是尽力而为，不能因为它挡住检查本身。
    attempt = begin_attempt(target_exe, manual)
    try:
        offer = _check_updates(current_version, arch, cancel)
        if offer is None:
            settle_attempt(target_exe, attempt, "success", "没有可用更新")
            return None
        return replace(offer, attempt=attempt)
    except Exception as error:  # 重试由网络层控制，只结算最外层的一次尝试。
        # 断网、限流、超时不是用户的错：记为 cancelled 不计失败次数，否则正常使用也会被暂停。
        transient = isinstance(error, (Cancelled, RateLimited, OSError, http.client.HTTPException))
        settle_attempt(target_exe, attempt, "cancelled" if transient else "failed", repr(error))
        raise


def _check_updates(current_version, arch, cancel=None):
    current = _version(current_version)  # 调用参数错误必须显式拒绝，不能伪装无更新。
    releases = []
    with _Budget(cancel) as budget:
        for page in range(1, 4):
            entries = _json(_fetch(_API + f"?per_page=20&page={page}", 1048576, budget))
            if not isinstance(entries, list):
                raise UpdateError("Release列表非法")
            releases.extend(entries)
            if len(entries) < 20:
                break
        choices = []
        for release in releases:
            if not isinstance(release, dict) or release.get("prerelease") is not False or release.get("draft", False):
                continue
            tag = release.get("tag_name")
            try:
                version = tag.removeprefix("v") if isinstance(tag, str) else ""
                if _version(version) > current:
                    choices.append((_version(version), release, version))
            except UpdateError:
                continue  # API中的预发布／非法版本不是稳定升级候选。
        if not choices:
            return None
        # 按版本降序最多试 3 个：最新 Release 畸形时退回次新，避免全量客户端一起卡死。
        failure = None
        for _, release, version in sorted(choices, key=lambda item: item[0], reverse=True)[:3]:
            try:
                return _offer_from(release, version, current_version, arch, budget)
            except UpdateError as error:  # 只降级"该 Release 不可用"，网络故障不在此列。
                failure = failure or error
        raise failure


def _offer_from(release, version, current_version, arch, budget):
    """取指定 Release 的附件并验签；任何不符都抛 UpdateError 交给上层决定是否降级。"""
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("缺少附件列表")
    urls = []
    for name in ("manifest.json", "manifest.sig", "AccountKeeper.exe", "update_helper.exe"):
        matches = [a for a in assets if isinstance(a, dict) and a.get("name") == name]
        if len(matches) != 1:
            raise UpdateError("同Release附件缺失或重名")
        url = matches[0].get("browser_download_url")
        _url(url)
        urls.append(url)
    offer = Offer(release.get("id"), version, tuple(urls),
                  _fetch(urls[0], 65536, budget), _fetch(urls[1], 1024, budget))
    verify_offer(offer, current_version, arch)
    return offer

def _target(path):
    path = Path(path).resolve(strict=True)
    if path.name != "AccountKeeper.exe" or not path.is_file():
        raise UpdateError("目标不是AccountKeeper.exe")
    return path

def _base(target):
    base = transaction_root(target)  # 小型交接材料归DATA_DIR，大文件另放同卷目录。
    if base.resolve() != base:
        raise UpdateError("事务记录目录越界")
    return base

def _small(path, limit=65536):
    if path.resolve() != path or not path.is_file():
        raise UpdateError("交接文件越界")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise UpdateError("交接文件过大")
    return raw

def _owned(work, target):
    payload = payload_path(work, target, "candidate.exe").parent
    if (not re.fullmatch("[0-9a-f]{32}", work.name) or work.resolve() != work
            or (payload.exists() and payload.stat().st_dev != target.stat().st_dev)):
        raise UpdateError("事务目录归属不符")
    owner = _json(_small(work / "owner.json", 4096))
    if not isinstance(owner, dict) or type(owner.get("schema")) is not int or owner != {"schema": 1, "transaction_id": work.name, "target": str(target)}:
        raise UpdateError("owner记录不符")


def protected_materials(work, target):
    from update_helper import read_json
    try:
        for path in (journal_path(target), journal_path(target, archived=True), state_path(target)):
            if not path.exists():
                continue
            record = read_json(path)
            if record.get("work") == str(work):
                return True
            for attempt in (record.get("active"), (record.get("last") or {}).get("attempt")):
                if attempt and (attempt.get("binding") or {}).get("work") == str(work):
                    return True  # 外层日志、归档或持久绑定仍引用时，整个验签证据目录都保留。
        return (work / "commit.json").exists()
    except Exception:
        return True  # 无法解释归属时禁止清理，不能用删除现场来掩盖损坏。


def _remove(work, target):
    if protected_materials(work, target):
        raise UpdateError("事务证据仍被引用，禁止删除")
    _owned(work, target)
    entries = transaction_entries(work, target)
    if any(p.name not in _FILES or p.resolve() != p or not p.is_file() for p in entries):
        raise UpdateError("存在非本事务文件，停止清理")
    for path in entries:
        if path.name != "owner.json":
            path.unlink()
    (work / "owner.json").unlink()
    remove_transaction_dirs(work, target)


def cleanup_incomplete(target_exe):
    from update_helper import Busy, FileLock
    target = _target(target_exe)
    try:
        with FileLock(target, "install", 0):
            _cleanup_incomplete(target)
    except Busy:
        return  # 安装正在发布绑定，不能在归属检查与删除之间穿插交接。


def _cleanup_incomplete(target):
    base = _base(target)
    if base.exists():
        for work in base.iterdir():
            if re.fullmatch("[0-9a-f]{32}", work.name) and work.is_dir() and not (work / "ready.json").exists():
                try:
                    _remove(work, target)
                except (OSError, UpdateError):  # 归属不明或被占用：保留现场，但不阻断本次下载。
                    continue


_STALE_SECONDS = 7 * 86400


def cleanup_stale(target_exe, current_version):
    """启动或下次检查时回收暂存目录；返回实际回收数量。"""
    from update_helper import Busy, FileLock
    target = _target(target_exe)
    try:
        with FileLock(target, "install", 0), FileLock(target, "download", 0):  # 清理和事务发布互斥。
            return _sweep(target, current_version)
    except Busy:
        return 0


def _sweep(target, current_version):
    base = _base(target)
    if not base.exists():
        return 0
    removed = 0
    for work in list(base.iterdir()):
        if not (re.fullmatch("[0-9a-f]{32}", work.name) and work.is_dir()):
            continue
        if protected_materials(work, target):
            continue
        if not _stale(work, target, current_version):
            continue
        try:
            _remove(work, target)
            removed += 1
        except UpdateError:
            if (work / "owner.json").exists():  # 归属不符：可能属于别的事务，保留现场。
                continue
            entries = transaction_entries(work, target)
            # 归属文件都没写成就崩了：只回收白名单文件，避免目录永久占位。
            if any(p.name not in _FILES or p.resolve() != p or not p.is_file() for p in entries):
                continue
            for path in entries:
                path.unlink()
            remove_transaction_dirs(work, target)
            removed += 1
        except OSError:
            continue  # 被占用：下次再试，不影响本次检查。
    return removed


def _stale(work, target, current_version):
    ready = work / "ready.json"
    if not ready.exists():
        return True  # 没发布就绪就是半套材料。
    try:
        r = _json(_small(ready))
        if not isinstance(r, dict) or not isinstance(r.get("version"), str):
            return True
        if _version(r["version"]) <= _version(current_version):
            return True  # 候选不比当前版本新，永远不会被安装。
        candidate = payload_path(work, target, "candidate.exe")
        if not candidate.is_file() or candidate.stat().st_size != r.get("size"):
            return True
    except (OSError, UpdateError, TypeError):  # 解析失败一律按损坏处理。
        return True
    return time.time() - ready.stat().st_mtime > _STALE_SECONDS


def _record(path, value):
    # 独占写入并落盘；ready由调用者最后原子发布，不能暴露半份JSON。
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())


def download(offer, target_exe, current_version, arch, cancel=None, progress=None, *, manual=False):
    from update_helper import FileLock
    with FileLock(target_exe, "download", 0):  # 同一目标只允许一个下载执行者，不误结算别人的失败。
        return _download_attempt(offer, target_exe, current_version, arch, cancel, progress, manual)


def _download_attempt(offer, target_exe, current_version, arch, cancel, progress, manual):
    from update_helper import begin_attempt, read_state, settle_attempt
    attempt = offer.attempt or begin_attempt(target_exe, manual)
    state = read_state(target_exe)
    if not state["active"] or any(state["active"][k] != attempt[k] for k in ("id", "seq")):
        raise UpdateError("下载尝试已过期")
    try:
        return _download(replace(offer, attempt=attempt), target_exe, current_version, arch, cancel, progress)
    except BaseException as error:  # ready仅是交接，不清零；失败/取消才在此结算。
        settle_attempt(target_exe, attempt, "cancelled" if isinstance(error, (Cancelled, KeyboardInterrupt)) else "failed", repr(error))
        raise


def _download_file(m, part, cancel, progress):
    """整次下载共享一个 300 秒预算，最多 3 次尝试；已落盘部分用 Range 续传。"""
    last = None
    with _Budget(cancel) as budget:
        for attempt in range(3):
            if cancel is not None and cancel.is_set():
                raise Cancelled("下载已取消")
            start = part.stat().st_size if part.exists() else 0
            if start > m["size"]:  # 越界残片不可信，直接丢弃重下。
                part.unlink()
                start = 0
            digest = hashlib.sha256()
            if start:  # 续传前重算前缀哈希，否则最终摘要只覆盖后半段。
                with part.open("rb") as prefix:
                    while chunk := prefix.read(1 << 20):
                        digest.update(chunk)
            try:
                with part.open("ab" if start else "wb") as stream:
                    _stream(m["url"], m["size"], budget, stream.write, m["size"], progress, start, digest)
                    stream.flush()
                    os.fsync(stream.fileno())
                if part.stat().st_size != m["size"]:
                    raise _Transient("下载长度不符")
                return digest.hexdigest()
            except _Restart:  # 服务端忽略 Range：清空后按同一预算从头重下。
                part.unlink(missing_ok=True)
                last = None
                continue
            except (OSError, http.client.HTTPException, _Transient) as error:
                last = error
                if attempt == 2:
                    break
                budget.check()  # 预算耗尽直接抛超时，不再重试。
                until = time.monotonic() + 2 ** attempt
                while time.monotonic() < until:  # 有界退避，且随时响应取消与超时。
                    budget.check()
                    budget.cancel.wait(min(0.05, budget.timeout()))
    raise last if last is not None else UpdateError("下载失败")


def _download(offer, target_exe, current_version, arch, cancel=None, progress=None):
    m = verify_offer(offer, current_version, arch)
    target = _target(target_exe)
    if cancel is not None and cancel.is_set():
        raise Cancelled("下载已取消")
    cleanup_incomplete(target)
    base = _base(target)
    base.mkdir(parents=True, exist_ok=True)
    work = base / secrets.token_hex(16)
    work.mkdir()
    ready = work / "ready.json"
    try:
        _record(work / "owner.json", {"schema": 1, "transaction_id": work.name, "target": str(target)})
        payload_path(work, target, "candidate.exe").parent.mkdir(parents=True, exist_ok=False)  # 创建失败时仍能凭owner清理轻量记录。
        _owned(work, target)
        digest = _download_file(m, payload_path(work, target, "candidate.exe.part"), cancel, progress)
        if digest != m["sha256"]:
            raise UpdateError("包SHA-256不符")
        if cancel is not None and cancel.is_set():
            raise Cancelled("下载已取消")
        (payload_path(work, target, "candidate.exe.part")).rename(payload_path(work, target, "candidate.exe"))
        helper_meta = dict(size=m["helper_size"], sha256=m["helper_sha256"], url=m["helper_url"])
        helper_part = payload_path(work, target, "update_helper.exe.part")
        if _download_file(helper_meta, helper_part, cancel, None) != m["helper_sha256"]:
            raise UpdateError("helper SHA-256不符")  # 与主包共用下载器，大小、续传、取消和300秒期限口径一致。
        helper_part.rename(payload_path(work, target, "update_helper.exe"))
        (work / "manifest.json").write_bytes(offer.manifest)
        (work / "manifest.sig").write_bytes(offer.signature)
        record = dict(schema=1, transaction_id=work.name, release_id=offer.release_id,
                      target=str(target), candidate=str(payload_path(work, target, "candidate.exe")), version=m["version"],
                      arch=arch, size=m["size"], sha256=m["sha256"], manifest=str(work / "manifest.json"),
                      signature=str(work / "manifest.sig"), assets=list(offer.assets), attempt=offer.attempt)
        _record(work / "ready.json.tmp", record)
        if cancel is not None and cancel.is_set():
            raise Cancelled("下载已取消")
        (work / "ready.json.tmp").replace(ready)
        return ready
    except BaseException as error:  # 包含调用方中断；就绪发布前均回收本事务。
        if not ready.exists():
            try:
                _remove(work, target)
            except (OSError, UpdateError) as cleanup_error:  # 清理失败明确保留路径，不能冒充成功。
                raise UpdateError(f"清理失败，残留目录：{work}；{cleanup_error}") from error
        raise


def verify_ready(ready_path, target_exe, current_version, arch, *, with_manifest=False):
    # helper可以独立复验；ready字段和磁盘路径均不是信任根。
    target = _target(target_exe)
    ready = Path(ready_path).absolute()
    work = ready.parent
    if ready.name != "ready.json" or work.parent != _base(target):
        raise UpdateError("ready路径越界")
    _owned(work, target)
    r = _json(_small(ready))
    if not isinstance(r, dict) or type(r.get("schema")) is not int or r.get("schema") != 1 or r.get("transaction_id") != work.name or r.get("target") != str(target):
        raise UpdateError("ready身份不符")
    for key, name in (("candidate", "candidate.exe"), ("manifest", "manifest.json"), ("signature", "manifest.sig")):
        expected = payload_path(work, target, name) if key == "candidate" else work / name
        if r.get(key) != str(expected):
            raise UpdateError("交接路径不符")
    offer = Offer(r.get("release_id"), r.get("version"), tuple(r.get("assets", ())),
                  _small(work / "manifest.json"), _small(work / "manifest.sig", 1024))
    m = verify_offer(offer, current_version, arch)
    if any(r.get(key) != m[key] for key in ("version", "arch", "size", "sha256")):
        raise UpdateError("ready与签名清单不符")
    candidate = payload_path(work, target, "candidate.exe")
    verify_payload(candidate, m["size"], m["sha256"])
    verify_payload(payload_path(work, target, "update_helper.exe"), m["helper_size"], m["helper_sha256"])
    return (candidate, m) if with_manifest else candidate


def verify_payload(path, expected_size, expected_sha):
    # 下载后和交接前都读盘上字节；内存摘要不能证明候选仍完整。
    digest, size = hashlib.sha256(), 0
    if path.resolve() != path:
        raise UpdateError("候选路径越界")
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            size += len(chunk)
            if size > expected_size:
                raise UpdateError("候选大小越界")
            digest.update(chunk)
    if size != expected_size or digest.hexdigest() != expected_sha:
        raise UpdateError("候选已被改写")
