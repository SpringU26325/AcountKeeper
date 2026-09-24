"""AccountKeeper application entry point."""

import ctypes
from pathlib import Path

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
    app = AccountKeeperApp(AccountStore())
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
