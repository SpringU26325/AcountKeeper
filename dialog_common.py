"""弹窗共用的原生矩形、工作区、居中与图标处理，不持有编辑会话。"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import tkinter as tk

from config import RESOURCE_DIR
from dialog_lifecycle import ManagedToplevel


def _native_window_rect(window: tk.Misc) -> tuple[int, wintypes.RECT]:
    """取得包含标题栏和边框的桌面绝对矩形。"""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    # winfo_id 是客户区句柄；64 位返回类型必须显式声明，取 GA_ROOT 包装窗才能连标题栏居中。
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    hwnd = user32.GetAncestor(window.winfo_id(), 2)
    rect = wintypes.RECT()
    if not hwnd or not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())
    if rect.right <= rect.left or rect.bottom <= rect.top:
        raise OSError("窗口矩形尚未就绪")
    return hwnd, rect


def _parent_work_area(parent: tk.Misc) -> tuple[wintypes.RECT, wintypes.RECT, bool]:
    """选父窗占据面积最大的屏幕，并判定是否必须退回工作区居中。"""
    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("screen", wintypes.RECT),
                    ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    hwnd, parent_rect = _native_window_rect(parent)
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
        ctypes.POINTER(wintypes.RECT), wintypes.LPARAM,
    )
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                                         callback_type, wintypes.LPARAM]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    # 最小化时此 API 使用最小化前的矩形；完全离屏时 2 表示选择最近显示器。
    monitor = user32.MonitorFromWindow(hwnd, 2)
    info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
    if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    visible = False

    @callback_type
    def inspect_monitor(handle, _dc, _rect, _data):
        nonlocal visible
        other = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
        if not user32.GetMonitorInfoW(handle, ctypes.byref(other)):
            return False  # 不在 ctypes 回调中抛异常；枚举失败由外层统一回退。
        work = other.work
        visible |= (min(parent_rect.right, work.right) > max(parent_rect.left, work.left)
                    and min(parent_rect.bottom, work.bottom) > max(parent_rect.top, work.top))
        return True

    if not user32.EnumDisplayMonitors(None, None, inspect_monitor, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return parent_rect, info.work, bool(user32.IsIconic(hwnd)) or not visible


def center_dialog_on_parent(parent: tk.Misc, dialog: ManagedToplevel) -> bool:
    """初始布局完成后只定位一次；返回 False 表示等待布局期间已经关闭。"""
    try:
        if dialog.closing:
            return False
        # 三个调用点先透明建窗；映射及 idle 布局取得真实尺寸，不按 CTk 初始 200px 猜位置。
        tk.Wm.deiconify(dialog)
        dialog.update_idletasks()
        if dialog.closing or not dialog.winfo_exists() or not parent.winfo_exists():
            return False  # idle 可触发关闭，不能再查询死窗口或取得 grab。
        try:
            parent_rect, work, fallback = _parent_work_area(parent)
            hwnd, rect = _native_window_rect(dialog)
            width, height = rect.right - rect.left, rect.bottom - rect.top
            # 超高弹窗不强塞父窗；最小化或与所有工作区无交集也用所选屏幕工作区中心。
            base = work if fallback or height > parent_rect.bottom - parent_rect.top else parent_rect
            x = base.left + (base.right - base.left - width) // 2
            y = base.top + (base.bottom - base.top - height) // 2
            x = max(work.left, min(x, work.right - width))
            y = max(work.top, min(y, work.bottom - height))
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user32.SetWindowPos.restype = wintypes.BOOL
            # 绝对物理坐标支持左/上副屏负值；NOSIZE|NOZORDER|NOACTIVATE 不改尺寸或抢激活。
            if not user32.SetWindowPos(hwnd, None, x, y, 0, 0, 0x0015):
                raise ctypes.WinError(ctypes.get_last_error())
        except (AttributeError, OSError) as error:
            # 原生定位失败只降级到 Tk 屏幕居中，不能阻断编辑或危险操作的取消流程。
            print(f"警告：弹窗原生定位失败，使用屏幕居中（{error}）。")
            x = max((dialog.winfo_screenwidth() - dialog.winfo_width()) // 2, 0)
            y = max((dialog.winfo_screenheight() - dialog.winfo_height()) // 2, 0)
            dialog.wm_geometry(f"+{x}+{y}")
        dialog.attributes("-alpha", 1.0)  # 定位后才显现；不绑定后续移动/缩放，尊重用户拖动。
        return True
    except tk.TclError:
        return False  # 父窗退出可连带销毁子窗，调用方保持原有取消返回值。


def _apply_app_icon(window: tk.Tk | tk.Toplevel) -> None:
    """为窗口设置应用图标。"""
    try:
        # 路径统一由 config.RESOURCE_DIR 提供：它带 sys._MEIPASS 兜底，
        # 打包成 exe 后资源被解压到临时目录，也能正确定位到应用图标。
        icon_path = RESOURCE_DIR / "image" / "app_icon.ico"
        if icon_path.exists():
            window.iconbitmap(str(icon_path))
    except Exception:
        # 图标只是装饰，Linux/macOS 下 iconbitmap 也可能不可用，失败时静默忽略。
        pass
