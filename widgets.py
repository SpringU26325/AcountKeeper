"""主页面及编辑窗共用的输入控件、工具栏与记录表格，只管理表现层状态。

输入控件收集金额大小和收支意图，符号转换及写入分别由 ui/dialogs 的提交入口处理。
RecordTableFrame 保存当前表格行的完整标签映射；Treeview 的标签值仅是显示摘要。
摘要和徽标预算共用 table_tag_badges.tag_layout，tooltip 直接读取完整元组。
表格相关验收入口为 _probe/probe_table_visuals.py，布局入口为 _probe/probe_main_layout.py。
"""

from __future__ import annotations

from datetime import date
import ctypes
from ctypes import wintypes
import tkinter as tk
from typing import Callable
from tkinter import ttk, font as tkfont

import customtkinter as ctk

from calendar_picker import ask_date
from file_picker import FileMenu
from popup_common import make_toggle_button
# 标签候选统一由 tag_prefs 算（就是它自己那份标签列表），
# 不再从数据库 DISTINCT 取历史标签：那条路会让用户临时输入的写法越积越多，
# 候选读取与偏好写入只在 tag_prefs 维护；UI 不缓存标签池，避免多处实现漂移。
from tag_picker import ask_tags
# 手输的多标签要按顿号拆开，拆分与清理规则（strip / 去空 / 首次出现去重）
# 新增区与编辑区共用一份拆分实现，避免手输标签的去空、去重口径漂移。
from tag_prefs import split_tag_input
from table_tag_badges import TagBadgeRenderer, tag_layout

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
        for button in self._tag_buttons.values():
            button.destroy()
        self._tag_buttons.clear()
        self._tags.clear()
        self.tag_entry.delete(0, "end")
        self._relayout_tags()

    def set_tags(self, tags: tuple[str, ...]) -> None:
        """用给定标签整体替换 chips，供编辑弹窗按记录值初始化。"""
        self.clear_tags()
        # 复用批量添加的去重、布局调度，确保初始化与用户输入遵循同一规则。
        self._add_tags(tags)

    def _pick_tags(self) -> None:
        """仅在 picker 返回完成结果时合并所选标签与输入框残留。"""
        picked = globals()["ask_tags"](
            self.winfo_toplevel(),
            self.tag_entry,
            self.tag_entry.get(),
            toggle_button=self.tag_button,
            selected_tags=self.get_tags(),
        )
        if picked is None:
            return
        # 选择器等待会处理事件，完成后再读手输残留；取消则不改当前输入。
        typed = split_tag_input(self.tag_entry.get())
        final = tuple(dict.fromkeys((*picked, *typed)))
        self.clear_tags()
        self._add_tags(final)


class InputFrame(ctk.CTkFrame):
    """双行录入卡片，收支符号仍交给逻辑层处理。"""

    def __init__(self, master: ctk.CTk, add_callback: Callable[[], None]) -> None:
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
        first_row.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 12))
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
        second_row.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 12))
        second_row.columnconfigure(0, weight=2, uniform="secondary_field")
        second_row.columnconfigure(1, weight=3, uniform="secondary_field")
        tags_field = _field_column(second_row, "标签（可选）")
        tags_field.grid(row=0, column=0, sticky="new", padx=(0, 12))
        # 只为主窗口启用提示，编辑区和选择器 footer 的预填交互保持原样。
        self.tag_chips = TagChipsFrame(
            tags_field, placeholder_text="输入标签后回车，或点 ▼ 选择")
        self.tag_chips.grid(row=1, column=0, sticky="ew")
        note_field = _field_column(second_row, "备注（可选）")
        note_field.grid(row=0, column=1, sticky="new")
        self.note_surface, self.note_entry = _entry_surface(note_field, self.note_var)
        self.note_surface.grid(row=1, column=0, sticky="ew")
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


