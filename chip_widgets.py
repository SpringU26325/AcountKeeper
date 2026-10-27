"""新增区与编辑区共用的标签控件；维护标签副本及布局，不写账本或偏好。"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

import customtkinter as ctk

from popup_common import entry_surface as _entry_surface, make_toggle_button
from tag_picker import ask_tags
from tag_prefs import split_tag_input

# ---------- 标签 chips 区的几何常量（需求 3.14.3 形态 1） ----------
# 一颗 chip 的高度与圆角：28 + 圆角 14 刚好是「药丸」形，也比一行输入框（38）矮，
# 一眼能看出它是可以点掉的小标，而不是输入框。
_CHIP_HEIGHT = 28
_CHIP_CORNER_RADIUS = 14
_CHIP_GAP_X = 6  # 同一行里 chip 之间的水平间距
_CHIP_GAP_Y = 4  # 换行后上下两行 chip 之间的垂直间距


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
