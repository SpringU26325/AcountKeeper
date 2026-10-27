"""主窗录入卡片及其标签变更通知；金额方向由主窗提交入口处理。"""

from __future__ import annotations

from datetime import date
import tkinter as tk
from typing import Callable

import customtkinter as ctk
import tag_picker

from calendar_picker import ask_date
from chip_widgets import TagChipsFrame
from popup_common import entry_surface as _entry_surface, make_toggle_button


def _field_column(master: ctk.CTkFrame, title: str) -> ctk.CTkFrame:
    """字段标题统一放在输入框上方。"""
    # 共用标题行高和间距，让日期、金额、类型、标签与备注的输入基线一致。
    field = ctk.CTkFrame(master, fg_color="transparent", corner_radius=0)
    field.columnconfigure(0, weight=1)
    ctk.CTkLabel(field, text=title, height=20, anchor="w",
                 font=("Microsoft YaHei UI", 11), text_color="#64748B").grid(
        row=0, column=0, sticky="ew", pady=(0, 6))
    return field


class _InputTagChips(TagChipsFrame):
    """仅为新增区通知标签变更，编辑控件不接入默认记忆或新增提示。"""

    def __init__(self, master: ctk.CTkBaseClass, on_change: Callable[[], None]) -> None:
        self._on_change = on_change
        super().__init__(master, placeholder_text="输入标签后回车，或点 ▼ 选择")

    def _schedule_relayout(self) -> None:
        super()._schedule_relayout()
        self._on_change()  # 手动增删后取消来源提示，药丸本身始终沿用统一样式。

    def clear_tags(self) -> None:
        picker = tag_picker._active_picker
        if picker is not None and picker.anchor is self.tag_entry:
            picker.close()  # 只结束本字段的面板，避免清空后旧选择再次回填。
        super().clear_tags()
        self._on_change()


