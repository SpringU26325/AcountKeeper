"""更新弹窗与线程协调；事务、验签和替换只调用既有公开接口。"""
from functools import wraps
import math
import os
from pathlib import Path
import queue
import re
import secrets
import threading
import time
import tkinter as tk
from typing import Any

import customtkinter as ctk
from dialog_lifecycle import ManagedToplevel
import update_helper as helper
import updater


def business_action(method):
    @wraps(method)
    def guarded(app, *args, **kwargs):
        if app._calendar_closing:
            return  # 退出排空后不能从嵌套模态循环重新提交业务。
        app._business_depth += 1
        try:
            return method(app, *args, **kwargs)
        finally:
            app._business_depth -= 1  # 回调真正退栈才允许交接，不以弹窗消失代替。
    return guarded


def discard_candidate(target, offer, ready):
    # 与安装执行者互斥；已有本事务日志时材料归恢复入口，绝不由UI删除。
    with helper.FileLock(target, "install", 0):
        if ready is not None:
            work = Path(ready).parent
            journal = helper.journal_path(target)
            if journal.exists() and helper.load_journal(target)["transaction"] == work.name:
                return
            if (Path(ready).name != "ready.json" or work.parent != helper.transaction_root(target)
                    or not re.fullmatch("[0-9a-f]{32}", work.name) or work.resolve() != work
                    or helper.payload_path(work, target, "candidate.exe").parent.stat().st_dev != target.stat().st_dev):
                raise updater.UpdateError("候选清理路径归属不符")
            owner = work / "owner.json"
            expected = dict(schema=1, transaction_id=work.name, target=str(target))
            if helper.read_json(owner) != expected:
                raise updater.UpdateError("候选owner不符，保留现场")
            allowed = {"owner.json", "candidate.exe", "candidate.exe.part", "manifest.json",
                       "manifest.sig", "ready.json", "ready.json.tmp", "update_helper.exe"}
            entries = helper.transaction_entries(work, target)
            if any(p.name not in allowed or p.resolve() != p or not p.is_file()
                   or p.stat().st_nlink != 1 for p in entries):
                raise updater.UpdateError("候选目录存在非本事务文件，保留现场")
            for path in entries:
                if path != owner:
                    path.unlink()
            owner.unlink()  # 最后移除归属证据，清理失败时尽量保留可重试依据。
            helper.remove_transaction_dirs(work, target)
        if offer is not None:
            helper.settle_attempt(target, offer.attempt, "cancelled", "用户取消更新")


