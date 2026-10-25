"""独立更新事务与早期恢复；导入不读配置、不连接账本、不初始化Windows API。"""
import argparse
from contextlib import closing, contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
import traceback


class UpdateFailure(Exception):
    """拒绝无法证明安全的更新或恢复。"""


class Paused(UpdateFailure):
    """自动检查已暂停；只有明确手动操作可开始新尝试。"""


class Busy(UpdateFailure):
    """正常中断（锁冲突、其他实例未退出）；不增加连续失败计数。"""


def target_path(path):
    p = Path(path).absolute()
    if p.name != "AccountKeeper.exe" or p.resolve() != p:
        raise UpdateFailure("目标路径必须是无重解析的AccountKeeper.exe")
    return p
# 独立helper由启动参数绑定数据目录；早期恢复独立定位路径，不导入配置或触碰账本。
_HELPER_DATA_DIR = None
_PAYLOAD_NAMES = {"candidate.exe", "candidate.exe.part", "update_helper.exe",
                  "update_helper.exe.part", "staged.exe", "restore.exe", "backup"}


def data_root():
    if _HELPER_DATA_DIR is not None:
        return _HELPER_DATA_DIR
    # 与config.py保持同步：相同的platformdirs参数、默认选项及ImportError降级路径。
    # 恢复判定必须先于配置加载，不能通过导入config获取路径。
    try:
        from platformdirs import user_data_dir
    except ImportError:
        root = Path.home() / "AccountKeeper"  # 与config的缺依赖降级规则一致。
    else:
        root = Path(user_data_dir("AccountKeeper", "SpringU26325"))
    root.mkdir(parents=True, exist_ok=True)  # 首次启动也需支持严格解析，不依赖config创建目录。
    return root.resolve(strict=True)


def update_root(target):
    # 用完整安装路径隔离轻量记录；不同目录的同名exe不能共享计数、日志或安装锁。
    key = hashlib.sha256(os.path.normcase(str(target_path(target))).encode("utf-8")).hexdigest()
    root = data_root() / "update" / key
    if root.resolve() != root:
        raise UpdateFailure("更新状态目录被重定向")
    root.mkdir(parents=True, exist_ok=True)
    return root


def journal_path(target, *, archived=False):
    return update_root(target) / (".accountkeeper-update.json.bak" if archived else ".accountkeeper-update.json")


def transaction_root(target):
    return update_root(target) / "transactions"


def payload_path(work, target, name):
    work, target = Path(work), target_path(target)
    if (work.parent != transaction_root(target) or work.resolve() != work
            or len(work.name) != 32 or any(c not in "0123456789abcdef" for c in work.name)
            or name not in _PAYLOAD_NAMES):
        raise UpdateFailure("安装包材料归属不符")
    # 安装包与恢复暂存必须留在目标exe同盘，os.replace才具有同卷原子替换语义。
    base = target.parent / ".accountkeeper-update"
    path = base / work.name / name
    if path.resolve() != path or (base.exists() and base.stat().st_dev != target.parent.stat().st_dev):
        raise UpdateFailure("安装包目录越界或异卷")
    return path


def transaction_entries(work, target):
    work = Path(work)
    payload = payload_path(work, target, "candidate.exe").parent
    entries = list(work.iterdir()) if work.exists() else []
    entries += list(payload.iterdir()) if payload.exists() else []
    # 元数据和大文件不能串到另一个根目录，否则清理可能跨越信任边界。
    if any(p.resolve() != p or p.parent != (payload if p.name in _PAYLOAD_NAMES else work)
           for p in entries):
        raise UpdateFailure("事务文件所在目录不符")
    return entries


def remove_transaction_dirs(work, target):
    payload = payload_path(work, target, "candidate.exe").parent
    if payload.exists():
        payload.rmdir()
    if Path(work).exists():
        Path(work).rmdir()


# 另一进程正在读同一文件、或文件处于删除挂起，都会表现为拒绝访问；只有预算耗尽才按失败处理。
_TRANSIENT = (5, 32, 303)


def transient(error):
    if not isinstance(error, OSError):
        return False
    return getattr(error, "winerror", None) in _TRANSIENT or error.errno in (13, 32)


def retry_transient(action, seconds=0.6):
    end = time.monotonic() + seconds
    while True:
        try:
            return action()
        except OSError as error:  # 缺失、越界等非瞬时错误立即抛出，不能被重试掩盖。
            if not transient(error) or time.monotonic() >= end:
                raise
            time.sleep(0.015)


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise UpdateFailure("记录含重复键")
            result[key] = value
        return result
    p = Path(path)

    def load():
        if p.resolve() != p:  # 越界不是瞬时错误，放在重试体内靠异常类型区分。
            raise UpdateFailure("记录路径不合法")
        return p.open("rb")

    try:
        stream = retry_transient(load)  # 只在共享窗口内重试，持久拒绝与越界照样抛出。
    except FileNotFoundError as error:  # 缺失沿用既有语义，不让调用方见到新的异常类型。
        raise UpdateFailure(f"记录缺失：{p}") from error
    with stream:
        if os.fstat(stream.fileno()).st_size > 1048576:  # 打开后再判大小，避免stat与替换竞争时误判。
            raise UpdateFailure(f"记录过大：{p}")
        try:
            return json.loads(stream.read(1048577).decode("utf-8"), object_pairs_hook=pairs)
        except (ValueError, UnicodeError) as error:  # 坏记录不能当成首次启动或未提交。
            raise UpdateFailure(f"记录损坏：{p}") from error


def atomic_json(path, value):
    p = Path(path)
    if p.resolve() != p:
        raise UpdateFailure("记录路径越界")
    temporary = p.with_name(p.name + ".tmp")
    if temporary.resolve() != temporary or (temporary.exists() and temporary.stat().st_nlink != 1):
        raise UpdateFailure("临时记录被重定向")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    retry_transient(lambda: os.replace(temporary, p))  # 调用方先持对应锁；共享窗口内有限重试。


LOG_NAME = ".accountkeeper-update.log"


def log_path(target):
    return update_root(target) / LOG_NAME


def log_line(target, message):
    """无控制台构建下唯一能留存的证据通道；日志自身失败不得改变事务结果。"""
    try:
        path = log_path(target)  # 状态目录不可写时也不能让诊断异常覆盖事务异常。
        if path.exists() and path.stat().st_size > 262144:
            path.unlink()  # 只保留最近一段，避免长期累积。
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{os.getpid()}] {message}\n")
    except (OSError, UpdateFailure):  # 写日志失败只放弃日志，绝不改事务结果。
        pass


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(65536):
            result.update(chunk)
    return result.hexdigest()


def copy_paths(source, destination):
    source, destination = Path(source), Path(destination)
    if source.resolve() != source or destination.resolve() != destination:
        raise UpdateFailure("复制源或路径校验失败")
    src = os.stat(source)
    try:
        dst = os.stat(destination)
    except FileNotFoundError:
        dst = None  # 目标未创建才允许xb；其余stat异常保持拒绝。
    current = os.stat(Path(sys.executable).resolve())
    if (src.st_nlink > 1 or (dst is not None and
            (dst.st_nlink > 1 or os.path.samestat(src, dst) or os.path.samestat(current, dst)))
            or destination == Path(sys.executable).resolve() or source == destination):
        raise UpdateFailure("复制涉及硬链接、同一物理文件或当前程序，禁止写入")
    return src, dst


def copy_checked(source, destination, expected, *, reusable=False):
    source, destination = Path(source), Path(destination)
    source_info, existing = copy_paths(source, destination)
    if digest(source) != expected:
        raise UpdateFailure("复制源哈希不符")
    if existing is not None:
        if not reusable:
            raise UpdateFailure("备份目标已存在，禁止覆盖")
        if digest(destination) == expected:
            return  # 完整恢复暂存可以重放，不能为重试再次截断它。
        destination.unlink()  # 仅已校验的单链接恢复暂存可丢弃；随后xb防止名称被抢占后覆盖。
    with source.open("rb") as src, destination.open("xb") as dst:
        source_open, destination_open = os.fstat(src.fileno()), os.fstat(dst.fileno())
        if (source_open.st_nlink > 1 or destination_open.st_nlink > 1
                or not os.path.samestat(source_open, source_info)
                or not os.path.samestat(destination_open, os.stat(destination))):
            raise UpdateFailure("打开后的复制文件身份改变")  # stat与open之间发生替换也不能开始写入。
        shutil.copyfileobj(src, dst, 65536)
        dst.flush()
        os.fsync(dst.fileno())
    if digest(destination) != expected:
        raise UpdateFailure("复制后哈希不符")


