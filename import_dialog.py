"""四区导入预览；只返回完整确认结果，不接入主页面或执行数据库写入。"""

from datetime import datetime
import tkinter as tk
from tkinter import ttk

import customtkinter as ctk

from dialog_lifecycle import ManagedToplevel
from import_session import Selection
from popup_common import anchored_position, make_toggle_button


class PreviewSelect(ctk.CTkFrame):
    """预览内共用选择框；统一实心三角与弹层，不依赖系统菜单主题。"""

    def __init__(self, master, *, values, command, font, width=180):
        super().__init__(master, width=width, height=34, corner_radius=8,
                         fg_color="#F8FAFC", border_color="#D8E1EA", border_width=1)
        self.values, self.command, self.font = tuple(values), command, font
        self.value, self.state, self.popup = self.values[0], "normal", None
        self.grid_columnconfigure(0, weight=1)
        self.label = ctk.CTkButton(self, text=self.value, command=self.open, font=font,
            width=max(60, width - 36), height=32, anchor="w", fg_color="transparent",
            hover_color="#D2DEE9", text_color="#243447", corner_radius=7)
        self.label.grid(row=0, column=0, sticky="ew", padx=(1, 0), pady=1)
        self.toggle = make_toggle_button(self, self.open, height=32)
        self.toggle.grid(row=0, column=1, padx=1, pady=1)
        self.bind("<Destroy>", self._destroyed, add="+")
        # 单个键盘焦点覆盖整个选择框，回车/空格展开，上下键在弹层内移动。
        self.label._canvas.configure(takefocus=1)
        for sequence in ("<Return>", "<space>", "<Down>"):
            self.label._canvas.bind(sequence, lambda event: self.open())

    def set(self, value):
        self.value = value
        self.label.configure(text=value)

    def get(self):
        return self.value

    def configure(self, **options):
        state = options.pop("state", None)
        super().configure(**options)
        if state is not None:
            self.state = state
            self.label.configure(state=state)
            self.toggle.configure(state=state)
            if state == "disabled":
                self.close()

    def cget(self, name):
        return self.state if name == "state" else super().cget(name)

    def open(self):
        if self.state == "disabled":
            return
        if self.popup is not None:
            self.close()
            return
        popup = self.popup = ManagedToplevel(self, fg_color="white")
        popup.withdraw()
        popup.overrideredirect(True)
        self.previous_grab = self.grab_current()
        area = ctk.CTkScrollableFrame(popup, fg_color="white", corner_radius=8,
            border_width=1, border_color="#D8E1EA", scrollbar_button_color="#C6D4DF")
        area.pack(fill="both", expand=True)
        self.items, self.area = [], area
        self.active = self.values.index(self.value) if self.value in self.values else 0
        for index, value in enumerate(self.values):
            item = ctk.CTkButton(area, text=("✓ " if value == self.value else "   ") + value,
                command=lambda i=index: self._pick(i), font=self.font, height=32,
                corner_radius=5, fg_color="white", hover_color="#E8F1FC",
                text_color="#243447", anchor="w")
            item.pack(fill="x", pady=1)
            self.items.append(item)
        popup.bind("<Down>", lambda event: self._move(1))
        popup.bind("<Up>", lambda event: self._move(-1))
        popup.bind("<Return>", lambda event: self._pick(self.active))
        popup.bind("<Escape>", lambda event: self.close())
        popup.bind("<ButtonPress-1>", self._outside, add="+")
        popup.bind("<FocusOut>", lambda event: popup.after_idle(self._focus_left), add="+")
        popup.finish_setup()
        popup.update_idletasks()
        # 与主页面一样以物理像素定位，避免高 DPI 下重复缩放或弹出屏幕。
        scale = self.winfo_height() / 34
        width, height = max(self.winfo_width(), round(245 * scale)), round(min(320, len(self.values) * 34 + 16) * scale)
        x, y = anchored_position((self.winfo_rootx(), self.winfo_rooty(), self.winfo_height()),
            (width, height), (self.winfo_screenwidth(), self.winfo_screenheight()))
        popup.wm_geometry(f"{width}x{height}+{x}+{y}")
        popup.deiconify()
        popup.grab_set()
        popup.focus_force()
        self.toggle.configure(text="▲")
        self._move(0)

    def _move(self, delta):
        self.active = (self.active + delta) % len(self.items)
        for index, item in enumerate(self.items):
            item.configure(fg_color="#E8F1FC" if index == self.active else "white")
        self.area._parent_canvas.yview_moveto(self.active / max(1, len(self.items)))
        return "break"

    def _pick(self, index):
        value = self.values[index]
        self.close()
        self.set(value)
        self.command(value)
        return "break"

    def _outside(self, event):
        popup = self.popup
        if popup and not (popup.winfo_rootx() <= event.x_root < popup.winfo_rootx() + popup.winfo_width()
                          and popup.winfo_rooty() <= event.y_root < popup.winfo_rooty() + popup.winfo_height()):
            self.close()
            return "break"

    def _focus_left(self):
        if self.popup and (self.focus_get() is None or self.focus_get().winfo_toplevel() is not self.popup):
            self.close()

    def close(self):
        popup, self.popup = self.popup, None
        if popup is not None:
            # 只释放本弹层的 grab，恢复原先仍存活的父弹窗；销毁时也走同一收尾。
            if self.grab_current() is popup:
                popup.grab_release()
            popup.destroy()
            if self.previous_grab is not None and self.previous_grab.winfo_exists():
                self.previous_grab.grab_set()
            if self.winfo_exists():
                self.toggle.configure(text="▼")
                self.label._canvas.focus_set()
        return "break"

    def _destroyed(self, event):
        if event.widget is self:
            self.close()

    def destroy(self):
        self.close()
        super().destroy()


