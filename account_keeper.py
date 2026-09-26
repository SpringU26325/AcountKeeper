"""AccountKeeper application entry point."""

import ctypes
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from store import AccountStore
from ui import AccountKeeperApp


def main() -> None:
    if hasattr(ctypes, "windll"):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    # 数据库损坏、被其它程序占用、目录无写入权限时，AccountStore() 或主窗口构造都会抛异常。
    # 这里必须兜底：打包成 exe 后没有控制台，异常直接退出会让用户只看到「双击没反应」。
    try:
        app = AccountKeeperApp(AccountStore())
    except Exception as error:
        messagebox.showerror("启动失败", "数据库文件损坏，请检查数据目录。")
        print(f"启动失败：{error}")
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "AccountKeeper.App"
        )
        icon_path = Path(__file__).resolve().parent / "image" / "logo.ico"
        app.iconbitmap(str(icon_path))
    except Exception as e:
        print(f"设置图标失败：{e}")
    app.mainloop()


if __name__ == "__main__":
    main()
