"""月度预算额度、进度及只读支出明细。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import sqlite3
import tkinter as tk
from tkinter import messagebox
from typing import Literal

import customtkinter as ctk

from budget_logic import BudgetProgress, BudgetState, build_budget_snapshot
from calendar_picker import ask_month
from chart_details import ChartDetails
from chart_window import BG, BLUE, BORDER, INK, MUTED
from dialog_common import _apply_app_icon, fit_dialog_to_work_area
from dialog_lifecycle import ManagedToplevel
from import_preview_controls import PreviewSelect
from store import AccountStore
from tag_aggregation import StatsRecord
from tag_prefs import build_tag_candidates


_FONT = ("Microsoft YaHei UI", 13)
_TITLE_FONT = ("Microsoft YaHei UI", 16, "bold")
_TOTAL = "总预算"
_EXCLUDED = "预算外支出"
_TAG_LABEL = "标签 · "
_WINDOW_MINIMUM = (660, 460)
_WINDOW_PREFERRED = (760, 570)
_STATES = {
    BudgetState.AVAILABLE: ("正常", BLUE),
    BudgetState.NEAR_LIMIT: ("接近上限", "#9A530D"),
    BudgetState.EXHAUSTED: ("已用完", "#B24710"),
    BudgetState.OVER_LIMIT: ("超支", "#B84035"),
}


class BudgetWindow(ManagedToplevel):
    """在主线程读账本缓存，已打开的明细沿用当前月的同一份统计结果。"""

    def __init__(self, master: tk.Misc, store: AccountStore) -> None:
        super().__init__(master, fg_color=BG)
        self.withdraw()
        self.attributes("-alpha", 0.0)
        self.title("预算管理")
        _apply_app_icon(self)
        self.store = store
        self.month = date.today().isoformat()[:7]
        self._limits: dict[str, BudgetProgress] = {}
        self._tags: dict[str, str | None] = {_TOTAL: None}
        self._detail_data: dict[str, tuple[tuple[StatsRecord, ...], Decimal]] = {}
        self.details: dict[str, ChartDetails] = {}
        self._opening_detail = False
        self._editing = (self.month, _TOTAL)
        self._loaded_amount = ""
        self._drafts: dict[tuple[str, str], str] = {}

        # 全页使用一个滚动区，小屏幕也能抵达额度操作及预算外明细。
        self.content = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        self.content.pack(fill="both", expand=True)
        heading = ctk.CTkFrame(self.content, fg_color="transparent")
        heading.pack(fill="x", padx=20, pady=(16, 12))
        ctk.CTkLabel(heading, text="预算管理", font=("Microsoft YaHei UI", 25, "bold"),
                     text_color=INK).pack(side="left")
        self.month_button = self._button(heading, self.month, self._select_month, width=100)
        self.month_button.pack(side="right")
        self._button(heading, "复制上月额度", lambda: self._change_limit("copy"), width=112).pack(
            side="right", padx=(0, 8))

        # 单个额度编辑区选择总预算或标签，切换选项时加载该预算的现有额度。
        editor = ctk.CTkFrame(self.content, fg_color="#FFFFFF", corner_radius=13, border_width=1, border_color=BORDER)
        editor.pack(fill="x", padx=20, pady=(0, 9))
        editor.columnconfigure(1, weight=1)
        self.editing_label = ctk.CTkLabel(editor, text="", anchor="w", justify="left", font=_FONT,
                                          text_color=INK, wraplength=560)
        self.editing_label.grid(row=0, column=0, columnspan=4, sticky="ew", padx=12, pady=(8, 0))
        tk.Misc.bind(editor, "<Configure>", self._wrap_editing_label, add="+")
        # 只缩短界面上的显示名，回填仍传原预算键，避免同名标签误写到总预算。
        self.target = PreviewSelect(editor, values=(_TOTAL,), command=self._select_target, font=_FONT,
                                    width=190, display=lambda value: value.removeprefix(_TAG_LABEL), group=(1, "标签"))
        self.target.grid_propagate(False)  # 完整名称由上方编辑对象展示，长标签不挤占额度输入框。
        self.target.grid(row=1, column=0, padx=(12, 8), pady=10, sticky="w")
        self.amount_entry = ctk.CTkEntry(editor, placeholder_text="预算额度（元）", font=_FONT,
                                         width=140, height=34, border_color=BORDER)
        self.amount_entry.grid(row=1, column=1, padx=(0, 8), sticky="ew")
        self._button(editor, "保存额度", lambda: self._change_limit("save"), primary=True).grid(
            row=1, column=2, padx=(0, 8))
        self.delete_button = self._button(editor, "删除额度", lambda: self._change_limit("delete"))
        self.delete_button.grid(row=1, column=3, padx=(0, 12))

        self.cards = ctk.CTkFrame(self.content, fg_color="transparent", corner_radius=0)
        self.cards.pack(fill="x", padx=20)
        self.excluded_button = self._button(self.content, _EXCLUDED, lambda: self._open_detail(_EXCLUDED))
        self.excluded_button.pack(fill="x", padx=20, pady=(8, 5))
        self.status = ctk.CTkLabel(self.content, text="", font=_FONT, anchor="w",
                                    text_color=MUTED, justify="left", wraplength=600)
        self.status.pack(fill="x", padx=20, pady=(0, 10))
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.finish_setup()
        if not self.closing:
            self.refresh()
            self._fill_amount(self.target.get())
            self.deiconify()
            self.update_idletasks()  # 透明映射后才能实测窗口边框和标题栏。
            fit_dialog_to_work_area(self.master, self, _WINDOW_MINIMUM, _WINDOW_PREFERRED)

    def _set_scaling(self, new_widget_scaling, new_window_scaling) -> None:
        super()._set_scaling(new_widget_scaling, new_window_scaling)
        if self._setup_complete and not self.closing:
            self.after_idle(self._fit_after_scaling)

    def _fit_after_scaling(self) -> None:
        if not self.closing and self.winfo_viewable():
            fit_dialog_to_work_area(self.master, self, _WINDOW_MINIMUM)

    @staticmethod
    def _button(master, text, command, *, width=90, primary=False) -> ctk.CTkButton:
        return ctk.CTkButton(master, text=text, command=command, width=width, height=34,
                             corner_radius=8, font=_FONT, fg_color=BLUE if primary else "#E8EEF5",
                             hover_color="#1C6BE0" if primary else "#DCE7F3",
                             text_color="#FFFFFF" if primary else INK, text_color_disabled="#78899E")

    def _select_month(self) -> None:
        month = ask_month(self, self.month, anchor=self.month_button, title="选择预算月份")
        if month is None or month == self.month or self.closing:
            return
        self._remember_draft()
        # 明细标题含年份和月份，切月时关闭旧明细，避免混淆两个月的账目。
        for detail in self.details.values():
            detail.destroy()
        self.details.clear()
        self.month = month
        self.month_button.configure(text=month)
        self.refresh()
        self._fill_amount(self.target.get())

    def _wrap_editing_label(self, event: tk.Event) -> None:
        # 事件尺寸以物理像素计，CTk 会再缩放 wraplength，先还原至逻辑宽度。
        width = max(1, int(event.width / self.editing_label._get_widget_scaling()) - 24)
        if width != self.editing_label.cget("wraplength"):
            self.editing_label.configure(wraplength=width)

    def _remember_draft(self) -> None:
        # 记住用户的原始输入（包括主动清空），让同月同目标能继续编辑。
        value = self.amount_entry.get()
        if value == self._loaded_amount:
            self._drafts.pop(self._editing, None)
        else:
            self._drafts[self._editing] = value

    def _select_target(self, name: str) -> None:
        self._remember_draft()
        self._fill_amount(name)

    def _edit_card(self, name: str) -> None:
        self._select_target(name)
        self.content._parent_canvas.yview_moveto(0)
        self.amount_entry.focus_set()

    def _fill_amount(self, name: str) -> None:
        self._editing = (self.month, name)
        self.target.set(name)
        self.editing_label.configure(text=f"正在编辑  {self.month} · {name}")
        self.amount_entry.delete(0, "end")
        progress = self._limits.get(name)
        self._loaded_amount = f"{progress.limit.amount:.2f}" if progress is not None else ""
        self.amount_entry.insert(0, self._drafts.get(self._editing, self._loaded_amount))
        self.delete_button.configure(state="normal" if progress is not None else "disabled")

    def _change_limit(self, action: Literal["save", "delete", "copy"]) -> None:
        name = self.target.get()
        tag = self._tags[name]
        month = self.month
        try:
            if action == "copy":
                self._remember_draft()
                count = self.store.copy_previous_budgets(month)
                message = f"已从上月补齐 {count} 项额度。" if count else "上月没有可补齐的额度。"
            elif action == "delete":
                if not messagebox.askyesno("删除额度", f"删除 {month} 的「{name}」额度？", parent=self) or self.closing:
                    return
                self.store.delete_budget(month, tag=tag)
                message = "额度已删除。"
            else:
                self.store.set_budget(month, Decimal(self.amount_entry.get().strip()), tag=tag)
                message = "额度已保存。"
        except (InvalidOperation, ValueError, sqlite3.Error, RuntimeError) as error:
            # 校验或写入失败仍保留额度草稿，方便用户直接修正或重试。
            self.status.configure(text="请输入有效的预算额度。" if isinstance(error, InvalidOperation) else str(error))
            return
        if action != "copy":
            self._drafts.pop((month, name), None)
        self.refresh(message)
        self._fill_amount(self.target.get())

    def refresh(self, message: str = "") -> None:
        old_details = self._detail_data
        self._detail_data = {}
        self._limits = {}
        for card in self.cards.winfo_children():
            card.destroy()
        try:
            snapshot = build_budget_snapshot(
                self.store.records, self.store.list_budgets(self.month), self.month,
                load_skipped_count=self.store.load_skipped_count,
            )
        except (sqlite3.Error, ValueError) as error:
            # 读取失败时禁用旧明细，避免把上一次结果展示成当前预算。
            self.status.configure(text=f"读取预算失败：{error}")
            self.excluded_button.configure(state="disabled", text=_EXCLUDED)
        else:
            tags = set(build_tag_candidates()) | {"未分类"}
            for record in self.store.records:
                tags.update(record.tags)
            # 已选对象或同月草稿即使暂时没有账目，也仍应出现在可选对象中。
            tags.update(name.removeprefix(_TAG_LABEL) for month, name in (*self._drafts, self._editing)
                        if month == self.month and name.startswith(_TAG_LABEL))
            for progress in snapshot.progress:
                tag = progress.limit.tag
                name = _TOTAL if tag is None else f"{_TAG_LABEL}{tag}"
                self._limits[name] = progress
                self._detail_data[name] = (progress.rows, progress.used)
                self._add_card(name, progress)
                if tag is not None:
                    tags.add(tag)
            if not self._limits:
                ctk.CTkLabel(self.cards, text="本月尚未设置预算。", font=_FONT, text_color=MUTED).pack(pady=30)
            self._tags = {_TOTAL: None, **{f"{_TAG_LABEL}{tag}": tag for tag in sorted(tags)}}
            self.target.set_values(self._tags)
            if self.target.get() not in self._tags:
                self._fill_amount(_TOTAL)
            self._detail_data[_EXCLUDED] = (snapshot.excluded_rows, snapshot.excluded_amount)
            self.excluded_button.configure(
                state="normal", text=f"{_EXCLUDED}  ¥ {snapshot.excluded_amount:,.2f}  ·  查看明细")
            skipped = f"已跳过 {snapshot.skipped_count} 笔无效日期或金额记录。" if snapshot.skipped_count else ""
            self.status.configure(text="  ".join(filter(None, (message, skipped))))
        self.delete_button.configure(state="normal" if self.target.get() in self._limits else "disabled")
        # 只重绘受影响的明细，避免不相干的写入重置页码或选中行。
        for name, detail in tuple(self.details.items()):
            if detail.closing or name not in self._detail_data:
                detail.destroy()
                del self.details[name]
            elif self._detail_data[name] != old_details.get(name):
                detail.refresh_rows(f"{self.month} · {name}", *self._detail_data[name])

    def _add_card(self, name: str, progress: BudgetProgress) -> None:
        state, color = _STATES[progress.state]
        card = ctk.CTkFrame(self.cards, fg_color="#FFFFFF", corner_radius=13,
                            border_width=1, border_color=BORDER)
        card.pack(fill="x", pady=(0, 8))
        card.columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text=name, font=_TITLE_FONT, text_color=INK,
                     wraplength=320, anchor="w", justify="left").grid(
                         row=0, column=0, sticky="w", padx=13, pady=(9, 0))
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=0, column=1, sticky="ne", padx=13, pady=(9, 0))
        self._button(actions, "调整额度", lambda: self._edit_card(name)).pack(side="left", padx=(0, 6))
        self._button(actions, "查看明细", lambda: self._open_detail(name)).pack(side="left")
        # 进度条在 100% 填满，超出金额和真实比例仍由文字完整显示。
        bar = ctk.CTkProgressBar(card, height=8, fg_color="#E8EEF5", progress_color=color)
        bar.set(float(min(progress.percent, Decimal(100)) / 100))
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=13, pady=(7, 5))
        metrics = (f"额度 ¥ {progress.limit.amount:,.2f}    已花 ¥ {progress.used:,.2f}    "
                   f"剩余 ¥ {progress.remaining:,.2f}")
        ctk.CTkLabel(card, text=metrics, font=_FONT, text_color=MUTED,
                     wraplength=420, anchor="w", justify="left").grid(
                         row=2, column=0, sticky="w", padx=13, pady=(0, 8))
        excess = f"  ¥ {progress.overspent:,.2f}" if progress.overspent else ""
        percent = f"{progress.percent:,.2f}%"
        # 舍入到阈值时补比较符号，显示说明和精确金额判档一致。
        if percent == "80.00%":
            if progress.state == BudgetState.AVAILABLE:
                percent = "<" + percent
            elif progress.percent > 80:
                percent = ">" + percent
        elif percent == "100.00%":
            if progress.state == BudgetState.NEAR_LIMIT:
                percent = "<" + percent
            elif progress.state == BudgetState.OVER_LIMIT:
                percent = ">" + percent
        ctk.CTkLabel(card, text=f"{percent}  ·  {state}{excess}", font=_FONT,
                     text_color=color, wraplength=170, justify="right").grid(
                         row=2, column=1, sticky="e", padx=13, pady=(0, 8))

    def _open_detail(self, name: str) -> None:
        if self._opening_detail or name not in self._detail_data:
            return
        detail = self.details.get(name)
        if detail is None or detail.closing:
            # CTk 的建窗会处理事件，建完后再核对月份与最新明细是否仍有效。
            month = self.month
            self._opening_detail = True
            try:
                detail = ChartDetails(self)
            finally:
                self._opening_detail = False
            if detail.closing or self.closing or month != self.month or name not in self._detail_data:
                detail.destroy()
                return
            self.details[name] = detail
        detail.show_rows(f"{self.month} · {name}", *self._detail_data[name])
