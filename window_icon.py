"""Windows 原生图标尺寸修正：启动入口先 iconbitmap，再在主窗 idle 时调用一次。

仅操作窗口包装层与 ICO，不读写用户数据；按调用时 DPI 选大小图标，不监听后续 DPI 变化。
加载的 HICON 仍由本模块负责释放，不能套用 SetWindowRgn 成功后移交系统的区域句柄规则。
"""

import ctypes
from ctypes import wintypes
from pathlib import Path
from typing import Any


def apply_window_icon_frames(window: Any, icon_path: Path) -> None:
    """替换 Tk 首帧缩放结果，并绑定窗销毁释放；不缓存或合并重复调用。"""
    # 当前为 Windows 专用入口；DLL 加载和签名设置在下面的 try 外，不承诺跨平台静默降级。
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    # 显式声明指针宽度，避免 64 位 HWND/HICON 或消息返回值被 ctypes 默认 c_int 截断。
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

    owned_icons: list[int] = []  # 正常路径由 Destroy 闭包持有；失败路径立即释放本次已加载的句柄。
    try:
        # winfo_id 是 Tk 客户区，标题栏/任务栏图标消息必须发给其原生顶层包装 HWND。
        hwnd = user32.GetAncestor(window.winfo_id(), 2)  # GA_ROOT
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

        try:
            user32.GetDpiForWindow.argtypes = [wintypes.HWND]
            user32.GetDpiForWindow.restype = wintypes.UINT
            dpi = user32.GetDpiForWindow(hwnd) or 96  # 零值按标准 DPI 计算；不在这里改变进程 DPI 模式。
            metric = user32.GetSystemMetricsForDpi
            metric.argtypes = [ctypes.c_int, wintypes.UINT]
            metric.restype = ctypes.c_int

            def dimensions(x: int, y: int) -> tuple[int, int]:
                return metric(x, dpi), metric(y, dpi)
        except AttributeError:
            # 旧系统缺少按窗口 DPI 的接口，沿用系统指标；只兜接口缺失，不吞其他计算错误。
            def dimensions(x: int, y: int) -> tuple[int, int]:
                return user32.GetSystemMetrics(x), user32.GetSystemMetrics(y)

        # 两帧都加载成功后才替换，第二帧读取失败时仍保留 Tk 原图标；此处不是任意失败的回滚事务。
        # 指标 49/50 是小图标宽高，11/12 是大图标宽高，顺序对应下方 WM_SETICON 的 0/1。
        for width_metric, height_metric in ((49, 50), (11, 12)):
            width, height = dimensions(width_metric, height_metric)
            if width <= 0 or height <= 0:
                raise OSError("Windows returned an invalid icon size")
            handle = user32.LoadImageW(
                None, str(icon_path), 1, width, height, 0x10,
            )  # IMAGE_ICON, LR_LOADFROMFILE
            # 未使用 LR_SHARED，这两份 HICON 属于本次调用，成功发消息也仍须 DestroyIcon。
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            owned_icons.append(handle)

        def release(event: Any) -> None:
            # 顶层 bind 会收到子控件 Destroy，只有目标窗口退出才释放，清空列表防止重复释放。
            if event.widget is window:
                for handle in owned_icons:
                    user32.DestroyIcon(handle)
                owned_icons.clear()

        # 追加监听保留宿主原订阅；闭包随窗寿命保留，本函数重复调用会新增监听，调用方应只调用一次。
        window.bind("<Destroy>", release, add="+")
        for kind, handle in enumerate(owned_icons):
            user32.SendMessageW(hwnd, 0x80, kind, handle)  # WM_SETICON
        # WM_SETICON 返回的旧句柄来自 Tk，不能由我们释放；只回收 owned_icons 中自己加载的两份。
    except Exception as error:
        for handle in owned_icons:
            user32.DestroyIcon(handle)
        owned_icons.clear()
        # 加载/注册阶段失败不替换 Tk 图标；若已发送部分消息，这里没有恢复旧句柄的逻辑。
        print(f"设置原生图标尺寸失败：{error}")
