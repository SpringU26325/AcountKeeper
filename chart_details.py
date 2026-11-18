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
        self.heading = ctk.CTkLabel(self, text="", anchor="w", font=("Microsoft YaHei UI", 17, "bold"))
        self.heading.pack(fill="x", padx=18, pady=(14, 0))
        self.summary = ctk.CTkLabel(self, text="", anchor="w", text_color="#687992")
        self.summary.pack(fill="x", padx=18, pady=(0, 8))

        # 表格只插入当前页。明细条数再多也可翻完，长备注在下方单独查看。
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=18)
        columns = ("日期", "金额（元）", "标签", "备注")
        self.table = ttk.Treeview(body, columns=columns, show="headings", selectmode="browse")
        for column, width, anchor in zip(columns, (110, 120, 180, 305), ("w", "e", "w", "w")):
            self.table.heading(column, text=column, anchor=anchor)
            self.table.column(column, width=width, minwidth=90, anchor=anchor, stretch=column in columns[2:])
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.table.pack(side="left", fill="both", expand=True)
        self.table.bind("<<TreeviewSelect>>", self._show_selection)

        self.memo = ctk.CTkTextbox(self, height=67, state="disabled", wrap="word")
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
        self.previous_page.configure(state="normal" if self._page else "disabled")
        self.next_page.configure(state="normal" if self._page + 1 < pages else "disabled")
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
