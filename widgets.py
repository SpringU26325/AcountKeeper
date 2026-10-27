"""主页面及编辑窗共用的输入与标签控件，兼容导出工具栏和记录表格。

输入控件收集金额大小和收支意图，符号转换及写入由 ui/dialogs 提交入口处理。
标签选择器及编辑窗共用同一 TagChipsFrame 类；表格实现归 record_table。
验收入口为 _probe/probe_tag_memory.py 和 _probe/probe_main_layout.py。
"""

from __future__ import annotations

from datetime import date
import tkinter as tk
from typing import Callable

import customtkinter as ctk
import tag_picker

from calendar_picker import ask_date
from popup_common import make_toggle_button
# 标签候选统一由 tag_prefs 算（就是它自己那份标签列表），
# 不再从数据库 DISTINCT 取历史标签：那条路会让用户临时输入的写法越积越多，
# 候选读取与偏好写入只在 tag_prefs 维护；UI 不缓存标签池，避免多处实现漂移。
from tag_picker import ask_tags
# 手输的多标签要按顿号拆开，拆分与清理规则（strip / 去空 / 首次出现去重）
# 新增区与编辑区共用一份拆分实现，避免手输标签的去空、去重口径漂移。
from tag_prefs import split_tag_input
# 公开重导出保持既有调用的类身份；实现模块不反向依赖输入控件或主窗口。
from record_table import (
    RecordTableFrame as RecordTableFrame,
    TagBadgeRenderer as TagBadgeRenderer,
    TreeviewTagTooltip as TreeviewTagTooltip,
    summarize_tags as summarize_tags,
    tag_layout as tag_layout,
)
from toolbar_frame import ToolbarFrame as ToolbarFrame

# ---------- 标签 chips 区的几何常量（需求 3.14.3 形态 1） ----------
# 一颗 chip 的高度与圆角：28 + 圆角 14 刚好是「药丸」形，也比一行输入框（38）矮，
# 一眼能看出它是可以点掉的小标，而不是输入框。
_CHIP_HEIGHT = 28
_CHIP_CORNER_RADIUS = 14
_CHIP_GAP_X = 6  # 同一行里 chip 之间的水平间距
_CHIP_GAP_Y = 4  # 换行后上下两行 chip 之间的垂直间距


def _entry_surface(
    master: ctk.CTkBaseClass,
    textvariable: tk.StringVar | None = None,
    placeholder_text: str | None = None,
) -> tuple[ctk.CTkFrame, ctk.CTkEntry]:
    """日期、金额、标签及备注共用的输入外框。"""
    # 外框统一描边；内部输入框和附加按钮不再各画一圈边框，避免控件割裂。
    surface = ctk.CTkFrame(master, height=40, corner_radius=8, border_width=1,
                           border_color="#D8E1EA", fg_color="#F8FAFC")
    surface.columnconfigure(0, weight=1)
    entry = ctk.CTkEntry(
        surface, width=1, height=38, corner_radius=0, border_width=0,
        fg_color="#F8FAFC", text_color="#243447", font=("Microsoft YaHei UI", 12),
        textvariable=textvariable, placeholder_text=placeholder_text,
    )
    # 最小请求宽度不占满网格；内边距避开外框圆角，伸缩空间交给字段外框。
    entry.grid(row=0, column=0, sticky="ew", padx=(9, 6), pady=1)
    return surface, entry


def _field_column(master: ctk.CTkFrame, title: str) -> ctk.CTkFrame:
    """字段标题统一放在输入框上方。"""
    # 共用标题行高和间距，让日期、金额、类型、标签与备注的输入基线一致。
    field = ctk.CTkFrame(master, fg_color="transparent", corner_radius=0)
    field.columnconfigure(0, weight=1)
    ctk.CTkLabel(field, text=title, height=20, anchor="w",
                 font=("Microsoft YaHei UI", 11), text_color="#64748B").grid(
        row=0, column=0, sticky="ew", pady=(0, 6))
    return field


