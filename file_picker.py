"""文件操作菜单：共用三角入口与定位，隐藏复用三项操作列表。"""

from collections.abc import Callable
import ctypes
from ctypes import wintypes
import tkinter as tk

import customtkinter as ctk

from dialog_lifecycle import ManagedToplevel, _cancel_owned_tasks
from popup_common import anchored_position, make_toggle_button

# 原生窗口及GDI句柄按指针宽度声明，避免64位环境被ctypes默认int截断。
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetAncestor.restype = wintypes.HWND
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
_user32.SetWindowRgn.restype = ctypes.c_int
_gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
_gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteObject.restype = wintypes.BOOL


class _FileWindow(ManagedToplevel):
    _rounded_target: tuple[int, int, int, int] | None = None

    def round_corners(self, event: tk.Event | None = None) -> None:
        if self.closing or (event is not None and event.widget is not self):
            return  # 子控件重排和退出中的Configure不重复裁剪外窗。
        hwnd = _user32.GetAncestor(self.winfo_id(), 2)  # GA_ROOT：裁剪Tk客户端之外的原生外窗。
        rect = wintypes.RECT()
        if not hwnd or not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        width, height = rect.right - rect.left, rect.bottom - rect.top
        radius = max(1, round(8 * ctk.ScalingTracker.get_window_scaling(self)))
        target = (hwnd, width, height, radius)
        if target == self._rounded_target:
            return  # 移动、同尺寸复用不新建区域，避免每次开菜单额外重绘。
        region = _gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, radius * 2, radius * 2)
        if not region:
            raise ctypes.WinError(ctypes.get_last_error())
        previous, self._rounded_target = self._rounded_target, target
        try:
            # 先登记形状，SetWindowRgn引起的几何事件重入不会再次创建同一区域。
            if not _user32.SetWindowRgn(hwnd, region, True):
                self._rounded_target = previous
                raise ctypes.WinError(ctypes.get_last_error())
            region = None  # 成功后归系统所有，替换或销毁时由系统释放，不能再DeleteObject。
        finally:
            if region is not None:
                _gdi32.DeleteObject(region)  # 只回收未移交的失败区域，避免泄漏或重复释放。

    def _windows_set_titlebar_color(self, color_mode: str) -> None:
        # 无标题栏菜单无需DWM重绘，避免CTk内部update及延迟焦点恢复打断开合。
        return

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self and isinstance(self.master, FileMenu):
            self.master.close()  # 原生销毁绕过菜单入口时也要先恢复三角、回收根窗绑定。
        super()._on_destroy(event)