class ToolbarFrame(ctk.CTkFrame):
    """分两行呈现全局工具与记录操作；业务和选中主键由主窗口回调管理。"""

    def __init__(
        self, master: ctk.CTk, filter_callback: Callable[..., None],
        refresh_callback: Callable[[], None], delete_callback: Callable[[], None],
        stats_callback: Callable[[], None], export_callback: Callable[[], None],
        backup_callback: Callable[[], None], chart_callback: Callable[[], None],
        open_folder_callback: Callable[[], None], edit_callback: Callable[[], None],
        import_callback: Callable[[], None] | None = None,
        batches_callback: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self.columnconfigure(0, weight=1)
        font_small = ("Microsoft YaHei UI", 12)
        neutral_button = dict(width=68, height=32, corner_radius=8, font=font_small,
                              fg_color="#E8EEF5", hover_color="#DDE7F2",
                              text_color="#475569")
        # 第一行区分全局操作；低频文件操作收进菜单，仍复用原有业务回调。
        heading = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        heading.columnconfigure(0, weight=1)
        ctk.CTkLabel(heading, text="账目记录", anchor="w",
                     font=("Microsoft YaHei UI", 13, "bold"),
                     text_color="#243447").grid(row=0, column=0, sticky="w")
        ctk.CTkButton(heading, text="统计", command=stats_callback,
                      **neutral_button).grid(row=0, column=1, padx=(0, 8))  # pyright: ignore[reportArgumentType]  # 参数已核验，混合字典展开丢失键值类型对应。
        ctk.CTkButton(heading, text="图表", command=chart_callback,
                      **neutral_button).grid(row=0, column=2, padx=(0, 8))  # pyright: ignore[reportArgumentType]  # 参数已核验，混合字典展开丢失键值类型对应。
        self._file_callbacks = {
            "导出 CSV": export_callback, "备份数据": backup_callback,
            "打开数据目录": open_folder_callback,
        }
        # 账单与批次共用文件菜单；隔离组件宿主不传业务回调时不显示无效入口。
        if import_callback is not None:
            self._file_callbacks["导入外部账单"] = import_callback
        if batches_callback is not None:
            self._file_callbacks["导入批次"] = batches_callback
        # 文件入口与日期/标签共用三角及定位；业务仍只通过原有回调执行。
        self.file_menu = FileMenu(heading, tuple(self._file_callbacks), self._run_file_action)
        self.file_menu.configure(fg_color="#E8EEF5")
        for button in (self.file_menu.label_button, self.file_menu.toggle_button):
            button.configure(fg_color="#E8EEF5", hover_color="#DDE7F2", text_color="#475569")
        self.file_menu.grid(row=0, column=3)

        # 第二行将搜索留在左侧，编辑/删除常驻右侧；只有搜索列吸收多余宽度。
        actions = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        actions.grid(row=1, column=0, sticky="ew")
        actions.columnconfigure(0, weight=1)
        self.search_entry = ctk.CTkEntry(
            actions, width=200, height=36, corner_radius=8, border_width=1,
            border_color="#D8E1EA", fg_color="#FFFFFF", font=font_small,
            placeholder_text="搜索日期/标签/备注")
        self.search_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        # 虚拟事件先于 Tk 插入/剪切执行，延后读取才能让鼠标粘贴同样实时筛选。
        self.search_entry.bind("<KeyRelease>", filter_callback, add=True)
        for sequence in ("<<Paste>>", "<<PasteSelection>>", "<<Cut>>", "<<Clear>>"):
            self.search_entry.bind(
                sequence, lambda _event: self.after_idle(filter_callback), add=True)
        self.refresh_button = ctk.CTkButton(
            actions, text="刷新", command=refresh_callback,
            **{**neutral_button, "height": 36})  # pyright: ignore[reportArgumentType]  # 参数已核验，高度覆盖后的混合字典同样丢失键值类型对应。
        self.refresh_button.grid(row=0, column=1)
        self.edit_button = ctk.CTkButton(
            actions, text="编辑选中", command=edit_callback, width=104, height=36,
            corner_radius=8, font=font_small, fg_color="#DCEAFE",
            hover_color="#CFE0F8", text_color="#245D9F", state="disabled")
        self.edit_button.grid(row=0, column=2, padx=(16, 8))
        self.delete_button = ctk.CTkButton(
            actions, text="删除选中", command=delete_callback, width=104, height=36,
            corner_radius=8, border_width=1, border_color="#E4C7C4",
            font=font_small, fg_color="#FFFFFF", hover_color="#FFF1EE",
            text_color="#B84035", state="disabled")
        self.delete_button.grid(row=0, column=3)
        self._record_actions_enabled = False

    def _run_file_action(self, action: str) -> None:
        # FileMenu已先隐藏并恢复三角，再进入模态业务，重复选择不依赖标题值变化。
        self._file_callbacks[action]()

    def set_record_actions_enabled(self, enabled: bool) -> None:
        if enabled == self._record_actions_enabled:
            return  # 选中事件可能重复投递，不必反复重画按钮。
        self._record_actions_enabled = enabled
        for button in (self.edit_button, self.delete_button):
            button.configure(state="normal" if enabled else "disabled")


def summarize_tags(tags: tuple[str, ...], width: int, font: tkfont.Font, scale: float = 1.0) -> str:
    """把共用布局结果转为原生 cell 摘要，N=0 时保留 +总数而不截断标签名。"""
    prefix, rest = tag_layout(tags, width, font, scale)
    # 原生值保留同一摘要语义；Canvas只改变视觉，不能靠解析字符串还原标签。
    return f"{prefix}{' ' if prefix else ''}+{rest}" if rest else prefix


class RecordTableFrame(ctk.CTkFrame):
    """承接 ui 的行插入，协调完整标签映射、原生摘要、徽标和悬停提示。"""

    def __init__(self, master: ctk.CTk, edit_callback: Callable[[tk.Event], None]) -> None:
        super().__init__(
            master,
            corner_radius=14,
            fg_color="#FFFFFF",
            border_width=1,
            border_color="#E0E8F0",
        )
        font_small = ("Microsoft YaHei UI", 11)
        # 底栏先占位，表格随后填剩余空间，最小窗口下编辑提示也不会被挤走。
        footer = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        footer.pack(side="bottom", fill="x", padx=12, pady=(0, 8))
        self.selection_label = ctk.CTkLabel(
            footer, text="请选择一条记录", height=20, font=font_small,
            text_color="#64748B")
        self.selection_label.pack(side="left")
        ctk.CTkLabel(footer, text="双击记录也可编辑", height=20, font=font_small,
                     text_color="#64748B").pack(side="right")
        table_inner = ctk.CTkFrame(self, fg_color="transparent")
        table_inner.pack(fill="both", expand=True, padx=10, pady=(8, 6))
        # 列 key 用 tags 而不是 category（#62）：这一列现在装的是多值标签串，
        # key 名与类型对齐后，以后任何人看到 "tags" 都不会再误以为它是单值类别。
        # 主键只保留在 iid 中；显示列删除 ID 不改变选择、双击和删除的行定位。
        columns = ("date", "amount", "tags", "note")
        # 仅复制本表格需要的clam元素，vista原生表头会忽略底色；不切全局主题。
        style = ttk.Style(self)
        for source in ("Treeview.field", "Treeheading.cell", "Treeheading.border"):
            name = "Account." + source
            if name not in style.element_names():
                style.element_create(name, "from", "clam", source)
        style.layout("Account.Treeview", [("Account.Treeview.field", {"sticky": "nswe", "children": [
            ("Treeview.padding", {"sticky": "nswe", "children": [("Treeview.treearea", {"sticky": "nswe"})]})]})])
        style.layout("Account.Treeview.Heading", [("Account.Treeheading.cell", {"sticky": "nswe"}),
            ("Account.Treeheading.border", {"sticky": "nswe", "children": [
                ("Treeheading.padding", {"sticky": "nswe", "children": [("Treeheading.text", {"sticky": "we"})]})]})])
        # 保留可更新的字体对象供测量/Canvas/tooltip 共用，参数由 _style_table 与原生行同步。
        self.tag_font = tkfont.Font(root=self, family="Microsoft YaHei UI", size=-12)
        self._style_table()
        self.tree = ttk.Treeview(
            table_inner,
            columns=columns,
            show="headings",
            selectmode="browse",
            style="Account.Treeview",
        )
        # 仅映射当前插入行的 iid→完整标签，值来自 store 缓存；筛选重建时清空，不解析 cell 文本。
        self.record_tags: dict[str, tuple[str, ...]] = {}
        self.tree.tag_configure("stripe", background="#F7FAFD")
        # show="headings" 表示隐藏默认的首列树形图标，只显示自定义表头。
        headings = (
            ("date", "日期", 120),
            ("amount", "金额", 120),
            # 表头及空标签口径不变；超宽标签仅在表现层生成摘要。
            ("tags", "标签", 140),
            ("note", "备注", 300),
        )
        for column, title, width in headings:
            self.tree.heading(column, text=title)
            self.tree.column(
                column,
                # 各列内容统一居中，与表头保持一致。
                width=width,
                anchor="center",
            )
        scrollbar = ttk.Scrollbar(
            table_inner,
            orient="vertical",
            command=self.tree.yview,
        )
        # 先建 tooltip 再建引用它的徽标层；滚动通知同时负责隐藏提示、同步滚动条和重绘覆盖层。
        self.tag_tooltip = TreeviewTagTooltip(self, scrollbar)
        self.tag_badges = TagBadgeRenderer(self, edit_callback)
        self.tree.configure(yscrollcommand=self.tag_tooltip.scrolled)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        # 双击保留为快捷入口，常驻编辑按钮让新用户也能发现此功能。
        self.tree.bind("<Double-1>", edit_callback, add="+")
        # 只让表格所在的第 0 行/列获得伸缩权重，保证窗口拉大时表格占满剩余空间。
        table_inner.rowconfigure(0, weight=1)
        table_inner.columnconfigure(0, weight=1)

    def set_selected_record(self, record_id: int | None) -> None:
        # 使用稳定的主键反馈选择对象，筛选/刷新清空选择时同时撤掉旧提示。
        self.selection_label.configure(
            text=f"已选中记录 ID：{record_id}" if record_id is not None else "请选择一条记录")

    def _style_table(self) -> None:
        scale = self._get_widget_scaling()
        # CTk使用逻辑像素，Tk负字号使用物理像素，避免表格被再次按系统点数放大。
        font = ("Microsoft YaHei UI", -max(1, round(12 * scale)))
        # 中英文字宽必须按原生行的同一字号测量，否则最大 N 与 Canvas 实际占宽会不一致。
        self.tag_font.configure(family=font[0], size=font[1])
        style = ttk.Style(self)
        # field元素仍会画边缘；三种边色置白，让卡片只保留外层的轻描边。
        style.configure("Account.Treeview", background="#FFFFFF", fieldbackground="#FFFFFF",
                        foreground="#334155", font=font, rowheight=round(32 * scale), borderwidth=0,
                        bordercolor="#FFFFFF", lightcolor="#FFFFFF", darkcolor="#FFFFFF")
        style.configure("Account.Treeview.Heading", background="#EAF2FA", foreground="#475569",
                        font=(*font, "bold"), relief="flat", borderwidth=0, padding=(0, round(7 * scale)))
        style.map("Account.Treeview", background=[("selected", "#D7E9FC")], foreground=[("selected", "#243447")])

    def _set_scaling(self, *args, **kwargs) -> None:
        super()._set_scaling(*args, **kwargs)
        # CTk 构造期也会触发缩放回调，字段存在后才同步原生表格并排入摘要重算。
        if hasattr(self, "tag_font"):
            self._style_table()  # 动态缩放必须同步测量字体，不能只放大CTk外框。
            if hasattr(self, "tag_tooltip"):
                self.tag_tooltip._layout()

    def set_record_tags(self, iid: str, tags: tuple[str, ...]) -> str:
        """保存完整标签并返回插入用摘要；列宽变化时也经此入口重新测量。"""
        self.record_tags[iid] = tags
        scale = self._get_widget_scaling()
        # bbox/列宽已经是物理像素，只缩放逻辑内边距及胶囊尺寸。
        return summarize_tags(tags, self.tree.column("tags", "width") - round(10 * scale), self.tag_font, scale)

    def clear_records(self) -> None:
        """供 ui 在删除旧行前调用：先使提示/映射失效，再隐藏旧绘制层。"""
        # 此入口只清表现层会话；Treeview 行仍由 ui 删除，账本缓存仍归 store 管理。
        self.tag_tooltip.clear()
        self.tag_badges.clear()  # 重建前隐藏旧Canvas，不能让同iid短暂显示上一轮内容。


def _tooltip_work_area(root: tk.Misc) -> tuple[int, int, int, int] | None:
    """读取 Windows 主窗所在屏幕的工作区；负坐标屏幕返回 None，首版不跨屏定位。"""
    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("screen", wintypes.RECT),
                    ("work", wintypes.RECT), ("flags", wintypes.DWORD)]
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    # 只查询主窗所属屏幕；64位句柄必须声明类型，不枚举或按鼠标选屏。
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    monitor = user32.MonitorFromWindow(root.winfo_id(), 2)
    info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
    if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    rect = info.work
    if rect.left < 0 or rect.top < 0:
        return None  # 首版不定位到负坐标屏幕，避免Tk把负位置解释成右/下边距。
    return rect.left, rect.top, rect.right, rect.bottom


