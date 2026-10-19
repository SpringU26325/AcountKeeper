"""独立更新事务与早期恢复；导入不读配置、不连接账本、不初始化Windows API。"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time


class UpdateFailure(Exception):
    """拒绝无法证明安全的更新或恢复。"""


class Paused(UpdateFailure):
    """自动检查已暂停；只有明确手动操作可开始新尝试。"""


def target_path(path):
    p = Path(path).absolute()
    if p.name != "AccountKeeper.exe" or p.resolve() != p:
        raise UpdateFailure("目标路径必须是无重解析的AccountKeeper.exe")
    return p


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise UpdateFailure("记录含重复键")
            result[key] = value
        return result
    p = Path(path)
    if p.resolve() != p or not p.is_file() or p.stat().st_size > 1048576:
        raise UpdateFailure("记录路径或大小不合法")
    try:
        with p.open("rb") as stream:
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
    os.replace(temporary, p)  # 调用方先持对应锁；计数和去重标记一次发布。


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(65536):
            result.update(chunk)
    return result.hexdigest()


def copy_checked(source, destination, expected):
    source, destination = Path(source), Path(destination)
    if source.resolve() != source or destination.resolve() != destination or digest(source) != expected:
        raise UpdateFailure("复制源或路径校验失败")
    with source.open("rb") as src, destination.open("wb") as dst:
        shutil.copyfileobj(src, dst, 65536)
        dst.flush()
        os.fsync(dst.fileno())
    if digest(destination) != expected:
        raise UpdateFailure("复制后哈希不符")


class FileLock:
    def __init__(self, target, kind, seconds=10):
        self.path = target_path(target).parent / (".accountkeeper-" + kind + ".lock")
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
                        raise UpdateFailure("已有更新或恢复操作持锁")
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


def image_processes(target):
    c, w, k = windows_api()
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
                if record:
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
    return target_path(target).parent / ".accountkeeper-update-state.json"


def read_state(target):
    target = target_path(target)
    path = state_path(target)
    if not path.exists():
        if (target.parent / ".accountkeeper-update.json").exists():
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
    return s


def begin_attempt(target, manual=False):
    target = target_path(target)
    if not target.is_file():
        raise UpdateFailure("目标缺失，先完成恢复再检查更新")
    with FileLock(target, "state"):
        s = read_state(target)
        if s["active"] is not None:
            raise UpdateFailure("上一尝试尚未结算")
        journal = target.parent / ".accountkeeper-update.json"
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


def transfer_attempt(target, attempt, owner, binding=None):
    with FileLock(target, "state"):
        s = read_state(target)
        if not s["active"] or any(s["active"][k] != attempt[k] for k in ("id", "seq")):
            raise UpdateFailure("交接已过期")
        s["active"]["owner"] = owner
        if binding is not None:
            s["active"]["binding"] = binding
        atomic_json(state_path(target), s)


def load_journal(target):
    target = target_path(target)
    j = read_json(target.parent / ".accountkeeper-update.json")
    if not isinstance(j, dict) or j.get("schema") != 1 or j.get("target") != str(target):
        raise UpdateFailure("事务身份不符")
    if j.get("phase") not in {"prepared", "backed_up", "switching", "launching", "confirming", "rolling_back", "rolled_back", "committed"}:
        raise UpdateFailure("未知事务阶段")
    work = Path(j["work"])
    if (work.parent != target.parent / ".accountkeeper-update" or work.resolve() != work
            or len(work.name) != 32 or any(c not in "0123456789abcdef" for c in work.name)):
        raise UpdateFailure("事务目录越界")
    expected = {"candidate": work / "candidate.exe", "helper": work / "update_helper.exe",
                "manifest": work / "manifest.json", "signature": work / "manifest.sig",
                "backup": work / "backup"}
    if any(j.get(k) != str(v) or v.resolve() != v for k, v in expected.items()):
        raise UpdateFailure("事务材料路径越界")
    s = read_state(target)
    a = s["active"] or (s["last"] or {}).get("attempt")
    binding = dict(work=str(work), data_dir=j["data_dir"], old_sha=j["old_sha"], new_sha=j["new_sha"])
    if j["attempt"]["seq"] > s["settled"] or j["phase"] not in ("committed", "rolled_back"):
        if not a or any(a[k] != j["attempt"][k] for k in ("id", "seq")) or a.get("binding") != binding:
            raise UpdateFailure("事务与持久尝试绑定不符")
    data = Path(j["data_dir"])
    if data.resolve() != data or not data.is_dir() or data == target.parent:
        raise UpdateFailure("数据目录归属不符")
    return j


def write_journal(j):
    j["sequence"] += 1
    j["updated"] = time.time()
    atomic_json(Path(j["target"]).parent / ".accountkeeper-update.json", j)


def verify_materials(j):
    from updater import Offer, verify_offer
    work = Path(j["work"])
    ready = read_json(work / "ready.json")
    if (work / "manifest.json").stat().st_size > 65536 or (work / "manifest.sig").stat().st_size > 1024:
        raise UpdateFailure("签名材料越界")
    offer = Offer(ready["release_id"], j["new_version"], tuple(ready["assets"]),
                  (work / "manifest.json").read_bytes(), (work / "manifest.sig").read_bytes())
    m = verify_offer(offer, j["old_version"], j["arch"])
    if m["sha256"] != j["new_sha"] or m["size"] != j["size"]:
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


def prepare_install(ready_path, target, old_version, arch, data_dir, helper_source=None):
    from updater import verify_ready
    target, data_dir = target_path(target), Path(data_dir).resolve(strict=True)
    candidate = verify_ready(ready_path, target, old_version, arch)
    ready, work = read_json(Path(ready_path)), candidate.parent
    attempt = ready.get("attempt")
    if not attempt:
        raise UpdateFailure("ready未登记完整尝试")
    with FileLock(target, "install"):
        records = own_records(target)
        if any(r not in records for r in image_processes(target)):
            raise UpdateFailure("其他实例尚未退出")
        source = Path(helper_source) if helper_source else Path(getattr(sys, "_MEIPASS", "")) / "update_helper.exe"
        source = source.resolve(strict=True)
        helper = work / "update_helper.exe"
        copy_checked(source, helper, digest(source))
        j = dict(schema=1, transaction=work.name, token=secrets.token_hex(32), attempt=attempt,
                 target=str(target), work=str(work), candidate=str(candidate), data_dir=str(data_dir),
                 helper=str(helper), helper_sha=digest(helper), manifest=str(work / "manifest.json"),
                 signature=str(work / "manifest.sig"), backup=str(work / "backup"), backups={},
                 old_version=old_version, new_version=ready["version"], arch=arch,
                 old_sha=digest(target), new_sha=ready["sha256"], size=ready["size"],
                 old_processes=records, new_processes=[], helper_processes=[], recovery_waiters=[],
                 phase="prepared", sequence=0, intent=None, steps=[], result=None, error=None,
                 new_started=False, created=time.time())
        transfer_attempt(target, attempt, process_identity(os.getpid()),
                         dict(work=str(work), data_dir=str(data_dir), old_sha=j["old_sha"], new_sha=j["new_sha"]))
        write_journal(j)
        return j


def launch_helper(j, recover=False):
    helper = Path(j["helper"])
    if helper.resolve() != helper or digest(helper) != j["helper_sha"]:
        raise UpdateFailure("helper副本校验失败")
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    command = [str(helper), "--recover" if recover else "--install", j["target"]]
    process = subprocess.Popen(command, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    launched = process_identity(process.pid)
    if launched is None:
        raise UpdateFailure("helper登记前已退出")
    end = time.monotonic() + 60
    while time.monotonic() < end:
        current = load_journal(Path(j["target"]))
        if launched in current.get("helper_processes", []):
            return  # helper持锁登记落盘后，旧实例才允许退出。
        if process.poll() is not None:
            raise UpdateFailure("helper接管前退出")
        time.sleep(0.05)
    raise UpdateFailure("helper接管超时；保留事务等待恢复")


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
            with closing(sqlite3.connect(src.as_uri() + "?mode=ro", uri=True, timeout=3)) as source:
                with closing(sqlite3.connect(dst)) as destination:
                    with source, destination:
                        source.backup(destination)
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


def stop_new(j):
    records = new_records(j)
    atomic_json(Path(j["work"]) / "stop.json", dict(token=j["token"]))
    if wait_processes(records, 3):
        return
    c, _, k = windows_api()
    for record in records:
        handle = open_process(record["pid"], True)
        if not handle:
            continue
        try:
            if wait_signal(handle, 0):
                continue
            if describe(handle, record["pid"]) == record:
                if not k.TerminateProcess(handle, 2):
                    raise c.WinError(c.get_last_error())
                if not wait_signal(handle, 5000):
                    raise UpdateFailure("新版未完全退出")
        finally:
            k.CloseHandle(handle)


def rollback(j):
    if committed(j):
        raise UpdateFailure("已提交事务禁止回退")
    target, work, data = Path(j["target"]), Path(j["work"]), Path(j["data_dir"])
    stop_new(j)
    if not wait_processes(j.get("recovery_waiters", []), 15) or image_processes(target):
        raise UpdateFailure("恢复仍有实例占用，保留pending")
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
                    copy_checked(Path(j["backup"]) / name, stage, expected)
                    os.replace(stage, destination)
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
        stage = work / "restore.exe"
        copy_checked(Path(j["backup"]) / "old.exe", stage, j["old_sha"])
        os.replace(stage, target)
    if digest(target) != j["old_sha"]:
        raise UpdateFailure("旧包未完整恢复")
    j.update(phase="rolled_back", result="failed", intent=None)
    write_journal(j)


def cleanup_finished(j):
    installed = committed(j)
    if (not installed and j["phase"] != "rolled_back") or digest(Path(j["target"])) != j["new_sha" if installed else "old_sha"]:
        raise UpdateFailure("终结包核对失败")
    backup = Path(j["backup"])
    if backup.exists():
        allowed = {"old.exe", "account.db", "settings.json", "tags.json", "categories.json", "import_mappings.json"}
        for p in backup.iterdir():
            if p.name not in allowed or p.resolve() != p or not p.is_file():
                raise UpdateFailure("备份目录含未知文件，停止清理")
            p.unlink()
        backup.rmdir()
    for name in ("candidate.exe", "staged.exe", "restore.exe"):
        p = Path(j["work"]) / name
        if p.resolve() != p:
            raise UpdateFailure("暂存路径越界")
        p.unlink(missing_ok=True)


def recover_transaction(target):
    j = load_journal(target)
    verify_materials(j)
    if committed(j):
        settle_attempt(target, j["attempt"], "success", "健康提交补记")
        cleanup_finished(j)
        j.update(phase="committed", result="success", intent=None)
        write_journal(j)
        return "committed"
    settle_attempt(target, j["attempt"], "failed", j.get("error") or "执行者中断")
    if j["phase"] != "rolled_back":
        j.update(phase="rolling_back", intent="recover")
        write_journal(j)
        rollback(j)
    elif digest(Path(j["target"])) != j["old_sha"]:
        raise UpdateFailure("回退结果与旧包不符")
    return "rolled_back"


def run_worker(target, recover=False):
    target = target_path(target)
    with FileLock(target, "install", 0):
        j = load_journal(target)
        if Path(sys.executable).resolve() != Path(j["helper"]) or digest(Path(j["helper"])) != j["helper_sha"]:
            raise UpdateFailure("不是登记的独立helper")
        j["helper_processes"] = own_records(Path(j["helper"]))
        transfer = read_state(target)["active"]
        if transfer and transfer["id"] == j["attempt"]["id"]:
            transfer_attempt(target, j["attempt"], j["helper_processes"][0])
        write_journal(j)
        if recover:
            status = recover_transaction(target)
            if status == "rolled_back":
                subprocess.Popen([str(target)], env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"),
                                 creationflags=subprocess.CREATE_NO_WINDOW)
            return
        try:
            verify_materials(j)
            if not wait_processes(j["old_processes"], 15) or image_processes(target):
                raise UpdateFailure("旧实例未完全退出")
            snapshot(j)
            stage = Path(j["work"]) / "staged.exe"
            copy_checked(Path(j["candidate"]), stage, j["new_sha"])
            if digest(target) != j["old_sha"]:
                raise UpdateFailure("目标在准备期间变化")
            j.update(phase="switching", intent="replace")
            write_journal(j)
            os.replace(stage, target)
            if digest(target) != j["new_sha"]:
                raise UpdateFailure("替换后包不符")
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
                        expected = dict(transaction=j["transaction"], token=j["token"], version=j["new_version"],
                                        sha256=j["new_sha"], migrations=True, processes=records)
                        if health != expected or not records or any(wait_processes([r], 0) for r in records):
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
                        cleanup_finished(j)
                        return
                time.sleep(0.05)
            raise UpdateFailure("新版确认超时")
        except Exception as error:  # 提交后错误只能留待清理，不得重新恢复数据。
            j["error"] = repr(error)
            write_journal(j)
            if committed(j):
                settle_attempt(target, j["attempt"], "success", "提交后清理待重试")
                return
            try:
                status = recover_transaction(target)
                if status == "rolled_back":
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


def bootstrap():
    if not getattr(sys, "frozen", False):
        return None  # 源码启动不执行exe替换，也不探测正式账本。
    target = target_path(Path(sys.executable).resolve())
    path = target.parent / ".accountkeeper-update.json"
    if not path.exists():
        s = read_state(target)
        if s["active"] and wait_processes([s["active"]["owner"]], 0):
            settle_attempt(target, s["active"], "failed", "检查或下载进程中断")
        return None
    j = load_journal(target)
    verify_materials(j)
    if committed(j):
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
    with FileLock(target, "install", 0):
        j = load_journal(target)
        owner = (read_state(target)["active"] or {}).get("owner")
        if owner and not wait_processes([owner], 0):
            raise UpdateFailure("事务执行者仍存活")
        settle_attempt(target, j["attempt"], "failed", j.get("error") or "未提交执行者中断")
        j["recovery_waiters"] = own_records(target)
        write_journal(j)
    launch_helper(j, True)
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
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--install", type=Path)
    group.add_argument("--recover", type=Path)
    args = parser.parse_args()
    if not getattr(sys, "frozen", False):
        raise UpdateFailure("独立执行必须使用冻结helper")
    try:
        run_worker(args.install or args.recover, bool(args.recover))
        return 0
    except Exception as error:  # 独立helper无异常弹窗；状态保留pending，退出码显式失败。
        print(f"更新helper失败：{error!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
