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
from tag_picker import ask_tags
# 输入的标签串按顿号拆分、清理的规则与新增区、偏好层共用一份实现
# （tag_prefs.split_tag_input），本模块不再自己写一遍（#58 Step 3a）。
import tag_prefs
from store import Account


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
    # #58 Step 2c-2：标签已改为多值，编辑框仍只提供一个单行输入框（候选弹窗负责多选），
    # 预填时把多个标签用「、」拼起来 —— 与表格单元格显示逐字符相同，用户看到什么就改什么。
    category_var = tk.StringVar(value="、".join(record.tags))
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
        # 且它的原生下拉菜单压不住位置，原因详见 tag_picker.py 的模块注释。）
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
            # 留引用是给 tag_picker 用的：它的「点弹窗外面就关」监视器必须把
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
                picked = ask_tags(
                    dialog, target, var.get(),
                    toggle_button=toggle,
                )
                # 返回 None 表示用户取消/按 ESC/再点一次 ▼，此时保持输入框原值不变。
                if picked:
                    var.set(picked)
                # 这里不用再补 grab_set：编辑弹窗原来握着的 grab 是 tag_picker
                # 主动借走、关闭时原样还回来的（见 tag_picker._cleanup）。
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
        # #58 Step 3a：标签改为多值，而且 0 个标签是合法的（§3.14.4 需答 Q2），
        # 所以「类别非空」这条校验连同提示里的那半句一起删除；留下的
        # 「金额不能为 0」是数据层也认的业务约束。
        if parsed_amount == 0:
            error_var.set("金额不能为 0；正数表示收入，负数表示支出。")
            return
        # 输入框里的一行文字按顿号拆成标签元组：与表格单元格、CSV 的展示口径同源
        # （§3.14.4），所以「餐饮、交通」这种写法在这里天然就是两个标签；
        # 拆分含 strip / 去空 / 首次出现去重，规则与偏好层共用一份实现。
        parsed_tags = tag_prefs.split_tag_input(category_var.get())
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
