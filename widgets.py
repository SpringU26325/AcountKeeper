"""Reusable user-interface frames for AccountKeeper."""

from __future__ import annotations

from datetime import date
import tkinter as tk
from typing import Callable
from tkinter import ttk

import customtkinter as ctk


class InputFrame(ctk.CTkFrame):
    """Input controls for adding a record."""

    def __init__(self, master: ctk.CTk, add_callback: Callable[[], None]) -> None:
        super().__init__(
            master,
            corner_radius=14,
            fg_color="#FFFFFF",
            border_width=1,
            border_color="#D8E1EA",
        )
        font_regular = ("Microsoft YaHei UI", 12)
        font_small = ("Microsoft YaHei UI", 11)
        # 日期默认填今天，减少用户手动输入的次数。
        self.date_var = tk.StringVar(value=date.today().isoformat())
        self.amount_var = tk.StringVar()
        # 默认选中「支出」，因为日常记账中支出占绝大多数。
        self.amount_type_var = tk.StringVar(value="支出")
        self.category_var = tk.StringVar()
        self.note_var = tk.StringVar()

        ctk.CTkLabel(
            self,
            text="添加记录",
            font=("Microsoft YaHei UI", 13, "bold"),
            text_color="#243447",
        ).grid(row=0, column=0, columnspan=8, padx=16, pady=(12, 4), sticky="w")

        # 保持原有列结构：日期 / 金额 / 类别 / 备注。
        field_specs = (
            ("日期", self.date_var, 13),
            ("金额", self.amount_var, 12),
            ("类别", self.category_var, 14),
            ("备注", self.note_var, 28),
        )

        # 先把标签和普通输入框按原来的列布局绘制。
        for column, (label, variable, width) in enumerate(field_specs):
            ctk.CTkLabel(
                self,
                text=label,
                font=font_small,
                text_color="#455A64",
            ).grid(
                row=1,
                column=column * 2,
                padx=(16, 6),
                pady=(4, 14),
                sticky="w",
            )

            if label != "金额":
                entry = ctk.CTkEntry(
                    self,
                    width=width * 8,
                    height=36,
                    font=font_regular,
                    corner_radius=9,
                    border_width=1,
                    border_color="#C6D4DF",
                    fg_color="#F8FAFC",
                    textvariable=variable,
                )
                entry.grid(
                    row=1,
                    column=column * 2 + 1,
                    padx=(0, 10),
                    pady=(4, 14),
                    sticky="ew",
                )
                continue

            # 金额区域使用一个透明的内部容器，确保切换按钮和输入框在同一行水平排列。
            # 若把两个控件各自 grid 到不同列，行高会被撑开，导致整行控件全部错位。
            amount_frame = ctk.CTkFrame(
                self,
                fg_color="transparent",
                corner_radius=0,
            )
            amount_frame.grid(
                row=1,
                column=column * 2 + 1,
                padx=(0, 10),
                pady=(4, 14),
                sticky="ew",
            )
            # 这里不设置「列权重」：容器内部用 pack 水平排列子控件，列的伸缩权重不会生效，
            # 留着只会让人误以为宽度分配由 grid 控制，所以直接省略。

            # 支出/收入切换按钮（需求 3.2）：只决定金额的正负号，不直接改写输入框里的数字。
            self.amount_type_button = ctk.CTkSegmentedButton(
                amount_frame,
                values=["支出", "收入"],
                variable=self.amount_type_var,
                width=92,
                height=32,
                corner_radius=8,
                font=("Microsoft YaHei UI", 10),
                selected_color="#2F80ED",
                selected_hover_color="#256AC4",
                # 浅色主题下必须显式指定未选中态的底色与文字色，否则「收入」二字会看不见。
                unselected_color="#C6D4DF",
                unselected_hover_color="#B7C7D4",
                text_color="#455A64",
            )
            self.amount_type_button.pack(side="left", padx=(0, 8), pady=0)

            # 金额输入框刻意不用 textvariable，而是用 placeholder_text 显示占位提示；
            # 代价是取用户输入时必须读控件本身（amount_entry.get()），不能读 StringVar。
            # 也正因为如此，这里不绑定 <KeyRelease> 去回写 StringVar：粘贴（尤其右键粘贴）
            # 只发送 <<Paste>> 之类的虚拟事件、不产生按键事件，靠按键回写必然漏掉粘贴内容。
            self.amount_entry = ctk.CTkEntry(
                amount_frame,
                width=120,
                height=36,
                font=font_regular,
                corner_radius=9,
                border_width=1,
                border_color="#C6D4DF",
                fg_color="#F8FAFC",
                placeholder_text=" 请输入金额 ",
            )
            self.amount_entry.pack(side="left", fill="y", expand=True)

        ctk.CTkButton(
            self,
            text="添加记录",
            command=add_callback,
            width=108,
            height=36,
            corner_radius=10,
            fg_color="#2F80ED",
            hover_color="#256AC4",
            font=font_small,
        ).grid(row=1, column=8, padx=(4, 16), pady=(4, 14), sticky="e")
        # 只给输入框所在的奇数列分配伸缩权重，标签列保持固定宽度不被拉伸。
        for column in (1, 3, 5, 7):
            self.columnconfigure(column, weight=1)


