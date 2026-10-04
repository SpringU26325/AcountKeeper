"""项目弹窗的构建保护及所属 Tcl 定时任务清理。"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk


def _cancel_owned_tasks(window: tk.Misc) -> None:
    """只取消窗口控件树注册的 Python after 命令，不影响其他窗口。"""
    owners: dict[str, tk.Misc] = {}
    pending = [window]
    while pending:
        widget = pending.pop()
        # 使用 Python 控件树：Destroy 事件期间，部分子窗口已无法通过 winfo 查询。
        pending.extend(widget.children.values())
        for command in widget._tclCommands or ():
            owners[command] = widget
    for timer in window.tk.splitlist(window.tk.call("after", "info")):
        script, _kind = window.tk.call("after", "info", timer)
        command = window.tk.splitlist(script)[0]
        owner = owners.get(command)
        if owner is not None:
            # 必须由注册命令的控件取消，才能同步摘掉其 _tclCommands，避免重复删除。
            tk.Misc.after_cancel(owner, timer)


class ManagedToplevel(ctk.CTkToplevel):
    """创建者完成布局后调用 finish_setup，再进入 wait_window。"""

    def __init__(self, master: tk.Misc, **kwargs) -> None:
        self._setup_complete = False
        self._close_requested = False
        self._destroying = False
        self._destroyed = False
        self._building_children: set[ManagedToplevel] = set()
        parent = master.winfo_toplevel()
        self._building_parent = parent if isinstance(parent, ManagedToplevel) else None
        if self._building_parent is not None:
            # CTk 构造会 update；父窗必须在进入构造前知道此时不能真正销毁。
            self._building_parent._building_children.add(self)
        try:
            super().__init__(master, **kwargs)
        except BaseException:
            # 初始化失败也要释放父窗的构建状态，不能永久阻止其后续关闭。
            self._release_parent()
            raise
        self.bind("<Destroy>", self._on_destroy, add="+")

    @property
    def closing(self) -> bool:
        return self._close_requested or self._destroying or self._destroyed

    def after(self, ms, func=None, *args):
        if func is None:
            return super().after(ms)
        target = getattr(func, "__self__", None)

        def call_when_alive():
            if isinstance(target, tk.Misc):
                # CTk 焦点恢复可能由另一个仍存活的窗持有任务；保留任务但检查目标。
                try:
                    if not target.winfo_exists():
                        return
                except tk.TclError:
                    return  # 解释器关闭时只跳过存活查询，不吞掉业务回调中的异常。
                if isinstance(target, ManagedToplevel) and target.closing:
                    return
            return func(*args)

        return super().after(ms, call_when_alive)

    def _release_parent(self) -> None:
        parent, self._building_parent = self._building_parent, None
        if parent is not None:
            parent._building_children.discard(self)
            if parent._close_requested:
                parent.destroy()

    def finish_setup(self) -> None:
        """退出构建临界段，兑现期间收到的关闭请求，不靠固定延时猜就绪。"""
        self._setup_complete = True
        self._release_parent()
        if self._close_requested:
            self.destroy()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            # 原生 destroy 绕过 Python 入口时仍清理任务；只处理本窗的 Destroy。
            _cancel_owned_tasks(self)
            self._destroyed = True
            if not self._destroying:
                # 原生 Tk 销毁只移除窗口；补做 Python 控件树、外观及缩放订阅的收尾。
                self._destroying = True
                try:
                    super().destroy()
                finally:
                    self._destroying = False
            self._release_parent()

    def destroy(self) -> None:
        if self._destroying or self._destroyed:
            return  # 重复关闭及 Destroy 回调重入不再重复删除 Tcl 命令。
        self._close_requested = True
        if not self._setup_complete or self._building_children:
            return  # 保留关闭意图；创建者完成初始化时由 finish_setup 兑现。
        self._destroying = True
        try:
            _cancel_owned_tasks(self)
            super().destroy()
        finally:
            self._destroyed = True
            self._destroying = False
            self._release_parent()
