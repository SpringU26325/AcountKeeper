"""Dialog windows used by AccountKeeper."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import tkinter as tk

import customtkinter as ctk

from store import Account


def _apply_logo_icon(window: tk.Misc) -> None:
    """为窗口设置项目 logo 图标。"""
    try:
        # 用 __file__ 定位图片，保证无论从哪个工作目录启动都能找到 logo。
        icon_path = Path(__file__).resolve().parent / "image" / "logo.ico"
        if icon_path.exists():
            window.iconbitmap(str(icon_path))
    except Exception:
        # 图标只是装饰，Linux/macOS 下 iconbitmap 也可能不可用，失败时静默忽略。
        pass


def ask_month(parent: ctk.CTk, title: str, prompt: str) -> str | None:
    """显示自定义月份输入框，并返回通过校验的月份。"""
    # 不用 simpledialog/messagebox，是为了统一配色、圆角和 logo 图标风格。
    dialog = ctk.CTkToplevel(parent)
    dialog.title(title)
    dialog.geometry("420x240")
    dialog.resizable(False, False)
    # transient 让对话框始终显示在父窗口之上，父窗口最小化时它也跟着最小化。
    dialog.transient(parent)
    dialog.configure(fg_color="#F0F4F8")
    _apply_logo_icon(dialog)

    dialog_font = ("Microsoft YaHei UI", 11)
    title_font = ("Microsoft YaHei UI", 13, "bold")
    # 用单元素列表而非普通变量保存结果：闭包能直接写入，且无需 nonlocal 声明。
    result: list[str | None] = [None]

    content = ctk.CTkFrame(dialog, fg_color="transparent")
    content.pack(fill="both", expand=True)
    ctk.CTkLabel(
        content,
        text=title,
        font=title_font,
        text_color="#243447",
    ).pack(anchor="w", padx=24, pady=(20, 0))
    ctk.CTkLabel(
        content,
        text=prompt,
        font=dialog_font,
        text_color="#455A64",
    ).pack(anchor="w", padx=24, pady=(12, 8))

    month_var = tk.StringVar()
    entry = ctk.CTkEntry(
        content,
        textvariable=month_var,
        font=dialog_font,
        height=38,
        corner_radius=9,
        border_width=1,
        border_color="#C6D4DF",
        fg_color="#FFFFFF",
    )
    entry.pack(fill="x", padx=24)
    # 错误提示预留在这个标签里，避免每输错一次都弹出一个 messagebox 打断用户。
    error_var = tk.StringVar()
    ctk.CTkLabel(
        content,
        textvariable=error_var,
        text_color="#C62828",
        font=("Microsoft YaHei UI", 10),
    ).pack(anchor="w", padx=24, pady=(5, 0))

    buttons = ctk.CTkFrame(content, fg_color="transparent")
    buttons.pack(fill="x", padx=24, pady=(14, 0))
    # 左右两列等宽，让「取消」和「确定」两个按钮宽度一致、整体对称。
    buttons.grid_columnconfigure((0, 1), weight=1)

    def cancel() -> None:
        # 保持 result[0] 为 None，调用方据此判断用户取消。
        dialog.destroy()

    def confirm() -> None:
        month = month_var.get().strip()
        try:
            # 先用正则卡住明显的格式错误（如 2024/01、2024-1），
            # 再用 strptime 做二次校验，拦住 2024-13 这类月份越界的输入。
            valid_format = re.fullmatch(r"\d{4}-\d{2}", month) is not None
            if not valid_format:
                raise ValueError
            datetime.strptime(month, "%Y-%m")
        except ValueError:
            # 校验失败不关闭窗口，只显示错误并把焦点交回输入框，方便用户直接改正。
            error_var.set("格式错误，请输入有效的 YYYY-MM 月份。")
            entry.focus_set()
            return
        result[0] = month
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
        font=dialog_font,
    ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ctk.CTkButton(
        buttons,
        text="确定",
        command=confirm,
        width=120,
        height=34,
        corner_radius=9,
        fg_color="#2F80ED",
        hover_color="#256AC4",
        font=dialog_font,
    ).grid(row=0, column=1, sticky="ew", padx=(6, 0))
    # 把窗口右上角的关闭按钮也当作「取消」，避免出现无法关闭或状态不明确的对话框。
    dialog.protocol("WM_DELETE_WINDOW", cancel)
    # 回车等于确定、Esc 等于取消，符合桌面软件的通用操作习惯。
    dialog.bind("<Return>", lambda _event: confirm())
    dialog.bind("<Escape>", lambda _event: cancel())
    entry.focus_set()
    # grab_set 把键盘/鼠标焦点锁在对话框内，主窗口在接受输入期间不可操作；
    # wait_window 会阻塞在这里，直到对话框被销毁，因此下面的 return 一定能拿到最终结果。
    dialog.grab_set()
    parent.wait_window(dialog)
    return result[0]


def ask_edit_record(
    parent: ctk.CTk,
    record: Account,
) -> tuple[str, Decimal, str, str] | None:
    """显示预填记录编辑框，并返回通过校验的字段。"""
    dialog = ctk.CTkToplevel(parent)
    dialog.title("编辑记录")
    # 编辑框比月份框更高，因为要纵向排列四个字段。
    dialog.geometry("460x360")
    dialog.resizable(False, False)
    dialog.transient(parent)
    dialog.configure(fg_color="#F0F4F8")
    _apply_logo_icon(dialog)

    dialog_font = ("Microsoft YaHei UI", 11)
    title_font = ("Microsoft YaHei UI", 13, "bold")
    # 用单元素列表承载返回值，闭包函数 confirm 可以直接写入。
    result: list[tuple[str, Decimal, str, str] | None] = [None]
    # 四个字段都用当前记录值预填，用户只需改动需要修改的部分，减少重复输入。
    date_var = tk.StringVar(value=record.record_date)
    # 金额统一显示为两位小数，与表格中的显示格式保持一致。
    amount_var = tk.StringVar(value=f"{record.amount:.2f}")
    category_var = tk.StringVar(value=record.category)
    note_var = tk.StringVar(value=record.note)

    content = ctk.CTkFrame(dialog, fg_color="transparent")
    content.pack(fill="both", expand=True, padx=24, pady=20)
    ctk.CTkLabel(
        content,
        text="编辑记录",
        font=title_font,
        text_color="#243447",
    ).pack(anchor="w")

    fields = (
        ("日期", date_var),
        ("金额", amount_var),
        ("类别", category_var),
        ("备注", note_var),
    )
    # 按顺序收集输入框，用于最后把焦点落到第一个字段上。
    entries: list[ctk.CTkEntry] = []
    for label, variable in fields:
        ctk.CTkLabel(
            content,
            text=label,
            font=dialog_font,
            text_color="#455A64",
        ).pack(anchor="w", pady=(10, 3))
        # 这里的输入框不需要 placeholder_text，因此可以放心使用 textvariable 双向绑定。
        entry = ctk.CTkEntry(
            content,
            textvariable=variable,
            font=dialog_font,
            height=34,
            corner_radius=9,
            border_width=1,
            border_color="#C6D4DF",
            fg_color="#FFFFFF",
        )
        entry.pack(fill="x")
        entries.append(entry)

    error_var = tk.StringVar()
    ctk.CTkLabel(
        content,
        textvariable=error_var,
        text_color="#C62828",
        font=("Microsoft YaHei UI", 10),
    ).pack(anchor="w", pady=(5, 0))

    buttons = ctk.CTkFrame(content, fg_color="transparent")
    buttons.pack(fill="x", pady=(10, 0))
    # 两列等宽，保证两个按钮左右对称。
    buttons.grid_columnconfigure((0, 1), weight=1)

    def cancel() -> None:
        # 返回 None 表示取消，调用方据此不做任何更新。
        dialog.destroy()

    def confirm() -> None:
        try:
            # 日期格式与金额合法性同时校验，任一失败都走统一的错误提示。
            parsed_date = datetime.strptime(
                date_var.get().strip(), "%Y-%m-%d"
            ).date()
            parsed_amount = Decimal(amount_var.get().strip())
            # Decimal("nan") / Decimal("Infinity") 解析本身不会报错，但入库后任何金额大小比较
            # 都会抛 InvalidOperation，所以和新增记录一样在入口处就拒收非有限数。
            # 这一层同时还承担"自我纠错"：用户编辑那条历史 NaN 记录时，必须先改成合法数字。
            if not parsed_amount.is_finite():
                raise ValueError
        except (ValueError, InvalidOperation):
            error_var.set("日期格式应为 YYYY-MM-DD，金额必须是数字。")
            return
        parsed_category = category_var.get().strip()
        # 与新增记录保持同样的业务约束：金额不能为 0、类别不能为空。
        if parsed_amount == 0 or not parsed_category:
            error_var.set(
                "金额不能为 0；正数表示收入，负数表示支出，类别不能为空。"
            )
            return
        # 金额保持用户填写的正负号，因此编辑时可直接切换收入/支出属性。
        result[0] = (
            parsed_date.isoformat(),
            parsed_amount,
            parsed_category,
            note_var.get().strip(),
        )
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
        font=dialog_font,
    ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ctk.CTkButton(
        buttons,
        text="确定",
        command=confirm,
        width=120,
        height=34,
        corner_radius=9,
        fg_color="#2F80ED",
        hover_color="#256AC4",
        font=dialog_font,
    ).grid(row=0, column=1, sticky="ew", padx=(6, 0))
    # 关闭按钮等同取消；回车确定、Esc 取消，与月份对话框保持一致的操作习惯。
    dialog.protocol("WM_DELETE_WINDOW", cancel)
    dialog.bind("<Return>", lambda _event: confirm())
    dialog.bind("<Escape>", lambda _event: cancel())
    entries[0].focus_set()
    # 锁住焦点并阻塞等待，确保返回的编辑结果一定已经由用户确认。
    dialog.grab_set()
    parent.wait_window(dialog)
    return result[0]


def confirm_delete(parent: ctk.CTk) -> bool:
    """显示不带系统快捷键标记的删除确认框。"""
    # 不用 messagebox.askyesno，是因为系统弹窗无法定制文字与配色，
    # 也无法明确哪个按钮是"危险"操作。
    dialog = ctk.CTkToplevel(parent)
    dialog.title("确认删除")
    dialog.geometry("360x180")
    dialog.resizable(False, False)
    dialog.transient(parent)
    dialog.configure(fg_color="#F0F4F8")
    _apply_logo_icon(dialog)
    # 默认返回 False（未确认），只有点「确认」才会置为 True。
    result = [False]

    content = ctk.CTkFrame(dialog, fg_color="transparent")
    content.pack(fill="both", expand=True, padx=24, pady=20)
    ctk.CTkLabel(
        content,
        text="确定要删除这条记录吗？",
        font=("Microsoft YaHei UI", 12),
        text_color="#243447",
    ).pack(anchor="w", pady=(8, 18))

    buttons = ctk.CTkFrame(content, fg_color="transparent")
    buttons.pack(fill="x")
    # 两列等宽，让「取消」与「确认」按钮尺寸完全对称。
    buttons.grid_columnconfigure((0, 1), weight=1)

    def cancel() -> None:
        # 保持 result[0] 为 False，调用方据此放弃删除。
        dialog.destroy()

    def confirm() -> None:
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
    # grab_set 阻止用户在删除确认期间操作主窗口；
    # wait_window 阻塞调用方直到对话框关闭，保证返回值一定是最终决定。
    dialog.grab_set()
    parent.wait_window(dialog)
    return result[0]