class ToolbarFrame(ctk.CTkFrame):
    """Search, action buttons, and summary controls."""

    def __init__(
        self,
        master: ctk.CTk,
        filter_callback: Callable[..., None],
        refresh_callback: Callable[[], None],
        delete_callback: Callable[[], None],
        stats_callback: Callable[[], None],
        export_callback: Callable[[], None],
        chart_callback: Callable[[], None],
        open_folder_callback: Callable[[], None],
    ) -> None:
        super().__init__(master, fg_color="transparent")
        font_small = ("Microsoft YaHei UI", 11)
        # 所有工具按钮共用同一套尺寸参数，保证视觉高度完全一致。
        button_config = {
            "height": 34,
            "corner_radius": 9,
            "font": font_small,
        }
        self.search_var = tk.StringVar()
        # 需求 3.9 要求保留变量监听：任何对 search_var 的赋值都会立刻刷新列表；
        # 用户实际输入时则由输入框上的事件直接调用 filter_callback（见下方绑定）。
        self.search_var.trace_add("write", filter_callback)
        # 与金额输入框同理：为了使用原生 placeholder_text（需求 3.9）而放弃 textvariable，
        # 所以不靠 StringVar 回写内容，筛选时统一直接读 search_entry.get()。
        self.search_entry = ctk.CTkEntry(
            self,
            width=200,
            height=34,
            corner_radius=9,
            border_width=1,
            border_color="#C6D4DF",
            fg_color="#FFFFFF",
            font=font_small,
            placeholder_text="🔍 搜索日期/类别/备注",
        )
        self.search_entry.pack(side="left", padx=(0, 10))
        # 实时筛选的触发源必须挂在输入框上：没有 textvariable 就没有变量可监听，
        # 逐字符过滤只能由输入事件驱动。
        # 键盘输入用 <KeyRelease>；粘贴/剪切/清空走的是 <<Paste>>/<<Cut>>/<<Clear>> 虚拟事件
        # （右键粘贴尤其不会产生按键事件），漏掉它们就会出现"粘了字但列表不刷新"。
        # 虚拟事件的控件级回调先于 Tk 的类绑定执行，此刻文本还没插进输入框，
        # 因此必须 after_idle 延后到插入完成之后再读，否则过滤用的还是旧内容。
        self.search_entry.bind("<KeyRelease>", filter_callback, add="+")
        for sequence in ("<<Paste>>", "<<PasteSelection>>", "<<Cut>>", "<<Clear>>"):
            self.search_entry.bind(
                sequence,
                lambda _event: self.after_idle(filter_callback),
                add="+",
            )
        # 工具按钮按「轻-重」顺序从左到右排列，删除这类破坏性操作用红色以示警示。
        ctk.CTkButton(
            self,
            text="刷新",
            command=refresh_callback,
            width=78,
            fg_color="#607D8B",
            hover_color="#4F6873",
            **button_config,
        ).pack(side="left")
        ctk.CTkButton(
            self,
            text="删除选中记录",
            command=delete_callback,
            width=132,
            fg_color="#E76F51",
            hover_color="#C9573D",
            **button_config,
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            self,
            text="月度统计",
            command=stats_callback,
            width=108,
            fg_color="#5B8E7D",
            hover_color="#477564",
            **button_config,
        ).pack(side="left")
        ctk.CTkButton(
            self,
            text="导出为CSV",
            command=export_callback,
            width=110,
            fg_color="#7B6D8D",
            hover_color="#635775",
            **button_config,
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            self,
            text="查看图表",
            command=chart_callback,
            width=108,
            fg_color="#4A90D9",
            hover_color="#3679BA",
            **button_config,
        ).pack(side="left")
        ctk.CTkButton(
            self,
            text="打开数据目录",
            command=open_folder_callback,
            width=132,
            fg_color="#607D8B",
            hover_color="#4F6873",
            **button_config,
        ).pack(side="left", padx=8)
        self.summary_var = tk.StringVar()
        # 右侧的收/支汇总标签用 pack(side="right") 固定靠右，不随左侧按钮增减而移动。
        ctk.CTkLabel(
            self,
            textvariable=self.summary_var,
            font=font_small,
            text_color="#546E7A",
        ).pack(side="right")


