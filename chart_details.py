"""财务分析的只读账目明细窗口。"""

from decimal import Decimal
from tkinter import ttk

import customtkinter as ctk

from dialog_common import _apply_app_icon
from dialog_lifecycle import ManagedToplevel
from tag_aggregation import StatsRecord


class ChartDetails(ManagedToplevel):
    PAGE_SIZE = 100

    def __init__(self, master):
        super().__init__(master, fg_color="#F0F5FC")
        self.withdraw()  # 等首批内容就绪再显示，避免先闪出空窗。
        self.transient(master)
        self.title("账目明细")
        _apply_app_icon(self)
        self.geometry("780x490")
        self.minsize(620, 360)
        self._rows: tuple[StatsRecord, ...] = ()
        self._page = 0
        font = ("Microsoft YaHei UI", 14)
        self.heading = ctk.CTkLabel(self, text="", anchor="w", text_color="#17243B",
                                    font=("Microsoft YaHei UI", 20, "bold"))
        self.heading.pack(fill="x", padx=18, pady=(14, 0))
        self.summary = ctk.CTkLabel(self, text="", anchor="w", font=font, text_color="#526782")
        self.summary.pack(fill="x", padx=18, pady=(0, 8))

        # 用与财务分析一致的卡片容纳原生表格，内边距让表头和滚动条避开圆角。
        body = ctk.CTkFrame(self, fg_color="#FFFFFF", corner_radius=13, border_width=1, border_color="#DCE6F3")
        body.pack(fill="both", expand=True, padx=18)
        columns = ("日期", "金额（元）", "标签", "备注")
        self.table = ttk.Treeview(body, columns=columns, show="headings", selectmode="browse",
                                  style="Details.Treeview")
        for column, anchor in zip(columns, ("w", "e", "w", "w")):
            self.table.heading(column, text=column, anchor=anchor)
            self.table.column(column, anchor=anchor, stretch=column in columns[2:])
        self._style_table(body._get_widget_scaling())
        scroll = ctk.CTkScrollbar(body, width=12, command=self.table.yview, button_color="#C7D6E8",
                                  button_hover_color="#9FB5CF")
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y", padx=(4, 8), pady=8)
        self.table.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        self.table.bind("<<TreeviewSelect>>", self._show_selection)

        self.memo = ctk.CTkTextbox(self, height=67, state="disabled", wrap="word", font=font,
                                  fg_color="#E8EFF9", text_color="#334155", corner_radius=9)
        self.memo.pack(fill="x", padx=18, pady=(8, 0))
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(pady=(8, 12))
        self.previous_page = ctk.CTkButton(footer, width=76, text="上一页", command=lambda: self._render_page(-1))
        self.previous_page.pack(side="left")
        self.page_label = ctk.CTkLabel(footer, text="", width=74)
        self.page_label.pack(side="left")
        self.next_page = ctk.CTkButton(footer, width=76, text="下一页", command=lambda: self._render_page(1))
        self.next_page.pack(side="left")
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.finish_setup()

    def _style_table(self, scale: float) -> None:
        style = ttk.Style(self)
        # 复制可着色的元素到明细表，Windows 原生表头会忽略色值，且全局主题不能跟着切换。
        if "Details.Treeview.field" not in style.element_names():
            for name in ("Treeview.field", "Treeheading.cell"):
                style.element_create(f"Details.{name}", "from", "clam", name)
            style.layout("Details.Treeview", [("Details.Treeview.field", {"sticky": "nswe", "children": [
                ("Treeview.treearea", {"sticky": "nswe"})]})])
            style.layout("Details.Treeview.Heading", [("Details.Treeheading.cell", {"sticky": "nswe"}),
                ("Treeheading.padding", {"sticky": "nswe", "children": [("Treeheading.text", {"sticky": "we"})]})])
        # 给 ttk 传缩放后的物理像素，使字号、行高和列宽与同窗的 CTk 控件同步。
        font = ("Microsoft YaHei UI", -round(14 * scale))
        style.configure("Details.Treeview", font=font, rowheight=round(34 * scale), foreground="#334155",
                        background="#FFFFFF", fieldbackground="#FFFFFF", borderwidth=0)
        style.configure("Details.Treeview.Heading", font=(*font, "bold"), foreground="#475569",
                        background="#EAF2FA", padding=(3, round(8 * scale)), relief="flat")
        style.map("Details.Treeview", background=[("selected", "#D7E9FC")], foreground=[("selected", "#17243B")])
        for column, width in zip(self.table["columns"], (120, 146, 166, 260)):
            self.table.column(column, width=round(width * scale), minwidth=round(90 * scale))

    def _set_scaling(self, new_widget_scaling, new_window_scaling) -> None:
        super()._set_scaling(new_widget_scaling, new_window_scaling)
        if hasattr(self, "table"):  # 构建期间可能先收到缩放回调，等表格存在再更新。
            self._style_table(new_widget_scaling)

    def show_rows(self, title: str, rows: tuple[StatsRecord, ...], amount: Decimal) -> None:
        # 同日按主键倒序，复用窗口时从第一页开始，避免继承上一个图形的选中状态。
        self._rows = tuple(sorted(rows, key=lambda row: (row.record_date, row.record_id), reverse=True))
        self._page = 0
        self.heading.configure(text=title)
        self.summary.configure(text=f"共 {len(rows)} 笔  ·  合计 ¥ {amount:,.2f}" if rows else "暂无账目")
        self._render_page(0)
        self.deiconify()
        self.lift()

    def _render_page(self, step: int) -> None:
        pages = max(1, (len(self._rows) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self._page = min(max(self._page + step, 0), pages - 1)
        start = self._page * self.PAGE_SIZE
        self.table.delete(*self.table.get_children())
        for row in self._rows[start:start + self.PAGE_SIZE]:
            self.table.insert("", "end", values=(row.record_date, f"{row.amount:,.2f}",
                                                  "、".join(row.tags) or "—", " ".join(row.note.splitlines())))
        self.table.yview_moveto(0)
        self.page_label.configure(text=f"{self._page + 1} / {pages}")
        # 颜色和翻页状态一起切换，只有能点击的按钮保留蓝底白字。
        for button, enabled in ((self.previous_page, self._page > 0), (self.next_page, self._page + 1 < pages)):
            button.configure(state="normal" if enabled else "disabled",
                             fg_color="#2563EB" if enabled else "#E2E8F0", hover_color="#1D4ED8",
                             text_color="#FFFFFF", text_color_disabled="#64748B")
        self._show_selection()

    def _show_selection(self, _event=None) -> None:
        selected = self.table.selection()
        text = "选中一笔查看完整标签和备注" if self._rows else ""
        if selected:
            row = self._rows[self._page * self.PAGE_SIZE + self.table.index(selected[0])]
            text = f"标签：{'、'.join(row.tags) or '—'}\n备注：{row.note or '—'}"
        # 仅程序回填时开启文本框，保留长内容和原始换行，同时禁止用户写入。
        self.memo.configure(state="normal")
        self.memo.delete("1.0", "end")
        self.memo.insert("1.0", text)
        self.memo.configure(state="disabled")