class UpdateFlow:
    def __init__(self, app, target, version, arch, data_dir):
        self.app, self.target = app, target
        self.version, self.arch, self.data_dir = version, arch, data_dir
        self.messages = queue.Queue()
        self.token = None
        self.thread = self.pending = None
        self.cancel_event = threading.Event()
        self.phase, self.manual = "idle", False
        self.offer = self.ready = self.window = None
        self.manifest = {}
        self.closing = self.exit_ready = self.blocked = self.building = False
        self.retry_at = 0
        self.cleanup_error = None
        self.start_job = None
        self.progress = self.detail = None
        self.poll_job = app.after(50, self._poll)
        app.protocol("WM_DELETE_WINDOW", app.destroy)

    def startup(self):
        if self.target is not None and not self.closing and self.start_job is None:
            self.start_job = self.app.after(2000, self._automatic)

    def _automatic(self):
        self.start_job = None
        self.check(False)

    def check(self, manual=True, *, retry=False):
        if self.closing or self.exit_ready or self.building:
            return
        if self.cleanup_error is not None:
            self._cleanup_failed(self.cleanup_error)
            return  # 清理未成功不能复用旧ready进入安装或开启新尝试。
        if manual and self.start_job is not None:
            self.app.after_cancel(self.start_job)
            self.start_job = None  # 手动检查已接管本会话意图，不再重复触发启动检查。
        if self.target is None:
            self._show("检查更新", "源码运行，不支持自动更新。", [("关闭", self._dismiss)])
            return
        if self.blocked:
            self._error(RuntimeError("安装交接未完成，请退出后重新启动以恢复"))
            return
        if not manual and (self.phase != "idle" or self.thread is not None):
            return  # 自动定时器不打断手动检查、确认或本会话就绪包。
        self.manual = manual
        if self.thread is not None:
            if self.window is not None:
                self.window.deiconify()
                self.window.lift()
        elif self.phase in ("install", "draining", "discard"):
            return  # 菜单重入不能把排空或交接阶段切回准备安装。
        elif self.ready is not None:
            self._ready_window()
        elif self.offer is not None:
            self._offer_window()
        elif time.time() < self.retry_at:
            self._error(updater.RateLimited(self.retry_at))
        else:
            self._start("check", retry)
            if manual:
                self._show("检查更新", "正在检查更新…", [("取消", self.cancel)])

    def _start(self, kind, retry=False):
        self.phase, self.pending = kind, None
        self.token = secrets.token_hex(16)  # 每个阶段也换token，拒绝前一次下载的迟到进度。
        self.cancel_event = threading.Event()
        token, cancel = self.token, self.cancel_event
        offer, ready, manual = self.offer, self.ready, self.manual
        def run():
            before = None
            try:
                if kind == "check":
                    # 状态包含计数、列表及可空尝试字典；本地标注不改变冻结接口的读取行为。
                    state: dict[str, Any] = helper.read_state(self.target)
                    before = state["active"]
                    if manual and state["paused"] and not retry:
                        value = ("paused", state["reasons"])
                    else:
                        candidate = updater.check_updates(self.version, self.arch, cancel,
                                                         target_exe=self.target, manual=manual)
                        value = ("offer", candidate, updater.verify_offer(candidate, self.version, self.arch)) if candidate else ("none",)
                elif kind == "download":
                    last = 0
                    def report(done, total):
                        nonlocal last
                        now = time.monotonic()
                        if now - last >= 0.1 or done == total:
                            self.messages.put((token, kind, "progress", (done, total)))
                            last = now  # 只节流进度；终态独立投递，不能被进度合并吞掉。
                    value = updater.download(offer, self.target, self.version, self.arch,
                                             cancel, report, manual=manual)
                elif kind == "discard":
                    value = discard_candidate(self.target, offer, ready)
                else:
                    journal = helper.prepare_install(ready, self.target, self.version, self.arch, self.data_dir)
                    helper.launch_helper(journal)  # 返回意味着已登记接管，不只是Popen成功。
                    value = None
                self.messages.put((token, kind, "done", value))
            except Exception as error:
                if kind in ("check", "download", "install"):
                    try:
                        state = helper.read_state(self.target)
                        active = state["active"]
                        attempt = offer.attempt if offer is not None else active if before is None else None
                        journal_path = helper.journal_path(self.target)
                        owned_journal = False
                        if kind == "install":
                            if ready is None:
                                raise updater.UpdateError("安装就绪路径缺失，保留事务现场")  # 缺失归属依据时不能继续结算。
                            owned_journal = (journal_path.exists()
                                             and helper.load_journal(self.target)["transaction"] == Path(ready).parent.name)
                        if (attempt and active and attempt["id"] == active["id"]
                                and active["owner"] == helper.process_identity(os.getpid())
                                and not owned_journal and (kind == "install" or not active["binding"])):
                            outcome = "cancelled" if isinstance(error, (updater.Cancelled, helper.Busy)) else "failed"
                            helper.settle_attempt(self.target, attempt, outcome, repr(error))
                    except Exception as settlement_error:
                        error = updater.UpdateError(f"{error}；尝试结算失败：{settlement_error}")
                self.messages.put((token, kind, "error", error))
        self.thread = threading.Thread(target=run, name="accountkeeper-update", daemon=False)
        try:
            self.thread.start()
        except RuntimeError as error:
            self.pending = (kind, False, error)  # 未能启动也进入正常错误通道，不留永久忙态。

    def _poll(self):
        self.poll_job = None
        for _ in range(64):
            try:
                token, kind, event, value = self.messages.get_nowait()
            except queue.Empty:
                break
            if token != self.token or kind != self.phase or self.exit_ready:
                continue  # 校验活跃token与阶段，旧异常也不得覆盖新弹窗。
            if event == "progress":
                if (not self.building and not self.closing and not self.cancel_event.is_set() and self.window is not None
                        and not self.window.closing and self.progress is not None and self.detail is not None):
                    # 建窗或销毁期间缺少文字控件时跳过进度，等待完整UI接收下一条消息。
                    done, total = value
                    self.progress.set(done / total)
                    self.detail.configure(text=f"{done / 1048576:.1f} / {total / 1048576:.1f} MB · {done / total:.0%}")
            else:
                self.pending = (kind, event == "done", value)
        if (not self.building and self.thread is not None and not self.thread.is_alive()
                and self.pending is not None):
            result, self.pending, finished = self.pending, None, self.thread
            self._finish(*result)  # 消息投递后仍须等线程真的结束，避免退出时尚有文件占用。
            if self.thread is finished:
                self.thread = None  # UI终态也完成后才对嵌套事件公布空闲；续接清理线程则保留。
        if self.exit_ready:
            return
        try:
            if self.thread is None and not self.building:
                if self.closing:
                    if self.offer is not None or self.ready is not None:
                        self._start("discard")
                    elif self.app.prepare_update_exit():
                        self._exit()
                        return
                elif self.phase == "draining" and self.app.prepare_update_exit():
                    self._start("install")
                    self._show("准备安装", "正在等待更新助手接管…", [])
        except Exception as error:
            self.closing, self.blocked = False, True
            self._error(error)  # 收尾失败必须可见，不能误报已退出或继续安装。
        self.poll_job = self.app.after(50, self._poll)

    def _finish(self, kind, ok, value):
        self.phase = "idle"
        if ok and kind == "check" and value[0] == "offer":
            _, self.offer, self.manifest = value
        elif ok and kind == "download":
            self.ready = value
        elif ok and kind == "discard":
            self.offer = self.ready = None
            self.cleanup_error = None
        elif ok and kind == "install":
            self.offer = self.ready = None
            self._exit()
            return
        if ok and kind in ("check", "download") and self.cancel_event.is_set() and not self.closing:
            self._start("discard")  # 取消与成功返回竞态时，Offer/ready仍必须结算和回收。
            self._show("取消更新", "正在结束本次更新…", [])
            return
        if not ok:
            if kind == "download" and self.thread is not None and self.thread.ident is not None:
                # 线程尚未建立时不假定后台已经完成结算，保留现有候选处理语义。
                self.offer = None  # 后台已结算；错误弹窗建窗重入时也不能复用旧Offer。
            if kind == "install":
                self.blocked = True  # 接管超时可能仍有helper存活，禁止重试和恢复业务写入。
            if kind == "discard":
                self.closing = False
                self._cleanup_failed(value)
            elif not self.closing and not isinstance(value, updater.Cancelled):
                if kind != "check" or self.manual:
                    self._error(value, kind)
            else:
                self._dismiss()
            return
        if self.closing:
            return  # 成功与取消竞态时先接住Offer/ready，下一轮统一清理并结算。
        if self.ready is not None:
            self._ready_window()
        elif self.offer is not None:
            self._offer_window()
        elif kind == "check" and value[0] == "paused":
            reasons = value[1]
            reason = str(reasons[-1]["reason"])[:130] if reasons else "状态待核实"
            self._show("自动检查已暂停", f"连续失败达到3次。\n最近原因：{reason}",
                       [("关闭", self._dismiss), ("仅重试本次", lambda: self.check(retry=True))])
        elif kind == "check" and self.manual:
            self._show("检查更新", f"当前已是最新版本 (v{self.version})，无需更新。", [("关闭", self._dismiss)])
            if self.detail is not None:
                self.detail.configure(text_color="#2F80ED")  # 无更新属于普通信息，使用蓝色避免与失败警示混淆。
        else:
            self._dismiss()

    def _offer_window(self):
        if self.offer is None:
            raise updater.UpdateError("更新候选缺失，无法显示更新信息")  # 不能用虚构版本展示不完整候选。
        self._show("发现更新", f"发现新版本 v{self.offer.version}\n当前版本 v{self.version}\n更新包大小 {self.manifest['size'] / 1048576:.1f} MB",
                   [("暂不更新", self.decline), ("立即下载", self.download)])
        self.phase = "offer"  # CTk建窗会重入事件循环；控件齐备后才发布可操作阶段。

    def download(self):
        if self.phase != "offer" or self.thread is not None or self.closing:
            return
        if self.offer is None:
            raise updater.UpdateError("更新候选缺失，无法开始下载")  # 阶段标记不能替代候选本身的存在性。
        self._start("download")
        self._show("下载更新", f"正在下载新版本 v{self.offer.version}", [("取消", self.cancel)], progress=True)

    def decline(self):
        if self.thread is None and not self.closing:
            self._start("discard")
            self._show("取消更新", "正在结束本次更新…", [])

    def cancel(self):
        if self.phase not in ("check", "download") or self.thread is None:
            return
        self.cancel_event.set()
        if self.detail is not None:
            self.detail.configure(text="正在取消，请稍候…")  # 等待后台清理，不强杀线程。
        for button in self.buttons:
            button.configure(state="disabled")

    def _ready_window(self):
        self._show("准备安装", "下载校验完成，是否立即重启并更新？\n重启后生效。",
                   [("暂不重启", self._dismiss), ("立即重启", self.install)])
        self.phase = "ready"  # 不能让嵌套回调把半张表单视为安装确认已就绪。

    def install(self):
        if self.phase != "ready" or self.thread is not None or self.closing:
            return
        self.phase = "draining"
        self._show("准备安装", "正在结束后台任务，请稍候…", [])

    def _error(self, error, kind=None):
        if isinstance(error, updater.RateLimited):
            try:
                remaining = float(error.retry_at) - time.time()
                if not math.isfinite(remaining):
                    raise ValueError("限流时间不是有限数")
                seconds = math.ceil(min(86400, max(0, remaining)))
            except (TypeError, ValueError, OverflowError, OSError):  # 展示异常不能炸穿UI事件循环。
                seconds = 60
            self.retry_at = time.time() + seconds
            text = f"更新源限流，请在{seconds}秒后重试。"  # 展示有界时长，避开Windows日历时间转换范围。
        else:
            reason = "网络连接超时" if isinstance(error, TimeoutError) else str(error)[:130]
            text = ("安装交接失败：" if self.blocked else "下载失败：" if kind == "download" else "更新失败：") + reason
        actions = [("退出应用", self.close)] if self.blocked else [("关闭", self._dismiss), ("重试", self.check)]
        self._show("更新失败", text, actions, error=True)

    def _cleanup_failed(self, error):
        self.cleanup_error = error
        self._show("清理失败", str(error)[:130], [("重试清理并退出", self.close)], error=True)

    def _dismiss(self):
        if self.window is not None:
            window, self.window = self.window, None
            window.destroy()
        self.progress = self.detail = None

    def _window_close(self):
        if self.phase == "offer":
            self.decline()
        elif self.phase == "download":
            if self.window is not None:
                self.window.withdraw()  # 迟到关闭回调遇到空窗口时跳过隐藏，后台任务和token保持有效。
        elif self.phase == "check":
            self.cancel()
        elif self.phase not in ("install", "draining", "discard"):
            self.close() if self.blocked else self._dismiss()

    def _show(self, title, text, actions, *, progress=False, error=False):
        from dialogs import center_dialog_on_parent
        from config import RESOURCE_DIR
        self.building = True
        try:
            self._dismiss()
            window = self.window = ManagedToplevel(self.app, fg_color="#F0F4F8")
            try:
                window.iconbitmap(str(RESOURCE_DIR / "image" / "app_icon.ico"))
            except Exception as icon_error:
                print(f"设置更新窗口图标失败：{icon_error}")  # 图标失败仅诊断，不阻断检查/下载及取消操作。
            window.attributes("-alpha", 0.0)
            window.title("AccountKeeper 更新")
            window.geometry("460x280" if progress else "460x250")
            tk.Wm.resizable(window, False, False)
            window.transient(self.app)
            content = ctk.CTkFrame(window, fg_color="transparent")
            content.pack(fill="both", expand=True, padx=24, pady=20)
            # 与现有编辑/删除弹窗共用字体、底色、按钮高度和9px圆角。
            ctk.CTkLabel(content, text=("⚠ " if error else "") + title,
                         font=("Microsoft YaHei UI", 13, "bold"), anchor="w",
                         text_color="#C62828" if error else "#243447").pack(fill="x")
            self.detail = ctk.CTkLabel(content, text=text, wraplength=408, justify="left", anchor="w",
                                       font=("Microsoft YaHei UI", 11), text_color="#C62828" if error else "#243447")
            self.detail.pack(fill="x", pady=(10, 8))
            if progress:
                self.progress = ctk.CTkProgressBar(content, progress_color="#2F80ED", height=10)
                self.progress.set(0)
                self.progress.pack(fill="x", pady=8)
                ctk.CTkLabel(content, text="点击右上角 X 可后台下载", text_color="#607D8B",
                             font=("Microsoft YaHei UI", 11)).pack(anchor="w")
            row = ctk.CTkFrame(content, fg_color="transparent")
            row.pack(side="bottom", fill="x", pady=(12, 0))
            self.buttons = []
            token = self.token
            def invoke(callback):
                if self.window is window and token == self.token and not window.closing:
                    callback()  # 建窗中的重入与旧按钮事件也必须校验窗口和任务身份。
            for i, (label, callback) in enumerate(actions):
                row.grid_columnconfigure(i, weight=1)
                primary = len(actions) > 1 and i == len(actions) - 1
                button = ctk.CTkButton(row, text=label, command=lambda cb=callback: invoke(cb), height=34,
                                       corner_radius=9, font=("Microsoft YaHei UI", 11),
                                       fg_color="#2F80ED" if primary else "#90A4AE",
                                       hover_color="#256AC4" if primary else "#78909C")
                button.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 6 if i < len(actions) - 1 else 0))
                self.buttons.append(button)
            window.protocol("WM_DELETE_WINDOW", lambda: invoke(self._window_close))
            window.bind("<Escape>", lambda _event: invoke(self._window_close))
            window.finish_setup()
            if self.closing or self.exit_ready:
                self._dismiss()
            elif not window.closing:
                center_dialog_on_parent(self.app, window)
        finally:
            self.building = False  # CTk构造会处理事件，退出协调不能在半张表单时销毁根窗。

    def close(self):
        if self.exit_ready:
            return
        self.closing = True
        if self.start_job is not None:
            self.app.after_cancel(self.start_job)
            self.start_job = None
        if self.phase in ("check", "download"):
            self.cancel_event.set()

    def _exit(self):
        self.exit_ready = True
        if self.start_job is not None:
            self.app.after_cancel(self.start_job)
            self.start_job = None
        self._dismiss()
        self.app.destroy()  # 主线程正常销毁，既有根窗生命周期负责取消其余所属任务。