class InputFrame(ctk.CTkFrame):
    """双行录入卡片，收支符号仍交给逻辑层处理。"""

    def __init__(self, master: ctk.CTk, add_callback: Callable[[], None],
                 clear_tags_callback: Callable[[], None] | None = None) -> None:
        super().__init__(master, corner_radius=14, fg_color="#FFFFFF",
                         border_width=1, border_color="#E0E8F0")
        self.date_var = tk.StringVar(value=date.today().isoformat())
        self.amount_type_var = tk.StringVar(value="支出")
        self.note_var = tk.StringVar()
        self.columnconfigure(0, weight=1)
        # 紧凑标题与上下留白给最小窗口保留记录行，不靠缩小输入框来腾空间。
        ctk.CTkLabel(self, text="记一笔", height=20, font=("Microsoft YaHei UI", 13, "bold"),
                     text_color="#243447", anchor="w").grid(
            row=0, column=0, sticky="ew", padx=16, pady=(8, 6))

        # 第一行让日期、金额分配弹性空间，收支与添加保持固定尺寸和输入框基线。
        first_row = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        first_row.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        first_row.columnconfigure((0, 1), weight=1, uniform="primary_field")
        date_field = _field_column(first_row, "日期")
        date_field.grid(row=0, column=0, sticky="new", padx=(0, 12))
        self.date_surface, self.date_entry = _entry_surface(date_field, self.date_var)
        self.date_surface.grid(row=1, column=0, sticky="ew")
        self.date_button = make_toggle_button(self.date_surface, self._pick_date)
        self.date_button.grid(row=0, column=1, padx=(0, 1), pady=1, sticky="e")

        amount_field = _field_column(first_row, "金额")
        amount_field.grid(row=0, column=1, sticky="new", padx=(0, 12))
        # 金额不用 textvariable，保留占位提示；“元”只是单位，不参与正负号转换。
        self.amount_surface, self.amount_entry = _entry_surface(
            amount_field, placeholder_text="请输入金额")
        self.amount_surface.grid(row=1, column=0, sticky="ew")
        ctk.CTkLabel(self.amount_surface, text="元", width=28, height=38,
                     font=("Microsoft YaHei UI", 11), text_color="#64748B").grid(
            row=0, column=1, padx=(0, 3), pady=1)
        type_field = _field_column(first_row, "收支类型")
        type_field.grid(row=0, column=2, sticky="n", padx=(0, 12))
        self.amount_type_button = ctk.CTkSegmentedButton(
            type_field, values=["支出", "收入"], variable=self.amount_type_var,
            width=120, height=40, dynamic_resizing=False, corner_radius=8,
            border_width=0, fg_color="#FFFFFF", font=("Microsoft YaHei UI", 11),
            selected_color="#CFE0F8", selected_hover_color="#BFD6F5",
            unselected_color="#F0F3F7", unselected_hover_color="#E2E8F0",
            text_color="#243447")
        self.amount_type_button.grid(row=1, column=0)
        self.add_button = ctk.CTkButton(
            first_row, text="添加记录", command=add_callback, width=104, height=40,
            corner_radius=8, fg_color="#2F80ED", hover_color="#256BC7",
            font=("Microsoft YaHei UI", 11))
        self.add_button.grid(row=0, column=3, sticky="se")

        # 第二行标签与备注按 2:3 分配；标签增加仅向下展开，不推动输入框和备注。
        second_row = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        # 收紧卡片留白，为最小窗口展开三行标签后保留表格的可视高度。
        second_row.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
        second_row.columnconfigure(0, weight=2, uniform="secondary_field")
        second_row.columnconfigure(1, weight=3, uniform="secondary_field")
        tags_field = ctk.CTkFrame(second_row, fg_color="transparent", corner_radius=0)
        tags_field.columnconfigure(0, weight=1)
        tags_field.grid(row=1, column=0, sticky="new", padx=(0, 12))
        # 来源提示与纯文本清空共享原有标题行，既不挤占输入宽度也不增加字段行数。
        # 两侧标题共用父容器的同一行，清空按钮的实际请求高度会同时作用于两侧输入基线。
        tags_header = ctk.CTkFrame(second_row, fg_color="transparent", corner_radius=0)
        tags_header.grid(row=0, column=0, sticky="ew", padx=(0, 12), pady=(0, 6))
        tags_header.columnconfigure(1, weight=1)
        ctk.CTkLabel(tags_header, text="标签（可选）", width=0, height=20,
                     font=("Microsoft YaHei UI", 11), text_color="#64748B").grid(row=0, column=0)
        self.tag_memory_hint = ctk.CTkLabel(
            tags_header, text="", width=0, height=20, anchor="w",
            font=("Microsoft YaHei UI", 10), text_color="#94A3B8")
        self.tag_memory_hint.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.clear_tags_button = ctk.CTkButton(
            tags_header, text="清空", width=44, height=20, border_width=0, corner_radius=4,
            fg_color="transparent", hover_color="#F0F3F7", text_color="#64748B",
            text_color_disabled="#CBD5E1", font=("Microsoft YaHei UI", 11), state="disabled",
            command=clear_tags_callback or self.clear_tags)
        self.clear_tags_button.grid(row=0, column=2, sticky="e")
        self.tag_chips = _InputTagChips(tags_field, self._tag_input_changed)
        self.tag_chips.grid(row=0, column=0, sticky="ew")
        self._last_tag_text = ""
        self.tag_chips.tag_entry.bind("<KeyRelease>", self._tag_entry_changed, add="+")
        for sequence in ("<<Paste>>", "<<Cut>>"):
            self.tag_chips.tag_entry.bind(sequence, self._queue_tag_entry_changed, add="+")
        ctk.CTkLabel(second_row, text="备注（可选）", height=20, anchor="w",
                     font=("Microsoft YaHei UI", 11), text_color="#64748B").grid(
            row=0, column=1, sticky="ew", pady=(0, 6))
        note_field = ctk.CTkFrame(second_row, fg_color="transparent", corner_radius=0)
        note_field.columnconfigure(0, weight=1)
        note_field.grid(row=1, column=1, sticky="new")
        self.note_surface, self.note_entry = _entry_surface(note_field, self.note_var)
        self.note_surface.grid(row=0, column=0, sticky="ew")
        # 只处理主页面入口的外侧圆角，避免共享chips在编辑窗里跟着改外观。
        for button in (self.date_button, self.tag_chips.tag_button):
            button.configure(background_corner_colors=("#F8FAFC", "#FFFFFF", "#FFFFFF", "#F8FAFC"))

    def _pick_date(self) -> None:
        """日历仍贴日期字段弹出，取消时保留原值。"""
        # 完整外框作为锚点，避免内嵌输入框的左侧留白使日历发生视觉偏移。
        picked = ask_date(self.winfo_toplevel(), self.date_var.get().strip(),
                          anchor=self.date_surface)
        if picked:
            self.date_var.set(picked)

    def get_tags(self) -> tuple[str, ...]:
        return self.tag_chips.get_tags()

    def collect_tags(self) -> tuple[str, ...]:
        # 提交前冲刷残留手输标签，保持原有多标签去重和拆分口径。
        return self.tag_chips.collect_tags()

    def clear_tags(self) -> None:
        self.tag_chips.clear_tags()


    def set_remembered_tags(self, tags: tuple[str, ...]) -> None:
        self.tag_chips.set_tags(tags)
        self.tag_memory_hint.configure(text="沿用上次" if tags else "")
        self._sync_tag_controls()

    def _sync_tag_controls(self) -> None:
        self._last_tag_text = self.tag_chips.tag_entry.get()
        populated = bool(self.tag_chips.get_tags() or self._last_tag_text)
        self.clear_tags_button.configure(state="normal" if populated else "disabled")

    def _tag_input_changed(self) -> None:
        self.tag_memory_hint.configure(text="")
        self._sync_tag_controls()

    def _tag_entry_changed(self, _event: tk.Event | None = None) -> None:
        if self.tag_chips.tag_entry.get() != self._last_tag_text:
            self._tag_input_changed()  # 光标键不改变内容时，保留沿用提示。

    def _queue_tag_entry_changed(self, _event: tk.Event) -> None:
        revision = self.tag_chips._revision
        # 虚拟粘贴/剪切事件先于文本变化；延期核对且不跨清空/方向切换更新提示。
        self.after_idle(lambda: self._tag_entry_changed()
                        if revision == self.tag_chips._revision else None)
