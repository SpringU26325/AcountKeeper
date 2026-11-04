"""更新终态和孤立载荷清理；不导入配置、不打开账本，调用方必须在后台执行。"""
import ctypes
from ctypes import wintypes as w
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time

import update_helper as h


class Keep(Exception):
    """证据不足或预算耗尽，保留现场。"""


def checked(path):
    path = Path(path)
    if not path.is_absolute() or str(path).startswith("\\\\") or path.resolve() != path:
        raise Keep("路径不规范")
    for part in (path, *path.parents):
        try:
            if part.lstat().st_file_attributes & 0x400:
                raise Keep("路径含重解析点")
        except FileNotFoundError:
            continue  # 缺失允许幂等重试；悬空链接仍会被lstat识别。
    return path


def stamp(path):
    checked(path)
    s = path.lstat()
    directory = bool(s.st_file_attributes & 16)
    if not directory and (s.st_nlink != 1 or not path.is_file()):
        raise Keep("非普通单链接文件")
    return [s.st_dev, s.st_ino, directory, 0 if directory else s.st_size,
            0 if directory else s.st_mtime_ns, s.st_file_attributes]


def tick(end, stop):
    if stop.is_set() or time.monotonic() >= end:
        raise Keep("取消或超过2秒预算")


def file_digest(path, pins=None):
    if pins is None or path not in pins.handles:
        return h.digest(path)
    result = hashlib.sha256()
    for chunk in pins.chunks(path):
        result.update(chunk)
    return result.hexdigest()


def read_record(path, pins=None):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise Keep("重复JSON键")
            result[key] = value
        return result
    try:
        before = stamp(path)
    except FileNotFoundError:
        return None, None
    if before[2] or before[3] > 65536:
        raise Keep("记录类型或大小异常")
    if pins is not None and path in pins.handles:
        raw = b"".join(pins.chunks(path, 65537))
    else:
        with path.open("rb") as stream:
            raw = stream.read(65537)
    if len(raw) > 65536 or stamp(path) != before:
        raise Keep("读取期间记录变化")
    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise Keep("记录不是对象")
    return value, [before, hashlib.sha256(raw).hexdigest()]


def evidence(target, token, pins=None):
    root = h.update_root(target)
    work = root / "transactions" / token
    paths = [root / name for name in (".accountkeeper-update-state.json",
             ".accountkeeper-update.json", ".accountkeeper-update.json.bak")]
    paths += [work / "owner.json", work / "commit.json"] if token else []
    values, fingerprint = [], []
    for path in paths:
        value, identity = read_record(path, pins)
        values.append(value)
        fingerprint.append([str(path), identity])
    state = h.read_state(target)  # 先限制文件大小，再复用既有完整状态结构验证。
    if state["active"] is not None:
        raise Keep("仍有活动更新尝试")
    return values, fingerprint, work, state


def helper_busy():
    """宁可被其他安装的helper阻止，也不能把查询失败当退出证据。"""
    try:
        c, _, api = h.windows_api()
        class Entry(c.Structure):
            _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("pid", w.DWORD),
                        ("heap", c.c_size_t), ("module", w.DWORD), ("threads", w.DWORD),
                        ("parent", w.DWORD), ("priority", w.LONG), ("flags", w.DWORD),
                        ("name", w.WCHAR * 260)]
        for name in ("Process32FirstW", "Process32NextW"):
            getattr(api, name).argtypes = [w.HANDLE, c.POINTER(Entry)]
            getattr(api, name).restype = w.BOOL
        snapshot = api.CreateToolhelp32Snapshot(2, 0)
        if snapshot == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        try:
            entry = Entry(size=c.sizeof(Entry))
            more = api.Process32FirstW(snapshot, c.byref(entry))
            while more:
                if entry.name.casefold() in ("update_helper.exe", "staged.exe"):
                    if h.process_identity(entry.pid) is not None:
                        return True
                more = api.Process32NextW(snapshot, c.byref(entry))
            if c.get_last_error() != 18:
                raise c.WinError(c.get_last_error())
            return False
        finally:
            api.CloseHandle(snapshot)
    except Exception:
        return True  # 权限不足、枚举失败及身份查询异常都保守视为存活。


