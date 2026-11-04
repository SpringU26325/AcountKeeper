"""启动后的更新残留确认；Tk只消费结果，磁盘和进程操作全部交后台。"""
import queue
import threading
import time
import tkinter as tk

import customtkinter as ctk
from dialog_lifecycle import ManagedToplevel
import update_cleanup as cleanup


class CleanupUI:
    def __init__(self, app, target):
        self.app, self.target = app, target
        self.stop = threading.Event()
        self.messages = queue.Queue()
        self.pending = []
        self.window = self.job = None
        self.running = False
        self.closed = False
        self.binding = app.bind("<Destroy>", self._destroyed, add="+")
        self._launch("scan")
        self.job = app.after(100, self._poll)

    def _launch(self, action, candidate=None):
        if self.stop.is_set():
            return
        self.running = True
        def run():
            result = []
            try:
                if action == "scan":
                    result = cleanup.scan(self.target, self.stop)
                elif action == "delete":
                    cleanup.erase(self.target, candidate, self.stop, time.monotonic() + 2)
                elif action == "cancel":
                    with cleanup.locks(self.target):
                        cleanup.suppression(self.target, candidate, cancel=True)
            except Exception as error:
                cleanup.record(self.target, "startup_cleanup", "kept", repr(error))
            finally:
                self.messages.put(result)  # 后台不调用after/winfo，退出中的解释器不会被工作线程触碰。
        threading.Thread(target=run, daemon=True).start()

    def _destroyed(self, event):
        if event.widget is self.app:
            self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stop.set()  # 每个删除动作前检查此标记，关闭后不再开始迟到删除。
        self.pending.clear()
        try:
            if self.job is not None:
                self.app.after_cancel(self.job)
            if self.window is not None and self.window.winfo_exists():
                self.window.destroy()
            self.app.unbind("<Destroy>", self.binding)
        except tk.TclError:
            pass  # 父窗原生销毁时资源已不可访问；只结束本控制器。
        self.job = self.window = None

    def _poll(self):
        self.job = None
        try:
            flow = getattr(self.app, "update_flow", None)
            if self.closed or getattr(self.app, "_calendar_closing", False) or (flow and flow.closing):
                self.close()
                return
            try:
                self.pending.extend(self.messages.get_nowait())
                self.running = False
            except queue.Empty:
                pass
            # 更新弹窗、业务模态或主窗隐藏时不叠加确认；重查仍在后台执行。
            busy = (flow and (flow.phase != "idle" or flow.window is not None or flow.blocked))
            if (self.pending and not self.running and self.window is None and not busy
                    and self.app.winfo_viewable() and not self.app.grab_current()):
                self._show(self.pending.pop(0))
            if not self.closed and (self.running or self.pending or self.window is not None):
                self.job = self.app.after(100, self._poll)
        except Exception:
            self.close()  # 可选清扫UI失败只关闭自己，不向应用回调链抛出异常。

    def _show(self, candidate):
        window = self.window = ManagedToplevel(self.app, fg_color="#FFFFFF")
        window.title("清理更新残留")
        window.geometry("560x300")
        window.transient(self.app)
        # 不grab、不wait_window：主窗仍可记账，确认仅授权当前快照的候选。
        ctk.CTkLabel(window, text="发现可清理的更新残留", font=("Microsoft YaHei UI", 17, "bold")).pack(pady=(18, 8))
        ctk.CTkLabel(window, text="仅删除此处显示的更新残留，不涉及您的账本。",
                     font=("Microsoft YaHei UI", 12)).pack()
        box = ctk.CTkTextbox(window, height=100, wrap="char")
        box.pack(fill="both", expand=True, padx=20, pady=12)
        names = "update_helper.exe" if candidate["files"] else "空目录"
        box.insert("1.0", candidate["path"] + "\n\n内容：" + names)
        box.configure(state="disabled")  # 完整路径可选择、滚动，不能用省略号隐藏删除边界。
        row = ctk.CTkFrame(window, fg_color="transparent")
        row.pack(pady=(0, 16))
        decided = False

        def decide(action):
            nonlocal decided
            if decided or self.closed:
                return
            decided = True
            window.destroy()
            self.window = None
            self._launch(action, candidate)

        ctk.CTkButton(row, text="暂不清理（7天）", command=lambda: decide("cancel")).pack(side="left", padx=8)
        ctk.CTkButton(row, text="清理", command=lambda: decide("delete")).pack(side="left", padx=8)
        window.protocol("WM_DELETE_WINDOW", lambda: decide("cancel"))
        window.bind("<Escape>", lambda _event: decide("cancel"))
        window.bind("<Return>", lambda _event: "break")  # 回车不隐式授权删除。
        window.finish_setup()


def start(app, target):
    try:
        if getattr(app, "_update_cleanup", None) is None:
            app._update_cleanup = CleanupUI(app, target)
    except Exception:
        pass  # 导入/线程或Tk初始化失败也不影响既有启动与更新流程。
