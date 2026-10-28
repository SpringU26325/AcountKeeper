"""绿色版卸载：只清理显式清单，外部执行者独立等待进程退出。"""
import base64
from contextlib import ExitStack
import ctypes
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time

import update_helper as helper


_DATA_FILES = {"account.db", "account.db.bak", "account.db-wal", "account.db-shm",
               "account.db-journal", "settings.json", "tags.json", "categories.json",
               "import_mappings.json"}
_TRANSACTION_FILES = {"owner.json", "manifest.json", "manifest.sig", "ready.json",
                      "ready.json.tmp", "pending.json", "stop.json", "healthy.json",
                      "failure.json", "commit.json"}
_PAYLOAD_FILES = {"candidate.exe", "candidate.exe.part", "update_helper.exe",
                  "update_helper.exe.part", "staged.exe", "restore.exe"}
_STATE_FILES = {".accountkeeper-update-state.json", ".accountkeeper-update-state.json.tmp",
                ".accountkeeper-update.json", ".accountkeeper-update.json.tmp",
                ".accountkeeper-update.json.bak", ".accountkeeper-update.log"}


def checked_path(path):
    path = Path(path)
    # 拒绝UNC、相对路径和每一级重解析，避免桌面混放或目录跳转扩大删除范围。
    if not path.is_absolute() or str(path).startswith("\\\\") or path.resolve() != path:
        raise ValueError(f"路径不是本地规范绝对路径：{path}")
    for part in (path, *path.parents):
        if part.exists() and part.lstat().st_file_attributes & 0x400:
            raise ValueError(f"路径含重解析点：{part}")
    return path


