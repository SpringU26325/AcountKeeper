"""生成 Ed25519 发版密钥对；私钥只在交互终端显示一次，不写盘、不进日志。

本脚本不 import 签名脚本：生成密钥不需要知道客户端，任一侧被替换都不牵连另一侧。
私钥以明文显示在终端，屏幕缓冲与回滚区会留存，必须按末行提示立即清理。
"""
import hashlib
import sys

from nacl.signing import SigningKey

_CONFIRM_WORDS = {"SHOW", "YES"}


def _fingerprint(public):
    """公钥指纹：与 sign_release.py 同算法，供签名时人眼核对。"""
    text = hashlib.sha256(public).hexdigest()[:16].upper()
    return "-".join(text[index:index + 4] for index in range(0, 16, 4))


def _require_tty():
    # 私钥只显示一次；输出被重定向或 IDE 捕获，等于把私钥写进日志文件。
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise RuntimeError("需要交互终端：请在 cmd 或 PowerShell 窗口运行，IDE 内置终端不可用")


def _confirm():
    # 根私钥上屏即无法收回，生成前必须由人明确确认，避免误运行就直接暴露。
    answer = input("即将在本终端显示根私钥。确认请输入 SHOW 或 YES（其他输入直接退出）：")
    return answer.strip().upper() in _CONFIRM_WORDS  # 确认词不是机密，容错归一没有风险。


def main():
    try:
        _require_tty()
    except RuntimeError as error:
        print(f"生成失败：{error}", file=sys.stderr)
        return 1
    if not _confirm():
        print("已取消：未生成、未显示任何密钥。")
        return 1
    key = SigningKey.generate()
    public = bytes(key.verify_key)
    fingerprint = _fingerprint(public)
    # 先打公钥：公钥可公开，先出可避免私钥滚出屏幕后连公钥也找不到。
    print("公钥（64 位 hex，可公开）：")
    print(public.hex())
    # 长指纹前9个字符是8位摘要加中间连字符，与客户端字典键保持一致，避免手拼出错。
    print(f'"{fingerprint[:9]}": bytes.fromhex("{public.hex()}"),')
    print("请直接将上述完整行复制，替换 updater.py 中 PUBLIC_KEYS 字典里的旧行")
    print(f"公钥指纹 {fingerprint}（这是用于人眼核对签名的指纹，不要填进代码；签名时应与 sign_release.py 打印的一致）")
    print()
    print("私钥（64 位 hex，仅显示这一次；离线保存，禁止入库、入云、截图上传）：")
    print(bytes(key).hex())
    print()
    print("！立即截图或抄写私钥，随后执行 cls 清理屏幕缓冲：本窗口回滚区仍可翻出私钥。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
