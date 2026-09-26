"""AccountKeeper application entry point."""

import ctypes
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from settings import get_last_export_dir
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
    # 需求 3.11：启动时读一次 settings.json，取出「上次导出路径」，供导出对话框当初始目录。
    # 数据库路径固定为 config.DB_PATH，不接受配置覆盖，因此这里只关心导出目录。
    # get_last_export_dir() 内部已兜住全部异常，并保证返回的目录当前可用；没记住或记录已失效时
    # 它直接返回默认目录——新方案没有「回退」这个概念，所以这里不弹任何提示框。
    # 第二个返回值表示「本次是否真的用上了记住的目录」，当前没有分支需要它，显式丢弃。
    last_export_dir, _ = get_last_export_dir()
    # 数据库损坏、被其它程序占用、目录无写入权限时，AccountStore() 或主窗口构造都会抛异常。
    # 这里必须兜底：打包成 exe 后没有控制台，异常直接退出会让用户只看到「双击没反应」。
    try:
        # 数据库位置固定为 config.DB_PATH，因此不传任何路径参数；
        # 旧 CSV 迁移逻辑已在 #40 中删除，store 不再接收 legacy_csv_path。
        store = AccountStore()
        # 把上次导出目录注入界面层，导出时作初始目录、成功后写回配置（界面层不重复读配置）。
        app = AccountKeeperApp(store, last_export_dir=last_export_dir)
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
    # 新方案取消了「启动回退提示」：读不到上次导出目录只是便利功能失灵，不值得弹窗打扰用户。
    app.mainloop()


if __name__ == "__main__":
    main()