def stamp(path):
    info = path.lstat()
    if not path.is_dir() and info.st_nlink != 1:
        raise ValueError(f"文件有硬链接，禁止卸载：{path}")
    # 与Win32句柄的文件编号和100ns FILETIME对齐，删除时核验同一物理对象。
    return dict(path=str(path), id=str(info.st_ino), size=str(info.st_size),
                write=str(info.st_mtime_ns // 100 + 116444736000000000))


def build_plan(target, data, erase):
    target, data = checked_path(target), checked_path(data)
    if (target.name != "AccountKeeper.exe" or not target.is_file() or not data.is_dir()
            or target.parent == data or target.parent in data.parents or data in target.parents):
        raise ValueError("程序与数据目录必须存在、独立且互不包含")
    parents = helper.own_records(target)
    root = helper.update_root(target)
    if root.parent.parent != data:
        raise ValueError("数据目录与更新器定位不一致")
    files, removable = {target}, set()
    with ExitStack() as stack:
        for kind in ("install", "download", "state"):
            stack.enter_context(helper.FileLock(target, kind, 0))
        gate = helper.DataGate(data)
        gate.seconds = 0
        stack.enter_context(gate)
        if helper.read_state(target)["active"] is not None:
            raise ValueError("更新尝试尚未结算，请重启恢复后再卸载")
        journal = helper.journal_path(target)
        if journal.exists():
            record = helper.load_journal(target)
            if record["phase"] not in ("committed", "rolled_back") or helper.helper_alive(record):
                raise ValueError("更新或恢复尚未结束，禁止卸载")
        reservation = data / ".accountkeeper-data-update.json"
        if reservation.exists():
            raise ValueError("数据目录仍有更新预约，请重启恢复后再卸载")
        if (any(r not in parents for group in helper.data_members(data) for r in group)
                or any(r not in parents for r in helper.image_processes(target, all_paths=True))):
            raise ValueError("还有其他记账实例，请全部退出后再卸载")

        def collect(folder, allowed):
            if folder.exists():
                checked_path(folder)
                removable.add(folder)
                for item in folder.iterdir():
                    if item.name in allowed:
                        checked_path(item)
                        if not item.is_file():
                            raise ValueError(f"白名单文件类型异常：{item}")
                        files.add(item)

        # 当前路径的事务仍需owner绑定；历史路径只收集直接子级白名单，不追踪旧exe位置。
        collect(root, _STATE_FILES)
        history_locks = []
        lock_names = {f".accountkeeper-{kind}.lock" for kind in ("install", "download", "state")}
        for historical in root.parent.iterdir():
            if historical == root or not re.fullmatch(r"(?:[0-9a-f]{32}|[0-9a-f]{64})", historical.name):
                continue
            checked_path(historical)
            if not historical.is_dir() or not any(
                    p.name in _STATE_FILES | lock_names and p.is_file() for p in historical.iterdir()):
                continue  # 哈希名不足以证明归属，空目录和仅含用户文件的目录保留。
            for kind in ("install", "download", "state"):
                lock = helper.FileLock(target, kind, 0)
                lock.path = historical / f".accountkeeper-{kind}.lock"
                stack.enter_context(lock)  # 规划期也锁住历史目录，不能把正在下载的目录当作无主残留。
                history_locks.append(lock.path)
            state = historical / ".accountkeeper-update-state.json"
            old_journal = historical / ".accountkeeper-update.json"
            if ((state.exists() and helper.read_json(state).get("active") is not None)
                    or (old_journal.exists() and helper.read_json(old_journal).get("phase") not in ("committed", "rolled_back"))):
                raise ValueError("历史路径仍有更新或恢复未结束，禁止卸载")
            collect(historical, _STATE_FILES)
        removable.add(root.parent)  # 执行者仅删除空目录，有保留内容时绝不递归删除。
        transactions = root / "transactions"
        payload_root = target.parent / ".accountkeeper-update"
        if transactions.exists():
            checked_path(transactions)
            removable.add(transactions)
            for work in transactions.iterdir():
                if not re.fullmatch("[0-9a-f]{32}", work.name):
                    continue
                checked_path(work)
                owner = work / "owner.json"
                if not owner.exists() or helper.read_json(owner) != {
                        "schema": 1, "transaction_id": work.name, "target": str(target)}:
                    continue  # 无owner证据的孤立目录留给人工，不能凭目录名认领。
                collect(work, _TRANSACTION_FILES)
                payload = payload_root / work.name
                collect(payload, _PAYLOAD_FILES)
                collect(payload / "backup", _DATA_FILES | {"old.exe"})
        if payload_root.exists():
            checked_path(payload_root)
            if any(p.parent == payload_root for p in removable):
                removable.add(payload_root)  # 空目录同名也不足以证明归属，至少要认领过一个事务。
        if erase:
            collect(data, _DATA_FILES | {".accountkeeper-sessions.json", ".accountkeeper-sessions.json.tmp"})
        locks = [root / f".accountkeeper-{kind}.lock" for kind in ("install", "download", "state")]
        locks.extend(history_locks)
        locks.append(data / ".accountkeeper-data.lock")
        # 所有祖先目录均以句柄固定；桌面/安装目录只固定，永远不标记删除。
        directories = set()
        for item in files | set(locks) | removable:
            checked_path(item)
            directories.update(item.parents)
            if item.is_dir():
                directories.add(item)
        plan = dict(target=str(target), data=str(data), erase=bool(erase), notify=True,
                    parents=parents, locks=[stamp(p) for p in locks],
                    files=[stamp(p) for p in sorted(files)],
                    directories=[dict(stamp(p), remove=p in removable)
                                 for p in sorted(directories, key=lambda p: len(p.parts))])
    return plan


# 固定脚本仅从环境读取JSON数据；任何用户路径都不参与PowerShell语法拼接。
_WORKER = r'''
$ErrorActionPreference = 'Stop'
$plan = $null
$readySignalled = $false
$handles = New-Object System.Collections.ArrayList
function Notify($text, $failure) {
    if ($plan.notify -and (!$plan.event -or $readySignalled)) {
        [AKDelete]::Notice($text, $failure)
    }
}
try {
    [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
    $plan = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:AK_UNINSTALL_PLAN)) | ConvertFrom-Json
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class AKDelete {
    [StructLayout(LayoutKind.Sequential, Pack=4)] public struct Info {
        public uint Attr; public long Created, Accessed, Written;
        public uint Volume, HighSize, LowSize, Links, HighId, LowId;
    }
    [StructLayout(LayoutKind.Sequential)] public struct Overlap {
        public IntPtr Internal, InternalHigh; public uint Offset, OffsetHigh; public IntPtr Event;
    }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern SafeFileHandle CreateFileW(string p, uint a, uint s, IntPtr sa, uint c, uint f, IntPtr t);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool GetFileInformationByHandle(SafeFileHandle h, out Info i);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool SetFileInformationByHandle(SafeFileHandle h, int k, ref byte value, uint n);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool LockFileEx(SafeFileHandle h, uint flags, uint reserved, uint low, uint high, ref Overlap o);
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern int MessageBoxW(IntPtr owner, string text, string title, uint flags);
    public static void Notice(string text, bool failed) {
        Check(MessageBoxW(IntPtr.Zero, text, "AccountKeeper 卸载", failed ? 0x10010u : 0x10040u) != 0);
    }
    static void Check(bool ok) { if (!ok) throw new Win32Exception(Marshal.GetLastWin32Error()); }
    public static Info Inspect(SafeFileHandle h) {
        Info i; Check(GetFileInformationByHandle(h, out i));
        if ((i.Attr & 0x400) != 0 || ((i.Attr & 16) == 0 && i.Links != 1))
            throw new Exception("拒绝重解析点或硬链接");
        return i;
    }
    public static SafeFileHandle Open(string p, uint access, uint share) {
        var h = CreateFileW(p, access, share, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero);
        if (h.IsInvalid) { h.Dispose(); throw new Win32Exception(Marshal.GetLastWin32Error(), p); }
        try { Inspect(h); return h; } catch { h.Dispose(); throw; }
    }
    public static void Match(SafeFileHandle h, string id, string size, string write, bool directory) {
        var i = Inspect(h);
        if (!directory && (i.Attr & 1) != 0) throw new Exception("文件为只读，停止卸载");
        if ((((ulong)i.HighId << 32) | i.LowId).ToString() != id ||
            (((i.Attr & 16) != 0) != directory) || (!directory &&
            ((((ulong)i.HighSize << 32) | i.LowSize).ToString() != size || i.Written.ToString() != write)))
            throw new Exception("文件身份或内容时刻改变，停止删除");
    }
    public static void Lock(SafeFileHandle h) {
        var o = new Overlap(); Check(LockFileEx(h, 3, 0, 1, 0, ref o));
    }
    public static void Delete(SafeFileHandle h) {
        Inspect(h); byte value = 1; Check(SetFileInformationByHandle(h, 4, ref value, 1));
    }
    public static bool Alive(int id, long created, bool wait) {
        try {
            using (var p = System.Diagnostics.Process.GetProcessById(id)) {
                if (p.StartTime.ToUniversalTime().ToFileTimeUtc() != created) return false;
                return wait ? !p.WaitForExit(60000) : !p.HasExited;
            }
        } catch (ArgumentException) { return false; }
    }
}
'@
    if ($plan.event) {
        $ready = [Threading.EventWaitHandle]::OpenExisting($plan.event)
        [void]$ready.Set(); $ready.Dispose()
        $readySignalled = $true
    }
    foreach ($p in $plan.parents) {
        if ([AKDelete]::Alive($p.pid, $p.created, $true)) { throw '主程序未退出，停止卸载' }
    }
    $dirs = @{}
    foreach ($d in $plan.directories) {
        # 祖先不共享删除权限，堵住检查后把目录换成junction的时间窗口。
        $access = if ($d.remove) {65536} else {0}
        $h = [AKDelete]::Open($d.path, $access, 3)
        [void]$handles.Add($h); $dirs[$d.path] = $h
        [AKDelete]::Match($h, $d.id, '', '', $true)
    }
    $locks = @()
    foreach ($f in $plan.locks) {
        $h = [AKDelete]::Open($f.path, [uint32]3221291008, 3)
        [void]$handles.Add($h); $locks += $h
        [AKDelete]::Match($h, $f.id, $f.size, $f.write, $false)
        [AKDelete]::Lock($h)
    }
    $sessions = [IO.Path]::Combine($plan.data, '.accountkeeper-sessions.json')
    if ([IO.File]::Exists($sessions)) {
        $groups = [IO.File]::ReadAllText($sessions) | ConvertFrom-Json
        foreach ($group in $groups) { foreach ($p in $group) {
            if (!$p.pid -or !$p.created -or !$p.image) { throw '实例登记损坏' }
            if ([AKDelete]::Alive($p.pid, $p.created, $false)) { throw '仍有账本实例运行' }
        }}
    }
    foreach ($p in [Diagnostics.Process]::GetProcesses()) {
        try { if ($p.ProcessName.StartsWith('AccountKeeper', [StringComparison]::OrdinalIgnoreCase)) {
            throw '仍有记账程序运行，停止卸载'
        }} finally { $p.Dispose() }
    }
    $currentRoot = [IO.Path]::GetDirectoryName($plan.locks[0].path)
    foreach ($stateRoot in @($plan.locks | ForEach-Object { [IO.Path]::GetDirectoryName($_.path) } | Select-Object -Unique)) {
        # 持锁后复查每个历史目录，堵住规划结束至执行开始之间新增更新尝试的窗口。
        $statePath = [IO.Path]::Combine($stateRoot, '.accountkeeper-update-state.json')
        if ([IO.File]::Exists($statePath)) {
            $state = [IO.File]::ReadAllText($statePath) | ConvertFrom-Json
            if (($stateRoot -eq $currentRoot -and $state.target -cne $plan.target) -or $state.active) {
                throw '更新状态改变，停止卸载'
            }
        }
    }
    if ([IO.File]::Exists([IO.Path]::Combine($plan.data, '.accountkeeper-data-update.json'))) {
        throw '更新预约仍存在，停止卸载'
    }
    $opened = @()
    foreach ($f in $plan.files) {
        # 先独占打开全部待删文件再动手；被SQLite或杀软占用时不会先删掉一半账本。
        $h = [AKDelete]::Open($f.path, [uint32]2147549184, 0)
        [void]$handles.Add($h); $opened += $h
        [AKDelete]::Match($h, $f.id, $f.size, $f.write, $false)
    }
    foreach ($h in $opened) { [AKDelete]::Delete($h); $h.Dispose() }
    foreach ($h in $locks) { [AKDelete]::Delete($h); $h.Dispose() }
    foreach ($d in @($plan.directories | Sort-Object { $_.path.Length } -Descending)) {
        if ($d.remove -and [IO.Directory]::GetFileSystemEntries($d.path).Length -eq 0) {
            [AKDelete]::Delete($dirs[$d.path]); $dirs[$d.path].Dispose()
        }
    }
    $text = if ($plan.erase) {'卸载完成。已清理白名单应用数据；未知文件及导出的 CSV/ZIP 已保留。'}
            else {"卸载完成。账本已保留在：`n" + $plan.data}
    Notify $text $false
    if (!$plan.notify) { [Console]::Out.WriteLine('OK') }
} catch {
    $text = "删除失败，请重启后手动删除。`n" + $_.Exception.Message
    if ($plan) { $text += "`n程序：" + $plan.target + "`n数据：" + $plan.data }
    try { Notify $text $true } finally { [Console]::Error.WriteLine($text) }
    exit 1
} finally {
    foreach ($h in $handles) { $h.Dispose() }
}
'''


def worker_start(plan):
    environment = dict(os.environ)
    for name, value in (("AK_UNINSTALL_PLAN", json.dumps(plan, ensure_ascii=False)),
                        ("AK_UNINSTALL_SCRIPT", _WORKER)):
        encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
        if len(encoded) > 30000:
            raise ValueError("卸载清单过长，未启动删除，请手动清理")
        environment[name] = encoded
    # 命令恒定，数据只经Base64环境变量解码；不调用shell、不拼接路径或用户文案。
    command = "& ([ScriptBlock]::Create([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:AK_UNINSTALL_SCRIPT))))"
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    return subprocess.Popen([str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
                            env=environment, cwd=os.environ["SystemRoot"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=subprocess.CREATE_NO_WINDOW)


class CleanupJob:
    def __init__(self, plan):
        from ctypes import wintypes as w
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateEventW.argtypes = [ctypes.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR]
        self.api.CreateEventW.restype = w.HANDLE
        self.api.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        self.api.WaitForSingleObject.restype = w.DWORD
        self.api.CloseHandle.argtypes = [w.HANDLE]
        name = "Local\\AccountKeeper.Uninstall." + secrets.token_hex(16)
        self.handle = self.api.CreateEventW(None, True, False, name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            self.process = worker_start(dict(plan, event=name))
            self.deadline = time.monotonic() + 20
        except Exception:
            self.api.CloseHandle(self.handle)  # 外部程序未启动也必须释放就绪事件。
            raise

    def ready(self):
        if self.process.poll() is not None:
            _, error = self.process.communicate()
            self.close()
            raise RuntimeError(error.decode("utf-8", errors="replace") or "卸载程序未能启动，未删除数据")
        result = self.api.WaitForSingleObject(self.handle, 0)
        if result == 0:
            self.close()
            return True
        if result != 258:
            raise ctypes.WinError(ctypes.get_last_error())
        if time.monotonic() >= self.deadline:
            self.abort()
            raise RuntimeError("卸载程序就绪超时，未删除数据")
        return False

    def abort(self):
        # UI接管失败必须撤销外部执行者，避免随后关闭应用反而触发迟到删除。
        if self.process.poll() is None:
            self.process.kill()
        self.process.communicate()
        self.close()

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def request_uninstall(app, data):
    from tkinter import messagebox, TclError, BooleanVar
    import customtkinter as ctk
    from dialog_common import center_dialog_on_parent, _apply_app_icon
    from dialog_lifecycle import ManagedToplevel
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        messagebox.showinfo("卸载", "源码运行不提供卸载，请使用打包后的绿色版。", parent=app)
        return
    flow = app.update_flow
    if (app._calendar_closing or app._business_depth or app._backup_running or app.grab_current()
            or flow is None or flow.closing or flow.blocked
            or flow.phase in ("install", "draining", "discard")):
        messagebox.showwarning("暂不能卸载", "请先结束当前业务、更新或恢复操作。", parent=app)
        return
    result = [None]
    dialog = ManagedToplevel(app, fg_color="#FFFFFF")
    dialog.title("卸载 AccountKeeper")
    dialog.geometry("600x450")
    dialog.attributes("-alpha", 0.0)  # 布局与定位完成后再显示，避免确认窗在默认位置闪现。
    dialog.transient(app)
    dialog.resizable(False, False)
    _apply_app_icon(dialog)  # 沿用资源根目录及冻结包兜底，两个确认状态共用真实应用图标。
    body = ctk.CTkFrame(dialog, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=24, pady=(16, 8))
    symbol = ctk.CTkLabel(body, text="✓", font=("Segoe UI Symbol", 34), text_color="#2F80ED")
    symbol.pack()
    title = ctk.CTkLabel(body, text="卸载软件，保留您的账本", font=("Microsoft YaHei UI", 18, "bold"), text_color="#243447")
    title.pack(pady=(3, 6))
    badge = ctk.CTkLabel(body, text="推荐 · 默认保留", height=24, corner_radius=6, fg_color="#E8F2FF",
                         text_color="#245D9F", font=("Microsoft YaHei UI", 11, "bold"))
    badge.pack()
    description = ctk.CTkLabel(body, text="", wraplength=520, height=56, corner_radius=8,
                               font=("Microsoft YaHei UI", 12), text_color="#475569")
    description.pack(fill="x", pady=(10, 8))
    card = ctk.CTkFrame(body, fg_color="#F3F7FC", border_width=1, border_color="#D8E5F3", corner_radius=8)
    card.pack(fill="x")
    path_title = ctk.CTkLabel(card, text="账本保留位置", font=("Microsoft YaHei UI", 12, "bold"), text_color="#243447")
    path_title.pack(anchor="w", padx=14, pady=(8, 0))
    path = ctk.CTkTextbox(card, height=64, wrap="char", border_width=0, fg_color="#F3F7FC",
                         text_color="#475569", font=("Microsoft YaHei UI", 12))
    path.pack(side="left", fill="x", expand=True, padx=(10, 0), pady=(0, 8))
    path.insert("1.0", str(data))
    path.configure(state="disabled")  # 长路径可滚动和选中，完整值始终保留，不用省略号隐去删除边界。

    def copy_path():
        try:
            dialog.clipboard_clear()
            dialog.clipboard_append(str(data))
            copy.configure(text="已复制")
        except TclError:
            messagebox.showwarning("复制失败", "无法写入剪贴板，请在路径框中选中并复制。", parent=dialog)  # 剪贴板失败可见反馈，不影响取消流程。

    copy = ctk.CTkButton(card, text="复制路径", command=copy_path, width=80, height=30, fg_color="#F3F7FC",
                         hover_color="#E0EBF8", text_color="#245D9F", font=("Microsoft YaHei UI", 11))
    copy.pack(side="right", padx=10, pady=8)
    protected = ctk.CTkLabel(body, text="✓ 您导出的 CSV / ZIP 文件始终保留", text_color="#475569", font=("Microsoft YaHei UI", 12))
    protected.pack(anchor="w", pady=(10, 0))
    understood = BooleanVar(dialog, False)
    ack = ctk.CTkCheckBox(body, text="我已理解，删除后无法恢复", variable=understood, height=26,
                         font=("Microsoft YaHei UI", 12), fg_color="#B84035", hover_color="#A3342B",
                         command=lambda: danger.configure(state="normal" if understood.get() else "disabled"))
    ctk.CTkFrame(dialog, height=1, fg_color="#E8EEF5").pack(fill="x")
    row = ctk.CTkFrame(dialog, fg_color="transparent")
    row.pack(fill="x", padx=18, pady=18)
    row.grid_columnconfigure((0, 1, 2), weight=1, uniform="uninstall-actions")

    def finish(value):
        if value is True and not understood.get():
            return  # 即使收到迟到的确认事件，也不能绕过明确勾选。
        result[0] = value
        dialog.destroy()

    def render(erasing):
        understood.set(False)  # 返回再进入危险状态必须重新勾选，不能继承上一次的删除授权。
        symbol.configure(text="⚠" if erasing else "✓", text_color="#B84035" if erasing else "#2F80ED")
        title.configure(text="永久删除账本？" if erasing else "卸载软件，保留您的账本", text_color="#B84035" if erasing else "#243447")
        badge.configure(text="不可恢复" if erasing else "推荐 · 默认保留", fg_color="#FFF1EE" if erasing else "#E8F2FF",
                        text_color="#B84035" if erasing else "#245D9F")
        description.configure(text="此操作不可逆，所有账本、配置和标签都会被永久删除，但您导出的 CSV/ZIP 文件不受影响。" if erasing
                              else "卸载后，账本、配置和标签仍保存在本机。", fg_color="#FFF1EE" if erasing else "transparent",
                              text_color="#B84035" if erasing else "#475569", font=("Microsoft YaHei UI", 12, "bold" if erasing else "normal"))
        path_title.configure(text="将清理的数据位置" if erasing else "账本保留位置")
        protected.pack_forget()
        ack.pack_forget()
        (ack if erasing else protected).pack(anchor="w", pady=(10, 0))
        keep.configure(text="返回上一步" if erasing else "保留并卸载", command=lambda: render(False) if erasing else finish(False),
                       fg_color="#FFFFFF" if erasing else "#2F80ED", text_color="#245D9F" if erasing else "#FFFFFF")
        danger.configure(text="永久删除并卸载" if erasing else "彻底删除…", command=lambda: finish(True) if erasing else render(True),
                         fg_color="#B84035" if erasing else "#FFFFFF", text_color="#FFFFFF" if erasing else "#B84035",
                         state="disabled" if erasing else "normal")
        # 依据实际请求高度适配字体/DPI；路径区独立滚动，超长路径不会把按钮推出窗口。
        dialog.update_idletasks()
        scale = ctk.ScalingTracker.get_widget_scaling(body)
        height = max(430, round((body.winfo_reqheight() + row.winfo_reqheight()) / scale) + 61)
        dialog.geometry(f"600x{height}")

    cancel = ctk.CTkButton(row, text="取消", command=lambda: finish(None), fg_color="#FFFFFF", hover_color="#E8EEF5",
                          border_width=1, border_color="#CCD7E3", text_color="#243447")
    keep = ctk.CTkButton(row, text="", border_width=1, border_color="#2F80ED", hover_color="#DCEAFE")
    danger = ctk.CTkButton(row, text="", border_width=1, border_color="#B84035", hover_color="#FFF1EE", text_color_disabled="#D0918A")
    for index, button in enumerate((cancel, keep, danger)):
        button.configure(width=1, height=40, corner_radius=8, font=("Microsoft YaHei UI", 12))
        button.grid(row=0, column=index, sticky="ew", padx=6)
    render(False)
    dialog.protocol("WM_DELETE_WINDOW", lambda: finish(None))
    dialog.bind("<Escape>", lambda _event: finish(None))
    dialog.bind("<Return>", lambda _event: "break")  # 回车不会触发不可逆操作，危险操作只接受明确点击。
    dialog.finish_setup()
    if dialog.closing or not center_dialog_on_parent(app, dialog):
        return
    dialog.grab_set()
    app.wait_window(dialog)
    if result[0] is not None:
        app._uninstall_choice = result[0]
        app.destroy()  # 既有UpdateFlow先取消下载、结算及排空，最终destroy才生成清单。
