"""临时入口，阶段 5 接主页面后删除；仅用于人工验收导入预览。"""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory


def main():
    parser = argparse.ArgumentParser(description="临时打开外部账单预览，不写入账本。")
    parser.add_argument("bill_path", type=Path, help="账单文件路径（.xlsx 或 .csv）")
    args = parser.parse_args()

    from import_reader import read_bill_file
    from import_rules import load_vocabulary

    try:
        bill = read_bill_file(args.bill_path)
        vocabulary = load_vocabulary()
    except (OSError, ValueError) as error:
        # 读取失败在建窗前退出，避免留下无内容的 root 或工作线程。
        parser.exit(2, f"无法打开预览：{error}\n")

    import customtkinter as ctk
    from import_dialog import ImportDialog
    from import_session import ImportSession

    root = ctk.CTk()
    root.withdraw()  # 只显示预览窗口，关闭预览后由 finally 清理隐藏的 root。
    session = None
    try:
        # 映射保存仅落在临时目录，人工验收不会覆盖正式映射偏好。
        with TemporaryDirectory(prefix=".import-preview-", dir=Path(__file__).resolve().parent) as folder:
            session = ImportSession(bill, vocabulary)
            dialog = ImportDialog(root, session, mapping_path=Path(folder) / "import_mappings.json")

            def preview_closed(event):
                if event.widget is dialog:
                    root.quit()  # 子控件销毁不能提前结束整个预览。

            dialog.bind("<Destroy>", preview_closed, add="+")
            root.mainloop()
    finally:
        if session is not None:
            session.close()
        root.destroy()  # 不注册导入回调，不连接数据库或执行 SQL。


if __name__ == "__main__":
    main()