class ImportDialog(ManagedToplevel):
    def __init__(self, parent, session, *, on_confirm=None, mapping_path=None):
        super().__init__(parent, fg_color="#F0F4F8")
        self.session, self.on_confirm, self.mapping_path = session, on_confirm, mapping_path
        self.accepted_result = None
        self.title("导入外部账单")
        self.geometry("1220x740")
        self.minsize(980, 660)
        self.font = ("Microsoft YaHei UI", 12)
        self._poll_job = self._render_job = None
        self._render_id = self._mapping_key = 0
        self._ending, self._more = False, False
        self.row_locations, self.group_controls, self.pair_controls = {}, {}, {}
        self._sections, self._trees, self._row_controls = {}, {}, []
        self._preview_target = self._preview_ready = None
        self._preview_result = None
        self._selection_number = None
        self._scroll_reset = False
        self._advance_after_render = False
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Destroy>", self._destroyed_event, add="+")
        try:
            self._build()
            self.finish_setup()
            self.refresh()
            self._ensure_poll()
        except Exception:
            # 构建失败也必须释放线程及 Tk 任务，不能留下半个预览窗。
            self.finish_setup()
            self.destroy()
            raise

    def _label(self, parent, text="", **options):
        options.setdefault("height", 22)
        options.setdefault("text_color", "#243447")
        return ctk.CTkLabel(parent, text=text, font=self.font, **options)

    def _check(self, parent, **options):
        return ctk.CTkCheckBox(parent, font=self.font, fg_color="#2F80ED", hover_color="#256BC7",
                              border_color="#90A4AE", text_color="#243447", **options)

    def _button(self, parent, text, command, **options):
        primary = options.pop("primary", False)
        options.setdefault("fg_color", "#2F80ED" if primary else "#F0F4F8")
        options.setdefault("hover_color", "#256BC7" if primary else "#D2DEE9")
        options.setdefault("text_color", "white" if primary else "#243447")
        button = ctk.CTkButton(parent, text=text, command=command, font=self.font,
                              corner_radius=8, **options)
        button.enabled_color = options["fg_color"]
        return button

    def _button_state(self, button, enabled):
        # CTk 默认只灰掉文字；同时弱化底色，避免禁用的蓝按钮看起来仍能提交。
        button.configure(state="normal" if enabled else "disabled",
                         fg_color=button.enabled_color if enabled else "#E5EBF1")

    def _build(self):
        # 卡片与底栏固定；只有中部两个面板滚动，避免操作入口被长列表挤走。
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        heading = ctk.CTkFrame(self, fg_color="transparent")
        heading.grid(row=0, column=0, sticky="ew", padx=20, pady=(10, 0))
        heading.grid_columnconfigure(0, weight=1)
        self._label(heading, self.session.bill.file_name, anchor="w").grid(row=0, column=0, sticky="ew")
        self._label(heading, "① 核对字段对应  →  ② 处理待确认记录  →  ③ 确认导入", anchor="e").grid(row=0, column=1, sticky="e")
        self.guide = self._label(heading, "正在分析账单，请稍候", anchor="w")
        self.guide.grid(row=1, column=0, sticky="ew")
        self.guide_button = self._button(heading, "去处理", lambda: self.switch_view("待确认"), width=80)
        self.guide_button.grid(row=1, column=1, sticky="e", padx=8)
        self.cards = {}
        card_area = ctk.CTkFrame(self, fg_color="transparent")
        card_area.grid(row=1, column=0, sticky="ew", padx=18, pady=8)
        for index, view in enumerate(("将导入", "规则跳过", "待确认", "非交易行")):
            card_area.grid_columnconfigure(index, weight=1)
            button = self._button(card_area, "", lambda v=view: self.switch_view(v), height=54)
            button.grid(row=0, column=index, sticky="ew", padx=4)
            self.cards[view] = button
        self.banner = self._label(self, "", anchor="w", wraplength=1100)
        self.banner.grid(row=2, column=0, sticky="ew", padx=20)
        middle = ctk.CTkFrame(self, fg_color="transparent")
        middle.grid(row=3, column=0, sticky="nsew", padx=18, pady=8)
        middle.grid_columnconfigure(1, weight=1)
        middle.grid_rowconfigure(0, weight=1)
        left = ctk.CTkFrame(middle, fg_color="white", corner_radius=14, width=300)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        left.grid_rowconfigure(2, weight=1)
        self._label(left, "账单字段对应\n选择文件中的哪一列作为记账字段", anchor="w", justify="left").grid(row=0, column=0, sticky="w", padx=10, pady=6)
        self.saved_area = ctk.CTkScrollableFrame(left, height=72, width=282, fg_color="white",
            label_text="已保存映射", label_fg_color="#F0F4F8", label_text_color="#64748B", label_font=self.font)
        self.saved_area._scrollbar.configure(height=60)  # 避免默认滚动条最小高度撑出大片空白。
        self.saved_area.grid(row=1, column=0, sticky="ew", padx=6)
        self.saved_empty = self._label(left, "暂无可复用映射，可直接核对下方字段。", wraplength=280)
        self.fields_area = ctk.CTkScrollableFrame(left, width=282, fg_color="white")
        self.fields_area.grid(row=2, column=0, sticky="nsew", padx=6)
        self._label(left, "修改字段映射将重置待确认决定", wraplength=280).grid(row=3, column=0, padx=8)
        self.save_button = self._button(left, "保存此映射，下次复用", self.save_mapping)
        self.save_button.grid(row=4, column=0, sticky="e", padx=10, pady=8)
        right = ctk.CTkFrame(middle, fg_color="white", corner_radius=14)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(4, weight=1)
        toolbar = ctk.CTkFrame(right, fg_color="white")
        toolbar.grid(row=0, column=0, sticky="ew", padx=10, pady=4)
        toolbar.grid_columnconfigure(0, weight=1)
        self.search_var = tk.StringVar(master=self, value=self.session.search_text)
        self.search_entry = ctk.CTkEntry(toolbar, textvariable=self.search_var, font=self.font,
            fg_color="#F8FAFC", border_color="#D8E1EA", text_color="#243447",
            placeholder_text="搜索行号、日期、金额、备注或处理结果")
        self.search_entry.grid(row=0, column=0, sticky="ew")
        self._label(toolbar, "搜索：行号、日期、金额、备注或处理结果", anchor="w").grid(row=1, column=0, sticky="w")
        self._search_trace = self.search_var.trace_add("write", self._search_changed)
        self._button(toolbar, "清空", lambda: self.search_var.set(""), width=50).grid(row=0, column=1, padx=4)
        self.view_title = self._label(right, "", anchor="w")
        self.view_title.grid(row=1, column=0, sticky="ew", padx=12)
        filters = ctk.CTkFrame(right, fg_color="white")
        filters.grid(row=2, column=0, sticky="ew", padx=10)
        self.only_var = tk.BooleanVar(master=self, value=self.session.only_unhandled)
        self.only_check = self._check(filters, text="仅看未处理", variable=self.only_var,
            command=self._filter_changed)
        self.only_check.pack(side="left")
        self.next_button = self._button(filters, "定位下一笔未处理", self.locate_next, width=145)
        self.next_button.pack(side="left", padx=8)
        self.all_button = self._button(filters, "查看全部", self._show_all, width=80)
        self.all_button.pack(side="right")
        self.progress_label = self._label(right, "", anchor="w")
        self.progress_label.grid(row=3, column=0, sticky="ew", padx=12)
        self.details = ctk.CTkScrollableFrame(right, fg_color="white")
        self.details.grid(row=4, column=0, sticky="nsew", padx=8, pady=4)
        self.details.grid_columnconfigure(0, weight=1)
        inspect = ctk.CTkFrame(right, fg_color="#F8FAFC", corner_radius=8)
        inspect.grid(row=5, column=0, sticky="ew", padx=10)
        self.inspect_title = self._label(inspect, "单笔核对 · 点击上方记录查看", anchor="w")
        self.inspect_title.pack(fill="x", padx=8)
        self.raw_text = ctk.CTkTextbox(inspect, height=64, font=self.font, fg_color="#F8FAFC", text_color="#243447")
        self.raw_text.pack(fill="x", padx=4)
        self.row_hint = self._label(inspect, "选择记录后，可查看原值和可用操作。", anchor="w", wraplength=590)
        self.row_hint.pack(fill="x", padx=8)
        row_bar = ctk.CTkFrame(right, fg_color="white")
        row_bar.grid(row=6, column=0, sticky="ew", padx=10, pady=6)
        self.income_button = self._button(row_bar, "此笔入账", lambda: self._row_action("入账"), width=80, primary=True)
        self.skip_button = self._button(row_bar, "跳过此笔", lambda: self._row_action("跳过"), width=80)
        self.income_button.pack(side="left")
        self.skip_button.pack(side="left", padx=8)
        self.duplicate_var = tk.BooleanVar(master=self)
        self.duplicate_check = self._check(row_bar, text="仍要导入（只解除重复跳过）",
            variable=self.duplicate_var, command=self._duplicate_changed)
        self.duplicate_check.pack(side="left")
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=4, column=0, sticky="ew", padx=20, pady=12)
        bottom.grid_columnconfigure(0, weight=1)
        self.footer = self._label(bottom, "预览尚未写入账本", anchor="w", wraplength=720)
        self.footer.grid(row=0, column=0, sticky="ew")
        self._button(bottom, "取消", self.destroy, width=80).grid(row=0, column=1, padx=8)
        self.submit_button = self._button(bottom, "确认导入", self.submit, width=155, primary=True)
        self.submit_button.grid(row=0, column=2)
        self._style_table()
        self._build_fields()

    def _style_table(self):
        # 只配置预览专属样式；复制 clam 表头元素，与主页面一致且不切换全局主题。
        style = ttk.Style(self)
        for source in ("Treeview.field", "Treeheading.cell", "Treeheading.border"):
            name = "ImportPreview." + source
            if name not in style.element_names():
                style.element_create(name, "from", "clam", source)
        style.layout("ImportPreview.Treeview", [("ImportPreview.Treeview.field", {"sticky": "nswe", "children": [
            ("Treeview.padding", {"sticky": "nswe", "children": [("Treeview.treearea", {"sticky": "nswe"})]})]})])
        style.layout("ImportPreview.Treeview.Heading", [("ImportPreview.Treeheading.cell", {"sticky": "nswe"}),
            ("ImportPreview.Treeheading.border", {"sticky": "nswe", "children": [
                ("Treeheading.padding", {"sticky": "nswe", "children": [("Treeheading.text", {"sticky": "we"})]})]})])
        scale = self.details._get_widget_scaling()
        font = ("Microsoft YaHei UI", -round(12 * scale))
        style.configure("ImportPreview.Treeview", font=font, rowheight=round(32 * scale), borderwidth=0,
                        background="white", fieldbackground="white", foreground="#334155",
                        bordercolor="white", lightcolor="white", darkcolor="white")
        style.configure("ImportPreview.Treeview.Heading", font=(*font, "bold"), background="#EAF2FA",
                        foreground="#475569", relief="flat", borderwidth=0, padding=(0, round(7 * scale)))
        style.map("ImportPreview.Treeview", background=[("selected", "#D7E9FC")], foreground=[("selected", "#243447")])

    def _build_fields(self):
        for widget in self.fields_area.winfo_children():
            widget.destroy()
        self.field_controls = {}
        session = self.session
        headers = session.headers()
        names = [f"{header or '无列名'} · 第 {i + 1} 列" for i, header in enumerate(headers)]
        self._column_names = names
        sample_rows = [row for row in session.bill.rows
                       if session.mapping and row.row_number > (session.mapping.header_row or 0)]
        roles = (("date", "日期 *"), ("amount", "金额 *"), ("direction", "收支方向"),
            ("status", "状态"), ("trade_type", "交易类型"), ("order_id", "主单号"),
            ("counterparty", "交易对方"), ("product", "商品 / 说明"),
            ("original_note", "原备注"), ("payment", "支付方式"))
        if self._more:
            roles += (("secondary_order_id", "副单号"), ("category", "消费分类"))
        for index, (role, title) in enumerate(roles):
            self._label(self.fields_area, title, anchor="w").grid(row=index * 3, column=0, sticky="w")
            bound = session.mapping.columns.get(role, ()) if session.mapping else ()
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
            needs_review = role in (session.mapping.review_roles if session.mapping else ()) or role in ("date", "amount") and (not bound or any(i >= len(names) for i in bound))
            control.configure(border_color="#B7791F" if needs_review else "#D8E1EA")
            control.grid(row=index * 3 + 1, column=0, sticky="ew")
            self.field_controls[role] = control
            sample = next((" / ".join(str(row.values[i]) for i in bound if i < len(row.cells) and row.values[i] is not None)
                           for row in sample_rows if any(i < len(row.cells) and row.values[i] is not None for i in bound)), "")
            hint = ("请核对 · " if needs_review else "") + ("样例：" + sample[:42] if sample else "尚未选择来源列" if not bound else "此列暂无样例值")
            self._label(self.fields_area, hint, anchor="w", wraplength=245, justify="left", text_color="#9B6B13" if needs_review else "#64748B").grid(
                row=index * 3 + 2, column=0, sticky="w", pady=(0, 8))
            if role in ("counterparty", "product", "original_note", "payment"):
                self._button(self.fields_area, "多列", lambda r=role: self._multi_sources(r), width=45).grid(
                    row=index * 3 + 1, column=1, padx=2)
        row = len(roles) * 3
        self._button(self.fields_area, "收起更多字段" if self._more else "更多字段", self._toggle_more).grid(row=row, column=0)
        # 重建控件时复用 Tcl 变量，避免旧变量的循环引用被工作线程 GC 回收。
        self.option_vars = getattr(self, "option_vars", {})
        for offset, (option, title) in enumerate((("include_counterparty", "备注含交易对方"),
                ("include_product", "备注含商品/说明"), ("include_original_note", "备注含原备注"),
                ("include_payment", "备注含支付方式"), ("include_time", "保留时间到备注 [HH:MM]")), 1):
            var = self.option_vars.get(option) or tk.BooleanVar(master=self)
            var.set(getattr(session.options, option))
            self.option_vars[option] = var
            self._check(self.fields_area, text=title, variable=var,
                command=lambda key=option: self._note_changed(key)).grid(row=row + offset, column=0, sticky="w", pady=3)

    def _toggle_more(self):
        self._more = not self._more
        self._build_fields()

    def _field_changed(self, role, text):
        if text.startswith("多列："):
            return
        indices = () if text == "不使用" or text.startswith("请选择") else (self._column_names.index(text),)
        self._run(lambda: self.session.update_role(role, indices))

    def _note_changed(self, option):
        self._run(lambda: self.session.update_note_options(**{option: self.option_vars[option].get()}))

    def _multi_sources(self, role):
        popup = self._popup("选择备注来源列")
        area = ctk.CTkScrollableFrame(popup, width=440, height=230, fg_color="white")
        area.pack(fill="both", expand=True, padx=12)
        current = self.session.mapping.columns.get(role, ()) if self.session.mapping else ()
        variables = []
        for index, name in enumerate(self._column_names):
            var = tk.BooleanVar(master=popup, value=index in current)
            variables.append(var)
            self._check(area, text=name, variable=var).pack(anchor="w", pady=4)
        def apply():
            self._run(lambda: self.session.update_role(role, tuple(i for i, v in enumerate(variables) if v.get())))
            popup.destroy()
        self._button(popup, "应用字段映射", apply).pack(pady=10)
        popup.finish_setup()

    def _run(self, operation):
        try:
            operation()
        except (ValueError, OSError) as error:
            self.footer.configure(text=str(error))  # 保留现场及当前选择，不把失败伪装成功。
            return False
        self.refresh()
        self._ensure_poll()
        return True

    def _ensure_poll(self):
        if not self._ending and self.session.busy and self._poll_job is None:
            self._poll_job = self.after(50, self._poll)

    def _poll(self):
        self._poll_job = None
        if self._ending:
            return
        if self.session.poll():
            self.refresh()
            if self._preview_target is not None and not self.session.busy:
                self._finish_preview()
        self._ensure_poll()

    def refresh(self):
        if self._ending:
            return
        session = self.session
        if self._preview_result is not session.result:
            # 规则结果/决定生效后，旧试算不再代表当前选择；筛选切换仍保留有效候选。
            self._preview_target = self._preview_ready = None
            self._preview_result = session.result
        historical = session.result is None and session.obsolete is not None
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
            old_total, old_done, old_remaining = session.obsolete.progress
            progress = f"历史回看：总量 {old_total} 笔 · 原已处理 {old_done} 笔 · 原剩余 {old_remaining} 笔（已作废）"
        self.progress_label.configure(text=progress)
        self.progress_label.grid() if progress else self.progress_label.grid_remove()
        self.submit_button.configure(text=f"确认导入（{session.counts['将导入']} 笔）")
        self._button_state(self.submit_button, session.can_submit)
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
        self.guide.configure(text=next_step)
        self._button_state(self.guide_button, session.result and remaining and not session.busy)
        self.footer.configure(text="预览尚未写入账本 · " + next_step)
        waiting = session.current_view == "待确认"
        self.only_check.configure(state="normal" if waiting else "disabled")
        self.next_button.configure(state="normal" if waiting and session.result and not session.busy else "disabled")
        key = (session.version, id(session.mapping))
        if key != self._mapping_key:
            self._mapping_key = key
            self._build_fields()
            self._saved_candidates()
        self.save_button.configure(state="normal" if session.result and not session.busy else "disabled")
        self._render(historical)

    def _render(self, historical):
        # 每次筛选生成独立渲染编号；销毁旧表后旧批次不能继续插入。
        self._render_id += 1
        token, version = self._render_id, self.session.version
        if self._render_job:
            self.after_cancel(self._render_job)
            self._render_job = None
        self._scroll_fraction = 0 if self._scroll_reset else self.details._parent_canvas.yview()[0]
        self._scroll_reset = False
        for child in self.details.winfo_children():
            child.destroy()
        self.row_locations, self.group_controls, self.pair_controls = {}, {}, {}
        self._sections, self._trees = {}, {}
        old_selection = self._selection_number
        self._selection_number = None
        self._restore_selection = old_selection
        self.inspect_title.configure(text="单笔核对 · 点击上方记录查看")
        self._set_raw("选择一笔记录查看日期、金额、备注和原始内容。\n需要批量处理时，请使用对应组标题处的操作。")
        self._set_row_state(None, historical)
        rows = self.session.visible_rows(historical=historical)
        full = self.session.visible_rows(historical=historical, summary=False)
        suffix = " · 历史回看（已作废）" if historical else ""
        self.view_title.configure(text=f"{self.session.current_view} · 共 {len(full)} 笔{suffix}" +
            (f"，当前展示前 5 笔" if len(rows) < len(full) else ""))
        self.all_button.configure(state="normal" if len(rows) < len(full) else "disabled")
        if not rows:
            self._label(self.details, "未找到匹配记录" if self.session.search_text else "当前视图无记录").grid(row=0, column=0)
        self._render_job = self.after(0, lambda: self._render_batch(rows, 0, token, version, historical))

    def _render_batch(self, rows, start, token, version, historical):
        if self._ending or token != self._render_id or version != self.session.version:
            return
        self._render_job = None
        for row in rows[start:start + 20]:
            tree = self._tree_for(row, historical)
            values = self.session.display_values(row)
            result = values[-1]
            if historical:
                result = "已作废（映射已变，决定不能应用） · " + result
            elif self.session.current_view == "待确认" and row.decision:
                result = "✓ 已处理 · " + result
            elif row.reason == "用户选择跳过":
                result = "用户选择 · " + result
            tag = "obsolete" if historical else "handled" if self.session.current_view == "待确认" and row.decision else "normal"
            item = tree.insert("", "end", values=(*values[:4], result), tags=(tag,))
            tree.configure(height=len(tree.get_children()))
            self.row_locations[row.row_number] = (tree, item, row)
        if start + 20 < len(rows):
            self._render_job = self.after(1, lambda: self._render_batch(rows, start + 20, token, version, historical))
        else:
            self.details._parent_canvas.yview_moveto(self._scroll_fraction)
            location = self.row_locations.get(self._restore_selection)
            if location and not self._advance_after_render:
                location[0].selection_set(location[1])
                self._selected(location[0])
            if self._advance_after_render and not historical and not self.session.busy:
                self._advance_after_render = False
                if not self.session.error and self.session.current_view == "待确认" and self.session.only_unhandled:
                    self.locate_next()
            # 默认退款对也自动准备核对内容，不要求用户先猜到要点某笔明细。
            if not historical and not self.session.busy and not self._selection_number:
                if self._preview_ready and self._preview_ready == self._preview_target and (
                        self._preview_ready[1] in self.pair_controls if self._preview_ready[0] == "pair" else
                        self._preview_ready[1] in self.group_controls if self._preview_ready[0] == "group" else False):
                    self._finish_preview()
                elif self.pair_controls and self._preview_target is None:
                    pair = next(iter(self.pair_controls))
                    self._prepare_pair(pair)

    def _tree_for(self, row, historical):
        pending = self.session.current_view == "待确认"
        group = row.group if pending else "用户选择跳过" if row.reason == "用户选择跳过" else "明细"
        pair = row.pair_key if pending else None
        tree_key = (group, pair)
        if tree_key in self._trees:
            return self._trees[tree_key]
        if group not in self._sections:
            section = ctk.CTkFrame(self.details, fg_color="white")
            section.grid(row=len(self._sections), column=0, sticky="ew", pady=4)
            section.grid_columnconfigure(0, weight=1)
            self._sections[group] = section
            if pending:
                source = self.session.obsolete if historical else self.session
                ids = source.groups.get(group, ())
                pairs = [k for k in source.pairs if set(k) <= set(ids)]
                single = len(pairs) == 1 and len(ids) == 2
                title = f"{group} · {len(ids)} 笔" + (f" / {len(pairs)} 对" if pairs else "")
                self._processing_header(section, title, group, pairs[0] if single else None, historical)
            elif group == "用户选择跳过":
                self._label(section, "用户选择跳过 · 由用户明确决定", anchor="w").pack(fill="x")
        section = self._sections[group]
        source = self.session.obsolete if historical else self.session
        if pair and not (len(source.groups.get(group, ())) == 2 and len(source.pairs) == 1):
            self._processing_header(section, f"退款对 · 原始第 {pair[0]}、{pair[1]} 行", None, pair, historical)
        tree = ttk.Treeview(section, columns=("row", "date", "amount", "note", "result"),
                            show="headings", height=1, style="ImportPreview.Treeview", selectmode="browse")
        for key, title, width in zip(tree["columns"], ("原始行", "本地日期", "带符号金额", "备注", "处理结果"), (65, 95, 95, 160, 220)):
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=45, stretch=True)
        tree.tag_configure("obsolete", foreground="#84909E", background="#F0F4F8")
        tree.tag_configure("handled", foreground="#64748B", background="#F0F4F8")
        tree.pack(fill="x", pady=3)
        tree.bind("<<TreeviewSelect>>", lambda event, t=tree: self._selected(t))
        tree.bind("<MouseWheel>", lambda event: self._wheel(event))
        self._trees[tree_key] = tree
        return tree

    def _processing_header(self, parent, title, group, pair, historical):
        frame = ctk.CTkFrame(parent, fg_color="#F0F4F8")
        frame.pack(fill="x", pady=3)
        frame.grid_columnconfigure(0, weight=1)
        caption = self._label(frame, title, anchor="w", wraplength=200, justify="left")
        caption.grid(row=0, column=0, sticky="w", padx=8, pady=3)
        action_area = ctk.CTkFrame(frame, fg_color="transparent")
        action_area.grid(row=0, column=1, sticky="e", padx=6, pady=3)
        choices = self.session.obsolete if historical else self.session
        selection = choices.pair_choices.get(pair, Selection("两笔都跳过")) if pair else choices.group_choices.get(group, Selection())
        actions = ("两笔都跳过", "只记支出", "两笔都记", "只记退款") if pair else self.session.group_actions(group)
        values = list(actions)
        menu = PreviewSelect(action_area, values=values, width=150, font=self.font,
            command=lambda action: self._choose(action, group, pair))
        menu.set(selection.value or "选择处理方式")
        menu.pack(side="left", padx=3)
        confirm = self._button(action_area, "确认本对" if pair else "确认处理本组",
            lambda: self._confirm_selection(group, pair), width=100, primary=True,
            fg_color="#9B6B13" if selection.state == "已选择未确认" and selection.explicit else "#2F80ED")
        confirm.pack(side="left", padx=3)
        status = "已作废" if historical else "✓ 已确认" if selection.state == "已确认" else "待确认" if selection.explicit else "默认选项未确认" if pair else "未选择"
        status_label = self._label(frame, status, anchor="w", wraplength=260, justify="left")
        status_label.grid(row=1, column=0, sticky="w", padx=8)
        enabled = not historical and not self.session.busy
        menu.configure(state="normal" if enabled else "disabled")
        ready = self._preview_ready == ("pair" if pair else "group", pair or group, self.session.version)
        self._button_state(confirm, enabled and (pair or ready and selection.value))
        confirm.configure(text="核对本对" if pair and not ready else "确认本对" if pair else "确认处理本组")
        hint = ("已作废，仅供回看" if historical else "✓ 已确认" if selection.state == "已确认" else
                "默认选项未确认，两笔仍待确认" if pair and not selection.explicit else
                "尚未生效，请确认" if ready else "正在生成结果预览" if selection.explicit else "请先选择处理方式")
        status_label.configure(text=hint, text_color="#25835B" if not historical and selection.state == "已确认" else "#9B6B13")
        clear = None
        if group:
            clear = self._button(frame, "清除本组决定", lambda: self._run(lambda: self.session.clear_group(group)), width=115)
            clear.grid(row=1, column=1, sticky="e", padx=9, pady=2)
            clear.configure(state="normal" if enabled else "disabled")
        controls = {"menu": menu, "confirm": confirm, "clear": clear, "status": status, "status_label": status_label}
        (self.pair_controls if pair else self.group_controls)[pair or group] = controls
        if group:
            explanation = {"疑似退款对": "相似记录金额相反，请核对两笔", "规则冲突": "不同信息给出了不一致的处理结果，请核对原因",
                           "已识别退款": "请确认是否将退款作为收入记入账本", "未知状态": "无法自动决定是否入账，请查看原始内容"}.get(group, "")
            count = len(choices.groups.get(group, ()))
            self._label(parent, f"{explanation}\n批量处理本组（{count} 笔）：包含已处理及筛选隐藏的记录。",
                        anchor="w", justify="left", wraplength=570).pack(fill="x", padx=6)

    def _choose(self, action, group, pair):
        self._preview_target = ("pair" if pair else "group", pair or group, self.session.version)
        self._preview_ready = None
        self._selection_number = None  # 组核对不能被旧行的选择恢复回调抢占。
        def operation():
            if pair:
                self.session.select_pair(pair, action)
                self.session.request_preview(key=pair, action=action)
            else:
                self.session.select_group(group, action)
                self.session.request_group_preview(group)
        self._run(operation)

    def _confirm_selection(self, group, pair):
        if pair and self._preview_ready != ("pair", pair, self.session.version):
            self._prepare_pair(pair)
            return
        version = self.session.version
        self._advance_after_render = True
        self._preview_target = self._preview_ready = None
        self._run(lambda: self.session.confirm_pair(pair, version=version) if pair else
                  self.session.confirm_group(group, version=version))

    def _prepare_pair(self, pair):
        self._selection_number = None
        self._preview_target, self._preview_ready = ("pair", pair, self.session.version), None
        action = self.session.pair_choices.get(pair, Selection("两笔都跳过")).value
        self._run(lambda: self.session.request_preview(key=pair, action=action))

    def _row_action(self, action):
        number = self._selection_number
        if number is None:
            return
        if action == "跳过":
            self._advance_after_render = True
            self._run(lambda: self.session.confirm_row(number, action))
        else:
            if self._preview_ready != ("row", number, self.session.version):
                self.footer.configure(text="请先核对最终入账预览")
                return
            self._advance_after_render = True
            self._preview_target = self._preview_ready = None
            self._run(lambda: self.session.confirm_row(number, action))

    def _finish_preview(self):
        kind, target, version = self._preview_target
        if version != self.session.version:
            self._preview_target = self._preview_ready = None
            return
        if self.session.preview_error or self.session.preview is None:
            self.footer.configure(text=self.session.preview_error or "无法生成最终入账预览")
            self.row_hint.configure(text=self.session.preview_error or "无法生成结果，请核对字段或选择跳过。")
            return
        ids = (target,) if kind == "row" else target if kind == "pair" else self.session.groups[target]
        self._preview_ready = self._preview_target
        if self.session.current_view != "待确认" or not any(row.row_number in ids for row in self.session.visible_rows(summary=False)):
            return  # 跨视图/搜索保留候选，但不把别处的批量核对内容填入当前详情。
        # 最终内容在只读核对区呈现，原确认按钮就是明确决定，不额外再弹确认框。
        rows = [row for row in self.session.preview.rows if row.row_number in ids]
        self.inspect_title.configure(text=f"正在处理：原始第 {target} 行" if kind == "row" else f"最终处理预览 · {'本对' if kind == 'pair' else '本组'}共 {len(ids)} 笔")
        self._set_raw(self._inspection_text(rows, final=True))
        self.row_hint.configure(text="核对入账后内容，再点「此笔入账」；不记这笔可点「跳过此笔」。" if kind == "row" else
                                "尚未生效：核对以上内容后，点击对应标题处的确认按钮。")
        controls = self.pair_controls.get(target) if kind == "pair" else self.group_controls.get(target) if kind == "group" else None
        if controls:
            self._button_state(controls["confirm"], True)
            controls["confirm"].configure(text="确认本对" if kind == "pair" else "确认处理本组")
            if controls["status"] != "✓ 已确认":
                controls["status_label"].configure(text="默认选项未确认，两笔仍待确认" if controls["status"] == "默认选项未确认" else "尚未生效，请确认")
            else:
                self.row_hint.configure(text="当前处理方式已确认；改选其他方式后，需再次确认。")
        if kind == "row" and self._selection_number == target:
            self._button_state(self.income_button, True)

    def _inspection_text(self, rows, *, final=False):
        blocks = []
        headers = self.session.headers()
        for row in rows:
            number, date, amount, note, result = self.session.display_values(row)
            heading = "入账后内容" if final and row.category == "将导入" else "处理后内容" if final else "当前内容"
            raw = "\n".join(f"  {headers[i] if i < len(headers) and headers[i] else '第 ' + str(i + 1) + ' 列'}：{value}"
                            for i, value in enumerate(row.raw_values))
            blocks.append(f"{heading} · 原始第 {number} 行\n日期：{date}    金额：{amount}（收入 + / 支出 −）\n备注：{note}\n处理结果：{result}\n\n原始内容（只读）\n{raw}")
        return "\n\n────────────\n\n".join(blocks)

    def _selected(self, tree):
        selected = tree.selection()
        if not selected:
            return
        match = next(((n, r) for n, (t, item, r) in self.row_locations.items() if t is tree and item == selected[0]), None)
        if match is None:
            return
        number, row = match
        self._selection_number = number
        self.session.located_row = number
        historical = self.session.result is None
        notice = "已作废（映射已变，决定不能应用）\n" if historical else ""
        self.inspect_title.configure(text=f"{'历史回看' if historical else '正在处理'}：原始第 {number} 行")
        self._set_raw(notice + self._inspection_text((row,)))
        self._set_row_state(row, historical)
        if not historical and not self.session.busy and self.session.current_view == "待确认" and row.row_number in {n for ids in self.session.groups.values() for n in ids}:
            if row.pair_key or row.can_import and row.group != "疑似退款对":
                target = ("pair", row.pair_key, self.session.version) if row.pair_key else ("row", number, self.session.version)
                if self._preview_ready == target and self.session.preview is not None:
                    self._preview_target = target
                    self._finish_preview()
                elif self._preview_target != target or self.session.preview is None:
                    self._preview_target, self._preview_ready = target, None
                    action = self.session.pair_choices.get(row.pair_key, Selection("两笔都跳过")).value
                    self._run(lambda: self.session.request_preview(key=row.pair_key, action=action) if row.pair_key else
                              self.session.request_preview(number=number))

    def _set_raw(self, text):
        self.raw_text.configure(state="normal")
        self.raw_text.delete("1.0", "end")
        self.raw_text.insert("1.0", text)
        self.raw_text.configure(state="disabled")

    def _set_row_state(self, row, historical):
        enabled = row is not None and not historical and not self.session.busy
        origins = {n for ids in self.session.groups.values() for n in ids}
        ordinary = enabled and self.session.current_view == "待确认" and row.row_number in origins and row.pair_key is None
        ready = row is not None and self._preview_ready == ("row", row.row_number, self.session.version)
        self._button_state(self.income_button, ordinary and ready and row.can_import and row.group != "疑似退款对")
        self._button_state(self.skip_button, ordinary)
        self.duplicate_var.set(bool(row and row.duplicate_released))
        self.duplicate_check.configure(state="normal" if enabled and row.duplicate_of is not None else "disabled")
        self.row_hint.configure(text="旧决定已作废，仅供回看；请修正字段重试或取消。" if historical else
            "点击上方一笔记录查看原值；批量处理请使用组标题处的操作。" if row is None else
            "正在生成结果预览，请稍候。" if self.session.busy else
            "此笔已有处理决定，可切到「待确认」并取消「仅看未处理」后修改。" if row.row_number in origins and self.session.current_view != "待确认" else
            "这两笔需要一起决定，请使用退款对标题右侧的操作。" if row.pair_key else
            "此笔暂不能入账：" + row.reason + "；可跳过或修正字段。" if ordinary and not row.can_import else
            "勾选「仍要导入」仅解除重复限制，还需通过其他规则。" if row.duplicate_of is not None else
            "请核对结果，再选择此笔入账或跳过。" if ordinary else "当前记录无需处理，可核对原始内容。")

    def _duplicate_changed(self):
        number = self._selection_number
        if number is not None:
            self._run(lambda: self.session.release_duplicate(number, self.duplicate_var.get()))

    def _wheel(self, event):
        self.details._parent_canvas.yview_scroll(-int(event.delta / 120), "units")
        return "break"

    def switch_view(self, view):
        self.session.set_view(view)
        self._scroll_reset = True
        self.refresh()

    def _search_changed(self, *_args):
        if not self._ending:
            self.session.set_search(self.search_var.get())
            self._scroll_reset = True
            self.refresh()

    def _filter_changed(self):
        self.session.set_only_unhandled(self.only_var.get())
        self.refresh()

    def _show_all(self):
        self.session.show_all.add(self.session.current_view)
        self.refresh()

    def locate_next(self):
        number = self.session.next_unhandled()
        if number is None:
            self.footer.configure(text="当前筛选无未处理记录")
            return
        location = self.row_locations.get(number)
        if location is not None:
            tree, item, _row = location
            tree.selection_set(item)
            tree.focus(item)
            tree.see(item)
            bounds = tree.bbox(item)
            if bounds:
                canvas = self.details._parent_canvas
                y = tree.winfo_rooty() - self.details.winfo_rooty() + bounds[1]
                height = max(1, self.details.winfo_height())
                canvas.yview_moveto(y / height)

    def _popup(self, title):
        popup = ManagedToplevel(self, fg_color="#F0F4F8")
        popup.title(title)
        popup.geometry("680x390")
        popup.transient(self)
        popup.protocol("WM_DELETE_WINDOW", popup.destroy)
        return popup

    def _saved_candidates(self):
        for child in self.saved_area.winfo_children():
            child.destroy()
        try:
            candidates = self.session.mapping_candidates(self.mapping_path)
        except (ValueError, OSError) as error:
            self.saved_empty.grid_remove()
            self.saved_area.grid(row=1, column=0, sticky="ew", padx=6)
            self._label(self.saved_area, "映射读取失败：" + str(error), wraplength=270).pack()
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
            self._button(line, name, lambda m=candidate.mapping: self._run(lambda: self.session.apply_saved_mapping(m)), width=215).pack(side="left")
            self._button(line, "×", lambda f=candidate.mapping.fingerprint: self._delete_mapping(f), width=30).pack(side="right")

    def _delete_mapping(self, fingerprint):
        if self._run(lambda: self.session.delete_mapping(fingerprint, self.mapping_path)):
            self._saved_candidates()  # 删除只收缩列表，不自动套用其他项。

    def save_mapping(self):
        popup = self._popup("保存此映射")
        headers = self.session.headers()
        default = datetime.now().strftime("%m-%d %H:%M") + " " + " ".join(headers[:2])
        entry = ctk.CTkEntry(popup, width=610, font=self.font)
        entry.pack(padx=15, pady=20)
        entry.insert(0, default)
        notice = self._label(popup, "同指纹覆盖需明确确认；最多保存 20 份。", wraplength=610)
        notice.pack(pady=8)
        button = None
        def save(overwrite=False):
            try:
                self.session.save_mapping(entry.get(), overwrite=overwrite, path=self.mapping_path)
            except (ValueError, OSError) as error:
                notice.configure(text=str(error))
                if "同指纹" in str(error) and not overwrite:
                    button.configure(text="确认覆盖此映射", command=lambda: save(True))
                return
            self.footer.configure(text="映射已保存")
            self._saved_candidates()
            popup.destroy()
        button = self._button(popup, "保存", save, primary=True)
        button.pack(pady=10)
        popup.finish_setup()

    def submit(self):
        try:
            result = self.session.export_result()
        except ValueError as error:
            self.footer.configure(text=str(error))
            return
        # 没有回调也可作为独立预览使用；这里绝不执行 store 的任何操作。
        if self.on_confirm:
            self.on_confirm(result)
        self.accepted_result = result
        self.destroy()

    def _destroyed_event(self, event):
        if event.widget is self:
            self._stop()

    def _stop(self):
        if self._ending:
            return
        self._ending = True
        self._render_id += 1
        for job in (self._poll_job, self._render_job):
            if job:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass  # 原生父窗先销毁时任务可能已被所属窗口清理。
        self._poll_job = self._render_job = None
        if hasattr(self, "_search_trace"):
            try:
                self.search_var.trace_remove("write", self._search_trace)
            except tk.TclError:
                pass  # 原生销毁的解释器可能已清除了 Tcl 变量。
        self.session.close()

    def destroy(self):
        self._stop()
        super().destroy()