class FileLock:
    def __init__(self, target, kind, seconds=10):
        self.path = update_root(target) / (".accountkeeper-" + kind + ".lock")
        self.seconds, self.stream = seconds, None

    def __enter__(self):
        import msvcrt
        if self.path.resolve() != self.path:
            raise UpdateFailure("锁路径越界")
        if self.path.exists() and self.path.stat().st_nlink != 1:
            raise UpdateFailure("锁文件不是独占归属")
        self.stream = self.path.open("a+b")
        try:
            if self.path.stat().st_size == 0:
                self.stream.write(b"\0")
                self.stream.flush()
            end = time.monotonic() + self.seconds
            while True:
                try:
                    self.stream.seek(0)
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
                    return self
                except OSError:  # 有限等锁；拒绝重叠事务，不以锁忙当作成功。
                    if time.monotonic() >= end:
                        raise Busy("已有更新或恢复操作持锁")  # 并发中断，不是更新失败。
                    time.sleep(0.02)
        except BaseException:  # 未取得锁也必须关闭文件。
            self.stream.close()
            self.stream = None
            raise

    def __exit__(self, *args):
        import msvcrt
        try:
            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self.stream.close()


class DataGate(FileLock):
    def __init__(self, data):
        self.path = Path(data).resolve(strict=True) / ".accountkeeper-data.lock"
        self.seconds, self.stream = 10, None  # 门闩按真实数据目录归属，跨安装目录共用。


def data_members(data):
    path = Path(data) / ".accountkeeper-sessions.json"
    members = read_json(path) if path.exists() else []
    if not isinstance(members, list) or any(not isinstance(group, list) or not group for group in members):
        raise UpdateFailure("数据目录实例登记损坏")
    return [group for group in members if not wait_processes(group, 0)]  # 登记一直保留到进程真正退出。


def data_reservation(j):
    return dict(target=j["target"], transaction=j["transaction"], token=j["token"])


def register_data(data, context=None):
    with DataGate(data):
        path = Path(data) / ".accountkeeper-data-update.json"
        if path.exists() and (context is None or read_json(path) != data_reservation(context.j)
                              or not helper_alive(context.j)):
            raise Busy("数据目录正在更新或恢复，暂不能打开账本")
        members = data_members(data)
        records = own_records(Path(sys.executable).resolve())
        if records not in members:
            atomic_json(Path(data) / ".accountkeeper-sessions.json", members + [records])


def reserve_data(j, allowed):
    data = Path(j["data_dir"])
    with DataGate(data):
        path = data / ".accountkeeper-data-update.json"
        if path.exists() and read_json(path) != data_reservation(j):
            raise Busy("相同数据目录已有其他更新事务")
        members = data_members(data)
        if any(record not in allowed and not wait_processes([record], 0)
               for group in members for record in group):
            raise Busy("相同数据目录仍被其他实例占用")
        if any(record not in allowed and not any(record in group for group in members)
               for record in image_processes(Path(j["target"]), all_paths=True)):
            raise Busy("有未登记数据目录的旧版实例，请先关闭其他AccountKeeper")  # 无法证明旧副本使用其他账本时保守拒绝。
        atomic_json(path, data_reservation(j))  # 持久预约覆盖旧进程退出到helper接管的整个空窗。


def release_data(j):
    data = Path(j["data_dir"])
    with DataGate(data):
        path = data / ".accountkeeper-data-update.json"
        if path.exists() and read_json(path) == data_reservation(j):
            path.unlink()  # 只解除本事务预约，不能解除后来执行者的互斥。


