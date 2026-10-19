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
import threading
import time
from urllib.parse import urljoin, urlsplit
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

# TEST ONLY：对应私钥仅在隔离探针；真实发布前必须更换公钥。
PUBLIC_KEY = bytes.fromhex("27ddbc844141850e7b744205c52d232e0f24b0c4d072cc924495470e62f08b4f")
_API = "https://gitee.com/api/v5/repos/zhuo-chunyu/account-keeper/releases"
_HOSTS = {"gitee.com", "foruda.gitee.com"}
_FILES = {"candidate.exe.part", "candidate.exe", "manifest.json", "manifest.sig",
          "ready.json.tmp", "owner.json"}


class UpdateError(Exception):
    """拒绝不可信或不完整的更新候选。"""


class Cancelled(UpdateError):
    """取消不能被当作可重试的网络故障。"""


class RateLimited(UpdateError):
    def __init__(self, retry_at):
        super().__init__("更新源限流，请稍后重试")
        self.retry_at = retry_at


@dataclass(frozen=True)
class Offer:
    release_id: int
    version: str
    assets: tuple[str, str, str]
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
    if not isinstance(offer.assets, tuple) or len(offer.assets) != 3:
        raise UpdateError("附件快照不完整")
    for url in offer.assets:
        _url(url)
    if not isinstance(offer.manifest, bytes) or len(offer.manifest) > 65536 or not isinstance(offer.signature, bytes) or len(offer.signature) > 1024:
        raise UpdateError("清单或签名越界")
    try:
        signature = base64.b64decode(offer.signature.rstrip(b"\r\n"), validate=True)
        if len(signature) != 64 or len(PUBLIC_KEY) != 32:
            raise ValueError("签名／公钥长度错误")
        VerifyKey(PUBLIC_KEY).verify(offer.manifest, signature)
    except (BadSignatureError, ValueError, TypeError, binascii.Error) as error:  # 密码学失败绝不降级为哈希验证。
        raise UpdateError("Ed25519验签失败") from error
    m = _json(offer.manifest)
    fields = {"schema", "app", "version", "channel", "arch", "size", "sha256", "url"}
    if not isinstance(m, dict) or set(m) != fields or type(m["schema"]) is not int or m["schema"] != 1:
        raise UpdateError("清单格式不受支持")
    if m["app"] != "AccountKeeper" or m["channel"] != "stable" or not isinstance(arch, str) or m["arch"] != arch:
        raise UpdateError("应用／渠道／架构不符")
    if _version(m["version"]) <= _version(current_version) or m["version"] != offer.version:
        raise UpdateError("版本不符或不是升级")
    if type(m["size"]) is not int or not 1 <= m["size"] <= 256 * 1024 * 1024:
        raise UpdateError("包大小非法")
    if not isinstance(m["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", m["sha256"]) or m["url"] != offer.assets[2]:
        raise UpdateError("包哈希／同Release附件绑定不符")
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

def _stream(url, limit, budget, sink, expected=None, progress=None):
    for hop in range(4):
        p = _url(url)
        connection = http.client.HTTPSConnection(p.hostname, 443, timeout=budget.timeout())
        response = None
        try:
            connection.sock = budget.connect(p.hostname)
            connection.request("GET", (p.path or "/") + ("?" + p.query if p.query else ""),
                               headers={"User-Agent": "AccountKeeper", "Accept-Encoding": "identity"})
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
                    delay = int(value) if value.isascii() and value.isdigit() else parsedate_to_datetime(value).timestamp() - time.time()
                except (ValueError, TypeError, OverflowError):  # 无效限流时间使用保守冷却。
                    delay = 60
                raise RateLimited(time.time() + max(0, delay))
            if 500 <= response.status <= 599:
                raise OSError("更新源暂时不可用")
            if response.status != 200 or response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise UpdateError("HTTP状态或编码不受支持")
            declared = response.getheader("Content-Length")
            if declared is not None and (not declared.isascii() or not declared.isdigit() or int(declared) > limit or (expected is not None and int(declared) != expected)):
                raise UpdateError("声明长度不符")
            total, digest = 0, hashlib.sha256()
            while True:
                budget.check()
                if budget.sock is not None:
                    budget.sock.settimeout(budget.timeout())
                chunk = response.read1(65536)
                budget.check()
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise UpdateError("接收字节数越界")
                sink(chunk)
                digest.update(chunk)
                if progress:
                    progress(total, expected)
            if (declared is not None and total != int(declared)) or (expected is not None and total != expected):
                raise UpdateError("下载提前中断")
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
    attempt = begin_attempt(target_exe, manual)
    try:
        offer = _check_updates(current_version, arch, cancel)
        if offer is None:
            settle_attempt(target_exe, attempt, "success", "没有可用更新")
            return None
        return replace(offer, attempt=attempt)
    except Exception as error:  # 重试由网络层控制，只结算最外层的一次尝试。
        settle_attempt(target_exe, attempt, "cancelled" if isinstance(error, Cancelled) else "failed", repr(error))
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
        _, release, version = max(choices, key=lambda item: item[0])
        assets = release.get("assets")
        if not isinstance(assets, list):
            raise UpdateError("缺少附件列表")
        urls = []
        for name in ("manifest.json", "manifest.sig", "AccountKeeper.exe"):
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
    base = target.parent / ".accountkeeper-update"
    if base.exists() and (base.resolve() != base or base.stat().st_dev != target.stat().st_dev):
        raise UpdateError("暂存目录越界或异卷")
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
    if not re.fullmatch("[0-9a-f]{32}", work.name) or work.resolve() != work or work.stat().st_dev != target.stat().st_dev:
        raise UpdateError("事务目录归属不符")
    owner = _json(_small(work / "owner.json", 4096))
    if not isinstance(owner, dict) or type(owner.get("schema")) is not int or owner != {"schema": 1, "transaction_id": work.name, "target": str(target)}:
        raise UpdateError("owner记录不符")


def _remove(work, target):
    _owned(work, target)
    entries = list(work.iterdir())
    if any(p.name not in _FILES or p.resolve() != p or not p.is_file() for p in entries):
        raise UpdateError("存在非本事务文件，停止清理")
    for path in entries:
        if path.name != "owner.json":
            path.unlink()
    (work / "owner.json").unlink()
    work.rmdir()


def cleanup_incomplete(target_exe):
    target = _target(target_exe)
    base = _base(target)
    if base.exists():
        for work in base.iterdir():
            if re.fullmatch("[0-9a-f]{32}", work.name) and work.is_dir() and not (work / "ready.json").exists():
                _remove(work, target)


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


def _download(offer, target_exe, current_version, arch, cancel=None, progress=None):
    m = verify_offer(offer, current_version, arch)
    target = _target(target_exe)
    if cancel is not None and cancel.is_set():
        raise Cancelled("下载已取消")
    cleanup_incomplete(target)
    base = _base(target)
    base.mkdir(exist_ok=True)
    work = base / secrets.token_hex(16)
    work.mkdir()
    ready = work / "ready.json"
    try:
        _record(work / "owner.json", {"schema": 1, "transaction_id": work.name, "target": str(target)})
        _owned(work, target)
        with _Budget(cancel) as budget:
            with (work / "candidate.exe.part").open("xb") as stream:
                digest = _stream(m["url"], m["size"], budget, stream.write, m["size"], progress)
                stream.flush()
                os.fsync(stream.fileno())
            if digest != m["sha256"]:
                raise UpdateError("包SHA-256不符")
            budget.check()
            (work / "candidate.exe.part").rename(work / "candidate.exe")
            (work / "manifest.json").write_bytes(offer.manifest)
            (work / "manifest.sig").write_bytes(offer.signature)
            record = dict(schema=1, transaction_id=work.name, release_id=offer.release_id,
                          target=str(target), candidate=str(work / "candidate.exe"), version=m["version"],
                          arch=arch, size=m["size"], sha256=m["sha256"], manifest=str(work / "manifest.json"),
                          signature=str(work / "manifest.sig"), assets=list(offer.assets), attempt=offer.attempt)
            _record(work / "ready.json.tmp", record)
            budget.check()
            (work / "ready.json.tmp").replace(ready)
            return ready
    except BaseException as error:  # 包含调用方中断；就绪发布前均回收本事务。
        if not ready.exists():
            try:
                _remove(work, target)
            except (OSError, UpdateError) as cleanup_error:  # 清理失败明确保留路径，不能冒充成功。
                raise UpdateError(f"清理失败，残留目录：{work}；{cleanup_error}") from error
        raise


def verify_ready(ready_path, target_exe, current_version, arch):
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
        if r.get(key) != str(work / name):
            raise UpdateError("交接路径不符")
    offer = Offer(r.get("release_id"), r.get("version"), tuple(r.get("assets", ())),
                  _small(work / "manifest.json"), _small(work / "manifest.sig", 1024))
    m = verify_offer(offer, current_version, arch)
    if any(r.get(key) != m[key] for key in ("version", "arch", "size", "sha256")):
        raise UpdateError("ready与签名清单不符")
    candidate, digest, size = work / "candidate.exe", hashlib.sha256(), 0
    if candidate.resolve() != candidate:
        raise UpdateError("候选路径越界")
    with candidate.open("rb") as stream:
        while chunk := stream.read(65536):
            size += len(chunk)
            if size > m["size"]:
                raise UpdateError("候选大小越界")
            digest.update(chunk)
    if size != m["size"] or digest.hexdigest() != m["sha256"]:
        raise UpdateError("候选已被改写")
    return candidate
