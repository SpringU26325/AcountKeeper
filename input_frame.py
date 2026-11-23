"""主窗录入卡片及其标签变更通知；金额方向由主窗提交入口处理。"""

from __future__ import annotations

from datetime import date
import tkinter as tk
from typing import Callable

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk
import tag_picker

from calendar_picker import ask_date
from chip_widgets import TagChipsFrame
from dialog_lifecycle import ManagedToplevel
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
        super().__init__(master, placeholder_text="输入标签后回车，或点 ▼ 选择", inline=True)

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
                 clear_tags_callback: Callable[[], None] | None = None, *,
                 budgets_available: bool = False) -> None:
        super().__init__(master, corner_radius=14, fg_color="#FFFFFF",
                         border_width=1, border_color="#E0E8F0")
        self.date_var = tk.StringVar(value=date.today().isoformat())
        self.amount_type_var = tk.StringVar(value="支出")
        self.note_var = tk.StringVar()
        self.budget_excluded_var = tk.BooleanVar(master=self)
        self._budget_messages: tuple[str, ...] = ()
        self._budget_details: ManagedToplevel | None = None
        self._opening_budget_details = False
        self.columnconfigure(0, weight=1)
        ctk.CTkLabel(self, text="记一笔", height=24, font=("Microsoft YaHei UI", 16, "bold"),
                     text_color="#172F54", anchor="w").grid(row=0, column=0, sticky="w", padx=16, pady=(5, 2))

        # 日期和金额吸收窗口余量，收支按钮与两个输入框对齐。
        first_row = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        first_row.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 4))
        first_row.columnconfigure(0, weight=3, uniform="primary_field")
        first_row.columnconfigure(1, weight=4, uniform="primary_field")
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
        # 原画布只承载这一张轮廓图，尺寸或焦点变化时更新同一个图元。
        self._amount_outline = self.amount_surface._canvas.create_image(0, 0, anchor="nw")
        self._amount_outline_style = None
        self._amount_outline_photo: ImageTk.PhotoImage | None = None
        tk.Misc.bind(self.amount_surface, "<Configure>", self._draw_amount_outline, add="+")
        self.amount_surface.grid(row=1, column=0, sticky="ew")
        # 焦点反馈画在现有外框上，保留内部金额框原有的键盘和占位行为。
        self.amount_entry.bind("<FocusIn>", lambda _event: self._set_amount_focus(True), add="+")
        self.amount_entry.bind("<FocusOut>", lambda _event: self._set_amount_focus(False), add="+")
        ctk.CTkLabel(self.amount_surface, text="元", width=28, height=38,
                     font=("Microsoft YaHei UI", 11), text_color="#64748B").grid(
            row=0, column=1, padx=(0, 8), pady=1)  # 内缩到圆角以内，单位底色不会盖住右侧描边。
        type_field = _field_column(first_row, "收支类型")
        type_field.grid(row=0, column=2, sticky="n")
        self.amount_type_button = ctk.CTkSegmentedButton(
            type_field, values=["支出", "收入"], variable=self.amount_type_var,
            width=120, height=40, dynamic_resizing=False, corner_radius=8,
            border_width=0, fg_color="#FFFFFF", font=("Microsoft YaHei UI", 11),
            selected_color="#CFE0F8", selected_hover_color="#BFD6F5",
            unselected_color="#F0F3F7", unselected_hover_color="#E2E8F0",
            text_color="#243447")
        self.amount_type_button.grid(row=1, column=0)
        # 第二行标签与备注按 2:3 分配；标签增加仅向下展开，不推动输入框和备注。
        second_row = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        # 收紧卡片留白，为最小窗口展开三行标签后保留表格的可视高度。
        second_row.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 4))
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
                     font=("Microsoft YaHei UI", 11), text_color="#64748B").grid(row=0, column=1, sticky="ew", pady=(0, 6))
        note_field = ctk.CTkFrame(second_row, fg_color="transparent", corner_radius=0)
        note_field.columnconfigure(0, weight=1)
        note_field.grid(row=1, column=1, sticky="new")
        self.note_surface, self.note_entry = _entry_surface(note_field, self.note_var)
        self.note_surface.grid(row=0, column=0, sticky="ew")

        # 底栏只显示保存摘要和明细入口，长内容另有视口，避免挤占账目表高度。
        ctk.CTkFrame(self, height=1, corner_radius=0, fg_color="#DCE6F3").grid(
            row=3, column=0, sticky="ew", padx=16)
        footer = ctk.CTkFrame(self, height=38, fg_color="transparent", corner_radius=0)
        footer.grid_propagate(False)
        footer.grid(row=4, column=0, sticky="ew", padx=16, pady=(2, 3))
        footer.columnconfigure(1, weight=1)
        footer.rowconfigure(0, weight=1)
        self.budget_notice_mark = ctk.CTkLabel(
            footer, text="✓", width=18, height=18, corner_radius=9, fg_color="#187045",
            text_color="#FFFFFF", font=("Microsoft YaHei UI", 11, "bold"))
        self._budget_feedback = ctk.CTkFrame(footer, fg_color="transparent")
        self.budget_notice = ctk.CTkLabel(self._budget_feedback, text="", text_color="#854D0E",
                                          font=("Microsoft YaHei UI", 12))
        self.budget_notice.pack(side="left", padx=(0, 8))
        self.budget_details_button = ctk.CTkButton(
            self._budget_feedback, text="查看全部", width=72, height=27, command=self._show_budget_details,
            fg_color="#E8EFF9", hover_color="#DCE7F3", text_color="#172F54", font=("Microsoft YaHei UI", 12))
        self.budget_details_button.pack(side="left")
        if budgets_available:
            self.budget_checkbox = ctk.CTkCheckBox(
                footer, text="不计入预算", variable=self.budget_excluded_var,
                width=0, height=20, checkbox_width=16, checkbox_height=16,
                border_width=2, border_color="#94A3B8", text_color="#475569",
                font=("Microsoft YaHei UI", 12))
            self.budget_checkbox.grid(row=0, column=2, sticky="e", padx=(8, 10))
            self.amount_type_var.trace_add("write", self._sync_budget_checkbox)
        self.add_button = ctk.CTkButton(
            footer, text="添加记录", command=add_callback, width=110, height=36,
            corner_radius=8, fg_color="#205FD4", hover_color="#1B4FB0",
            font=("Microsoft YaHei UI", 12, "bold"))
        self.add_button.grid(row=0, column=3, sticky="e")
        # 只处理主页面入口的外侧圆角，避免共享chips在编辑窗里跟着改外观。
        for button in (self.date_button, self.tag_chips.tag_button):
            button.configure(background_corner_colors=("#F8FAFC", "#FFFFFF", "#FFFFFF", "#F8FAFC"))

    def set_budget_notice(self, messages: tuple[str, ...]) -> None:
        if self._budget_details is not None:
            self._budget_details.destroy()
            self._budget_details = None
        self._budget_messages = messages
        if messages:
            self.budget_notice.configure(text=f"已保存 · {len(messages)} 项预算提醒")
            self.budget_notice_mark.grid(row=0, column=0, padx=(0, 5))
            self._budget_feedback.grid(row=0, column=1, sticky="w")
        elif self._budget_feedback.winfo_manager():
            self._budget_feedback.grid_forget()  # 清除网格参数，缩放后也不会显现已清除的摘要。
            self.budget_notice_mark.grid_forget()

    def _show_budget_details(self) -> None:
        messages = self._budget_messages
        if not messages or self._opening_budget_details:
            return
        if self._budget_details is not None and not self._budget_details.closing:
            self._budget_details.lift()
            return
        # 由用户主动打开完整内容，不使用 grab，记账窗口仍可操作。
        parent = self.winfo_toplevel()
        self._opening_budget_details = True
        try:
            window = ManagedToplevel(parent, fg_color="#F0F5FC")
        finally:
            self._opening_budget_details = False  # CTk 建窗时可能再次收到按钮事件。
        window.withdraw()
        self._budget_details = window
        window.title("预算提醒")
        window.transient(parent)
        window.geometry("510x300")
        ctk.CTkLabel(window, text=f"{len(messages)} 项预算提醒", font=("Microsoft YaHei UI", 17, "bold"),
                     text_color="#172F54").pack(anchor="w", padx=16, pady=(14, 6))
        details = ctk.CTkTextbox(window, wrap="word", fg_color="#FFFFFF", text_color="#854D0E",
                                 font=("Microsoft YaHei UI", 14), padx=10, pady=8, spacing3=5)
        details.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        details.insert("1.0", "\n".join(messages))
        details.configure(state="disabled")
        window.bind("<Escape>", lambda _event: window.destroy())
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        if messages is not self._budget_messages:
            window.destroy()  # CTk 建窗可处理事件，期间若有新提交，旧明细不得显现。
        window.finish_setup()
        if not window.closing:
            window.deiconify()

    def _sync_budget_checkbox(self, *_args: str) -> None:
        # 切到收入时清空本次标记，避免切回支出后误用上一种输入的勾选。
        spending = self.amount_type_var.get() == "支出"
        if not spending:
            self.budget_excluded_var.set(False)
        self.budget_checkbox.configure(state="normal" if spending else "disabled")

    def _set_amount_focus(self, focused: bool) -> None:
        self.amount_surface.configure(border_color="#2367DA" if focused else "#D8E1EA")
        self._draw_amount_outline()

    def _draw_amount_outline(self, _event: tk.Event | None = None) -> None:
        surface = self.amount_surface
        width, height = surface.winfo_width(), surface.winfo_height()
        if width <= 1 or height <= 1:
            return  # 首次布局前的占位尺寸不参与绘制。
        scale = surface._get_widget_scaling()
        radius = round(surface.cget("corner_radius") * scale)
        stroke = max(1, round(surface.cget("border_width") * scale))
        background, fill, border = (
            surface._apply_appearance_mode(surface.cget(name)) for name in ("bg_color", "fg_color", "border_color")
        )
        canvas = surface._canvas
        style = (width, height, radius, stroke, background, fill, border)
        if style != self._amount_outline_style:
            # 在实际像素的四倍尺寸上绘制完整圆角矩形，再按像素覆盖率缩回，四角共用同一个轮廓。
            factor = 4
            image = Image.new("RGB", (width * factor, height * factor), background)
            ImageDraw.Draw(image).rounded_rectangle(
                (0, 0, image.width - 1, image.height - 1),
                radius=radius * factor, fill=fill, outline=border, width=stroke * factor)
            self._amount_outline_photo = ImageTk.PhotoImage(
                image.resize((width, height), Image.Resampling.BOX), master=surface)
            canvas.itemconfigure(self._amount_outline, image=self._amount_outline_photo)
            self._amount_outline_style = style
        canvas.tag_raise(self._amount_outline)  # CTk 重新绘制底层时，金额框仍以完整轮廓为准。

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
        self._set_tag_hint("沿用上次" if tags else "")
        self._sync_tag_controls()

    def _set_tag_hint(self, text: str) -> None:
        """提示文案没变就不 configure：CTk 每次 configure 都会触发一次重绘。"""
        if self.tag_memory_hint.cget("text") != text:
            self.tag_memory_hint.configure(text=text)

    def _sync_tag_controls(self) -> None:
        self._last_tag_text = self.tag_chips.tag_entry.get()
        populated = bool(self.tag_chips.get_tags() or self._last_tag_text)
        state = "normal" if populated else "disabled"
        # 状态没变时跳过 configure，避免连续添加时每笔都白重绘一次按钮。
        if self.clear_tags_button.cget("state") != state:
            self.clear_tags_button.configure(state=state)

    def _tag_input_changed(self) -> None:
        self._set_tag_hint("")
        self._sync_tag_controls()

    def _tag_entry_changed(self, _event: tk.Event | None = None) -> None:
        if self.tag_chips.tag_entry.get() != self._last_tag_text:
            self._tag_input_changed()  # 光标键不改变内容时，保留沿用提示。

    def _queue_tag_entry_changed(self, _event: tk.Event) -> None:
        revision = self.tag_chips._revision
        # 虚拟粘贴/剪切事件先于文本变化；延期核对且不跨清空/方向切换更新提示。
        self.after_idle(lambda: self._tag_entry_changed()
                        if revision == self.tag_chips._revision else None)