@contextmanager
def locked_helper(path, size, expected):
    import ctypes
    import msvcrt
    from ctypes import wintypes as w
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
    api.CreateFileW.restype = w.HANDLE
    api.CloseHandle.argtypes, api.CloseHandle.restype = [w.HANDLE], w.BOOL
    def acquire(p, access, share, flags):
        handle = api.CreateFileW(str(p), access, share, None, 3, flags, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle
    with ExitStack() as stack:
        for parent in reversed(path.parents):
            handle = acquire(parent, 0, 3, 0x02000000)
            stack.callback(api.CloseHandle, handle)  # 祖先目录也禁止改名，避免整目录换包。
        handle = acquire(path, 0x80000000, 1, 0)  # 只共享读；拒绝写/删除，仍允许Windows加载映像。
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            api.CloseHandle(handle)
            raise  # 转交失败时句柄仍由本函数负责，不能泄漏防替换锁。
        with os.fdopen(descriptor, "rb") as stream:
            value = hashlib.file_digest(stream, "sha256").hexdigest()
            if os.fstat(stream.fileno()).st_size != size or value != expected or path.resolve() != path:
                raise UpdateFailure("锁定的可执行文件与签名清单不符")
            yield  # 启动方持有至接管或健康确认结束；正常、异常及超时出口均关闭。


def windows_api():
    if hasattr(windows_api, "loaded"):
        return windows_api.loaded
    import ctypes
    from ctypes import wintypes as w
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, args, result in (
        ("OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        ("CloseHandle", [w.HANDLE], w.BOOL),
        ("WaitForSingleObject", [w.HANDLE, w.DWORD], w.DWORD),
        ("GetProcessTimes", [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4, w.BOOL),
        ("QueryFullProcessImageNameW", [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)], w.BOOL),
        ("TerminateProcess", [w.HANDLE, w.UINT], w.BOOL),
        ("CreateToolhelp32Snapshot", [w.DWORD, w.DWORD], w.HANDLE),
    ):
        getattr(k, name).argtypes, getattr(k, name).restype = args, result
    windows_api.loaded = ctypes, w, k
    return windows_api.loaded


def wait_signal(handle, milliseconds):
    c, _, k = windows_api()
    value = k.WaitForSingleObject(handle, max(0, milliseconds))
    if value not in (0, 258):
        raise c.WinError(c.get_last_error())
    return value == 0


def describe(handle, pid):
    c, w, k = windows_api()
    times = [w.FILETIME() for _ in range(4)]
    if not k.GetProcessTimes(handle, *(c.byref(t) for t in times)):
        raise c.WinError(c.get_last_error())
    size, name = w.DWORD(32768), c.create_unicode_buffer(32768)
    if not k.QueryFullProcessImageNameW(handle, 0, name, c.byref(size)):
        raise c.WinError(c.get_last_error())
    return dict(pid=pid, created=times[0].dwHighDateTime << 32 | times[0].dwLowDateTime,
                image=str(Path(name.value).resolve()))


def open_process(pid, terminate=False):
    c, _, k = windows_api()
    if type(pid) is not int or pid < 1:
        raise UpdateFailure("缺少有效进程身份")
    handle = k.OpenProcess(0x00101000 | (1 if terminate else 0), False, pid)
    if not handle and c.get_last_error() != 87:
        raise c.WinError(c.get_last_error())  # 无句柄的拒绝访问没有退出证据。
    return handle


def process_identity(pid):
    _, _, k = windows_api()
    handle = open_process(pid)
    if not handle:
        return None
    try:
        if wait_signal(handle, 0):
            return None
        try:
            return describe(handle, pid)
        except OSError as error:  # 只有同一对象随后signal，才容忍查询5。
            if getattr(error, "winerror", None) == 5 and wait_signal(handle, 1000):
                return None
            raise
    finally:
        k.CloseHandle(handle)


def wait_processes(records, seconds):
    end = time.monotonic() + seconds
    _, _, k = windows_api()
    for record in records:
        if not isinstance(record, dict) or set(record) != {"pid", "created", "image"}:
            raise UpdateFailure("进程登记不完整")
        handle = open_process(record["pid"])
        if not handle:
            continue
        try:
            if wait_signal(handle, 0):
                continue
            try:
                actual = describe(handle, record["pid"])
            except OSError as error:  # 核验沿用整组剩余预算，失败不能伪装普通超时。
                if getattr(error, "winerror", None) == 5 and wait_signal(handle, max(0, int((end - time.monotonic()) * 1000))):
                    continue
                raise
            if actual != record:
                continue  # PID复用意味着登记对象已退出，不能误等或终止新对象。
            if not wait_signal(handle, max(0, int((end - time.monotonic()) * 1000))):
                return False
        finally:
            k.CloseHandle(handle)
    return True


def image_processes(target, *, all_paths=False):
    c, w, k = windows_api()
    wanted = os.path.normcase(str(target_path(target)))  # 只认目标路径，同名不同目录不算本实例。
    class Entry(c.Structure):
        _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("pid", w.DWORD),
                    ("heap", c.c_size_t), ("module", w.DWORD), ("threads", w.DWORD),
                    ("parent", w.DWORD), ("priority", w.LONG), ("flags", w.DWORD),
                    ("name", w.WCHAR * 260)]
    for name in ("Process32FirstW", "Process32NextW"):
        getattr(k, name).argtypes, getattr(k, name).restype = [w.HANDLE, c.POINTER(Entry)], w.BOOL
    handle = k.CreateToolhelp32Snapshot(2, 0)
    if handle == c.c_void_p(-1).value:
        raise c.WinError(c.get_last_error())
    records, entry = [], Entry()
    entry.size = c.sizeof(entry)
    try:
        more = k.Process32FirstW(handle, c.byref(entry))
        while more:
            if entry.name.casefold().startswith("accountkeeper") and entry.name.casefold().endswith(".exe"):
                record = process_identity(entry.pid)
                if record and (all_paths or os.path.normcase(record["image"]) == wanted):
                    records.append(record)
            more = k.Process32NextW(handle, c.byref(entry))
        if c.get_last_error() != 18:
            raise c.WinError(c.get_last_error())
    finally:
        k.CloseHandle(handle)
    return records


def own_records(target):
    result = [process_identity(os.getpid())]
    parent = process_identity(os.getppid())
    if parent and os.path.normcase(parent["image"]) == os.path.normcase(str(target)):
        result.append(parent)
    if any(r is None for r in result):
        raise UpdateFailure("当前进程登记失败")
    return result


def state_path(target):
    return update_root(target) / ".accountkeeper-update-state.json"


def read_state(target):
    target = target_path(target)
    path = state_path(target)
    if not path.exists():
        if journal_path(target).exists():
            raise UpdateFailure("有事务却缺少计数状态，禁止按零初始化")
        return dict(schema=1, target=str(target), sequence=0, settled=0, failures=0,
                    paused=False, active=None, last=None, reasons=[])
    s = read_json(path)
    if not isinstance(s, dict) or type(s.get("schema")) is not int or s.get("schema") != 1 or s.get("target") != str(target):
        raise UpdateFailure("失败状态身份不符")
    if any(type(s.get(k)) is not int or s[k] < 0 for k in ("sequence", "settled", "failures")):
        raise UpdateFailure("失败状态计数损坏")
    if s["settled"] > s["sequence"] or type(s.get("paused")) is not bool or s["paused"] != (s["failures"] >= 3):
        raise UpdateFailure("失败状态次序损坏")
    a = s.get("active")
    if (s["sequence"] - s["settled"] not in (0, 1) or not isinstance(s.get("reasons"), list)
            or (a is None) != (s["sequence"] == s["settled"])):
        raise UpdateFailure("尝试结算状态损坏")
    if a is not None and (not isinstance(a, dict) or a.get("seq") != s["sequence"] or a.get("seq") <= s["settled"]):
        raise UpdateFailure("当前尝试损坏")
    receipt = s.get("committed")
    if receipt is not None:
        if (not isinstance(receipt, dict) or set(receipt) != {"target", "sha256", "data_dir", "transaction", "token"}
                or receipt.get("target") != str(target)):
            raise UpdateFailure("终态安装凭据身份损坏")
        for name, length in (("sha256", 64), ("transaction", 32), ("token", 64)):
            value = receipt.get(name)
            if not isinstance(value, str) or len(value) != length or any(c not in "0123456789abcdef" for c in value):
                raise UpdateFailure("终态安装凭据格式损坏")
        data = Path(receipt["data_dir"])
        if data.resolve() != data or data == target.parent:
            raise UpdateFailure("终态安装凭据数据路径越界")  # 缺材料放行仍须保留凭据和数据目录的严格身份边界。
    return s


def begin_attempt(target, manual=False):
    target = target_path(target)
    if not target.is_file():
        raise UpdateFailure("目标缺失，先完成恢复再检查更新")
    pending = read_state(target)["active"]  # 锁外预检：死亡执行者遗留的悬挂尝试先补记，否则同一会话永久阻塞。
    if pending is not None:
        owner = pending.get("owner")
        if owner and not wait_processes([owner], 0):
            raise UpdateFailure("上一尝试尚未结算")
        settle_attempt(target, pending, "failed", "执行者中断未结算")
    with FileLock(target, "state"):
        s = read_state(target)
        if s["active"] is not None:
            raise UpdateFailure("上一尝试尚未结算")
        journal = journal_path(target)
        if journal.exists() and read_json(journal).get("phase") not in ("committed", "rolled_back"):
            raise UpdateFailure("恢复未完成，禁止新尝试")
        if s["paused"] and not manual:
            raise Paused("连续失败达到3次，自动检查已暂停")
        a = dict(id=secrets.token_hex(16), seq=s["sequence"] + 1,
                 owner=process_identity(os.getpid()), started=time.time(), binding=None)
        s.update(sequence=a["seq"], active=a)
        atomic_json(state_path(target), s)
        return dict(a)


def settle_attempt(target, attempt, outcome, reason):
    target = target_path(target)
    if outcome not in ("failed", "success", "cancelled"):
        raise UpdateFailure("未知结算结果")
    with FileLock(target, "state"):
        s = read_state(target)
        if attempt["seq"] <= s["settled"]:
            return s  # 旧失败/旧提交重放不能再次加一或清除后续失败。
        if not s["active"] or any(s["active"][k] != attempt[k] for k in ("id", "seq")):
            raise UpdateFailure("结算不是当前尝试")
        if outcome == "failed":
            s["failures"] += 1
            s["reasons"] = (s["reasons"] + [dict(id=attempt["id"], reason=str(reason)[:2000])])[-16:]
        elif outcome == "success":
            s["failures"], s["reasons"] = 0, []
        s.update(settled=attempt["seq"], paused=s["failures"] >= 3,
                 last=dict(attempt=s["active"], outcome=outcome, reason=str(reason)[:2000]),
                 active=None)
        atomic_json(state_path(target), s)
        return s


def abandon_attempt(target, attempt, outcome, reason):
    """接管者未成立时结算当前尝试；已交接、阶段已推进或不属于本进程一律保留pending交恢复。"""
    target = target_path(target)
    journal = journal_path(target)
    if journal.exists():
        j = read_json(journal)
        if j.get("phase") != "prepared" or j.get("helper_processes"):
            return  # helper已登记或阶段已推进，结算权归它或下次启动的入口补记。
    active = read_state(target)["active"]
    if active is None:
        return
    if attempt is None:
        if active.get("owner") != process_identity(os.getpid()):
            return  # 没有明确尝试身份时，只结算本进程自己持有的尝试。
        attempt = active
    settle_attempt(target, attempt, outcome, reason)


def transfer_attempt(target, attempt, owner, binding=None, *, handoff=None):
    with FileLock(target, "state"):
        s = read_state(target)
        if not s["active"] or any(s["active"][k] != attempt[k] for k in ("id", "seq")):
            raise UpdateFailure("交接已过期")
        s["active"]["owner"] = owner
        if binding is not None:
            s["active"]["binding"] = binding
        if handoff is not None:
            s["active"]["handoff"] = handoff  # preparing不能执行helper，armed才允许接管。
        atomic_json(state_path(target), s)


def reconcile_handoff(target):
    initial = read_state(target)
    owner = initial["active"] or (initial["last"] or {}).get("attempt") or {}
    if owner.get("handoff") != "preparing":
        return  # armed新版启动不能争抢正在由helper持有的安装锁。
    with FileLock(target, "install", 0), FileLock(target, "state"):
        s = read_state(target)
        a = s["active"] or (s["last"] or {}).get("attempt") or {}
        if a.get("handoff") != "preparing":
            return
        if a.get("owner") and not wait_processes([a["owner"]], 0):
            raise Busy("安装准备执行者尚未退出")
        binding = a["binding"]
        work = Path(binding["work"])
        if (work.parent != transaction_root(target) or work.resolve() != work
                or digest(target) != binding["old_sha"]
                or any((work / name).exists() for name in ("pending.json", "healthy.json", "commit.json"))):
            raise UpdateFailure("准备交接的安全证据不符")
        path = journal_path(target)
        j = read_json(path) if path.exists() else None
        if j and j.get("attempt", {}).get("id") == a["id"]:
            if (j.get("phase") != "prepared" or j.get("new_started") is not False
                    or j.get("helper_processes") or j.get("new_processes")
                    or any(j.get(k) != binding[k] for k in ("work", "data_dir", "old_sha", "new_sha"))):
                raise UpdateFailure("准备日志不允许自动补全")
            a["handoff"] = "armed"  # 日志已持久化，补齐接管许可后走正常恢复。
        else:
            if j and j.get("phase") not in ("committed", "rolled_back"):
                raise UpdateFailure("准备交接遇到其他未终结日志")
            release_data(dict(target=str(target), transaction=work.name, token=a.get("handoff_token"),
                              data_dir=binding["data_dir"]))
            a["binding"], a["handoff"] = None, "abandoned"  # 未授权执行过，旧包可安全继续启动。
        atomic_json(state_path(target), s)


def load_journal(target, *, archived=False):
    target = target_path(target)
    j = read_json(journal_path(target, archived=archived))
    if not isinstance(j, dict) or j.get("schema") != 1 or j.get("target") != str(target):
        raise UpdateFailure("事务身份不符")
    if j.get("phase") not in {"prepared", "backed_up", "switching", "launching", "confirming", "rolling_back", "rolled_back", "committed"}:
        raise UpdateFailure("未知事务阶段")
    if type(j.get("recovery_attempts", 0)) is not int or j.get("recovery_attempts", 0) < 0:
        raise UpdateFailure("恢复计数损坏")  # 恢复次数独立于失败结算，不能用布尔值或负数绕过阈值。
    work = Path(j["work"])
    if (work.parent != transaction_root(target) or work.resolve() != work
            or len(work.name) != 32 or any(c not in "0123456789abcdef" for c in work.name)):
        raise UpdateFailure("事务目录越界")
    expected = {"candidate": payload_path(work, target, "candidate.exe"), "helper": payload_path(work, target, "update_helper.exe"),
                "manifest": work / "manifest.json", "signature": work / "manifest.sig",
                "backup": payload_path(work, target, "backup")}
    if any(j.get(k) != str(v) or v.resolve() != v for k, v in expected.items()):
        raise UpdateFailure("事务材料路径越界")
    s = read_state(target)
    a = s["active"] or (s["last"] or {}).get("attempt")
    binding = dict(work=str(work), data_dir=j["data_dir"], old_sha=j["old_sha"], new_sha=j["new_sha"])
    if j["attempt"]["seq"] > s["settled"] or j["phase"] not in ("committed", "rolled_back"):
        if not a or any(a[k] != j["attempt"][k] for k in ("id", "seq")) or a.get("binding") != binding:
            raise UpdateFailure("事务与持久尝试绑定不符")
    data = Path(j["data_dir"])
    if data.resolve() != data or not data.is_dir() or data == target.parent or data != data_root():
        raise UpdateFailure("数据目录归属不符")
    return j


def write_journal(j):
    j["sequence"] += 1
    j["updated"] = time.time()
    atomic_json(journal_path(j["target"]), j)
    log_line(j["target"], f"phase={j['phase']} sequence={j['sequence']}")  # 与事务日志同一时间线，便于事后对时。


def verify_materials(j):
    from updater import Offer, verify_offer
    work = Path(j["work"])
    ready = read_json(work / "ready.json")
    if (work / "manifest.json").stat().st_size > 65536 or (work / "manifest.sig").stat().st_size > 1024:
        raise UpdateFailure("签名材料越界")
    offer = Offer(ready["release_id"], j["new_version"], tuple(ready["assets"]),
                  (work / "manifest.json").read_bytes(), (work / "manifest.sig").read_bytes())
    m = verify_offer(offer, j["old_version"], j["arch"])
    if m["sha256"] != j["new_sha"] or m["size"] != j["size"] or m["helper_sha256"] != j["helper_sha"]:
        raise UpdateFailure("日志与签名清单不符")
    return m


def committed(j):
    path = Path(j["work"]) / "commit.json"
    if not path.exists():
        if j["phase"] == "committed":
            raise UpdateFailure("提交阶段缺少不可逆标记")
        return False
    value = read_json(path)
    if value != dict(transaction=j["transaction"], token=j["token"], attempt=j["attempt"],
                     version=j["new_version"], sha256=j["new_sha"]):
        raise UpdateFailure("提交标记损坏，禁止猜测回退")
    return True


def helper_alive(j):
    return bool(j.get("helper_processes")) and not wait_processes(j["helper_processes"], 0)


def prepare_install(ready_path, target, old_version, arch, data_dir):
    """接管未成立时的失败出口：只结算本进程持有且仍处prepared的尝试，其余交恢复。"""
    target = target_path(target)
    try:
        return _install(ready_path, target, old_version, arch, data_dir)
    except Exception as error:
        try:
            abandon_attempt(target, None, "cancelled" if isinstance(error, Busy) else "failed", repr(error))
        except Exception as settle_error:  # 结算失败不能覆盖真实失败原因，只留日志。
            log_line(target, f"结算失败：{settle_error!r}")
        raise


def _install(ready_path, target, old_version, arch, data_dir):
    from updater import verify_ready
    data_dir = Path(data_dir).resolve(strict=True)
    if data_dir != data_root():
        raise UpdateFailure("安装数据目录与配置不符")  # helper和主程序必须使用同一份数据及状态。
    candidate, manifest = verify_ready(ready_path, target, old_version, arch, with_manifest=True)
    ready, work = read_json(Path(ready_path)), Path(ready_path).parent
    attempt = ready.get("attempt")
    if not attempt:
        raise UpdateFailure("ready未登记完整尝试")
    with FileLock(target, "install"):
        records = own_records(target)
        if any(r not in records for r in image_processes(target)):
            raise Busy("其他实例尚未退出")  # 另一个实例占用属于正常中断，不增加失败计数。
        helper = payload_path(work, target, "update_helper.exe")
        # 时序：副本已落盘、尚未启动，此时比对签名清单里的 helper 哈希，堵住替换副本的路径。
        if digest(helper) != manifest["helper_sha256"]:
            raise UpdateFailure("helper 与签名清单不符")
        j = dict(schema=1, transaction=work.name, token=secrets.token_hex(32), attempt=attempt,
                 target=str(target), work=str(work), candidate=str(candidate), data_dir=str(data_dir),
                 helper=str(helper), helper_sha=digest(helper), manifest=str(work / "manifest.json"),
                 signature=str(work / "manifest.sig"), backup=str(payload_path(work, target, "backup")), backups={},
                 old_version=old_version, new_version=ready["version"], arch=arch,
                 old_sha=digest(target), new_sha=ready["sha256"], size=ready["size"],
                 old_processes=records, new_processes=[], helper_processes=[], recovery_waiters=[],
                 phase="prepared", sequence=0, intent=None, steps=[], result=None, error=None,
                  new_started=False, recovery_attempts=0, created=time.time())
        transfer_attempt(target, attempt, process_identity(os.getpid()),
                         dict(work=str(work), data_dir=str(data_dir), old_sha=j["old_sha"], new_sha=j["new_sha"]),
                         handoff="preparing")
        with FileLock(target, "state"):
            s = read_state(target)
            s["active"]["handoff_token"] = j["token"]  # 预约清理只认本次随机身份。
            atomic_json(state_path(target), s)
        reserve_data(j, records)
        write_journal(j)
        transfer_attempt(target, attempt, process_identity(os.getpid()), handoff="armed")
        return j


def launch_helper(j, recover=False):
    with locked_helper(Path(j["helper"]), verify_materials(j)["helper_size"], j["helper_sha"]):
        return _launch_helper(j, recover)  # 验签、加锁后才启动；接管确认前始终禁止换包。


def _launch_helper(j, recover=False):
    target, process = Path(j["target"]), None
    try:
        manifest = verify_materials(j)  # 启动前重新验签，日志中的自算摘要不能代替发布方签名。
        helper = Path(j["helper"])
        if helper.resolve() != helper or helper.stat().st_size != manifest["helper_size"] or digest(helper) != j["helper_sha"]:
            raise UpdateFailure("helper副本校验失败")
        env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
        command = [str(helper), "--recover" if recover else "--install", j["target"],
                   "--data-dir", str(data_root())]  # 不依赖helper工作目录或临时解压目录推断路径。
        process = subprocess.Popen(command, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        launched = process_identity(process.pid)
        if launched is None:
            raise UpdateFailure("helper登记前已退出")
        end = time.monotonic() + 60
        while time.monotonic() < end:
            current = load_journal(target)
            if launched in current.get("helper_processes", []):
                return  # helper持锁登记落盘后，旧实例才允许退出。
            if process.poll() is not None:
                raise UpdateFailure("helper接管前退出")
            time.sleep(0.05)
        raise UpdateFailure("helper接管超时；保留事务等待恢复")
    except Exception as error:
        if process is None or process.poll() is not None:  # 仍在运行的helper可能继续提交，不能抢先结算。
            try:
                abandon_attempt(target, j["attempt"], "failed", repr(error))
            except Exception as settle_error:  # 结算失败不能覆盖真实失败原因，只留日志。
                log_line(target, f"结算失败：{settle_error!r}")
        else:
            log_line(target, f"helper仍在运行，保留pending：{error!r}")
        raise


def snapshot(j):
    import sqlite3
    backup, data = Path(j["backup"]), Path(j["data_dir"])
    backup.mkdir(exist_ok=True)
    j["intent"] = "backup"
    write_journal(j)
    copy_checked(Path(j["target"]), backup / "old.exe", j["old_sha"])
    hashes = {"old.exe": j["old_sha"]}
    for name in ("account.db", "settings.json", "tags.json", "categories.json", "import_mappings.json"):
        src, dst = data / name, backup / name
        if not src.exists():
            hashes[name] = None
            continue  # 原始缺失是快照的一部分，不能恢复成空文件。
        if src.resolve() != src or not src.is_file():
            raise UpdateFailure("快照源路径越界")
        if name == "account.db":
            copy_paths(src, dst)
            with dst.open("xb") as reserved:
                if os.fstat(reserved.fileno()).st_nlink != 1:
                    raise UpdateFailure("数据库备份目标存在硬链接")  # SQLite连接前独占创建，不能借已有别名覆盖原账本。
            with closing(sqlite3.connect(src.as_uri() + "?mode=ro", uri=True, timeout=3)) as source:
                with closing(sqlite3.connect(dst)) as destination:
                    with source, destination:
                        deadline = time.monotonic() + 3
                        def progress(_status, _remaining, _total):
                            if time.monotonic() >= deadline:
                                raise UpdateFailure("数据库快照超过3秒期限")  # busy重试也必须受总期限约束。
                        source.backup(destination, pages=128, progress=progress, sleep=0.05)
                        if destination.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                            raise UpdateFailure("更新快照完整性校验失败")
            with dst.open("rb+") as stream:
                os.fsync(stream.fileno())
            hashes[name] = digest(dst)
        else:
            hashes[name] = digest(src)
            copy_checked(src, dst, hashes[name])
    j.update(backups=hashes, phase="backed_up", intent=None)
    write_journal(j)


def new_records(j):
    records = list(j["new_processes"])
    pending = Path(j["work"]) / "pending.json"
    if pending.exists():
        value = read_json(pending)
        if value.get("transaction") != j["transaction"] or value.get("token") != j["token"]:
            raise UpdateFailure("新版登记不属于当前事务")
        records += value["processes"]
    if any(r.get("image") != j["target"] for r in records):
        raise UpdateFailure("新版登记映像越界")
    return list({(r["pid"], r["created"]): r for r in records}.values())


def process_key(record):
    # 两侧进程顺序由不同生产方决定（helper侧父进程在前、新版自身子进程在前），比较前统一成可排序三元组；非字典项转字符串，使其必然不等而不是抛TypeError。
    if not isinstance(record, dict):
        return repr(record)
    return (str(record.get("pid")), str(record.get("created")), str(record.get("image")))


def stop_new(j):
    if committed(j):
        raise UpdateFailure("已提交事务禁止终止新版")
    target, end = Path(j["target"]), time.monotonic() + 10
    atomic_json(Path(j["work"]) / "stop.json", dict(token=j["token"]))
    c, _, k = windows_api()
    while True:
        observed = image_processes(target)
        records = new_records(j)  # 扫描期间新版可能完成pending登记，只从事务登记取得终止权限。
        protected = j["old_processes"] + j["helper_processes"] + j.get("recovery_waiters", [])
        if any(set(r) != {"pid", "created", "image"} or type(r["pid"]) is not int
               or type(r["created"]) is not int or r["pid"] == os.getpid() or r in protected for r in records):
            raise UpdateFailure("新版进程登记不完整或混入受保护实例，禁止终止")
        unknown = [r for r in observed if r not in records]
        if unknown:
            raise UpdateFailure(f"存在未登记目标进程，禁止按路径查杀：{unknown!r}")
        # 空扫描也不能代替已登记对象的退出证据；为正常关闭先留出有限宽限。
        if wait_processes(records, min(3, max(0, end - time.monotonic()))) and not image_processes(target):
            return
        for record in records:
            handle = open_process(record["pid"], True)
            if not handle:
                continue
            try:
                if wait_signal(handle, 0) or describe(handle, record["pid"]) != record:
                    continue  # PID复用不构成终止授权，必须在同一句柄上核验三元组。
                if not k.TerminateProcess(handle, 2):
                    error = c.WinError(c.get_last_error())
                    if not wait_signal(handle, max(0, min(2000, int((end - time.monotonic()) * 1000)))):
                        raise error
                if not wait_signal(handle, max(0, min(5000, int((end - time.monotonic()) * 1000)))):
                    raise UpdateFailure(f"登记新版未完全退出：{record!r}")
            except OSError as error:
                if getattr(error, "winerror", None) not in (5, 1168) or not wait_signal(handle, 0):
                    raise UpdateFailure(f"无法确认登记新版已退出：{record!r}；{error!r}") from error
            finally:
                k.CloseHandle(handle)
        if time.monotonic() >= end:
            raise UpdateFailure(f"等待登记新版退出超过10秒：{records!r}")
        time.sleep(0.05)  # 再扫只发现阻塞，绝不把同路径进程追加为授权名单。


def reset_confirmation(j):
    stop_new(j)  # 必须先排除上一轮所有写入者，才能清理信号并重新启动。
    work, signals = Path(j["work"]), []
    for name in ("failure.json", "pending.json", "healthy.json", "stop.json"):
        path = work / name
        if not path.exists():
            continue
        if path.resolve() != path or path.stat().st_nlink != 1:
            raise UpdateFailure("确认信号路径越界或存在硬链接")
        value = read_json(path)
        if (not isinstance(value, dict) or value.get("token") != j["token"]
                or (name != "stop.json" and value.get("transaction") != j["transaction"])):
            raise UpdateFailure("旧确认信号不属于当前事务")
        if name == "failure.json":
            log_line(j["target"], f"重新确认前留存旧失败：{value.get('error')!r}")
        signals.append(path)
    for path in signals:
        path.unlink()  # 全部身份校验成功后才删除；不触碰提交标记、签名材料或快照。
    j["new_processes"] = []


def rollback(j):
    if committed(j):
        raise UpdateFailure("已提交事务禁止回退")
    target, work, data = Path(j["target"]), Path(j["work"]), Path(j["data_dir"])
    stop_new(j)
    if not wait_processes(j.get("recovery_waiters", []), 15) or image_processes(target):
        raise UpdateFailure("恢复仍有实例占用，保留pending")
    reserve_data(j, j["old_processes"] + new_records(j) + j.get("recovery_waiters", []))
    target_sha = digest(target) if target.exists() else None
    if target_sha not in (None, j["old_sha"], j["new_sha"]):
        raise UpdateFailure("目标为未知内容，禁止覆盖")
    need_backup = j["new_started"] or target_sha != j["old_sha"]
    if need_backup:
        allowed = {"old.exe", "account.db", "settings.json", "tags.json", "categories.json", "import_mappings.json"}
        if set(j["backups"]) != allowed:
            raise UpdateFailure("快照清单不完整或包含未知文件")
        if j["backups"].get("old.exe") != j["old_sha"]:
            raise UpdateFailure("没有完整旧包备份")
        for name, expected in j["backups"].items():
            if expected is not None and digest(Path(j["backup"]) / name) != expected:
                raise UpdateFailure("快照校验失败")
        if j["new_started"]:
            for name in ("account.db", "settings.json", "tags.json", "categories.json", "import_mappings.json"):
                if name not in j["backups"]:
                    raise UpdateFailure("快照缺少文件状态")
                j["intent"] = "restore:" + name
                write_journal(j)
                destination, expected = data / name, j["backups"][name]
                if destination.resolve() != destination:
                    raise UpdateFailure("恢复路径越界")
                if expected is None:
                    destination.unlink(missing_ok=True)
                else:
                    stage = data / (name + ".accountkeeper-restore")
                    copy_checked(Path(j["backup"]) / name, stage, expected, reusable=True)
                    retry_transient(lambda: os.replace(stage, destination))  # AV共享窗口内有限重试，不掩盖持久拒绝。
                if name == "account.db":
                    for suffix in ("-wal", "-shm", "-journal"):
                        sidecar = data / (name + suffix)
                        if sidecar.resolve() != sidecar:
                            raise UpdateFailure("SQLite伴随文件越界")
                        sidecar.unlink(missing_ok=True)  # 所有登记实例已退出，不能让新版WAL污染旧快照。
                j["steps"].append(name)
                write_journal(j)
        j["intent"] = "restore:exe"
        write_journal(j)
        stage = payload_path(work, target, "restore.exe")
        copy_checked(Path(j["backup"]) / "old.exe", stage, j["old_sha"], reusable=True)
        retry_transient(lambda: os.replace(stage, target))  # exe也可能被杀软短暂占用，与数据恢复使用同一重试口径。
    if digest(target) != j["old_sha"]:
        raise UpdateFailure("旧包未完整恢复")
    j.update(phase="rolled_back", result="failed", intent=None)
    write_journal(j)
    release_data(j)


def reconcile_sqlite(j):
    import sqlite3
    data = Path(j["data_dir"])
    database = data / "account.db"
    companions = [data / ("account.db" + suffix) for suffix in ("-journal", "-wal", "-shm")]
    if not database.exists() or not any(p.exists() for p in companions):
        return True  # 不创建原本不存在的账本；没有伴生文件也不额外触碰数据库。
    with DataGate(data):
        reservation = data / ".accountkeeper-data-update.json"
        if data_members(data) or (reservation.exists() and read_json(reservation) != data_reservation(j)):
            return False  # 已有业务实例或其他事务接管时交给其SQLite连接处理，不裸删活动WAL。
        for p in (database, *companions):
            if p.resolve() != p or (p.exists() and (not p.is_file() or p.stat().st_nlink != 1)):
                raise UpdateFailure("SQLite恢复路径被重定向")
        try:
            with closing(sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=0)) as db:
                with db:
                    if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                        raise UpdateFailure("SQLite原生完整性校验失败")  # 按真实校验结果诊断，不能把官方伴生文件当损坏。
                    if db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
                        return False  # 有读写者时SQLite报告busy，保留WAL中已提交账目并延后清理。
                    mode = db.execute("PRAGMA journal_mode").fetchone()[0]
                    db.execute("BEGIN EXCLUSIVE")
                    if mode != "wal":
                        for p in companions:
                            if p.exists() and p.stat().st_size == 0:
                                p.unlink()  # 非WAL模式独占事务下仅删除空残留；有效日志由SQLite自行恢复。
                    db.commit()  # WAL/shm由SQLite在最后连接关闭时清理，不能在WAL读者存活时手工删除。
        except sqlite3.Error as error:
            if (getattr(error, "sqlite_errorcode", 0) & 255) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                return False  # 未登记的外部连接也受SQLite原生锁保护，忙碌不等同于非法文件。
            raise UpdateFailure(f"SQLite原生恢复失败：{error}") from error
    return True


def record_commit(j):
    with FileLock(Path(j["target"]), "state"):
        state = read_state(j["target"])
        state["committed"] = dict(target=j["target"], sha256=j["new_sha"], data_dir=j["data_dir"],
                                  transaction=j["transaction"], token=j["token"])
        atomic_json(state_path(j["target"]), state)  # 独立终态凭据先落盘，材料目录被删除后仍能核实当前安装。


def retire_committed(j):
    if digest(Path(j["target"])) != j["new_sha"]:
        raise UpdateFailure("提交包与物理目标不符")  # 哈希核验必须先于所有尽力清理，不能被except吞掉。
    settle_attempt(j["target"], j["attempt"], "success", "入口提交补记")
    record_commit(j)
    release_data(j)
    try:
        if not helper_alive(j):
            with FileLock(Path(j["target"]), "install", 0):
                current = load_journal(j["target"])
                if current["transaction"] == j["transaction"] and current["phase"] == "committed":
                    journal_path(j["target"]).unlink()
    except (OSError, UpdateFailure) as error:
        log_line(j["target"], f"终态日志清理待重试：{error}")  # 可信终态已持久化，旧材料或清理失败不得阻断业务。


def cleanup_finished(j):
    installed = committed(j)
    if (not installed and j["phase"] != "rolled_back") or digest(Path(j["target"])) != j["new_sha" if installed else "old_sha"]:
        raise UpdateFailure("终结包核对失败")
    if not installed:
        reconcile_sqlite(j)  # 终态只处理当前数据库的原生日志，不重新恢复事务旧快照。
    release_data(j)  # 终结已核实，尽力删除备份失败不能让预约永久阻塞记账。
    backup = Path(j["backup"])
    if backup.exists():
        allowed = {"old.exe", "account.db", "settings.json", "tags.json", "categories.json", "import_mappings.json"}
        allowed.update({"account.db-journal", "account.db-wal", "account.db-shm"})  # 私有快照终结后可连同SQLite备份残留一起删除。
        for p in backup.iterdir():
            if p.name not in allowed or p.resolve() != p or not p.is_file():
                raise UpdateFailure("备份目录含未知文件，停止清理")
            p.unlink()
        backup.rmdir()
    for name in ("candidate.exe", "staged.exe", "restore.exe"):
        p = payload_path(Path(j["work"]), j["target"], name)
        if p.resolve() != p:
            raise UpdateFailure("暂存路径越界")
        p.unlink(missing_ok=True)
    if installed:
        log_path(j["target"]).unlink(missing_ok=True)  # 提交成功后日志无诊断价值；失败与回退保留。


def recover_transaction(target):
    j = load_journal(target)
    verify_materials(j)
    if committed(j):
        settle_attempt(target, j["attempt"], "success", "健康提交补记")
        cleanup_finished(j)
        j.update(phase="committed", result="success", intent=None)
        write_journal(j)
        return "committed"
    if j["phase"] == "rolled_back":
        if digest(Path(j["target"])) != j["old_sha"]:
            raise UpdateFailure("回退结果与旧包不符")
        reconcile_sqlite(j)
        release_data(j)  # 终态落盘后可能尚未解锁就崩溃；只补解匹配预约，绝不重放数据快照。
        return "rolled_back"  # 已终结的重放不再消耗恢复次数，也不改变原有结算幂等。
    j["recovery_attempts"] = j.get("recovery_attempts", 0) + 1
    write_journal(j)  # 每次恢复先落盘计数，恢复中崩溃也不能把同一attempt永远困在第一次。
    settle_attempt(target, j["attempt"], "failed", j.get("error") or "执行者中断")
    try:
        if j["phase"] != "rolled_back":
            j.update(phase="rolling_back", intent="recover")
            write_journal(j)
            rollback(j)
    except (OSError, UpdateFailure) as error:  # 第三次仍失败也必须尝试安全逃生；新包或未知数据绝不放行。
        j["error"] = f"{j.get('error') or '回滚失败'}; recovery={error!r}"
        write_journal(j)  # 保留实际中断原因，不能只有rolling_back而丢失恢复异常。
        log_line(target, traceback.format_exc())
        if j["recovery_attempts"] >= 3:
            return escape_transaction(j)
        raise
    if j["recovery_attempts"] >= 3:
        return escape_transaction(j)
    return "rolled_back"


def confirm_new(j):
    target, child, guard = Path(j["target"]), None, ExitStack()
    try:
        guard.enter_context(locked_helper(target, j["size"], j["new_sha"]))  # 同一只读共享句柄内验包、拉起并监督新版。
        reset_confirmation(j)  # 在Popen之前隔离上一轮信号，禁止跨启动轮次复用健康或失败记录。
        j.update(phase="launching", intent="launch", new_started=True)
        write_journal(j)
        end = time.monotonic() + 120
        child = subprocess.Popen([str(target), "--update-transaction", j["transaction"], "--update-token", j["token"]],
                                 env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"),
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        record = process_identity(child.pid)
        if record is None:
            raise UpdateFailure("新版登记前已退出")
        j.update(new_processes=[record], phase="confirming", intent=None)
        write_journal(j)
        confirm_end = None
        work = Path(j["work"])
        while time.monotonic() < end:
            if (work / "failure.json").exists():
                failure = read_json(work / "failure.json")
                if failure.get("transaction") != j["transaction"] or failure.get("token") != j["token"]:
                    raise UpdateFailure("失败记录身份不符")
                raise UpdateFailure(f"新版初始化失败：{failure.get('error')}")
            if child.poll() is not None:
                raise UpdateFailure(f"新版提前退出：{child.returncode}")
            if (work / "pending.json").exists():
                records = new_records(j)
                if confirm_end is None:
                    confirm_end = time.monotonic() + 60
                if time.monotonic() >= confirm_end:
                    break
                if (work / "healthy.json").exists():
                    health = read_json(work / "healthy.json")
                    # 新版自报的进程顺序取决于它自己的登记顺序，与helper侧相反，故拆出processes按三元组排序比较（保留重复项），其余字段仍要求全等。
                    processes = health.pop("processes", None) if isinstance(health, dict) else None
                    expected = dict(transaction=j["transaction"], token=j["token"], version=j["new_version"],
                                    sha256=j["new_sha"], migrations=True)
                    if (health != expected or not records or not isinstance(processes, list)
                            or sorted(map(process_key, processes)) != sorted(map(process_key, records))
                            or any(wait_processes([r], 0) for r in records)):
                        raise UpdateFailure("健康确认无效")
                    if digest(target) != j["new_sha"]:
                        raise UpdateFailure("健康包发生变化")
                    if time.monotonic() >= min(end, confirm_end):
                        raise UpdateFailure("健康确认已超过期限")
                    atomic_json(work / "commit.json", dict(transaction=j["transaction"], token=j["token"],
                                attempt=j["attempt"], version=j["new_version"], sha256=j["new_sha"]))
                    settle_attempt(target, j["attempt"], "success", "健康提交")
                    j.update(phase="committed", result="success")
                    write_journal(j)
                    record_commit(j)
                    cleanup_finished(j)
                    return
            time.sleep(0.05)
        raise UpdateFailure("新版确认超时")
    finally:
        try:
            if child is not None and not committed(j):
                stop_new(j)  # 未提交新版先退出并释放资源，防替换锁仍然持有。
                child.wait(5)
        finally:
            guard.close()  # 任何启动/超时/终止异常都先释放句柄，再交外层回滚替换exe。


def run_worker(target, recover=False):
    target = target_path(target)
    with FileLock(target, "install", 0):
        j = load_journal(target)
        if Path(sys.executable).resolve() != Path(j["helper"]) or digest(Path(j["helper"])) != j["helper_sha"]:
            raise UpdateFailure("不是登记的独立helper")
        a = read_state(target)["active"] or (read_state(target)["last"] or {}).get("attempt", {})
        if a.get("handoff") == "preparing":
            raise UpdateFailure("交接尚未持久化完成，禁止helper执行")
        j["helper_processes"] = own_records(Path(j["helper"]))
        transfer = read_state(target)["active"]
        if transfer and transfer["id"] == j["attempt"]["id"]:
            transfer_attempt(target, j["attempt"], j["helper_processes"][0])
        write_journal(j)
        if recover:
            status = recover_transaction(target)
            if status in ("rolled_back", "escaped"):
                subprocess.Popen([str(target)], env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"),
                                 creationflags=subprocess.CREATE_NO_WINDOW)
            return
        try:
            verify_materials(j)
            if not wait_processes(j["old_processes"], 15) or image_processes(target):
                raise UpdateFailure("旧实例未完全退出")
            reserve_data(j, [])  # 快照前再次核验，并依靠持久预约阻止新实例抢入。
            snapshot(j)
            stage = payload_path(Path(j["work"]), target, "staged.exe")
            copy_checked(Path(j["candidate"]), stage, j["new_sha"])
            if digest(target) != j["old_sha"]:
                raise UpdateFailure("目标在准备期间变化")
            j.update(phase="switching", intent="replace")
            write_journal(j)
            os.replace(stage, target)
            if digest(target) != j["new_sha"]:
                raise UpdateFailure("替换后包不符")
            confirm_new(j)
            return
        except Exception as error:  # 提交后错误只能留待清理，不得重新恢复数据。
            log_line(target, f"worker失败：{traceback.format_exc()}")
            j["error"] = repr(error)
            write_journal(j)
            if committed(j):
                settle_attempt(target, j["attempt"], "success", "提交后清理待重试")
                return
            try:
                status = recover_transaction(target)
                if status in ("rolled_back", "escaped"):
                    subprocess.Popen([str(target)], env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"),
                                     creationflags=subprocess.CREATE_NO_WINDOW)
            except Exception as recovery_error:  # 保留真实pending与失败原因，不能冒充回退完成。
                j = load_journal(target)
                j["error"] = f"{error!r}; recovery={recovery_error!r}"
                write_journal(j)
                raise


class BootContext:
    def __init__(self, j):
        self.j, self.records, self.sent = j, own_records(Path(j["target"])), False
        atomic_json(Path(j["work"]) / "pending.json",
                    dict(transaction=j["transaction"], token=j["token"], processes=self.records))

    def healthy(self, version):
        if version != self.j["new_version"]:
            raise UpdateFailure("运行版本与候选不符")
        atomic_json(Path(self.j["work"]) / "healthy.json", dict(transaction=self.j["transaction"],
                    token=self.j["token"], version=version, sha256=self.j["new_sha"],
                    migrations=True, processes=self.records))
        self.sent = True

    def poll(self):
        if committed(self.j):
            return True
        if (Path(self.j["work"]) / "stop.json").exists() or not helper_alive(self.j):
            raise UpdateFailure("更新监督已结束，禁止未提交运行")
        return False

    def failed(self, error):
        if not committed(self.j):
            atomic_json(Path(self.j["work"]) / "failure.json",
                        dict(transaction=self.j["transaction"], token=self.j["token"], error=repr(error)))


def rescue_new(target, token):
    target = target_path(target)
    with FileLock(target, "install", 10):
        j = load_journal(target)
        verify_materials(j)
        runner = payload_path(Path(j["work"]), target, "staged.exe")
        if token != j["token"] or Path(sys.executable).resolve() != runner or helper_alive(j):
            raise UpdateFailure("恢复执行者身份不符")
        if j["phase"] in ("committed", "rolled_back"):
            return  # 排队的重复执行者看到终态就退出，不能再次初始化或恢复已经开放的账本。
        if j["phase"] not in ("switching", "launching", "confirming") or digest(target) != j["new_sha"]:
            raise UpdateFailure("恢复接管阶段或主包不符")
        with locked_helper(runner, j["size"], j["new_sha"]):
            if not wait_processes(j.get("recovery_waiters", []), 30):
                raise Busy("接管前主程序尚未退出")
            j["helper_processes"] = own_records(runner)
            if read_state(target)["active"]:
                transfer_attempt(target, j["attempt"], j["helper_processes"][0])
            write_journal(j)
            try:
                confirm_new(j)  # 独立副本监督真实迁移、首帧和期限；失败后主包可完整退出再回滚。
            except Exception as error:
                if committed(j):
                    return  # 提交后的暂存清理失败不可覆盖用户新账。
                j["error"] = repr(error); write_journal(j)
                status = recover_transaction(target)
                if status in ("rolled_back", "escaped"):
                    subprocess.Popen([str(target)], env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"),
                                     creationflags=subprocess.CREATE_NO_WINDOW)


def escape_transaction(j):
    """仅在旧包与数据安全可证时隔离日志；调用者负责继续启动或拉起旧版。"""
    target, work, data = Path(j["target"]), Path(j["work"]), Path(j["data_dir"])
    if (j["phase"] in ("switching", "launching", "confirming") and target.is_file()
            and digest(target) == j["new_sha"] and not helper_alive(j) and not committed(j)):
        verify_materials(j)
        required = {"old.exe", "account.db", "settings.json", "tags.json", "categories.json", "import_mappings.json"}
        if (set(j["backups"]) != required or j["backups"]["old.exe"] != j["old_sha"]
                or any(value is not None and digest(Path(j["backup"]) / name) != value for name, value in j["backups"].items())):
            raise UpdateFailure("接管缺少可安全回退的快照")  # 在迁移前证明失败回退可执行，不能仅凭新包签名直接提交。
        runner = payload_path(work, target, "staged.exe")
        with locked_helper(target, j["size"], j["new_sha"]):
            copy_checked(target, runner, j["new_sha"], reusable=True)
        j["recovery_waiters"] = own_records(target)
        write_journal(j)
        with locked_helper(runner, j["size"], j["new_sha"]):
            subprocess.Popen([str(runner), "--update-rescue", str(target), j["token"]],
                             env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"), creationflags=subprocess.CREATE_NO_WINDOW)
        return "rescuing"  # 调用者必须退出，由独立副本确认或回滚，不能留在业务启动路径。
    if committed(j) or not target.is_file() or digest(target) != j["old_sha"]:
        raise UpdateFailure("逃生未获安全旧包，禁止放行业务")
    if not wait_processes(new_records(j), 0):
        raise UpdateFailure("新版尚未退出，禁止逃生")
    if not reconcile_sqlite(j):
        raise Busy("SQLite仍被占用，尚不能完成逃生数据核验")  # 未终结事务必须等原生恢复完成，不能只核对未合并WAL的主文件。
    if j["new_started"] or any((work / name).exists() for name in ("pending.json", "healthy.json")):
        for name in ("account.db", "settings.json", "tags.json", "categories.json", "import_mappings.json"):
            if name not in j["backups"]:
                raise UpdateFailure("逃生缺少数据快照证据")
            path, expected = data / name, j["backups"][name]
            if path.resolve() != path or (path.exists() if expected is None else not path.is_file() or digest(path) != expected):
                raise UpdateFailure("逃生数据未恢复，禁止放行业务")
    state = read_state(target)
    if state["active"]:
        settle_attempt(target, state["active"], "cancelled", "恢复次数达到3次，安全逃生")
    j.update(phase="rolled_back", result="failed", intent=None, escape_verified=True)
    write_journal(j)  # 解锁前先持久化终态；此后其他副本可写账，重启不得再用旧快照回退。
    release_data(j)  # 解锁失败保留终态日志，由入口补解；归档仍必须在解锁成功之后。
    path = journal_path(target)
    retry_transient(lambda: os.replace(path, path.with_name(path.name + ".bak")))
    return "escaped"


def recovery_evidence(target):
    # 日志不可读时仍从独立持久绑定和物理文件判危险态；诊断失败也只能报告未知，不能变成放行。
    try:
        s = read_state(target)
        attempt = s["active"] or (s["last"] or {}).get("attempt") or {}
        binding = attempt.get("binding") or {}
        work = Path(binding["work"])
        if (work.parent != transaction_root(target) or work.resolve() != work
                or len(work.name) != 32 or any(c not in "0123456789abcdef" for c in work.name)):
            raise UpdateFailure("物理证据目录越界")
        current = digest(target) if target.is_file() else None
        is_new, has_commit = current == binding.get("new_sha"), (work / "commit.json").exists()
        marker = read_json(work / "commit.json") if has_commit else {}
        if not isinstance(marker, dict) or not isinstance(marker.get("attempt", {}), dict):
            raise UpdateFailure("提交证据结构损坏")  # 无法解释的物理证据不能被当成有效提交。
        proven = (not s["active"] and (s["last"] or {}).get("outcome") == "success"
                  and marker.get("transaction") == work.name and marker.get("sha256") == binding.get("new_sha")
                  and all(marker.get("attempt", {}).get(k) == attempt.get(k) for k in ("id", "seq")))
        return f"target为新包={is_new}，commit.json存在={has_commit}，危险中间态={is_new and not proven}"
    except (OSError, UpdateFailure, KeyError, TypeError, ValueError):
        return "物理证据不足，危险状态无法排除"  # 缺少证据只能fail-closed，不能假定旧版安全。


def bootstrap():
    if not getattr(sys, "frozen", False):
        return None  # 源码启动不执行exe替换，也不探测正式账本。
    if len(sys.argv) == 4 and sys.argv[1] == "--update-rescue":
        rescue_new(Path(sys.argv[2]), sys.argv[3])
        return False  # 复制的主程序只执行恢复监督，绝不装载业务配置或主窗。
    target = target_path(Path(sys.executable).resolve())
    reconcile_handoff(target)  # 业务导入之前修复准备发布中断，armed事务仍严格恢复。
    path = journal_path(target)
    if not path.exists():
        s = read_state(target)
        a = s["active"] or (s["last"] or {}).get("attempt") or {}
        binding = a.get("binding")
        receipt = s.get("committed")
        if (receipt and not (s["active"] and binding) and (not binding or
                (binding.get("work") == str(transaction_root(target) / receipt["transaction"])
                 and binding.get("new_sha") == receipt.get("sha256") and binding.get("data_dir") == receipt.get("data_dir")))):
            if receipt.get("target") != str(target) or digest(target) != receipt.get("sha256"):
                raise UpdateFailure("终态安装凭据与当前主包不符")
            release_data(receipt)
            return None  # 日志及隐藏目录可被清理；有新安装绑定时仍走原有缺日志拦截。
        if binding:
            if digest(target) != binding.get("old_sha") or s["active"]:
                raise UpdateFailure(f"事务日志缺失，禁止打开账本：{recovery_evidence(target)}")
            archived = load_journal(target, archived=True)  # 归档也复用完整日志身份、阶段、路径和状态绑定校验。
            if (archived.get("escape_verified") is not True or not isinstance(archived.get("attempt"), dict)
                    or any(archived["attempt"].get(k) != a.get(k) for k in ("id", "seq"))
                    or any(archived.get(k) != binding.get(k) for k in ("old_sha", "new_sha", "work", "data_dir"))
                    or type(archived.get("recovery_attempts")) is not int or archived["recovery_attempts"] < 3):
                raise UpdateFailure("事务日志缺失且没有匹配的安全逃生归档")
            reconcile_sqlite(archived)
            release_data(archived)  # 对归档后的崩溃补做释放；只匹配本事务身份，不解除别人的预约。
            # .bak只由安全逃生发布；已放行旧版的账本可以继续写，不能再拿旧快照摘要否决正常记账。
        if s["active"] and wait_processes([s["active"]["owner"]], 0):
            settle_attempt(target, s["active"], "failed", "检查或下载进程中断")
        return None
    try:
        j = load_journal(target)
        if j["phase"] == "committed":
            retire_committed(j)
            return None  # 先验证实际主包，终态不再依赖可被用户删除的签名材料或commit文件。
        verify_materials(j)
    except (OSError, UpdateFailure, ValueError) as error:
        raise UpdateFailure(f"日志或材料损坏，禁止打开账本：{recovery_evidence(target)}") from error
    if committed(j):
        if digest(target) != j["new_sha"]:
            raise UpdateFailure("提交包与物理目标不符")  # 安全判定在尽力清理的except之外，不能被清理兜底吞掉。
        settle_attempt(target, j["attempt"], "success", "入口提交补记")
        if not helper_alive(j):
            try:
                with FileLock(target, "install", 0):
                    cleanup_finished(j)
                    helper = Path(j["helper"])
                    if helper.exists() and digest(helper) == j["helper_sha"]:
                        helper.unlink()  # 独立helper完全退出后才删除，提交证据仍保留。
                    j.update(phase="committed", result="success")
                    write_journal(j)
            except (OSError, UpdateFailure) as error:
                print(f"提交后清理待重试：{error}")  # 清理失败不能阻止正常记账或恢复旧数据。
        return None
    if j["phase"] == "rolled_back":
        settle_attempt(target, j["attempt"], "failed", j.get("error") or "回退完成补记")
        if digest(target) != j["old_sha"]:
            raise UpdateFailure("回退包与日志不符")
        reconcile_sqlite(j)
        release_data(j)  # 不依赖helper是否存在或已退出；终态核验后补解本事务预约，失败必须穿透入口。
        if not helper_alive(j):
            helper = Path(j["helper"])
            if helper.exists() and digest(helper) == j["helper_sha"]:
                try:
                    cleanup_finished(j)
                    helper.unlink()
                except OSError as error:
                    print(f"回退helper清理待重试：{error}")
        return None
    args = sys.argv[1:]
    authorized = ("--update-transaction" in args and "--update-token" in args
                  and args[args.index("--update-transaction") + 1] == j["transaction"]
                  and args[args.index("--update-token") + 1] == j["token"])
    if authorized and helper_alive(j):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            j = load_journal(target)
            if any(r in j["new_processes"] for r in own_records(target)):
                if digest(target) != j["new_sha"]:
                    raise UpdateFailure("待确认目标不符")
                return BootContext(j)
            time.sleep(0.02)
        raise UpdateFailure("新版启动身份未登记")
    if helper_alive(j):
        raise UpdateFailure("更新正在进行，请等待当前事务结束")
    if j["phase"] in ("switching", "launching", "confirming") and digest(target) == j["new_sha"]:
        with FileLock(target, "install", 0):
            return False if escape_transaction(load_journal(target)) == "rescuing" else None
    with FileLock(target, "install", 0):
        j = load_journal(target)
        if j.get("recovery_attempts", 0) >= 3 and not j["new_started"] and digest(target) == j["old_sha"]:
            escape_transaction(j)
            return None  # 当前映像已核实为旧版，直接继续；worker逃生则负责另起旧版。
        owner = (read_state(target)["active"] or {}).get("owner")
        if owner and not wait_processes([owner], 0):
            raise UpdateFailure("事务执行者仍存活")
        settle_attempt(target, j["attempt"], "failed", j.get("error") or "未提交执行者中断")
        j["recovery_waiters"] = own_records(target)
        write_journal(j)
    try:
        launch_helper(j, True)
    except Exception:  # 接管失败也消耗恢复次数，不能因helper永远起不来而永不触发逃生。
        with FileLock(target, "install", 0):
            j = load_journal(target)
            if helper_alive(j):
                raise  # 仍在运行的执行者拥有事务，禁止抢占或隔离。
            j["recovery_attempts"] = j.get("recovery_attempts", 0) + 1
            write_journal(j)
            if j["recovery_attempts"] >= 3:
                return False if escape_transaction(j) == "rescuing" else None
        raise
    return False  # 当前主exe必须先退出，临时helper才能恢复它。


def native_notice(message, ask=False):
    import ctypes
    return ctypes.windll.user32.MessageBoxW(None, str(message), "AccountKeeper 更新",
                                           0x00000004 | 0x00000020 if ask else 0x00000010) == 6


def offer_manual_retry(target, version, arch, data_dir):
    s = read_state(target)
    if not s["paused"]:
        return True
    reason = s["reasons"][-1]["reason"] if s["reasons"] else "状态待核实"
    if not native_notice(f"自动检查已暂停：连续失败{s['failures']}次。\n最近原因：{reason}\n是否仅手动重试本次？选择否将保持暂停。", True):
        return True
    from updater import check_updates, download
    offer = check_updates(version, arch, target_exe=target, manual=True)
    if offer is None:
        native_notice("检查成功，当前没有可用更新。")
        return True
    if not native_notice(f"发现版本{offer.version}，是否下载并安装？", True):
        settle_attempt(target, offer.attempt, "cancelled", "用户取消")
        return True
    ready = download(offer, target, version, arch, manual=True)
    j = prepare_install(ready, target, version, arch, data_dir)
    launch_helper(j)
    return False


def main():
    global _HELPER_DATA_DIR
    sys.modules["update_helper"] = sys.modules[__name__]  # updater复用同一个模块实例，避免丢失参数绑定。
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--install", type=Path)
    group.add_argument("--recover", type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    args = parser.parse_args()
    if not args.data_dir.is_absolute() or args.data_dir.resolve(strict=True) != args.data_dir:
        raise UpdateFailure("helper数据目录必须是已存在的无重解析绝对路径")
    _HELPER_DATA_DIR = args.data_dir
    if not getattr(sys, "frozen", False):
        raise UpdateFailure("独立执行必须使用冻结helper")
    try:
        run_worker(args.install or args.recover, bool(args.recover))
        return 0
    except Exception as error:  # 独立helper无异常弹窗；状态保留pending，退出码显式失败。
        log_line(args.install or args.recover, traceback.format_exc())
        print(f"更新helper失败：{error!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