@contextmanager
def locks(target):
    with ExitStack() as stack:
        for kind in ("install", "download", "state", "cleanup"):
            stack.enter_context(h.FileLock(target, kind, 0))
        yield  # 与安装、下载、结算及另一个清扫实例共享互斥，绝不等锁。


def record(target, source, action, detail):
    try:
        path = checked(h.update_root(target) / f"cleanup-{source}.jsonl")
        if path.exists() and (stamp(path)[3] > 262144):
            return False  # 日志已满停止删除；不为腾空间覆盖既有证据。
        if path.exists():
            stamp(path)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(time=time.time(), pid=os.getpid(), source=source,
                         action=action, detail=str(detail)), ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False  # 诊断失败不影响启动；删除者须在写意图失败时让路。


def inventory(folder, allowed):
    files = []
    if folder.exists():
        checked(folder)
        with os.scandir(folder) as entries:
            for entry in entries:
                if len(files) >= len(allowed) or entry.name not in allowed:
                    raise Keep("目录含未知内容或备份")
                path = folder / entry.name
                identity = stamp(path)
                if identity[2]:
                    raise Keep("候选含子目录")
                files.append([str(path), identity])
    return sorted(files)


def plan(target, folder, source, journal=None, pins=None):
    target, folder = checked(target), checked(folder)
    base = target.parent / ".accountkeeper-update"
    if folder != base and (folder.parent != base or not re.fullmatch("[0-9a-f]{32}", folder.name)):
        raise Keep("目录不在唯一扫描根下")
    token = folder.name if folder != base else ""
    values, proof, work, state = evidence(target, token, pins)
    if helper_busy():
        raise Keep("helper存活或无法查询")
    files = inventory(folder, {"update_helper.exe"} if token else set())
    extra = []
    if source == "committed_cleanup":
        # 加锁前已由load_journal完整校验；锁定后从同一日志句柄重读，仍须与原日志全文相等。
        current = h.load_journal(target) if pins is None else values[1]
        receipt = state.get("committed")
        expected = dict(target=str(target), sha256=current["new_sha"], data_dir=current["data_dir"],
                        transaction=token, token=current["token"])
        if (current != journal or current["phase"] != "committed" or receipt != expected
                or h.helper_alive(current) or file_digest(target, pins) != current["new_sha"]):
            raise Keep("终态身份或退出证据不符")
        if files and file_digest(Path(files[0][0]), pins) != current["helper_sha"]:
            raise Keep("helper摘要不符")
        names = {"owner.json", "manifest.json", "manifest.sig", "ready.json", "ready.json.tmp",
                 "pending.json", "healthy.json", "stop.json", "failure.json", "commit.json"}
        extra = inventory(work, names)
        if values[3] is not None and values[3] != dict(schema=1, transaction_id=token, target=str(target)):
            raise Keep("owner与终态不符")
    else:
        if values[1] is not None or (token and work.exists()):
            raise Keep("存在事务日志或对应元数据目录")
        # 检查持久绑定与归档引用；不遍历AppData中的其他事务内容。
        for value in values:
            if value is not None and token and token in json.dumps(value, ensure_ascii=False):
                raise Keep("仍有事务引用")
    identity = stamp(folder) if folder.exists() else None
    if source != "committed_cleanup" and identity is None:
        raise Keep("候选已消失，来源未知")
    digest = file_digest(Path(files[0][0]), pins) if files else None
    return dict(path=str(folder), identity=identity, files=files + extra, digest=digest, evidence=proof,
                work=str(work) if source == "committed_cleanup" else None, source=source)