class TagChipsFrame(ctk.CTkFrame):
    """维护当前输入的标签副本；增删 chip 不修改账本记录或常用标签池。"""

    def __init__(
        self,
        master: ctk.CTkBaseClass,
        on_layout_change: Callable[[], None] | None = None,
        *,
        placeholder_text: str | None = None,
    ) -> None:
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self._tags: list[str] = []  # 保留输入/选择顺序，提交时导出 tuple，不向外暴露可变列表。
        self._tag_buttons: dict[str, ctk.CTkButton] = {}  # 名称同时是去重身份和控件查找键。
        self._chips_width = -1
        self._revision = 0  # 清空/换方向后，嵌套选择器返回的旧结果必须失效。
        # 高度重算只关心实际行数变化；宽度变化但仍排成相同行数时无需通知外层。
        self._layout_rows = 0
        self._on_layout_change = on_layout_change
        self.columnconfigure(0, weight=1)

        # 原生占位符不能绑定 textvariable；直接读控件，避免把提示文字当成标签。
        self.input_surface, self.tag_entry = _entry_surface(
            self, placeholder_text=placeholder_text)
        self.input_surface.grid(row=0, column=0, sticky="ew")
        self.tag_entry.bind("<Return>", lambda _event: self._commit_tag_input())

        self.tag_button = make_toggle_button(self.input_surface, self._pick_tags)
        self.tag_button.grid(row=0, column=1, padx=(0, 1), pady=1, sticky="e")

        # 半行宽字段更容易换行；视口最多三行，余下滚动，防止挤掉备注和表格。
        self.chips_view = ctk.CTkScrollableFrame(self, width=0, height=28, corner_radius=0,
                                               fg_color="transparent")
        # CTk 内部滚动条默认申请 200px 高；取消该最小请求，才能让一行标签只占一行。
        self.chips_view._scrollbar.configure(height=0)
        self.chips_view._scrollbar.grid_remove()
        self.chips_view.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.chips_frame = ctk.CTkFrame(self.chips_view, fg_color="transparent", corner_radius=0)
        self.chips_view.columnconfigure(0, weight=1)
        self.chips_frame.grid(row=0, column=0, sticky="ew")
        self.chips_frame.grid_remove()
        self.chips_view.grid_remove()
        self.chips_frame.bind("<Configure>", self._on_chips_configure)

    def _commit_tag_input(self) -> None:
        """把输入框里的文字冲刷成 chip，并清空输入框。"""
        self._add_tags(split_tag_input(self.tag_entry.get()))
        self.tag_entry.delete(0, "end")

    def _add_tag(self, name: str) -> bool:
        """加一颗 chip；名字为空或已存在时什么都不做。"""
        return self._add_tags((name,))

    def _add_tags(self, names: tuple[str, ...]) -> bool:
        """批量加 chip，并在整批完成后只安排一次流式重排。"""
        added = False
        for name in names:
            tag = (name or "").strip()
            if not tag or tag in self._tag_buttons:
                continue
            button = ctk.CTkButton(
                self.chips_frame,
                text=f"{tag} ×",
                command=lambda target=tag: self._remove_tag(target),
                # 按文字请求宽度排布，短标签不再占默认 140px，避免无意义换行。
                width=0,
                height=_CHIP_HEIGHT,
                corner_radius=_CHIP_CORNER_RADIUS,
                fg_color="#E3EAF2",
                hover_color="#D2DEE9",
                text_color="#243447",
                font=("Microsoft YaHei UI", 11),
            )
            self._tag_buttons[tag] = button
            self._tags.append(tag)
            added = True
        if added:
            self._schedule_relayout()
        return added

    def _remove_tag(self, name: str) -> None:
        """删除当前记录的一颗 chip，不触碰常用标签池。"""
        button = self._tag_buttons.pop(name, None)
        if button is None:
            return
        button.destroy()
        if name in self._tags:
            self._tags.remove(name)
        self._schedule_relayout()

    def _schedule_relayout(self) -> None:
        """等新 chip 完成几何测量后再重排，避免按未测量的 1px 宽度换行。"""
        self.chips_view.grid()
        self.chips_frame.grid()
        self.after_idle(self._relayout_tags)

    def _on_chips_configure(self, event: tk.Event) -> None:
        """容器宽度改变才重排，掐断高度变化造成的 Configure 自激。"""
        if event.width == self._chips_width:
            return
        self._chips_width = event.width
        self._relayout_tags()

    def _relayout_tags(self) -> None:
        """按当前宽度流式换行；仅行数变化时通知外层调整高度。"""
        if not self._tag_buttons:
            self.chips_view.grid_remove()
            self.chips_frame.grid_remove()
            rows = 0
        else:
            width = self._chips_available_width()
            column = 0
            row = 0
            used = 0
            for tag in self._tags:
                button = self._tag_buttons.get(tag)
                if button is None:
                    continue
                # 标签名完整保留，以控件请求宽度决定换行，不用字符个数猜中英文字宽。
                need = button.winfo_reqwidth() + _CHIP_GAP_X
                if column and width and used + need > width:
                    row += 1
                    column = 0
                    used = 0
                button.grid(
                    row=row,
                    column=column,
                    padx=(0, _CHIP_GAP_X),
                    pady=(0, _CHIP_GAP_Y),
                    sticky="w",
                )
                used += need
                column += 1
            rows = row + 1

        # 仅超出三行视口才显示滚动条，常见一两个标签不必占用滚动条的宽度。
        if rows > 3:
            self.chips_view._scrollbar.grid()
        else:
            self.chips_view._scrollbar.grid_remove()

        if rows != self._layout_rows:
            self.chips_view.configure(height=min(rows, 3) * (_CHIP_HEIGHT + _CHIP_GAP_Y))
            self._layout_rows = rows
            if self._on_layout_change is not None:
                self._on_layout_change()

    def _chips_available_width(self) -> int:
        """优先取已布局容器宽度；都只有初始 1px 时用 0 表示暂不换行。"""
        for widget in (self.chips_frame, self):
            width = widget.winfo_width()
            if width > 1:
                return width
        return 0

    def get_tags(self) -> tuple[str, ...]:
        """返回 chips 中的标签，不含输入框里尚未提交的文字。"""
        return tuple(self._tags)

    def collect_tags(self) -> tuple[str, ...]:
        """先冲刷输入框残留，再返回提交用的标签元组。"""
        self._commit_tag_input()
        return tuple(self._tags)

    def clear_tags(self) -> None:
        """清空 chips 与输入框，供新增记录成功后复位。"""
        self._revision += 1
        for button in self._tag_buttons.values():
            button.destroy()
        self._tag_buttons.clear()
        self._tags.clear()
        self.tag_entry.delete(0, "end")
        # 清空与回填常在同一事件内发生；空闲时再布局最终内容，避免同步隐藏Canvas后无法重新显示。
        self._schedule_relayout()

    def set_tags(self, tags: tuple[str, ...]) -> None:
        """用给定标签整体替换 chips，供编辑弹窗按记录值初始化。"""
        self.clear_tags()
        # 复用批量添加的去重、布局调度，确保初始化与用户输入遵循同一规则。
        self._add_tags(tags)

    def _pick_tags(self) -> None:
        """仅在 picker 返回完成结果时合并所选标签与输入框残留。"""
        revision = self._revision
        picked = globals()["ask_tags"](
            self.winfo_toplevel(),
            self.tag_entry,
            self.tag_entry.get(),
            toggle_button=self.tag_button,
            selected_tags=self.get_tags(),
        )
        if picked is None or revision != self._revision:
            return
        # 选择器等待会处理事件，完成后再读手输残留；取消则不改当前输入。
        typed = split_tag_input(self.tag_entry.get())
        final = tuple(dict.fromkeys((*picked, *typed)))
        self.clear_tags()
        self._add_tags(final)


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
