"""主界面工具栏，只管理展示及动作入口，业务行为由宿主回调注入。"""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from file_picker import FileMenu


class ToolbarFrame(ctk.CTkFrame):
    """分两行呈现全局工具与记录操作；业务和选中主键由主窗口回调管理。"""

    def __init__(
        self, master: ctk.CTk, filter_callback: Callable[..., None],
        refresh_callback: Callable[[], None], delete_callback: Callable[[], None],
        stats_callback: Callable[[], None], export_callback: Callable[[], None],
        backup_callback: Callable[[], None],
        open_folder_callback: Callable[[], None], edit_callback: Callable[[], None],
        import_callback: Callable[[], None] | None = None,
        batches_callback: Callable[[], None] | None = None,
        update_callback: Callable[[], None] | None = None,
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
        ctk.CTkButton(heading, text="财务分析", command=stats_callback,
                      **neutral_button).grid(row=0, column=1, padx=(0, 8))  # pyright: ignore[reportArgumentType]  # 参数已核验，混合字典展开丢失键值类型对应。
        self._file_callbacks = {
            "导出 CSV": export_callback, "备份数据": backup_callback,
            "打开数据目录": open_folder_callback,
        }
        # 账单与批次共用文件菜单；隔离组件宿主不传业务回调时不显示无效入口。
        if import_callback is not None:
            self._file_callbacks["导入外部账单"] = import_callback
        if batches_callback is not None:
            self._file_callbacks["导入批次"] = batches_callback
        if update_callback is not None:
            # 更新入口仍由主窗协调，工具栏不导入网络或事务逻辑。
            self._file_callbacks["检查更新"] = update_callback
        # 文件入口与日期/标签共用三角及定位；业务仍只通过原有回调执行。
        self.file_menu = FileMenu(heading, tuple(self._file_callbacks), self._run_file_action)
        self.file_menu.configure(fg_color="#E8EEF5")
        for button in (self.file_menu.label_button, self.file_menu.toggle_button):
            button.configure(fg_color="#E8EEF5", hover_color="#DDE7F2", text_color="#475569")
        self.file_menu.grid(row=0, column=2)

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
