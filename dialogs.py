"""Dialog windows used by AccountKeeper."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import tkinter as tk

import customtkinter as ctk

from calendar_picker import ask_date
from category_picker import ask_category
# 类别候选统一由 category_prefs 算（与新增记录输入区走同一条路），这里只借它的
# EXPENSE / INCOME 两个方向常量来判断「这条记录算支出还是收入」，候选本身由
# category_picker 在弹窗里现算。依赖方向也因此从 dialogs -> widgets 降为
# dialogs -> category_prefs。
from category_prefs import EXPENSE, INCOME
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
    """显示预填记录编辑框，并返回通过校验的字段。

    类别候选不再由调用方传入：点 ▼ 时由 category_picker 按**金额输入框此刻的内容**
    决定方向、再向 category_prefs 现算该方向的 user 列表，所以调用方少两个必传参数，
    也不会出现「有的调用点忘记传候选、列表莫名其妙变空」这种不一致。
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

    # 类别方向（需求 3.13）：按这条记录当前是收还是支取对应的一套。
    # 负数 = 支出、非负 = 收入，与表格里的符号约定（ui.add_record 的符号转换）保持一致。
    # 这个值只当**兜底方向**用（金额框解析不出方向时靠它），不再是全程固定值，
    # 真正在点 ▼ 那一刻生效的是下面 _current_direction() 的实时结果。
    category_direction = EXPENSE if record.amount < 0 else INCOME

    def _current_direction() -> str:
        """按金额输入框**此刻**的内容算收支方向，解析不出来时退回原记录的方向。

        为什么用实时金额而不是开窗时算好的固定方向：用户完全可能在编辑过程中把金额
        符号改过来（-25.00 改成 25.00，或反过来），类别列表应该跟着变化——支出类别与
        收入类别本来就是两回事，继续显示旧的那一套会让人在一个已经变成「收入」的记录上
        挑「餐饮」。
        """
        # NaN 会让 decimal 的比较运算直接抛 InvalidOperation（不是返回 False），
        # 所以解析和比较必须包在同一个 try 里，不能只保护 Decimal(...) 那一步。
        try:
            amount = Decimal(amount_var.get().strip())
            if amount > 0:
                return INCOME
            if amount < 0:
                return EXPENSE
        except (InvalidOperation, ValueError):
            pass
        # 走到这里只有三种情况：空串 / 非数字 / NaN（解析或比较失败）、以及金额为 0
        # （本身是非法值，无从判断用户想选哪一边）。三者都退回原记录方向，
        # 真正的非法值拦截交给 confirm() 里的校验，这里只负责选一套合理的候选。
        return category_direction

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
    # 四个字段现在都是 CTkEntry（类别字段在需求 3.13 的改版里从 CTkComboBox 换回了输入框，
    # 那个 ▼ 按钮不进这个列表），所以 entries[0].focus_set() 一定落在日期框上。
    entries: list[ctk.CTkEntry] = []
    for label, variable in fields:
        ctk.CTkLabel(
            content,
            text=label,
            font=dialog_font,
            text_color="#455A64",
        ).pack(anchor="w", pady=(10, 3))

        # 日期行采用「输入框 + ▼」组合：两者放进同一个横向容器，
        # 这样 ▼ 始终贴在输入框右侧，且行高与其它字段完全一致（分开 pack 会多占一行）。
        if label == "日期":
            date_row = ctk.CTkFrame(content, fg_color="transparent")
            date_row.pack(fill="x")
            entry = ctk.CTkEntry(
                date_row,
                textvariable=variable,
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

        # 类别字段（需求 3.13）：与新增记录输入区用同一套「输入框 + ▼」组合，
        # 既能点 ▼ 从预置/历史候选里挑，也能直接手输改成新写法。
        # （上一版用的是 CTkComboBox，箭头样式与日期那个 ▼ 不统一，
        # 且它的原生下拉菜单压不住位置，原因详见 category_picker.py 的模块注释。）
        if label == "类别":
            category_row = ctk.CTkFrame(content, fg_color="transparent")
            category_row.pack(fill="x")
            entry = ctk.CTkEntry(
                category_row,
                textvariable=variable,
                font=dialog_font,
                height=34,
                corner_radius=9,
                border_width=1,
                border_color="#C6D4DF",
                fg_color="#FFFFFF",
            )

            # ▼ 按钮改在闭包之前建：闭包要拿到它的引用（见下面 toggle=），
            # 而 command 得等闭包定义好才能接上，于是先建控件、后 configure。
            # 留引用是给 category_picker 用的：它的「点弹窗外面就关」监视器必须把
            # 本按钮排除掉，否则按下时先关掉列表、紧接着 command 又把它打开，
            # 表现出来就是「点 ▼ 关不上」。
            category_button = ctk.CTkButton(
                category_row,
                text="▼",
                width=36,
                height=34,
                corner_radius=9,
                fg_color="#E3EAF2",
                hover_color="#D2DEE9",
                text_color="#243447",
                font=("Microsoft YaHei UI", 11),
            )

            # 参数必须用「默认参数」把 entry / variable 当场绑死，不能直接引用外层名字：
            # 这两个名字在同一个 for 循环里会被后一轮（备注字段）重新赋值，
            # 闭包晚绑定拿到的就是备注框——实测类别列表会锚到备注框上，
            # 位置整体下移 92px、宽度多出 52px，而且选中项会被写进备注。
            def _pick_category(
                target: ctk.CTkEntry = entry,
                var: ctk.StringVar = variable,
                toggle: ctk.CTkButton = category_button,
            ) -> None:
                """打开类别选择器，把选中的类别回填到输入框（需求 3.13）。"""
                # 锚点用输入框而不是 category_row：弹窗宽度与输入框等宽、左边缘与输入框对齐。
                # toggle 传给弹窗：再点一次这个 ▼ 表示关闭列表（返回 None），输入框原值不动。
                # direction 在**点击这一刻**现算（_current_direction 读金额框当前内容）：
                # 用户可能刚把金额符号改过来，类别列表得跟着换成对应方向的那一套。
                # 注意 amount_var 不能被写进默认参数列表：它在 for 循环里从头到尾只被赋值一次，
                # 不存在 entry / variable 那种「下一轮被重新赋成备注框」的晚绑定问题。
                picked = ask_category(
                    dialog, target, var.get(),
                    toggle_button=toggle,
                    direction=_current_direction(),
                )
                # 返回 None 表示用户取消/按 ESC/再点一次 ▼，此时保持输入框原值不变。
                if picked:
                    var.set(picked)
                # 这里不用再补 grab_set：编辑弹窗原来握着的 grab 是 category_picker
                # 主动借走、关闭时原样还回来的（见 category_picker._cleanup）。
                # 自己再抢一次纯属重复，还会掩盖借还逻辑真实是否成对的问题。

            category_button.configure(command=_pick_category)

            # 与日期行同一套写法：按钮先 pack 且 side="right" 钉在右端（36x34），
            # 输入框再 pack 且 expand=True 占满剩余宽度，两者高度齐平。
            category_button.pack(side="right", padx=(6, 0))
            entry.pack(side="left", fill="x", expand=True)
            entries.append(entry)
            continue

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