class FileMenu(ctk.CTkFrame):
    def __init__(self, master: ctk.CTkBaseClass, actions: tuple[str, ...],
                 command: Callable[[str], None]) -> None:
        super().__init__(master, width=92, height=32, corner_radius=8, fg_color="#E3EAF2")
        self._actions, self._command = actions, command
        self._popup: _FileWindow | None = None
        self._open = False
        self._bindings: list[tuple[tk.Misc, str, str, str, str]] = []
        self._target: tuple[int, int, int, int] | None = None
        # 固定92×32入口，文字和32px三角都可点击，工具栏布局不随选择项变宽。
        self.grid_propagate(False)
        self.columnconfigure(0, weight=1)
        self.label_button = ctk.CTkButton(
            self, text="文件", command=self.toggle, width=1, height=30, corner_radius=7,
            fg_color="#E3EAF2", hover_color="#D2DEE9", text_color="#243447",
            font=("Microsoft YaHei UI", 11),
        )
        self.label_button.grid(row=0, column=0, sticky="ew", padx=(1, 0), pady=1)
        self.toggle_button = make_toggle_button(self, self.toggle, height=30, fg_color="#E3EAF2")
        self.toggle_button.grid(row=0, column=1, padx=(0, 1), pady=1)
        self.bind("<Destroy>", self._on_destroy, add=True)  # CTkFrame用布尔追加绑定，匹配参数类型并保留内部回调。

    def _ensure_window(self) -> _FileWindow:
        if self._popup is not None and self._popup.winfo_exists():
            return self._popup
        window = _FileWindow(self)
        self._popup = window  # 构造后立即登记，父窗关闭路径也能清理半成品。
        window.withdraw()
        window.overrideredirect(True)
        window.transient(self.winfo_toplevel())
        window.configure(fg_color="#F0F4F8")
        window.resizable(False, False)
        window.title("文件操作")
        window.bind("<Configure>", window.round_corners, add="+")
        # 三项32px、两道4px行距、上下各10px，菜单逻辑高124px。
        card = ctk.CTkFrame(window, corner_radius=8, fg_color="#FFFFFF")
        card.pack(fill="both", expand=True, padx=10, pady=10)
        for index, action in enumerate(self._actions):
            # 首/末行盖住卡片上下边缘：外侧角露出灰底，内侧角仍白，避免把卡片画回方形。
            top = "#F0F4F8" if index == 0 else "#FFFFFF"
            bottom = "#F0F4F8" if index == len(self._actions) - 1 else "#FFFFFF"
            corner_colors = (top, top, bottom, bottom)
            ctk.CTkButton(
                card, text=action, height=32, width=1,
                corner_radius=8 if index in (0, len(self._actions) - 1) else 6, anchor="w",
                background_corner_colors=corner_colors,  # type: ignore[reportArgumentType] # 库注解误写为单元素tuple，实际绘制依次读取四角。
                font=("Microsoft YaHei UI", 11), fg_color="#FFFFFF",
                hover_color="#D2DEE9", text_color="#455A64",
                command=lambda chosen=action: self._choose(chosen),
            ).pack(fill="x", pady=(0, 4 if index < len(self._actions) - 1 else 0))
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.finish_setup()
        return window

    def _listen(self, owner: tk.Misc, tag: str, sequence: str,
                callback: Callable) -> None:
        before = owner.tk.call("bind", tag, sequence)
        # 用原生绑定入口按owner注册，关闭时才能同时摘脚本与对应Tcl命令。
        funcid = (tk.Misc.bind_all(owner, sequence, callback, add="+") if tag == "all"
                  else tk.Misc.bind(owner, sequence, callback, add="+"))
        if funcid is None:
            raise RuntimeError("文件菜单事件绑定失败")
        after = owner.tk.call("bind", tag, sequence)
        added = after[len(before):]
        # 事件分发可能已经抓取旧脚本；命令删除后旧脚本必须跳过，防止Tcl异步报错。
        guarded = f"\nif {{[llength [info commands {funcid}]]}} {{\n{added}\n}}\n"
        owner.tk.call("bind", tag, sequence, before + guarded)
        self._bindings.append((owner, tag, sequence, funcid, guarded))

    def toggle(self) -> None:
        if self._open:
            self.close()
            return
        window = self._ensure_window()
        if window.closing or not self.winfo_exists():
            return  # 构建中的父窗关闭请求已兑现，不能重新显示被销毁的菜单。
        self._open = True
        self._target = None
        self.toggle_button.configure(text="▲")
        root = self.winfo_toplevel()
        try:
            # 只在开着时监听，不grab；排除整个入口，避免同一次点击关后又开。
            self._listen(root, "all", "<Button-1>", self._outside_click)
            self._listen(root, "all", "<Escape>", self._escape)
            self._listen(root, str(root), "<Configure>", self._reanchor)
            self._listen(root, str(root), "<Unmap>", self._parent_unmap)
            self._listen(self, str(self), "<Configure>", self._reanchor)
            self._reanchor()
            window.update_idletasks()  # 隐藏时先完成尺寸布局，不把初始200px拿去裁剪。
            if not self._open or window.closing:
                return  # 布局idle期间已关闭时不能继续显现旧窗口。
            window.round_corners()  # 首次显现之前已裁去四角，防止闪出方形底板。
            window.deiconify()
            window.lift()
        except BaseException:
            self.close()  # 打开失败也恢复箭头及绑定，不留下半开状态或覆盖业务异常。
            raise

    def _reanchor(self, _event: tk.Event | None = None) -> None:
        window = self._popup
        if not self._open or window is None:
            return
        scale = ctk.ScalingTracker.get_window_scaling(window)
        width, height = round(180 * scale), round(124 * scale)
        # winfo坐标与wm_geometry都是物理像素，只把逻辑宽高缩放一次。
        x, y = anchored_position(
            (self.winfo_rootx(), self.winfo_rooty(), self.winfo_height()),
            (width, height), (window.winfo_screenwidth(), window.winfo_screenheight()),
        )
        target = (width, height, x, y)
        if target != self._target:
            self._target = target  # Configure可再次进入，只提交变化的几何避免无限重排。
            # CTk换缩放会暂时锁定旧尺寸；原生边界按本次物理尺寸更新，避免124px被截成123px。
            tk.Wm.minsize(window, width, height)
            tk.Wm.maxsize(window, width, height)
            window.wm_geometry(f"{width}x{height}+{x}+{y}")

    @staticmethod
    def _contains(widget: tk.Misc, ancestor: tk.Misc) -> bool:
        node: tk.Misc | None = widget
        while node is not None:
            if node is ancestor:
                return True
            node = node.master
        return False

    def _outside_click(self, event: tk.Event) -> None:
        if self._open and not self._contains(event.widget, self) and (
                self._popup is None or not self._contains(event.widget, self._popup)):
            self.close()  # 不返回break，外点继续作用于用户实际点击的控件。

    def _escape(self, _event: tk.Event) -> str | None:
        if self._open:
            self.close()
            return "break"  # ESC只取消当前文件选择，不继续触发同次事件的后续all绑定。
        return None

    def _parent_unmap(self, event: tk.Event) -> None:
        if event.widget is self.winfo_toplevel():
            self.close()  # 主窗最小化或隐藏时菜单不能独自留在屏幕上。

    def _choose(self, action: str) -> None:
        if not self._open:
            return  # 关闭后迟到的按钮事件不能重复执行导出或备份。
        self.close()
        self._command(action)  # 回调可能打开模态对话框，必须在隐藏和解绑之后调用。

    def close(self) -> None:
        self._open = False  # 先设身份守卫，清理中的Configure/点击不再续开旧菜单。
        _cancel_owned_tasks(self)  # 点击动画及缓存窗的CTk延期任务归本入口，隐藏后不续画旧会话。
        for owner, tag, sequence, funcid, script in self._bindings:
            try:
                current = owner.tk.call("bind", tag, sequence)
                owner.tk.call("bind", tag, sequence, current.replace(script, ""))
                owner.deletecommand(funcid)  # 只摘本菜单的脚本和命令，不unbind_all破坏CTk。
            except tk.TclError:
                pass  # 原生父窗销毁可能先回收命令，仍需继续清理其他绑定。
        self._bindings.clear()
        try:
            self.toggle_button.configure(text="▼")
            if self._popup is not None and self._popup.winfo_exists():
                self._popup.withdraw()  # 关闭只隐藏，下一次复用同一控件树。
        except tk.TclError:
            pass  # 外部Destroy已回收子控件时不再设置死按钮或隐藏死窗口。

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            self.close()  # 原生父窗连带销毁也须摘掉根窗持有的外点/ESC监听。

    def destroy(self) -> None:
        self.close()
        if self._popup is not None:
            self._popup.destroy()  # 退出才真正销毁缓存，沿用ManagedToplevel所属任务清理。
            self._popup = None
        super().destroy()
