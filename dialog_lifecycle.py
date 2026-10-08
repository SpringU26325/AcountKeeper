"""CTkToplevel 的构建保护及所属 Tcl 定时任务清理（#63）。

构建期间仅登记销毁意图，创建者须在控件/绑定就绪后调用 finish_setup 才能兑现关闭。
本层管理窗口寿命，不管理选择结果、会话绑定或隐藏复用；这些仍由各弹窗控制器负责。
任务归属按 Python 控件的 Tcl 命令登记判断，不按回调内容猜测；注册在根窗的任务须另行收尾。
原生 Tk tooltip 不继承本类，仍由 widgets 的控制器持句柄、解绑并销毁。
"""

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
    # after info 列出整个解释器的任务；只取消首个命令属于此控件树的项，不能全局扫掉其他弹窗。
    for timer in window.tk.splitlist(window.tk.call("after", "info")):
        script, _kind = window.tk.call("after", "info", timer)
        command = window.tk.splitlist(script)[0]
        owner = owners.get(command)
        if owner is not None:
            # 必须由注册命令的控件取消，才能同步摘掉其 _tclCommands，避免重复删除。
            tk.Misc.after_cancel(owner, timer)


class ManagedToplevel(ctk.CTkToplevel):
    """构建中延迟销毁的 CTk 窗口；完成设置后才可进入 wait_window 或显示/隐藏复用。"""

    def __init__(self, master: tk.Misc, **kwargs) -> None:
        self._setup_complete = False  # 不等同于已映射：布局完但隐藏的缓存窗也必须完成设置。
        self._close_requested = False  # 保留构建中收到的销毁意图，不能靠延时猜什么时候可退出。
        self._destroying = False  # 防御原生 Destroy 回调重入 Python destroy。
        self._destroyed = False  # 真正退出后永久封住重复销毁；普通 withdraw 不设置它。
        self._building_children: set[ManagedToplevel] = set()  # 任一受管理子窗未完成构建时，父窗也要等待。
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
        """包含尚未兑现的销毁请求；调用方据此阻止重排、显现或创建新会话。"""
        return self._close_requested or self._destroying or self._destroyed

    def after(self, ms, func=None, *args):
        # func=None 保留 Tk 等待语义；有回调时只保护可识别的绑定控件目标，不代替会话身份校验。
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
        # 先移走父引用再兑现其关闭，避免父子 destroy 相互重入时重复注销构建登记。
        parent, self._building_parent = self._building_parent, None
        if parent is not None:
            parent._building_children.discard(self)
            if parent._close_requested:
                parent.destroy()

    def finish_setup(self) -> None:
        """退出构建临界段，兑现期间收到的关闭请求，不靠固定延时猜就绪。"""
        self._setup_complete = True  # 这是创建者交还构建保护的入口，调用后窗口可能立即被销毁。
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