class Pins:
    """同一句柄核验身份并标记删除，祖先禁止改名，杜绝路径偷换后递归删除。"""
    def __init__(self):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
        self.api.CreateFileW.restype = w.HANDLE
        self.api.CloseHandle.argtypes = [w.HANDLE]
        self.api.SetFileInformationByHandle.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.api.SetFilePointerEx.argtypes = [w.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), w.DWORD]
        self.api.ReadFile.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p]
        class Info(ctypes.Structure):
            _fields_ = [("attr", w.DWORD), ("created", w.FILETIME), ("access", w.FILETIME),
                        ("written", w.FILETIME), ("volume", w.DWORD), ("high", w.DWORD),
                        ("low", w.DWORD), ("links", w.DWORD), ("id_high", w.DWORD), ("id_low", w.DWORD)]
        self.Info = Info
        self.api.GetFileInformationByHandle.argtypes = [w.HANDLE, ctypes.POINTER(Info)]
        class Identity(ctypes.Structure):
            _fields_ = [("volume", ctypes.c_ulonglong), ("file_id", ctypes.c_ubyte * 16)]
        self.Identity = Identity
        self.api.GetFileInformationByHandleEx.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.handles = {}
        self.identities = {}

    def open(self, path, remove=False):
        identity = stamp(path)
        access = (65536 if remove else 0) | (0 if identity[2] else 0x80000000)
        handle = self.api.CreateFileW(str(path), access, 3 if identity[2] else 1,
                                      None, 3, 0x02200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        self.handles[path] = handle
        self.identities[path] = identity
        self.inspect(path)

    def inspect(self, path):
        identity = self.identities[path]
        info = self.Info()
        if not self.api.GetFileInformationByHandle(self.handles[path], ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        full = self.Identity()
        if not self.api.GetFileInformationByHandleEx(self.handles[path], 18, ctypes.byref(full), ctypes.sizeof(full)):
            raise ctypes.WinError(ctypes.get_last_error())
        size = info.high << 32 | info.low
        written = ((info.written.dwHighDateTime << 32 | info.written.dwLowDateTime) - 116444736000000000) * 100
        if (info.attr & 0x400 or (not identity[2] and info.links != 1)
                # FILE_ID_INFO与Python当前stat同用完整卷标识及128位文件ID，不能截断卷号比较。
                or full.volume != identity[0] or int.from_bytes(bytes(full.file_id), "little") != identity[1]
                or bool(info.attr & 16) != identity[2] or (not identity[2] and (size != identity[3] or written != identity[4]))
                or stamp(path) != identity):
            raise Keep("打开后的对象身份变化")

    def chunks(self, path, limit=None):
        # 不重开普通CRT读句柄：原GENERIC_READ|DELETE和拒绝写/删除共享的句柄全程持有。
        self.inspect(path)
        handle = self.handles[path]
        if not self.api.SetFilePointerEx(handle, 0, None, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        remaining = limit
        while remaining is None or remaining > 0:
            size = 65536 if remaining is None else min(65536, remaining)
            buffer, count = ctypes.create_string_buffer(size), w.DWORD()
            if not self.api.ReadFile(handle, buffer, size, ctypes.byref(count), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if not count.value:
                break
            yield buffer.raw[:count.value]
            if remaining is not None:
                remaining -= count.value
        self.inspect(path)  # 摘要和JSON都在同一受保护对象上读，读后再次核对身份、大小和写入时刻。

    def delete(self, path):
        flag = ctypes.c_byte(1)
        if not self.api.SetFileInformationByHandle(self.handles[path], 4, ctypes.byref(flag), 1):
            raise ctypes.WinError(ctypes.get_last_error())
        self.api.CloseHandle(self.handles.pop(path))

    def close(self):
        for handle in self.handles.values():
            self.api.CloseHandle(handle)
        self.handles.clear()
        self.identities.clear()


def erase(target, candidate, stop, end, journal=None):
    folder, source = Path(candidate["path"]), candidate["source"]
    with locks(target):
        tick(end, stop)
        if plan(target, folder, source, journal) != candidate:
            raise Keep("确认后事务、进程或内容变化")
        dirs = {folder}
        if folder != target.parent / ".accountkeeper-update":
            dirs.add(folder.parent)  # 最外层只删更新空壳，绝不把安装目录标为可删。
        if candidate["work"]:
            dirs.add(Path(candidate["work"]))
        files = [Path(name) for name, _identity in candidate["files"]]
        journal_path = h.journal_path(target) if journal is not None else None
        if journal_path is not None:
            files.append(journal_path)  # 最后删日志；材料部分清理失败时仍可在终态入口重试。
        pins = Pins()
        try:
            ancestors = set().union(*(set(p.parents) for p in dirs | set(files)))
            for path in sorted(ancestors | dirs, key=lambda p: len(p.parts)):
                tick(end, stop)
                if path.exists():
                    pins.open(path, path in dirs)
            for path in files:
                tick(end, stop)
                pins.open(path, True)
            if plan(target, folder, source, journal, pins) != candidate:
                raise Keep("锁定后证据变化")
            if not record(target, source, "intent", candidate):
                raise Keep("无法记录删除意图")
            for path in files:
                if path == journal_path:
                    continue
                tick(end, stop)
                pins.delete(path)
                record(target, source, "deleted", path)
            for path in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
                tick(end, stop)
                if path in pins.handles:
                    with os.scandir(path) as entries:
                        empty = next(entries, None) is None
                    if empty:
                        pins.delete(path)
                        record(target, source, "deleted", path)
                    elif path == folder or (candidate["work"] and path == Path(candidate["work"])):
                        raise Keep("目录新增内容，保留重试日志")
            if journal_path is not None:
                tick(end, stop)
                pins.delete(journal_path)
                record(target, source, "deleted", journal_path)
        finally:
            pins.close()  # 异常、取消和超时均释放全部句柄，不回滚已经完成的白名单删除。


def finish_committed(journal, stop=None):
    stop = stop if stop is not None else threading.Event()
    target = Path(journal["target"])
    try:
        end = time.monotonic() + 2
        with locks(target):
            folder = target.parent / ".accountkeeper-update" / journal["transaction"]
            candidate = plan(target, folder, "committed_cleanup", journal)
        erase(target, candidate, stop, end, journal)
    except Exception as error:
        record(target, "committed_cleanup", "kept", repr(error))


def defer_committed(journal):
    # bootstrap只登记后台任务；原有安全判定仍在入口执行，清理绝不等待主窗或阻断启动。
    threading.Thread(target=finish_committed, args=(dict(journal),), daemon=True).start()


def suppression(target, candidate, cancel=False):
    path = h.update_root(target) / "cleanup-suppression.json"
    value, _ = read_record(path)
    if value is None:
        h.atomic_json(path, {"schema": 1, "entries": {}})  # 提示前确认独立记录可持久化；失败便不提示。
    value = value if value is not None else {"schema": 1, "entries": {}}
    if value.get("schema") != 1 or not isinstance(value.get("entries"), dict):
        raise Keep("抑制记录损坏")
    entries = value["entries"]
    for item in entries.values():
        if (not isinstance(item, dict) or set(item) != {"fingerprint", "until"}
                or not isinstance(item["fingerprint"], str) or type(item["until"]) not in (int, float)
                or not 0 <= item["until"] < float("inf")):
            raise Keep("抑制记录条目损坏")
    fingerprint = hashlib.sha256(json.dumps(candidate, sort_keys=True).encode()).hexdigest()
    key = candidate["path"]
    if cancel:
        entries[key] = dict(fingerprint=fingerprint, until=time.time() + 7 * 86400)
        if len(json.dumps(value).encode()) > 60000:
            raise Keep("抑制记录已满")
        checked(path.with_name(path.name + ".tmp"))
        h.atomic_json(path, value)
        return True
    item = entries.get(key)
    return bool(item and item["fingerprint"] == fingerprint and item["until"] > time.time())


def scan(target, stop):
    end, found = time.monotonic() + 2, []
    try:
        with locks(target):
            value, _ = read_record(h.journal_path(target))
        if value is not None and value.get("phase") == "committed":
            finish_committed(value, stop)
        with locks(target):
            base = checked(Path(target).parent / ".accountkeeper-update")
            if not base.exists():
                return found
            with os.scandir(base) as entries:
                folders = []
                for entry in entries:
                    tick(end, stop)
                    if len(folders) >= 128:
                        raise Keep("超过128个直属目录预算")
                    folders.append(base / entry.name)
            for folder in folders or [base]:
                tick(end, stop)
                try:
                    candidate = plan(target, folder, "startup_cleanup")
                    if not suppression(target, candidate):
                        found.append(candidate)
                except Exception as error:
                    record(target, "startup_cleanup", "kept", f"{folder}: {error!r}")
    except Exception as error:
        record(target, "startup_cleanup", "kept", repr(error))
    return found  # 任何失败只丢弃清扫机会，不改变原有启动恢复判定。
