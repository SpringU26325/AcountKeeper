"""应用启动入口：先准备显示环境与导出目录，再创建 store、主窗和图标。

主窗由 UI 持有并使用 store 缓存；此处只负责组装，不读取 SQL 或启动探针。
config 导入时会创建数据目录，发生在 main 的构造异常处理之前，不能把该 try 当成全启动兜底。
"""

import ctypes
from tkinter import messagebox

import customtkinter as ctk

from config import RESOURCE_DIR
from settings import get_last_export_dir
from store import AccountStore
from ui import AccountKeeperApp
from window_icon import apply_window_icon_frames


def main() -> None:
    if hasattr(ctypes, "windll"):
        try:
            # 建窗前申请系统 DPI 感知；这里只声明一次，不处理运行中跨屏或混合 DPI。
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass  # 旧系统可能缺少接口，沿用当前进程的显示模式仍能启动。
    # 固定浅色与蓝色基调须早于控件构造，否则初始控件与后建弹窗可能采用不同默认值。
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    # 需求 3.11：启动时读一次 settings.json，取出「上次导出路径」，供导出对话框当初始目录。
    # 数据库路径固定为 config.DB_PATH，不接受配置覆盖，因此这里只关心导出目录。
    # get_last_export_dir() 校验记住的路径，失效时返回默认目录；这不保证以后写入一定成功，
    # 实际导出仍须处理权限或目录变化。默认选择不弹提示，避免便利功能失效阻断启动。
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
        # 覆盖 store 与主窗构造异常；提示文案统一，但不能据此断言实际原因必是数据库损坏。
        messagebox.showerror("启动失败", "数据库文件损坏，请检查数据目录。")
        print(f"启动失败：{error}")
        return
    try:
        # Windows 的任务栏分组身份与窗口图标分别设置；此装饰链失败只记录，不退出主循环。
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "AccountKeeper.App"
        )
        # 路径统一由 config.RESOURCE_DIR 提供：它带 sys._MEIPASS 兜底，打包成 exe 后也能定位到应用图标。
        icon_path = RESOURCE_DIR / "image" / "app_icon.ico"
        app.iconbitmap(str(icon_path))
        # Tk 可能把 ICO 首帧同时放大为大小图标；等原生窗口包装层就绪再各选所需尺寸。
        # idle 回调在主窗上登记，句柄释放交给 window_icon 的 Destroy 监听；它内部处理加载失败。
        app.after_idle(lambda: apply_window_icon_frames(app, icon_path))
    except Exception as e:
        # 此处兜同步设置失败；延迟回调的异常不能靠这个 try 捕获。
        print(f"设置图标失败：{e}")
    # 新方案取消了「启动回退提示」：读不到上次导出目录只是便利功能失灵，不值得弹窗打扰用户。
    app.mainloop()


if __name__ == "__main__":
    main()  # 仅作为入口运行才进入事件循环，导入本模块不创建主窗。
