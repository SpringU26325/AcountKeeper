"""Dialog windows used by AccountKeeper."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import tkinter as tk

import customtkinter as ctk

# 月份选择也走 calendar_picker：本模块原来那份自绘的 CTkEntry 输入框已删除，
# ask_month 只做一层薄封装。导入时改别名 picker_ask_month，避免与本模块下面
# 同名的 ask_month 包装函数互相覆盖（后者会遮蔽前者的名字）。
from calendar_picker import ask_date, ask_month as picker_ask_month
from config import RESOURCE_DIR
from store import Account
from widgets import TagChipsFrame


def _apply_logo_icon(window: tk.Misc) -> None:
    """为窗口设置项目 logo 图标。"""
    try:
        # 路径统一由 config.RESOURCE_DIR 提供：它带 sys._MEIPASS 兜底，
        # 打包成 exe 后资源被解压到临时目录，也能正确定位到 logo。
        icon_path = RESOURCE_DIR / "image" / "logo.ico"
        if icon_path.exists():
            window.iconbitmap(str(icon_path))
    except Exception:
        # 图标只是装饰，Linux/macOS 下 iconbitmap 也可能不可用，失败时静默忽略。
        pass


def ask_month(
    parent: ctk.CTk,
    title: str,
    initial_month: str | None = None,
    anchor: tk.Misc | None = None,
) -> str | None:
    """选择月份（严格返回 YYYY-MM），用户取消时返回 None。

    本函数只是 calendar_picker.ask_month 的一层薄封装（issues #3.2）：既统一了
    「月份选择」这一件事的入口，也让上层（ui.py）不必直接依赖 calendar_picker。
    改造前这里是自绘的 CTkEntry 输入框：一个 420x240 的独立窗口，靠正则 +
    strptime 校验用户手敲的内容，与「日期选择器」是两套实现、两套观感。
    现在两者共用同一个复用弹窗，选择行为、配色、定位规则完全一致。

    **形参顺序**：parent 之后只有 title 是必需的（它带语境，调用方必须给），
    initial_month / anchor 都可省略——只写 dialogs.ask_month(self, "导出账单")
    也是合法调用，此时定位到今天所在月份、弹窗退回屏幕居中。
    prompt 形参已在 issues #3.2 改造中删除：弹窗里不再有说明文字。

    Args:
        title: 窗口标题栏文字，调用方带语境（如「导出账单」「选择统计月份」「查看图表」）。
        initial_month: 预选月份（YYYY-MM），同时决定初始展示的年份；不传、或字符串
            不合法时定位到今天所在月份。
        anchor: 锚点控件（触发月份选择的按钮）：弹窗贴它的左下角弹出，不传则屏幕居中。
    """
    return picker_ask_month(parent, initial_month=initial_month, anchor=anchor, title=title)


def ask_edit_record(
    parent: ctk.CTk,
    record: Account,
) -> tuple[str, Decimal, tuple[str, ...], str] | None:
    """显示预填记录编辑框，并返回通过校验的字段。

    返回值的第三项是标签元组（不是单个字符串）：0 个标签也是合法结果，
    由调用方原样交给 store.update。
    标签候选不由调用方传入：点 ▼ 时由 picker 从唯一标签池现算，
    不随编辑中的金额符号变化；Decimal 与 InvalidOperation 仍用于金额解析和校验。
    """
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
    # #58 Step 3a：第三项由单个字符串改为标签元组，与 ask_edit_record 的返回注记一致。
    result: list[tuple[str, Decimal, tuple[str, ...], str] | None] = [None]
    # 四个字段都用当前记录值预填，用户只需改动需要修改的部分，减少重复输入。
    date_var = tk.StringVar(value=record.record_date)
    # 金额统一显示为两位小数，与表格中的显示格式保持一致。
    amount_var = tk.StringVar(value=f"{record.amount:.2f}")
    note_var = tk.StringVar(value=record.note)

    content = ctk.CTkFrame(dialog, fg_color="transparent")
    content.pack(fill="both", expand=True, padx=24, pady=20)
    ctk.CTkLabel(
        content,
        text="编辑记录",
        font=title_font,
        text_color="#243447",
    ).pack(anchor="w")

    resize_idle: list[str | None] = [None]

    def _resize_dialog() -> None:
        """按字段实际需求更新弹窗高度，保留原有 360px 高度作为下限。"""
        resize_idle[0] = None
        if not dialog.winfo_exists():
            return
        # after_idle 后布局尺寸已稳定；content 外侧上下各有 20px 留白。
        requested_height = content.winfo_reqheight() + 40
        height = max(360, requested_height)
        dialog.geometry(f"460x{height}")

    def _schedule_dialog_resize() -> None:
        """合并同一轮 chips 行数变化，避免连续配置窗口几何。"""
        if resize_idle[0] is None:
            resize_idle[0] = dialog.after_idle(_resize_dialog)

    def _cancel_pending_resize(event: tk.Event) -> None:
        """弹窗销毁时撤销 idle 回调，避免它访问已销毁的窗口。"""
        if event.widget is not dialog or resize_idle[0] is None:
            return
        try:
            dialog.after_cancel(resize_idle[0])
        except tk.TclError:
            pass
        resize_idle[0] = None

    dialog.bind("<Destroy>", _cancel_pending_resize, add="+")
    tag_chips = TagChipsFrame(content, on_layout_change=_schedule_dialog_resize)
    tag_chips.set_tags(record.tags)

    fields: tuple[tuple[str, tk.StringVar | TagChipsFrame], ...] = (
        ("日期", date_var),
        ("金额", amount_var),
        ("标签", tag_chips),
        ("备注", note_var),
    )

    def _pick_date() -> None:
        """打开日历选择器，把选中的日期回填到日期输入框（需求 3.12）。"""
        # anchor 传日期输入框本体：日历会贴着它弹出，而不是摆到屏幕中心。
        # 日期框是 entries 里的第一个（见下面 for 循环里那个 continue——
        # 日期行单独 append 后就跳过了剩余分支），而本闭包在定义时 entries
        # 还空着，所以必须按**调用时**求值的 entries[0] 取，不能提前存变量；
        # 空列表守卫只是防“字段配置被改得没有日期行”这种将来才会发生的事。
        picked = ask_date(
            dialog,
            date_var.get().strip(),
            anchor=entries[0] if entries else None,
        )
        # 返回 None 表示用户取消/按 ESC，此时保持输入框原值不变。
        if picked:
            date_var.set(picked)

    # 按顺序收集输入框，用于最后把焦点落到第一个字段上。
    # 只有日期、金额、备注是 CTkEntry；标签 chips 不进列表，首项仍是日期框。
    entries: list[ctk.CTkEntry] = []
    for label, value in fields:
        ctk.CTkLabel(
            content,
            text=label,
            font=dialog_font,
            text_color="#455A64",
        ).pack(anchor="w", pady=(10, 3))

        # chips 不是 StringVar，也不是单行输入框；按具体控件类型分流后直接占满字段行。
        if isinstance(value, TagChipsFrame):
            value.pack(fill="x")
            continue

        # 日期行采用「输入框 + ▼」组合：两者放进同一个横向容器，
        # 这样 ▼ 始终贴在输入框右侧，且行高与其它字段完全一致（分开 pack 会多占一行）。
        if label == "日期":
            date_row = ctk.CTkFrame(content, fg_color="transparent")
            date_row.pack(fill="x")
            entry = ctk.CTkEntry(
                date_row,
                textvariable=value,
                font=dialog_font,
                height=34,
                corner_radius=9,
                border_width=1,
                border_color="#C6D4DF",
                fg_color="#FFFFFF",
            )
            # 与 InputFrame 一致：按钮文字用「▼」（Microsoft YaHei UI 自带字形，
            # 不靠字体回退）而不是彩色 emoji 📅。📅 在该字体里没有字形，实际是靠
            # Windows 的字体回退显示出来的（主流环境实测正常，但多绕了一层，
            # 跨环境一致性差一些），而且它的彩色在灰蓝线框按钮里也显得跳。
            # 按钮先 pack 且 side="right" 钉在右端，宽度 36；
            # 输入框再 pack 且 expand=True 占满剩余空间，高度 34 与按钮齐平。
            ctk.CTkButton(
                date_row,
                text="▼",
                command=_pick_date,
                width=36,
                height=34,
                corner_radius=9,
                fg_color="#E3EAF2",
                hover_color="#D2DEE9",
                text_color="#243447",
                font=("Microsoft YaHei UI", 11),
            ).pack(side="right", padx=(6, 0))
            entry.pack(side="left", fill="x", expand=True)
            entries.append(entry)
            continue

        # 这里的输入框不需要 placeholder_text，因此可以放心使用 textvariable 双向绑定。
        entry = ctk.CTkEntry(
            content,
            textvariable=value,
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
        # #58 Step 3a：标签改为多值，而且 0 个标签是合法的（§3.14.4 需答 Q2），
        # 所以「标签非空」这条校验连同提示里的那半句一起删除；留下的
        # 「金额不能为 0」是数据层也认的业务约束。
        if parsed_amount == 0:
            error_var.set("金额不能为 0；正数表示收入，负数表示支出。")
            return
        parsed_tags = tag_chips.collect_tags()
        # 金额保持用户填写的正负号，因此编辑时可直接切换收入/支出属性。
        result[0] = (
            parsed_date.isoformat(),
            parsed_amount,
            parsed_tags,
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