def _tooltip_position(cell, size, work) -> tuple[int, int]:
    """用屏幕物理坐标的 cell 矩形、提示尺寸和工作区边界计算下方/上方位置。"""
    left, top, right, bottom = work
    x, y, _width, height = cell
    width, tip_height = size
    target_y = y + height + 6
    if target_y + tip_height > bottom - 8:
        target_y = y - tip_height - 6
    # 翻转后仍按同一个工作区钳制，不让边框盖进任务栏。
    return (max(left + 8, min(x, right - width - 8)),
            max(top + 8, min(target_y, bottom - tip_height - 8)))


class TreeviewTagTooltip:
    """控制溢出标签 cell 的 300ms 悬停及布局失效，自行管理原生 Tk 窗口和绑定。"""

    def __init__(self, table: RecordTableFrame, scrollbar: ttk.Scrollbar) -> None:
        self.table, self.tree, self.scrollbar = table, table.tree, scrollbar
        self.root = self.tree.winfo_toplevel()
        # 普通 hide 销毁提示窗口但保留控制器；它不是 CTkToplevel，不接入 dialog_lifecycle。
        self.window: tk.Toplevel | None = None
        self.show_job: str | None = None  # tree 注册的延迟显示任务，离开/交互时取消。
        self.idle_job: str | None = None  # tree 注册的摘要重算任务，刷新/永久退出时取消。
        self.current: str | None = None  # 悬停行 iid；列固定为 tags，同 iid 移动不重启计时。
        self.position = (0, 0)  # 始终是 Treeview 局部坐标，Canvas 事件须先转换。
        self.closed = False  # 永久退出守卫，避免销毁重入再次安排布局。
        self.bindings = []  # 每项保存注册控件、事件名、绑定 ID，销毁时在原控件上精确解绑。
        # 原生绑定追加安装，记录自己的ID；关闭时不影响编辑及其他订阅者。
        # 进入/离开由 cell 边界判断；点击、滚动、失焦、Unmap 隐藏，布局变化另排摘要重算。
        for widget, sequence, callback in (
            (self.tree, "<Motion>", self._motion), (self.tree, "<Leave>", self._leave),
            (self.tree, "<Configure>", self._layout), (self.tree, "<ButtonRelease-1>", self._layout),
            (self.tree, "<ButtonPress>", self.hide), (self.tree, "<Double-1>", self.hide),
            (self.tree, "<Unmap>", self.hide), (self.tree, "<Destroy>", self.destroy),
            (self.tree, "<MouseWheel>", self.hide),
            (self.root, "<FocusOut>", self.hide), (self.root, "<Unmap>", self.hide),
            (self.root, "<ButtonPress>", self.hide), (self.root, "<Configure>", self._root_layout),
        ):
            self.bindings.append((widget, sequence, tk.Misc.bind(widget, sequence, callback, add="+")))

    def _cell_at(self, x: int, y: int) -> str | None:
        """只返回当前点所在的溢出标签行；表头、空白、其他列及完整放下的标签均返回 None。"""
        iid = self.tree.identify_row(y)
        if not iid or self.tree.identify_region(x, y) != "cell":
            return None
        box = self.tree.bbox(iid, "tags")
        # identify_column有列边界命中余量；真实bbox优先，极窄标签列也能被悬停。
        if not box or not (box[0] <= x < box[0] + box[2] and box[1] <= y < box[1] + box[3]):
            return None
        tags = self.table.record_tags.get(iid, ())
        # 判定依据是完整标签而非摘要的 +N 字样，真实标签本身也可能含有这些字符。
        if tags and self.table.tag_font.measure("、".join(tags)) > box[2] - round(10 * self.table._get_widget_scaling()):
            return iid
        return None

    def _leave(self, event) -> None:
        # 从屏幕坐标反查真实目标，区分离开 cell 与跨入同 cell 的 Canvas 覆盖层。
        x, y = event.x_root - self.tree.winfo_rootx(), event.y_root - self.tree.winfo_rooty()
        target = self.tree.winfo_containing(event.x_root, event.y_root)
        # Treeview与覆盖Canvas之间的Leave不等于离开cell，保留原300ms任务。
        if (target is self.tree or target in self.table.tag_badges.canvases) and self._cell_at(x, y) == self.current:
            self.position = (x, y)
            return
        self.hide()

    def _motion(self, event: tk.Event) -> None:
        self.position = (event.x, event.y)
        state = event.state
        if not isinstance(state, int):
            self.hide()  # Tk事件可能携带非数字占位值；无法判断拖动状态时不启动提示。
            return
        if state & 0x700:
            self.hide()  # 按住鼠标拖动时不启动提示，避免拖列/拖选择中途冒窗。
            return
        iid = self._cell_at(*self.position)
        if iid == self.current:
            return  # 同cell内移动保留最初300ms计时，不反复推迟显示。
        self.hide()
        self.current = iid
        if iid is not None:
            self.show_job = self.tree.after(300, self._show)  # 延迟由控制器持有，不给每行各建一个任务。

    def _show(self) -> None:
        self.show_job = None  # 回调已消费句柄；显示前仍须确认行、鼠标位置和宿主可见性。
        iid = self.current
        if iid is None or not self.tree.winfo_viewable() or self._cell_at(*self.position) != iid:
            return
        try:
            work = _tooltip_work_area(self.root)
        except OSError:
            return  # 工作区读取失败只跳过提示，不用屏幕总高度冒充工作区。
        if work is None:
            return
        # tree 是真实创建父控件，但父控件销毁不替代 after/绑定清理，仍须走本控制器的 destroy。
        window = self.window = tk.Toplevel(self.tree, takefocus=0)
        window.withdraw()
        window.overrideredirect(True)
        # 每次现读完整元组，不能把可视摘要或上一次提示内容当成标签来源。
        label = tk.Label(window, text="、".join(self.table.record_tags[iid]),
                         font=self.table.tag_font, justify="left", bg="#FFFFFF", fg="#334155",
                         relief="flat", borderwidth=0, highlightthickness=1, highlightbackground="#D8E5F2",
                         padx=10, pady=7,
                         wraplength=max(1, min(420, work[2] - work[0] - 30)))
        label.pack()
        window.update_idletasks()
        if self.window is not window:
            return  # idle会处理销毁/刷新；不能继续操作已经失效的提示窗。
        try:
            box = self.tree.bbox(iid, "tags")
        except tk.TclError:
            self.hide()  # idle期间记录可能已删除，失效锚点必须连同隐藏窗口一起收尾。
            return
        if not box:
            self.hide()  # 行滚出视口时bbox为空字符串；不能继续取坐标或留下半成品提示窗。
            return
        cell_x, cell_y, cell_width, cell_height = box
        # bbox 是 tree 局部物理坐标，转到屏幕后才按主窗工作区翻转/钳制，不再乘 CTk 缩放。
        cell = (self.tree.winfo_rootx() + cell_x, self.tree.winfo_rooty() + cell_y, cell_width, cell_height)
        size = (min(label.winfo_reqwidth(), work[2] - work[0] - 16),
                min(label.winfo_reqheight(), work[3] - work[1] - 16))
        x, y = _tooltip_position(cell, size, work)
        window.geometry(f"{size[0]}x{size[1]}+{x}+{y}")
        window.deiconify()
        window.lift()

    def _cancel(self, name: str) -> None:
        job = getattr(self, name)
        if job is not None:
            setattr(self, name, None)  # 先清身份再取消 Tcl 任务，重复收尾不会拿旧句柄再次取消。
            try:
                self.tree.after_cancel(job)
            except tk.TclError:
                pass  # 父窗可能已先取消所属任务；重复收尾不能重新抛出销毁错误。

    def hide(self, _event=None) -> None:
        """取消显示并销毁提示窗口，保留绑定及布局任务，让控制器继续服务表格。"""
        self._cancel("show_job")
        self.current = None
        window, self.window = self.window, None  # 先移走引用，Destroy/idle 重入能识别旧提示已失效。
        if window is not None:
            window.destroy()

    def scrolled(self, first, last) -> None:
        self.hide()  # yscrollcommand也覆盖滚动条拖动及静止鼠标下的程序滚动。
        self.scrollbar.set(first, last)
        self.table.tag_badges.request()

    def clear(self) -> None:
        """刷新前取消显示和重算任务，并作废旧 iid 映射；不永久关闭控制器。"""
        self.hide()
        self._cancel("idle_job")
        self.table.record_tags.clear()  # 先作废旧iid映射，刷新后同ID不能接到旧标签。

    def _root_layout(self, event: tk.Event) -> None:
        # 主窗绑定也会收到子控件的几何事件；只认主窗自身，避免把表格等子控件布局误当成主窗变化。
        if event.widget is self.root:
            self._layout(event)

    def _layout(self, _event=None) -> None:
        # 旧提示位置/摘要宽度已失效；等本轮几何通知结束后再合并测量，避免边拖列边反复计算。
        self.hide()
        if not self.closed and self.idle_job is None:
            self.idle_job = self.tree.after_idle(self._reflow)

    def _reflow(self) -> None:
        self.idle_job = None
        # 本轮重算当前表格映射里的行（可含视口外行），Canvas 随后的 render 只扫描可见视口。
        # 必须从完整 tuple 重算，不能在已经缩略的 cell 文本上继续缩略；普通 Motion 不走此循环。
        for iid, tags in self.table.record_tags.items():
            if self.tree.exists(iid):
                self.tree.set(iid, "tags", self.table.set_record_tags(iid, tags))
        self.table.tag_badges.request()

    def destroy(self, _event=None) -> None:
        """永久关闭控制器：取消两类任务、销毁提示、清映射并按绑定 ID 精确解绑。"""
        if self.closed:
            return
        self.closed = True
        self.clear()
        for widget, sequence, binding in self.bindings:
            try:
                tk.Misc.unbind(widget, sequence, binding)
            except tk.TclError:
                pass  # 外部原生destroy也能进入此出口，已消失控件只做剩余收尾。
        self.bindings.clear()
