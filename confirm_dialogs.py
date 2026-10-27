"""删除与撤销导入的危险确认；每次新建销毁，只返回决定，不执行删除。"""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk

from dialog_common import _apply_app_icon, center_dialog_on_parent
from dialog_lifecycle import ManagedToplevel


def confirm_undo_import(parent: tk.Misc, batch) -> bool:
    """批次撤销危险确认；只返回决定，不连接数据库。"""
    dialog = ManagedToplevel(parent, fg_color="#F0F4F8")
    dialog.title("确认撤销导入")
    dialog.geometry("520x245")
    dialog.attributes("-alpha", 0)
    dialog.transient(parent)  # type: ignore[reportCallIssue, reportArgumentType] # Tk 接受任意已映射父窗，typeshed 的 Wm/Tcl_Obj 重载过窄。
    _apply_app_icon(dialog)
    result = [False]
    # 导入笔数是原始快照，不承诺被单笔删除过的记录仍全部存在。
    ctk.CTkLabel(dialog, text=f"撤销 {batch.file_name} 的导入？", wraplength=465,
                 font=("Microsoft YaHei UI", 14, "bold"), text_color="#243447").pack(padx=22, pady=(20, 8))
    ctk.CTkLabel(dialog, text=f"该批次原导入 {batch.imported_count} 笔。\n将删除其全部现存记录，包括已编辑或添加标签的记录。\n手动记录、其他批次和标签配置不受影响。",
                 wraplength=465, justify="left", font=("Microsoft YaHei UI", 12),
                 text_color="#475569").pack(padx=22, pady=8)
    actions = ctk.CTkFrame(dialog, fg_color="transparent")
    actions.pack(pady=12)

    def finish(confirmed):
        if not dialog.closing:
            result[0] = confirmed
            dialog.destroy()

    # 红色操作需明确点击；回车不默认执行整批删除，关闭/Esc均为取消。
    ctk.CTkButton(actions, text="取消", command=lambda: finish(False), width=140,
                 fg_color="#E8EEF5", hover_color="#DDE7F2", text_color="#475569",
                 corner_radius=8, font=("Microsoft YaHei UI", 12)).pack(side="left", padx=8)
    ctk.CTkButton(actions, text="确认撤销", command=lambda: finish(True), width=140,
                 fg_color="#B84035", hover_color="#A3342B", corner_radius=8,
                 font=("Microsoft YaHei UI", 12)).pack(side="left", padx=8)
    dialog.protocol("WM_DELETE_WINDOW", lambda: finish(False))
    dialog.bind("<Escape>", lambda _event: finish(False))
    dialog.finish_setup()
    if dialog.closing or not center_dialog_on_parent(parent, dialog):
        return False
    dialog.grab_set()
    parent.wait_window(dialog)
    return result[0]


def confirm_delete(parent: ctk.CTk) -> bool:
    """返回是否确认删除；本窗只收决定，调用方据此执行删除，关闭即销毁而不缓存。"""
    # 不用 messagebox.askyesno，是因为系统弹窗无法定制文字与配色，
    # 也无法明确哪个按钮是"危险"操作。
    dialog = ManagedToplevel(parent)
    dialog.attributes("-alpha", 0.0)  # 与编辑框共用透明布局、一次定位后显现的流程。
    dialog.title("确认删除")
    dialog.geometry("360x180")
    tk.Wm.resizable(dialog, False, False)  # 避免 CTk 额外标题栏重绘在定位后再次进入 update。
    dialog.transient(parent)
    dialog.configure(fg_color="#F0F4F8")
    _apply_app_icon(dialog)
    # 默认返回 False（未确认），只有点「确认」才会置为 True。
    result = [False]

    content = ctk.CTkFrame(dialog, fg_color="transparent")
    content.pack(fill="both", expand=True, padx=24, pady=20)
    # 文案逐字对齐需求 §3.5 的问句，不自拟「确定要删除这条记录吗？」这种近似说法，
    # 避免文档与界面两处措辞各自漂移（#27）。
    ctk.CTkLabel(
        content,
        text="确认删除该记录吗？",
        font=("Microsoft YaHei UI", 12),
        text_color="#243447",
    ).pack(anchor="w", pady=(8, 18))

    buttons = ctk.CTkFrame(content, fg_color="transparent")
    buttons.pack(fill="x")
    # 两列等宽，让「取消」与「确认」按钮尺寸完全对称。
    buttons.grid_columnconfigure((0, 1), weight=1)

    def cancel() -> None:
        if dialog.closing:
            return
        # 保持 result[0] 为 False，调用方据此放弃删除。
        dialog.destroy()

    def confirm() -> None:
        if dialog.closing:
            return
        result[0] = True
        dialog.destroy()

    ctk.CTkButton(
        buttons,
        text="取消",
        command=cancel,
        width=120,
        height=34,
        corner_radius=9,
        fg_color="#90A4AE",
        hover_color="#78909C",
        font=("Microsoft YaHei UI", 11),
    ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ctk.CTkButton(
        buttons,
        text="确认",
        command=confirm,
        width=120,
        height=34,
        corner_radius=9,
        fg_color="#E76F51",
        hover_color="#C9573D",
        font=("Microsoft YaHei UI", 11),
    ).grid(row=0, column=1, sticky="ew", padx=(6, 0))
    # 关闭按钮视为取消；回车/确认键确认、Esc 取消，与其它对话框保持一致。
    dialog.protocol("WM_DELETE_WINDOW", cancel)
    dialog.bind("<Return>", lambda _event: confirm())
    dialog.bind("<Escape>", lambda _event: cancel())
    dialog.finish_setup()
    if dialog.closing or not center_dialog_on_parent(parent, dialog):
        return False  # 关闭请求优先，保持删除确认的取消语义。
    # grab_set 阻止用户在删除确认期间操作主窗口；
    # wait_window 暂停本次调用但仍处理 Tk 事件，故按钮须检查 closing，不能假设等待期间无重入。
    dialog.grab_set()
    parent.wait_window(dialog)
    return result[0]
