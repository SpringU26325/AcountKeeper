"""Windows 主窗绘制实验；通过进程环境选择，不持久化用户设置。"""

import ctypes
from ctypes import wintypes
import os
import sys
import tkinter as tk


def set_composited(window, enabled):
    """只切换顶层窗口的双缓冲位，返回修改前后的扩展样式。"""
    if sys.platform != "win32":
        raise OSError("双缓冲实验仅支持 Windows")
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    # 32位系统导出名称不同；指针宽度必须与 HWND/窗口样式调用匹配。
    suffix = "PtrW" if ctypes.sizeof(ctypes.c_void_p) == 8 else "W"
    get_style = getattr(user, "GetWindowLong" + suffix)
    set_style = getattr(user, "SetWindowLong" + suffix)
    get_style.argtypes = [wintypes.HWND, ctypes.c_int]
    get_style.restype = ctypes.c_ssize_t
    set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    set_style.restype = ctypes.c_ssize_t
    # Tk 的 winfo_id 是客户区子窗，真正的带标题栏窗口由 GA_ROOT 定位。
    handle = user.GetAncestor(window.winfo_id(), 2)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    def read():
        ctypes.set_last_error(0)
        result = get_style(handle, -20)
        if not result and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        return result

    before = read()
    target = before | 0x02000000 if enabled else before & ~0x02000000
    if target != before:
        ctypes.set_last_error(0)
        previous = set_style(handle, -20, target)
        if not previous and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        after = read()
        if after != target:
            set_style(handle, -20, before)  # 读回失败时恢复原样式，不能留下半生效实验。
            raise OSError("WS_EX_COMPOSITED 样式读回不一致")
    return before, read()


def set_transitions_disabled(window, disabled):
    """只控制当前窗口的 DWM 过渡；不修改系统动画设置。"""
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    handle = user.GetAncestor(window.winfo_id(), 2)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    dwm = ctypes.WinDLL("dwmapi")
    dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    dwm.DwmSetWindowAttribute.restype = ctypes.c_long
    value = wintypes.BOOL(disabled)
    result = dwm.DwmSetWindowAttribute(handle, 3, ctypes.byref(value), ctypes.sizeof(value))
    if result:
        raise OSError(f"DwmSetWindowAttribute 失败 HRESULT=0x{result & 0xffffffff:08X}")


def configure_rendering(window):
    mode = os.environ.get("ACCOUNTKEEPER_RENDER_MODE", "normal")
    if mode not in ("normal", "no-transitions"):
        raise ValueError("ACCOUNTKEEPER_RENDER_MODE 必须为 normal 或 no-transitions；双缓冲实验因延迟停用")
    if mode == "normal" or sys.platform != "win32":
        return

    def apply(event=None):
        if event is not None and event.widget is not window:
            return  # 顶层 bindtag 会收到子控件 Map，不能重复调用 Win32。
        set_transitions_disabled(window, True)

    apply()
    # CTk 的标题栏处理可能重建包装窗口；只在主窗 Map 时重新应用属性。
    tk.Misc.bind(window, "<Map>", apply, add="+")
