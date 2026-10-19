"""应用启动入口：先恢复派发，再加载配置、数据层与主窗。

主窗由 UI 持有并使用 store 缓存；此处只负责组装，不读取 SQL 或启动探针。
恢复前只允许标准库与无导入副作用的helper接口，业务依赖在派发后加载。
"""

import sys
from pathlib import Path
from update_helper import bootstrap, native_notice, offer_manual_retry


def _run_app(context) -> None:
    import ctypes
    import platform
    from tkinter import messagebox
    import customtkinter as ctk
    from config import APP_VERSION, DATA_DIR, RESOURCE_DIR
    from settings import get_last_export_dir
    from store import AccountStore
    from ui import AccountKeeperApp
    from window_icon import apply_window_icon_frames
    if context is None and getattr(sys, "frozen", False):
        arch = "arm64" if platform.machine().upper() == "ARM64" else "x64" if sys.maxsize > 2**32 else "x86"
        try:
            if not offer_manual_retry(Path(sys.executable).resolve(), APP_VERSION, arch, DATA_DIR):
                return  # helper接管后当前主进程必须退出，不再打开数据库。
        except Exception as error:
            native_notice(f"自动更新保持暂停，原应用仍可使用：{error}")
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
        store = AccountStore(strict_migrations=context is not None)
        class PendingApp(AccountKeeperApp):
            def _build_widgets(self):
                self.withdraw()  # 控件/回调创建前隐藏，避免未提交时接到业务输入。
                super()._build_widgets()
        factory = PendingApp if context is not None else AccountKeeperApp
        # 把上次导出目录注入界面层，导出时作初始目录、成功后写回配置（界面层不重复读配置）。
        app = factory(store, last_export_dir=last_export_dir)
    except Exception as error:
        if context is not None:
            context.failed(error)  # 不弹阻塞确认框，由helper判失败并回退。
            return
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
    if context is not None:
        def await_commit():
            try:
                if not context.sent:
                    context.healthy(APP_VERSION)  # 严格迁移、控件构造及事件循环均已到达。
                if context.poll():
                    store.enable_business_writes()
                    app.deiconify()
                    return
                app.after(50, await_commit)
            except Exception as error:
                context.failed(error)  # 提交后failed不写失败，不能吞掉用户新账目。
                app.destroy()
        app.after_idle(await_commit)
    app.mainloop()


def main() -> None:
    context = None
    try:
        context = bootstrap()
        if context is not False:
            _run_app(context)
    except Exception as error:
        if context:
            context.failed(error)
        else:
            native_notice(f"启动或恢复未完成，禁止打开账本：{error}")


if __name__ == "__main__":
    main()  # 仅作为入口运行才进入事件循环，导入本模块不创建主窗。