class RecordTableFrame(ctk.CTkFrame):
    """Record table and its scrollbar."""

    def __init__(self, master: ctk.CTk, edit_callback: Callable[[tk.Event], None]) -> None:
        super().__init__(
            master,
            corner_radius=14,
            fg_color="#FFFFFF",
            border_width=1,
            border_color="#D8E1EA",
        )
        font_small = ("Microsoft YaHei UI", 11)
        table_inner = ctk.CTkFrame(self, fg_color="transparent")
        table_inner.pack(fill="both", expand=True, padx=10, pady=10)
        columns = ("id", "date", "amount", "category", "note")
        # 自定义 ttk 样式，是为了摆脱 Windows 原生 Treeview 的灰色边框和紧凑行高，
        # 让表格与浅色圆角卡片风格保持一致。
        style = ttk.Style(self)
        # borderwidth=0 用于去掉原生外观自带的立体边框；rowheight 加高让行更好点选。
        style.configure(
            "Account.Treeview",
            background="#FFFFFF",
            fieldbackground="#FFFFFF",
            foreground="#263238",
            rowheight=34,
            font=font_small,
            borderwidth=0,
        )
        style.configure(
            "Account.Treeview.Heading",
            background="#E8F0F6",
            foreground="#37474F",
            font=("Microsoft YaHei UI", 11, "bold"),
            relief="flat",
        )
        # 原生选中色是深蓝，与浅色主题冲突，这里改成浅蓝以保持整体协调。
        style.map("Account.Treeview", background=[("selected", "#D7E9FC")])
        self.tree = ttk.Treeview(
            table_inner,
            columns=columns,
            show="headings",
            style="Account.Treeview",
        )
        # show="headings" 表示隐藏默认的首列树形图标，只显示自定义表头。
        headings = (
            ("id", "ID", 70),
            ("date", "日期", 120),
            ("amount", "金额", 120),
            ("category", "类别", 140),
            ("note", "备注", 300),
        )
        for column, title, width in headings:
            self.tree.heading(column, text=title)
            self.tree.column(
                column,
                # 备注列内容较长，左对齐更易读；其余列都是短内容，居中对齐更整齐。
                width=width,
                anchor="center" if column != "note" else "w",
            )
        scrollbar = ttk.Scrollbar(
            table_inner,
            orient="vertical",
            command=self.tree.yview,
        )
        # 双向绑定：拖动滚动条能滚动表格，鼠标滚轮滚动表格时滚动条位置也会同步更新。
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        # 双击某一行即触发编辑回调（需求 3.5.1），比额外放一个「编辑」按钮更直观。
        self.tree.bind("<Double-1>", edit_callback)
        # 只让表格所在的第 0 行/列获得伸缩权重，保证窗口拉大时表格占满剩余空间。
        table_inner.rowconfigure(0, weight=1)
        table_inner.columnconfigure(0, weight=1)
