"""导入窗口的布局与字段/说明界面；用户操作通过回调交给门面。"""

from datetime import datetime
import tkinter as tk

import customtkinter as ctk

from dialog_lifecycle import ManagedToplevel
from import_preview_controls import PreviewControls, PreviewSelect


class ImportDialogView:
    def __init__(self, window, font, actions, *, file_name, search_text, only_unhandled):
        # window 仅作为 Tk 父级；业务状态由明确参数传入，事件只经 actions 回传。
        self.window, self.font, self.actions = window, font, actions
        self.file_name, self.search_text, self.only_unhandled = file_name, search_text, only_unhandled
        self.controls = PreviewControls(font)
        self._more, self.closed = False, False
        self.help_popup = None

    def build(self):
        # 卡片与底栏固定；只有中部两个面板滚动，避免操作入口被长列表挤走。
        self.window.grid_columnconfigure(0, weight=1)
        self.window.grid_rowconfigure(3, weight=1)
        heading = ctk.CTkFrame(self.window, fg_color="transparent")
        heading.grid(row=0, column=0, sticky="ew", padx=20, pady=(10, 0))
        heading.grid_columnconfigure(0, weight=1)
        self.controls.label(heading, self.file_name, anchor="w").grid(row=0, column=0, sticky="ew")
        # 说明只由显式按钮打开，避免初次弹窗打断字段核对。
        self.help_button = self.controls.button(heading, "使用说明", self.show_help, width=110,
            height=36, border_width=1, border_color="#8CB6ED", fg_color="#DCEAFE",
            hover_color="#CFE0F8", text_color="#245D9F")
        self.help_button.grid(row=0, column=1, sticky="e")
        # 当前步骤与下一步动作集中显示；说明放独立窗口，避免挤占长列表的浏览空间。
        guide_area = ctk.CTkFrame(heading, fg_color="#E8F1FC", border_color="#BED6F5", border_width=1, corner_radius=8)
        guide_area.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        guide_area.grid_columnconfigure(1, weight=1)
        self.guide_step = self.controls.label(guide_area, "① 核对字段", text_color="#1D5CA8", width=125)
        self.guide_step.grid(row=0, column=0, sticky="w", padx=8, pady=8)
        self.guide = self.controls.label(guide_area, "正在分析账单，请稍候", anchor="w", wraplength=650, justify="left")
        self.guide.grid(row=0, column=1, sticky="ew", padx=8)
        self.guide_button = self.controls.button(guide_area, "去处理", lambda: self.actions["view"]("待确认"), width=100, height=32, primary=True)
        self.guide_button.grid(row=0, column=2, sticky="e", padx=8, pady=6)
        self.cards = {}
        card_area = ctk.CTkFrame(self.window, fg_color="transparent")
        card_area.grid(row=1, column=0, sticky="ew", padx=18, pady=8)
        for index, view in enumerate(("将导入", "规则跳过", "待确认", "非交易行")):
            card_area.grid_columnconfigure(index, weight=1)
            button = self.controls.button(card_area, "", lambda v=view: self.actions["view"](v), height=54)
            button.grid(row=0, column=index, sticky="ew", padx=4)
            self.cards[view] = button
        self.banner = self.controls.label(self.window, "", anchor="w", wraplength=1100)
        self.banner.grid(row=2, column=0, sticky="ew", padx=20)
        middle = ctk.CTkFrame(self.window, fg_color="transparent")
        middle.grid(row=3, column=0, sticky="nsew", padx=18, pady=8)
        middle.grid_columnconfigure(1, weight=1)
        middle.grid_rowconfigure(0, weight=1)
        left = ctk.CTkFrame(middle, fg_color="white", corner_radius=14, width=300)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        left.grid_rowconfigure(2, weight=1)
        self.controls.label(left, "账单字段对应\n选择文件中的哪一列作为记账字段", anchor="w", justify="left").grid(row=0, column=0, sticky="w", padx=10, pady=6)
        self.saved_area = ctk.CTkScrollableFrame(left, height=72, width=282, fg_color="white",
            label_text="已保存映射", label_fg_color="#F0F4F8", label_text_color="#64748B", label_font=self.font)
        self.saved_area._scrollbar.configure(height=60)  # 避免默认滚动条最小高度撑出大片空白。
        self.saved_area.grid(row=1, column=0, sticky="ew", padx=6)
        self.saved_empty = self.controls.label(left, "暂无可复用映射，可直接核对下方字段。", wraplength=280)
        self.fields_area = ctk.CTkScrollableFrame(left, width=282, fg_color="white")
        self.fields_area.grid(row=2, column=0, sticky="nsew", padx=6)
        self.controls.label(left, "修改字段映射将重置待确认决定", wraplength=280).grid(row=3, column=0, padx=8)
        self.save_button = self.controls.button(left, "保存此映射，下次复用", self.actions["save"])
        self.save_button.grid(row=4, column=0, sticky="e", padx=10, pady=8)
        right = ctk.CTkFrame(middle, fg_color="white", corner_radius=14)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(5, weight=1)
        toolbar = ctk.CTkFrame(right, fg_color="white")
        toolbar.grid(row=0, column=0, sticky="ew", padx=10, pady=4)
        toolbar.grid_columnconfigure(0, weight=1)
        self.search_var = tk.StringVar(master=self.window, value=self.search_text)
        self.search_entry = ctk.CTkEntry(toolbar, textvariable=self.search_var, font=self.font,
            fg_color="#F8FAFC", border_color="#D8E1EA", text_color="#243447",
            placeholder_text="搜索行号、日期、金额、备注或处理结果")
        self.search_entry.grid(row=0, column=0, sticky="ew")
        self.controls.label(toolbar, "搜索：行号、日期、金额、备注或处理结果", anchor="w").grid(row=1, column=0, sticky="w")
        self._search_trace = self.search_var.trace_add("write", self.actions["search"])
        self.controls.button(toolbar, "清空", lambda: self.search_var.set(""), width=50).grid(row=0, column=1, padx=4)
        self.view_title = self.controls.label(right, "", anchor="w")
        self.view_title.grid(row=1, column=0, sticky="ew", padx=12)
        filters = ctk.CTkFrame(right, fg_color="white")
        filters.grid(row=2, column=0, sticky="ew", padx=10)
        self.only_var = tk.BooleanVar(master=self.window, value=self.only_unhandled)
        self.only_check = self.controls.check(filters, text="仅看未处理", variable=self.only_var,
            command=self.actions["filter"])
        self.only_check.pack(side="left")
        self.next_button = self.controls.button(filters, "定位下一笔未处理", self.actions["locate"], width=145)
        self.next_button.pack(side="left", padx=8)
        self.all_button = self.controls.button(filters, "查看全部", self.actions["all"], width=80)
        self.all_button.pack(side="right")
        self.progress_label = self.controls.label(right, "", anchor="w")
        self._build_pager(right)
        self.progress_label.grid(row=4, column=0, sticky="ew", padx=12)
        self.details = ctk.CTkScrollableFrame(right, fg_color="white")
        self.details.grid(row=5, column=0, sticky="nsew", padx=8, pady=4)
        self.details.grid_columnconfigure(0, weight=1)
        # 明细内统一路由；其他区域保留原滚动行为，不注册全局滚轮绑定。
        self.details._parent_canvas.configure(yscrollincrement=1)
        self.window.bind("<MouseWheel>", self.actions["wheel"], add="+")
        inspect = ctk.CTkFrame(right, fg_color="#F8FAFC", corner_radius=8)
        inspect.grid(row=6, column=0, sticky="ew", padx=10)
        self.inspect_title = self.controls.label(inspect, "单笔核对 · 点击上方记录查看", anchor="w")
        self.inspect_title.pack(fill="x", padx=8)
        self.raw_text = ctk.CTkTextbox(inspect, height=64, font=self.font, fg_color="#F8FAFC", text_color="#243447")
        self.raw_text.pack(fill="x", padx=4)
        self.row_hint = self.controls.label(inspect, "选择记录后，可查看原值和可用操作。", anchor="w", wraplength=590)
        self.row_hint.pack(fill="x", padx=8)
        row_bar = ctk.CTkFrame(right, fg_color="white")
        row_bar.grid(row=7, column=0, sticky="ew", padx=10, pady=6)
        self.income_button = self.controls.button(row_bar, "此笔入账", lambda: self.actions["row"]("入账"), width=80, primary=True)
        self.skip_button = self.controls.button(row_bar, "跳过此笔", lambda: self.actions["row"]("跳过"), width=80)
        self.income_button.pack(side="left")
        self.skip_button.pack(side="left", padx=8)
        self.duplicate_var = tk.BooleanVar(master=self.window)
        self.duplicate_check = self.controls.check(row_bar, text="仍要导入（只解除重复跳过）",
            variable=self.duplicate_var, command=self.actions["duplicate"])
        self.duplicate_check.pack(side="left")
        bottom = ctk.CTkFrame(self.window, fg_color="transparent")
        bottom.grid(row=4, column=0, sticky="ew", padx=20, pady=12)
        bottom.grid_columnconfigure(0, weight=1)
        self.footer = self.controls.label(bottom, "预览尚未写入账本", anchor="w", wraplength=720)
        self.footer.grid(row=0, column=0, sticky="ew")
        self.controls.button(bottom, "取消", self.actions["close"], width=80).grid(row=0, column=1, padx=8)
        self.submit_button = self.controls.button(bottom, "确认导入", self.actions["submit"], width=155, primary=True)
        self.submit_button.grid(row=0, column=2)

    def build_fields(self, headers, bill_rows, mapping, options):
        for widget in self.fields_area.winfo_children():
            widget.destroy()
        self.field_controls = {}
        self._field_columns = mapping.columns if mapping else {}
        names = [f"{header or '无列名'} · 第 {i + 1} 列" for i, header in enumerate(headers)]
        self._column_names = names
        sample_rows = [row for row in bill_rows
                       if mapping and row.row_number > (mapping.header_row or 0)]
        roles = (("date", "日期 *"), ("amount", "金额 *"), ("direction", "收支方向"),
            ("status", "状态"), ("trade_type", "交易类型"), ("order_id", "主单号"),
            ("counterparty", "交易对方"), ("product", "商品 / 说明"),
            ("original_note", "原备注"), ("payment", "支付方式"))
        if self._more:
            roles += (("secondary_order_id", "副单号"), ("category", "消费分类"))
        for index, (role, title) in enumerate(roles):
            self.controls.label(self.fields_area, title, anchor="w").grid(row=index * 3, column=0, sticky="w")
            bound = mapping.columns.get(role, ()) if mapping else ()
            empty = f"请选择{title.split()[0]}列" if role in ("date", "amount") else "不使用"
            value = names[bound[0]] if bound and bound[0] < len(names) else empty
            if len(bound) > 1:
                value = "多列：" + "、".join(str(i + 1) for i in bound)
            values = [empty, *names]
            if value not in values:
                values.append(value)
            control = PreviewSelect(self.fields_area, values=values, width=245, font=self.font,
                command=lambda text, r=role: self._field_changed(r, text))
            control.set(value)
            needs_review = role in (mapping.review_roles if mapping else ()) or role in ("date", "amount") and (not bound or any(i >= len(names) for i in bound))
            control.configure(border_color="#B7791F" if needs_review else "#D8E1EA")
            control.grid(row=index * 3 + 1, column=0, sticky="ew")
            self.field_controls[role] = control
            sample = next((" / ".join(str(row.values[i]) for i in bound if i < len(row.cells) and row.values[i] is not None)
                           for row in sample_rows if any(i < len(row.cells) and row.values[i] is not None for i in bound)), "")
            hint = ("请核对 · " if needs_review else "") + ("样例：" + sample[:42] if sample else "尚未选择来源列" if not bound else "此列暂无样例值")
            self.controls.label(self.fields_area, hint, anchor="w", wraplength=245, justify="left", text_color="#9B6B13" if needs_review else "#64748B").grid(
                row=index * 3 + 2, column=0, sticky="w", pady=(0, 8))
            if role in ("counterparty", "product", "original_note", "payment"):
                self.controls.button(self.fields_area, "多列", lambda r=role: self._multi_sources(r), width=45).grid(
                    row=index * 3 + 1, column=1, padx=2)
        row = len(roles) * 3
        self.controls.button(self.fields_area, "收起更多字段" if self._more else "更多字段", self._toggle_more).grid(row=row, column=0)
        # 重建控件时复用 Tcl 变量，避免旧变量的循环引用被工作线程 GC 回收。
        self.option_vars = getattr(self, "option_vars", {})
        for offset, (option, title) in enumerate((("include_counterparty", "备注含交易对方"),
                ("include_product", "备注含商品/说明"), ("include_original_note", "备注含原备注"),
                ("include_payment", "备注含支付方式"), ("include_time", "保留时间到备注 [HH:MM]")), 1):
            var = self.option_vars.get(option) or tk.BooleanVar(master=self.window)
            var.set(getattr(options, option))
            self.option_vars[option] = var
            self.controls.check(self.fields_area, text=title, variable=var,
                command=lambda key=option: self._note_changed(key)).grid(row=row + offset, column=0, sticky="w", pady=3)

    def _toggle_more(self):
        self._more = not self._more
        self.actions["rebuild_fields"]()

    def _field_changed(self, role, text):
        if text.startswith("多列："):
            return
        indices = () if text == "不使用" or text.startswith("请选择") else (self._column_names.index(text),)
        self.actions["role"](role, indices)

    def _note_changed(self, option):
        self.actions["note"](option, self.option_vars[option].get())

    def _multi_sources(self, role):
        popup = self._popup("选择备注来源列")
        area = ctk.CTkScrollableFrame(popup, width=440, height=230, fg_color="white")
        area.pack(fill="both", expand=True, padx=12)
        current = self._field_columns.get(role, ())
        variables = []
        for index, name in enumerate(self._column_names):
            var = tk.BooleanVar(master=popup, value=index in current)
            variables.append(var)
            self.controls.check(area, text=name, variable=var).pack(anchor="w", pady=4)
        def apply():
            self.actions["role"](role, tuple(i for i, v in enumerate(variables) if v.get()))
            popup.destroy()
        self.controls.button(popup, "应用字段映射", apply).pack(pady=10)
        popup.finish_setup()

    def _build_pager(self, parent):
        # 分页入口固定在筛选栏下方，不放入随明细滚动的容器。
        self.pager = ctk.CTkFrame(parent, fg_color="white")
        self.pager.grid(row=3, column=0, sticky="ew", padx=10, pady=3)
        self.pager.grid_columnconfigure(0, weight=1)
        self.page_label = self.controls.label(self.pager, "", anchor="w")
        self.page_label.grid(row=0, column=0, sticky="w")
        self.previous_page = self.controls.button(self.pager, "上一页", lambda: self.actions["page_delta"](-1), width=65)
        self.previous_page.grid(row=0, column=1, padx=3)
        self.next_page = self.controls.button(self.pager, "下一页", lambda: self.actions["page_delta"](1), width=65)
        self.next_page.grid(row=0, column=2, padx=3)
        self.page_var = tk.StringVar(master=self.window, value="1")
        self.page_entry = ctk.CTkEntry(self.pager, textvariable=self.page_var, width=55, font=self.font)
        self.page_entry.grid(row=0, column=3, padx=3)
        self.page_entry.bind("<Return>", lambda event: self.actions["page"](self.page_var.get()))
        self.jump_page = self.controls.button(self.pager, "跳页", lambda: self.actions["page"](
            self.page_var.get()), width=50)
        self.jump_page.grid(row=0, column=4)

    def update_pager(self, pages):
        self.pager.grid_remove() if pages.summary else self.pager.grid()
        self.page_label.configure(text=f"第 {pages.page} / {pages.total_pages} 页 · 匹配 {len(pages.rows)} 笔")
        self.page_var.set(str(pages.page))
        self.controls.button_state(self.previous_page, pages.page > 1)
        self.controls.button_state(self.next_page, pages.page < pages.total_pages)
        self.controls.button_state(self.jump_page, bool(pages.rows))
        self.page_entry.configure(state="normal" if pages.rows else "disabled")

    def refresh_status(self, session, historical):
        if session.busy and session.result is None:
            message = "正在重算，旧决定已作废" if historical else "正在分析账单"
        elif session.error:
            message = ("重算失败，旧快照仅供只读回看：" if historical else "分析/处理失败：") + session.error
        else:
            message = " · ".join(session.result.messages) if session.result else ""
        self.banner.configure(text=message, text_color="#9B6B13" if session.busy or session.error else "#64748B")
        self.banner.grid() if message else self.banner.grid_remove()
        for view, button in self.cards.items():
            count = session.counts[view] if session.result else "—"
            selected = view == session.current_view
            button.configure(text=f"{'✓ ' if selected else ''}{view} {count} 笔\n展开",
                fg_color="#E8F1FC" if selected else "white", border_width=2 if selected else 1,
                border_color="#2F80ED" if selected else "#D8E1EA")
        total, done, remaining = session.progress
        progress = f"本轮总量 {total} 笔 · 已处理 {done} 笔 · 剩余 {remaining} 笔" if total > 50 else ""
        if historical:
            old_total, old_done, old_remaining = session.obsolete.progress  # type: ignore[reportOptionalMemberAccess] # historical 分支保证 obsolete 非空。
            progress = f"历史回看：总量 {old_total} 笔 · 原已处理 {old_done} 笔 · 原剩余 {old_remaining} 笔（已作废）"
        self.progress_label.configure(text=progress)
        self.progress_label.grid() if progress else self.progress_label.grid_remove()
        self.submit_button.configure(text=f"确认导入（{session.counts['将导入']} 笔）")
        self.controls.button_state(self.submit_button, session.can_submit)
        if session.busy:
            next_step = "正在计算，请稍候"
        elif session.error:
            next_step = "处理失败，请检查错误提示；可修正字段重试或取消"
        else:
            missing = [title for role, title in (("date", "日期"), ("amount", "金额"))
                       if not session.mapping or not session.mapping.columns.get(role)]
            next_step = ("请先选择" + "、".join(missing) + "列" if missing else
                         f"还有 {remaining} 笔需要你处理，点击「去处理」逐笔或整组决定" if remaining else
                         "核对日期、金额与备注后，可确认导入" if session.can_submit else "请核对高亮字段及待确认记录")
        missing_required = not session.mapping or any(not session.mapping.columns.get(role) for role in ("date", "amount"))
        step = "① 核对字段" if missing_required or session.result is None else "② 处理待确认" if remaining else "③ 确认导入"
        self.guide_step.configure(text=step)
        self.guide.configure(text=next_step)
        self.controls.button_state(self.guide_button, session.result and remaining and not session.busy)
        self.footer.configure(text="预览尚未写入账本 · " + next_step)
        waiting = session.current_view == "待确认"
        self.only_check.configure(state="normal" if waiting else "disabled")
        self.next_button.configure(state="normal" if waiting and session.result and not session.busy else "disabled")
        self.save_button.configure(state="normal" if session.result and not session.busy else "disabled")

    def set_raw(self, text):
        self.raw_text.configure(state="normal")
        self.raw_text.delete("1.0", "end")
        self.raw_text.insert("1.0", text)
        self.raw_text.configure(state="disabled")

    def show_inspection(self, expanded):
        # 无记录待核对时只留提示标题，避免空详情挤占小窗口；有内容时完整展开。
        for widget, padding in ((self.raw_text, 4), (self.row_hint, 8)):
            if expanded:
                if not widget.winfo_manager():
                    widget.pack(fill="x", padx=padding)
            else:
                widget.pack_forget()

    def show_help(self):
        if self.closed:
            return
        if self.help_popup is not None:
            self.help_popup.lift()
            self.help_popup.focus_force()
            return
        popup = self.help_popup = self._popup("导入预览 · 使用说明")
        popup.geometry("680x440")
        self.controls.label(popup, "三步完成核对，预览中的选择尚未写入账本", anchor="w").pack(fill="x", padx=18, pady=(12, 6))
        steps = (
            ("① 核对字段对应", "看左侧日期、金额对应哪一列，并核对样例值。需要时修改下拉。\n修改字段会重算，并作废本轮已作出的决定。"),
            ("② 处理待确认记录", "「未知状态」不代表交易失败，请核对原始状态和收支方向。\n逐笔：点记录，看入账后内容，再选「此笔入账」或「跳过此笔」。\n整组：选择处理方式，核对结果，再点「确认处理本组」。\n整组包含筛选隐藏的记录；退款对的两笔需要一起决定。"),
            ("③ 确认导入", "待确认处理完、必需字段满足后，核对将导入笔数，再点底部确认。\n只选择处理方式尚未生效；关闭说明不会处理或跳过任何记录。"),
        )
        for title, body in steps:
            card = ctk.CTkFrame(popup, fg_color="white", corner_radius=8)
            card.pack(fill="x", padx=14, pady=4)
            self.controls.label(card, title, anchor="w", text_color="#1D5CA8").pack(fill="x", padx=10, pady=(5, 0))
            self.controls.label(card, body, anchor="w", justify="left", wraplength=625).pack(fill="x", padx=10, pady=(0, 6))
        self.help_close_button = self.controls.button(popup, "开始核对", popup.destroy, primary=True)
        self.help_close_button.pack(pady=10)
        popup.bind("<Escape>", lambda event: popup.destroy())
        popup.bind("<Destroy>", self._help_closed, add="+")
        # 显式说明不保存偏好，也不处理记录；关掉后可随时由按钮重看。
        popup.finish_setup()
        if not popup.closing:
            # 按实际尺寸居中并先完成映射，键盘关闭才能立即作用于说明窗。
            popup.update_idletasks()
            width, height = popup.winfo_width(), popup.winfo_height()
            x = max(8, min(self.window.winfo_rootx() + (self.window.winfo_width() - width) // 2, self.window.winfo_screenwidth() - width - 8))
            y = max(8, min(self.window.winfo_rooty() + (self.window.winfo_height() - height) // 2, self.window.winfo_screenheight() - height - 8))
            popup.wm_geometry(f"{width}x{height}+{x}+{y}")
            popup.lift()
            popup.focus_force()
            # CTk 在 Windows 建窗后约 10ms 恢复旧焦点；该恢复结束后说明再接收键盘。
            popup.after(20, popup.focus_force)  # type: ignore[reportArgumentType] # Tk 接受无参回调，存根对回调类型标注过窄。

    def _help_closed(self, event):
        if event.widget is self.help_popup:
            self.help_popup = None

    def _popup(self, title):
        popup = ManagedToplevel(self.window, fg_color="#F0F4F8")
        popup.title(title)
        popup.geometry("680x390")
        popup.transient(self.window)
        popup.protocol("WM_DELETE_WINDOW", popup.destroy)
        return popup

    def show_saved_candidates(self, candidates=(), error=None):
        for child in self.saved_area.winfo_children():
            child.destroy()
        if error is not None:
            self.saved_empty.grid_remove()
            self.saved_area.grid(row=1, column=0, sticky="ew", padx=6)
            self.controls.label(self.saved_area, "映射读取失败：" + str(error), wraplength=270).pack()
            return
        if not candidates:
            self.saved_area.grid_remove()
            self.saved_empty.grid(row=1, column=0, sticky="w", padx=10, pady=6)
        else:
            self.saved_empty.grid_remove()
            self.saved_area.grid(row=1, column=0, sticky="ew", padx=6)
        for candidate in candidates:
            line = ctk.CTkFrame(self.saved_area, fg_color="white")
            line.pack(fill="x", pady=2)
            name = candidate.mapping.name
            if candidate.missing_headers:
                name += " · 缺失：" + "、".join(candidate.missing_headers)
            self.controls.button(line, name, lambda m=candidate.mapping: self.actions["apply_mapping"](m), width=215).pack(side="left")
            self.controls.button(line, "×", lambda f=candidate.mapping.fingerprint: self.actions["delete_mapping"](f), width=30).pack(side="right")

    def show_save_mapping(self, headers):
        popup = self._popup("保存此映射")
        default = datetime.now().strftime("%m-%d %H:%M") + " " + " ".join(headers[:2])
        entry = ctk.CTkEntry(popup, width=610, font=self.font)
        entry.pack(padx=15, pady=20)
        entry.insert(0, default)
        notice = self.controls.label(popup, "同指纹覆盖需明确确认；最多保存 20 份。", wraplength=610)
        notice.pack(pady=8)
        button = None
        def save(overwrite=False):
            try:
                self.actions["save_mapping"](entry.get(), overwrite)
            except (ValueError, OSError) as error:
                notice.configure(text=str(error))
                if "同指纹" in str(error) and not overwrite:
                    button.configure(text="确认覆盖此映射", command=lambda: save(True))  # type: ignore[reportOptionalMemberAccess] # 本回调只经 button 的 command 触发，届时已绑定。
                return
            popup.destroy()
        button = self.controls.button(popup, "保存", save, primary=True)
        button.pack(pady=10)
        popup.finish_setup()

    def close(self):
        self.closed = True  # 先阻止重开说明，再撤销本视图的 Tcl 变量订阅。
        if self.help_popup is not None:
            self.help_popup.destroy()
        if hasattr(self, "_search_trace"):
            try:
                self.search_var.trace_remove("write", self._search_trace)
            except tk.TclError:
                pass  # 原生父窗销毁时 Tcl 变量可能已随解释器清理。

