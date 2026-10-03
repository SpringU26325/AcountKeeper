"""Select independent Windows title-bar and taskbar ICO frames."""

import ctypes
from ctypes import wintypes
from pathlib import Path
from typing import Any


def apply_window_icon_frames(window: Any, icon_path: Path) -> None:
    """Correct Tk's first-frame ICO scaling after its initial iconbitmap call."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.LoadImageW.argtypes = [
        wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.SendMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    ]
    user32.SendMessageW.restype = wintypes.LPARAM
    user32.DestroyIcon.argtypes = [wintypes.HANDLE]
    user32.DestroyIcon.restype = wintypes.BOOL
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int

    owned_icons: list[int] = []
    try:
        # winfo_id is the Tk client; WM_SETICON belongs on the native wrapper.
        hwnd = user32.GetAncestor(window.winfo_id(), 2)  # GA_ROOT
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

        try:
            user32.GetDpiForWindow.argtypes = [wintypes.HWND]
            user32.GetDpiForWindow.restype = wintypes.UINT
            dpi = user32.GetDpiForWindow(hwnd) or 96
            metric = user32.GetSystemMetricsForDpi
            metric.argtypes = [ctypes.c_int, wintypes.UINT]
            metric.restype = ctypes.c_int

            def dimensions(x: int, y: int) -> tuple[int, int]:
                return metric(x, dpi), metric(y, dpi)
        except AttributeError:
            def dimensions(x: int, y: int) -> tuple[int, int]:
                return user32.GetSystemMetrics(x), user32.GetSystemMetrics(y)

        # Load both frames before replacing either existing Tk-owned icon.
        for width_metric, height_metric in ((49, 50), (11, 12)):
            width, height = dimensions(width_metric, height_metric)
            if width <= 0 or height <= 0:
                raise OSError("Windows returned an invalid icon size")
            handle = user32.LoadImageW(
                None, str(icon_path), 1, width, height, 0x10,
            )  # IMAGE_ICON, LR_LOADFROMFILE
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            owned_icons.append(handle)

        def release(event: Any) -> None:
            if event.widget is window:
                for handle in owned_icons:
                    user32.DestroyIcon(handle)
                owned_icons.clear()

        window.bind("<Destroy>", release, add="+")
        for kind, handle in enumerate(owned_icons):
            user32.SendMessageW(hwnd, 0x80, kind, handle)  # WM_SETICON
        # Previous returned handles belong to Tk, so leave their ownership to Tk.
    except Exception as error:
        for handle in owned_icons:
            user32.DestroyIcon(handle)
        owned_icons.clear()
        # Retain Tk's initial icon if explicit frame selection is unavailable.
        print(f"设置原生图标尺寸失败：{error}")

