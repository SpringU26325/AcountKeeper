"""四区导入预览；只返回完整确认结果，不接入主页面或执行数据库写入。"""

from datetime import datetime
import tkinter as tk
from tkinter import ttk

import customtkinter as ctk

from dialog_lifecycle import ManagedToplevel
from import_pagination import ImportPagination
from import_session import Selection
from popup_common import anchored_position, make_toggle_button


class PreviewSelect(ctk.CTkFrame):
    """预览内共用选择框；统一实心三角与弹层，不依赖系统菜单主题。"""

    def __init__(self, master, *, values, command, font, width=180):
        super().__init__(master, width=width, height=34, corner_radius=8,
                         fg_color="#F8FAFC", border_color="#D8E1EA", border_width=1)
        self.values, self.command, self.font = tuple(values), command, font
        self.value, self.state, self.popup = self.values[0], "normal", None
        self._entry_pressed = False
        self.grid_columnconfigure(0, weight=1)
        self.label = ctk.CTkButton(self, text=self.value, command=self.open, font=font,
            width=max(60, width - 36), height=32, anchor="w", fg_color="transparent",
            hover_color="#D2DEE9", text_color="#243447", corner_radius=7)
        self.label.grid(row=0, column=0, sticky="ew", padx=(1, 0), pady=1)
        self.toggle = make_toggle_button(self, self.open, height=32)
        self.toggle.grid(row=0, column=1, padx=1, pady=1)
        self.bind("<Destroy>", self._destroyed, add="+")  # type: ignore[reportArgumentType] # Tk 的 add 实际接受 "+"，CTk 存根标成 bool。
        # 单个键盘焦点覆盖整个选择框，回车/空格展开，上下键在弹层内移动。
        self.label._canvas.configure(takefocus=1)
        for sequence in ("<Return>", "<space>", "<Down>"):
            self.label._canvas.bind(sequence, lambda event: self.open())

    def set(self, value):
        self.value = value
        self.label.configure(text=value)

    def get(self):
        return self.value

    def configure(self, **options):  # type: ignore[reportIncompatibleMethodOverride] # 有意放宽签名以接管 state 并联动子控件，运行时兼容父类。
        state = options.pop("state", None)
        super().configure(**options)
        if state is not None:
            self.state = state
            self.label.configure(state=state)
            self.toggle.configure(state=state)
            if state == "disabled":
                self.close()

    def cget(self, name):  # type: ignore[reportIncompatibleMethodOverride] # 有意放宽签名以拦截 state，运行时兼容父类。
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
        popup.bind("<ButtonRelease-1>", self._entry_released, add="+")
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

    @staticmethod
    def _inside(widget, event):
        return (widget.winfo_rootx() <= event.x_root < widget.winfo_rootx() + widget.winfo_width()
                and widget.winfo_rooty() <= event.y_root < widget.winfo_rooty() + widget.winfo_height())

    def _outside(self, event):
        popup = self.popup
        if popup and self._inside(self, event):
            # CTk 在松开时执行按钮命令；按下入口不能先收起，否则同一点击会重新展开。
            self._entry_pressed = True
            return "break"
        if popup and not self._inside(popup, event):
            self.close()
            return "break"

    def _entry_released(self, event):
        if self.popup is not None and self._entry_pressed:
            # grab 若把松开也送到弹层，由此完成收起；按钮已处理时 popup 为 None，不重复切换。
            return self.close()

    def _focus_left(self):
        focused = self.focus_get()  # 只查一次焦点，避免两次调用之间焦点变化使判断不一致。
        if self.popup and not self._entry_pressed and (focused is None or focused.winfo_toplevel() is not self.popup):
            self.close()

    def close(self):
        popup, self.popup = self.popup, None
        self._entry_pressed = False  # 收尾同时结束手势，不能把旧按下状态带到下次展开。
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
        self.help_popup = None
        self._wheel_remainder = 0.0
        self._render_id = self._mapping_key = 0
        self._ending, self._more = False, False
        self.row_locations, self.group_controls, self.pair_controls = {}, {}, {}
        self._sections, self._trees, self._row_controls = {}, {}, []
        self._preview_target = self._preview_ready = None
        self._preview_result = None
        self.pagination = ImportPagination()
        self._preview_meta = None
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
        button.enabled_color = options["fg_color"]  # type: ignore[reportAttributeAccessIssue] # 运行时附加的禁用底色，供 _button_state 切换。
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
        # 说明只由显式按钮打开，避免初次弹窗打断字段核对。
        self.help_button = self._button(heading, "使用说明", self.show_help, width=110,
            height=36, border_width=1, border_color="#8CB6ED", fg_color="#DCEAFE",
            hover_color="#CFE0F8", text_color="#245D9F")
        self.help_button.grid(row=0, column=1, sticky="e")
        # 当前步骤与下一步动作集中显示；说明放独立窗口，避免挤占长列表的浏览空间。
        guide_area = ctk.CTkFrame(heading, fg_color="#E8F1FC", border_color="#BED6F5", border_width=1, corner_radius=8)
        guide_area.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        guide_area.grid_columnconfigure(1, weight=1)
        self.guide_step = self._label(guide_area, "① 核对字段", text_color="#1D5CA8", width=125)
        self.guide_step.grid(row=0, column=0, sticky="w", padx=8, pady=8)
        self.guide = self._label(guide_area, "正在分析账单，请稍候", anchor="w", wraplength=650, justify="left")
        self.guide.grid(row=0, column=1, sticky="ew", padx=8)
        self.guide_button = self._button(guide_area, "去处理", lambda: self.switch_view("待确认"), width=100, height=32, primary=True)
        self.guide_button.grid(row=0, column=2, sticky="e", padx=8, pady=6)
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
        right.grid_rowconfigure(5, weight=1)
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
        self._build_pager(right)
        self.progress_label.grid(row=4, column=0, sticky="ew", padx=12)
        self.details = ctk.CTkScrollableFrame(right, fg_color="white")
        self.details.grid(row=5, column=0, sticky="nsew", padx=8, pady=4)
        self.details.grid_columnconfigure(0, weight=1)
        # 明细内统一路由；其他区域保留原滚动行为，不注册全局滚轮绑定。
        self.details._parent_canvas.configure(yscrollincrement=1)
        self.bind("<MouseWheel>", self._wheel, add="+")
        inspect = ctk.CTkFrame(right, fg_color="#F8FAFC", corner_radius=8)
        inspect.grid(row=6, column=0, sticky="ew", padx=10)
        self.inspect_title = self._label(inspect, "单笔核对 · 点击上方记录查看", anchor="w")
        self.inspect_title.pack(fill="x", padx=8)
        self.raw_text = ctk.CTkTextbox(inspect, height=64, font=self.font, fg_color="#F8FAFC", text_color="#243447")
        self.raw_text.pack(fill="x", padx=4)
        self.row_hint = self._label(inspect, "选择记录后，可查看原值和可用操作。", anchor="w", wraplength=590)
        self.row_hint.pack(fill="x", padx=8)
        row_bar = ctk.CTkFrame(right, fg_color="white")
        row_bar.grid(row=7, column=0, sticky="ew", padx=10, pady=6)
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

    def _build_pager(self, parent):
        # 分页入口固定在筛选栏下方，不放入随明细滚动的容器。
        self.pager = ctk.CTkFrame(parent, fg_color="white")
        self.pager.grid(row=3, column=0, sticky="ew", padx=10, pady=3)
        self.pager.grid_columnconfigure(0, weight=1)
        self.page_label = self._label(self.pager, "", anchor="w")
        self.page_label.grid(row=0, column=0, sticky="w")
        self.previous_page = self._button(self.pager, "上一页", lambda: self._change_page(
            self.pagination.page - 1), width=65)
        self.previous_page.grid(row=0, column=1, padx=3)
        self.next_page = self._button(self.pager, "下一页", lambda: self._change_page(
            self.pagination.page + 1), width=65)
        self.next_page.grid(row=0, column=2, padx=3)
        self.page_var = tk.StringVar(master=self, value="1")
        self.page_entry = ctk.CTkEntry(self.pager, textvariable=self.page_var, width=55, font=self.font)
        self.page_entry.grid(row=0, column=3, padx=3)
        self.page_entry.bind("<Return>", lambda event: self._change_page(self.page_var.get()))
        self.jump_page = self._button(self.pager, "跳页", lambda: self._change_page(
            self.page_var.get()), width=50)
        self.jump_page.grid(row=0, column=4)

    def _update_pager(self):
        pages = self.pagination
        self.pager.grid_remove() if pages.summary else self.pager.grid()
        self.page_label.configure(text=f"第 {pages.page} / {pages.total_pages} 页 · 匹配 {len(pages.rows)} 笔")
        self.page_var.set(str(pages.page))
        self._button_state(self.previous_page, pages.page > 1)
        self._button_state(self.next_page, pages.page < pages.total_pages)
        self._button_state(self.jump_page, bool(pages.rows))
        self.page_entry.configure(state="normal" if pages.rows else "disabled")

    def _change_page(self, page, *, target=None):
        if self._ending:
            return
        try:
            if self._selection_number is not None:
                self.pagination.remember(self._selection_number)
            self.pagination.move(page)
        except ValueError as error:
            self.footer.configure(text=str(error))
            return
        self.pagination.target = target
        self._preview_ready = None  # 就绪资格只属于已核对过的当前页，业务候选继续保留。
        self._scroll_reset = True
        self._render(self.session.result is None and self.session.obsolete is not None)

    def _preview_choice(self):
        kind, target, _version = self._preview_target  # type: ignore[reportGeneralTypeIssues] # 调用方与 _candidate_valid 已保证 _preview_target 非空。
        choices = self.session.pair_choices if kind == "pair" else self.session.group_choices
        return choices.get(target, Selection("两笔都跳过" if kind == "pair" else None)).value if kind != "row" else "入账"

    def _request_preview(self, operation):
        def request():
            operation()
            # 请求号与源结果共同保护同代次内的异步试算；页号仅保护展示，不废弃业务结果。
            self._preview_meta = (self.session, self.session.version, self.session.request_id,
                self.session.result, self._preview_target, self._preview_choice(),
                (self.pagination.epoch, self.pagination.page))
        return self._run(request)

    def _candidate_valid(self):
        meta = self._preview_meta
        return bool(meta and self._preview_target and meta[:3] == (
            self.session, self.session.version, self.session.request_id) and
            meta[3] is self.session.result and meta[4] == self._preview_target and
            meta[5] == self._preview_choice())

    def _bind_preview_page(self):
        if self._candidate_valid():
            self._preview_meta = (*self._preview_meta[:-1], (self.pagination.epoch, self.pagination.page))  # type: ignore[reportOptionalSubscript] # 仅当 _candidate_valid() 为真（meta 非空）时才进入。

    def _preview_current(self):
        return self._candidate_valid() and self._preview_meta[-1] == (self.pagination.epoch, self.pagination.page)  # type: ignore[reportOptionalSubscript] # 左值 _candidate_valid() 已保证 meta 非空。

    def _ensure_poll(self):
        if not self._ending and self.session.busy and self._poll_job is None:
            self._poll_job = self.after(50, self._poll)  # type: ignore[reportArgumentType] # Tk 接受无参回调，存根对回调类型标注过窄。

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
            self._preview_meta = None
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
            old_total, old_done, old_remaining = session.obsolete.progress  # type: ignore[reportOptionalMemberAccess] # historical 分支保证 obsolete 非空。
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
        missing_required = not session.mapping or any(not session.mapping.columns.get(role) for role in ("date", "amount"))
        step = "① 核对字段" if missing_required or session.result is None else "② 处理待确认" if remaining else "③ 确认导入"
        self.guide_step.configure(text=step)
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
        source = self.session.obsolete if historical else self.session.result
        context = (id(self.session), self.session.version, historical, self.session.current_view,
                   self.session.search_text, self.session.only_unhandled)
        summary = (self.session.current_view in ("规则跳过", "非交易行") and
                   self.session.current_view not in self.session.show_all and not self.session.search_text)
        self.pagination.bind(context, source, lambda: self.session.visible_rows(
            historical=historical, summary=False), summary)
        self._render_id = self.pagination.new_render()
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
        self._selection_number = None
        self._restore_selection = self.pagination.selections.get(self.pagination.page)
        self.inspect_title.configure(text="单笔核对 · 点击上方记录查看")
        self._set_raw("选择一笔记录查看日期、金额、备注和原始内容。\n需要批量处理时，请使用对应组标题处的操作。")
        self._set_row_state(None, historical)
        self._show_inspection(False)
        rows, full = self.pagination.page_rows, self.pagination.rows
        suffix = " · 历史回看（已作废）" if historical else ""
        self.view_title.configure(text=f"{self.session.current_view} · 共 {len(full)} 笔{suffix}" +
            (f"，当前展示前 5 笔" if summary and len(full) > 5 else ""))
        self.all_button.configure(state="normal" if summary and len(full) > 5 else "disabled")
        self._update_pager()
        if not rows:
            self._label(self.details, "未找到匹配记录" if self.session.search_text else "当前视图无记录").grid(row=0, column=0)
        self._render_job = self.after(0, lambda: self._render_batch(rows, 0, token, version, historical))  # type: ignore[reportArgumentType] # Tk 接受无参回调，存根对回调类型标注过窄。

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
            self._render_job = self.after(1, lambda: self._render_batch(rows, start + 20, token, version, historical))  # type: ignore[reportArgumentType] # Tk 接受无参回调，存根对回调类型标注过窄。
        else:
            self.details._parent_canvas.yview_moveto(self._scroll_fraction)
            target, self.pagination.target = self.pagination.target, None
            location = self.row_locations.get(target or self._restore_selection)
            if location and not self._advance_after_render:
                self._highlight(target or self._restore_selection)
                if token != self._render_id:
                    return  # 选择触发试算及 refresh 后不能继续操作已销毁控件。
            if self._advance_after_render and not historical and not self.session.busy:
                self._advance_after_render = False
                if not self.session.error and self.session.current_view == "待确认" and self.session.only_unhandled:
                    self.locate_next()
            # 默认退款对也自动准备核对内容，不要求用户先猜到要点某笔明细。
            if not historical and not self.session.busy and not self._selection_number:
                if self._candidate_valid() and self._preview_meta[-1] == (self.pagination.epoch, self.pagination.page):  # type: ignore[reportOptionalSubscript] # 左值 _candidate_valid() 已保证 meta 非空。
                    self._finish_preview()
                elif self.pair_controls:
                    pair = next(iter(self.pair_controls))
                    if self._preview_target == ("pair", pair, self.session.version) and self._candidate_valid():
                        self._bind_preview_page()
                        self._finish_preview()
                    else:
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
                members = set(ids)  # 整组成员只建一次，不能为每个对重复扫完整组。
                pairs = [k for k in source.pairs if set(k) <= members]
                single = len(pairs) == 1 and len(ids) == 2
                title = f"{group} · {len(ids)} 笔" + (f" / {len(pairs)} 对" if pairs else "")
                if single:
                    title += f" · 原始第 {pairs[0][0]}、{pairs[0][1]} 行"
                    title += " · 续（本页继续核对）" if self.pagination.continuation(pairs[0]) else ""
                self._processing_header(section, title, group, pairs[0] if single else None, historical)
            elif group == "用户选择跳过":
                self._label(section, "用户选择跳过 · 由用户明确决定", anchor="w").pack(fill="x")
        section = self._sections[group]
        source = self.session.obsolete if historical else self.session
        if pair and not (len(source.groups.get(group, ())) == 2 and len(source.pairs) == 1):
            title = f"退款对 · 原始第 {pair[0]}、{pair[1]} 行"
            title += " · 续（本页继续核对）" if self.pagination.continuation(pair) else ""
            self._processing_header(section, title, None, pair, historical)
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
        if "续（" in title:
            caption.configure(text_color="#64748B")  # 续标题同时用文字与弱化颜色区分。
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
        ready = self._preview_current() and self._preview_ready == ("pair" if pair else "group", pair or group, self.session.version)
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
        controls = {"menu": menu, "confirm": confirm, "clear": clear, "status": status,
                    "status_label": status_label, "caption": caption}
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
        self.pagination.remember(None)
        def operation():
            if pair:
                self.session.select_pair(pair, action)
                self.session.request_preview(key=pair, action=action)
            else:
                self.session.select_group(group, action)
                self.session.request_group_preview(group)
        self._request_preview(operation)

    def _confirm_selection(self, group, pair):
        ready = self._preview_ready == ("pair" if pair else "group", pair or group, self.session.version)
        if not ready or not self._preview_current():
            if pair:
                self._prepare_pair(pair)
            else:
                self.footer.configure(text="请先选择处理方式并核对本组预览")
            return
        version = self.session.version
        self._advance_after_render = True
        self._preview_target = self._preview_ready = None
        self._run(lambda: self.session.confirm_pair(pair, version=version) if pair else
                  self.session.confirm_group(group, version=version))

    def _prepare_pair(self, pair):
        self._selection_number = None
        self.pagination.remember(None)
        self._preview_target, self._preview_ready = ("pair", pair, self.session.version), None
        action = self.session.pair_choices.get(pair, Selection("两笔都跳过")).value
        self._request_preview(lambda: self.session.request_preview(key=pair, action=action))

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
        kind, target, version = self._preview_target  # type: ignore[reportGeneralTypeIssues] # 调用前已由 _candidate_valid 保证 _preview_target 非空。
        if version != self.session.version or not self._candidate_valid():
            self._preview_target = self._preview_ready = None
            return
        if self._preview_meta[-1] != (self.pagination.epoch, self.pagination.page):  # type: ignore[reportOptionalSubscript] # 上方 _candidate_valid() 已保证 meta 非空。
            return  # 业务结果留在会话中；旧页回传不能填写当前核对区。
        if self.session.preview_error or self.session.preview is None:
            self.footer.configure(text=self.session.preview_error or "无法生成最终入账预览")
            self.row_hint.configure(text=self.session.preview_error or "无法生成结果，请核对字段或选择跳过。")
            return
        ids = (target,) if kind == "row" else target if kind == "pair" else self.session.groups[target]
        self._preview_ready = self._preview_target
        if self.session.current_view != "待确认" or not any(number in self.row_locations for number in ids):
            return  # 跨视图/搜索保留候选，但不把别处的批量核对内容填入当前详情。
        # 最终内容在只读核对区呈现，原确认按钮就是明确决定，不额外再弹确认框。
        members = set(ids)
        rows = [row for row in self.session.preview.rows if row.row_number in members]
        self._show_inspection(True)
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
        self.pagination.remember(number)
        self.session.located_row = number
        historical = self.session.result is None
        notice = "已作废（映射已变，决定不能应用）\n" if historical else ""
        self._show_inspection(True)
        self.inspect_title.configure(text=f"{'历史回看' if historical else '正在处理'}：原始第 {number} 行")
        self._set_raw(notice + self._inspection_text((row,)))
        self._set_row_state(row, historical)
        if not historical and not self.session.busy and self.session.current_view == "待确认" and row.row_number in {n for ids in self.session.groups.values() for n in ids}:
            if row.pair_key or row.can_import and row.group != "疑似退款对":
                target = ("pair", row.pair_key, self.session.version) if row.pair_key else ("row", number, self.session.version)
                if self._preview_target == target and self._candidate_valid() and self.session.preview is not None:
                    self._preview_target = target
                    self._bind_preview_page()
                    self._finish_preview()
                elif self._preview_target != target or self.session.preview is None:
                    self._preview_target, self._preview_ready = target, None
                    action = self.session.pair_choices.get(row.pair_key, Selection("两笔都跳过")).value
                    self._request_preview(lambda: self.session.request_preview(key=row.pair_key, action=action) if row.pair_key else
                              self.session.request_preview(number=number))

    def _set_raw(self, text):
        self.raw_text.configure(state="normal")
        self.raw_text.delete("1.0", "end")
        self.raw_text.insert("1.0", text)
        self.raw_text.configure(state="disabled")

    def _show_inspection(self, expanded):
        # 无记录待核对时只留提示标题，避免空详情挤占小窗口；有内容时完整展开。
        for widget, padding in ((self.raw_text, 4), (self.row_hint, 8)):
            if expanded:
                if not widget.winfo_manager():
                    widget.pack(fill="x", padx=padding)
            else:
                widget.pack_forget()

    def _set_row_state(self, row, historical):
        enabled = row is not None and not historical and not self.session.busy
        origins = {n for ids in self.session.groups.values() for n in ids}
        ordinary = enabled and self.session.current_view == "待确认" and row.row_number in origins and row.pair_key is None  # type: ignore[reportOptionalMemberAccess] # enabled 已包含 row is not None，静态无法从布尔别名推断。
        ready = row is not None and self._preview_ready == ("row", row.row_number, self.session.version)
        self._button_state(self.income_button, ordinary and ready and row.can_import and row.group != "疑似退款对")  # type: ignore[reportOptionalMemberAccess] # ordinary/ready 已隐含 row 非空。
        self._button_state(self.skip_button, ordinary)
        self.duplicate_var.set(bool(row and row.duplicate_released))
        self.duplicate_check.configure(state="normal" if enabled and row.duplicate_of is not None else "disabled")  # type: ignore[reportOptionalMemberAccess] # enabled 已包含 row is not None。
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
        if self._ending or event.widget.winfo_toplevel() is not self:
            return
        widget = event.widget
        while widget is not None and widget is not self.details._parent_frame:
            widget = getattr(widget, "master", None)
        if widget is None:
            return  # 字段区、备注文本、下拉弹层不借用明细的滚动速度。
        row_height = int(ttk.Style(self).lookup("ImportPreview.Treeview", "rowheight"))
        distance = self._wheel_remainder - event.delta * row_height * 3 / 120
        pixels = int(distance)
        self._wheel_remainder = distance - pixels  # 小幅连续滚动累积，不因取整而丢失。
        self.details._parent_canvas.yview_scroll(pixels, "units")
        return "break"

    def show_help(self):
        if self._ending:
            return
        if self.help_popup is not None:
            self.help_popup.lift()
            self.help_popup.focus_force()
            return
        popup = self.help_popup = self._popup("导入预览 · 使用说明")
        popup.geometry("680x440")
        self._label(popup, "三步完成核对，预览中的选择尚未写入账本", anchor="w").pack(fill="x", padx=18, pady=(12, 6))
        steps = (
            ("① 核对字段对应", "看左侧日期、金额对应哪一列，并核对样例值。需要时修改下拉。\n修改字段会重算，并作废本轮已作出的决定。"),
            ("② 处理待确认记录", "「未知状态」不代表交易失败，请核对原始状态和收支方向。\n逐笔：点记录，看入账后内容，再选「此笔入账」或「跳过此笔」。\n整组：选择处理方式，核对结果，再点「确认处理本组」。\n整组包含筛选隐藏的记录；退款对的两笔需要一起决定。"),
            ("③ 确认导入", "待确认处理完、必需字段满足后，核对将导入笔数，再点底部确认。\n只选择处理方式尚未生效；关闭说明不会处理或跳过任何记录。"),
        )
        for title, body in steps:
            card = ctk.CTkFrame(popup, fg_color="white", corner_radius=8)
            card.pack(fill="x", padx=14, pady=4)
            self._label(card, title, anchor="w", text_color="#1D5CA8").pack(fill="x", padx=10, pady=(5, 0))
            self._label(card, body, anchor="w", justify="left", wraplength=625).pack(fill="x", padx=10, pady=(0, 6))
        self.help_close_button = self._button(popup, "开始核对", popup.destroy, primary=True)
        self.help_close_button.pack(pady=10)
        popup.bind("<Escape>", lambda event: popup.destroy())
        popup.bind("<Destroy>", self._help_closed, add="+")
        # 显式说明不保存偏好，也不处理记录；关掉后可随时由按钮重看。
        popup.finish_setup()
        if not popup.closing:
            # 按实际尺寸居中并先完成映射，键盘关闭才能立即作用于说明窗。
            popup.update_idletasks()
            width, height = popup.winfo_width(), popup.winfo_height()
            x = max(8, min(self.winfo_rootx() + (self.winfo_width() - width) // 2, self.winfo_screenwidth() - width - 8))
            y = max(8, min(self.winfo_rooty() + (self.winfo_height() - height) // 2, self.winfo_screenheight() - height - 8))
            popup.wm_geometry(f"{width}x{height}+{x}+{y}")
            popup.lift()
            popup.focus_force()
            # CTk 在 Windows 建窗后约 10ms 恢复旧焦点；该恢复结束后说明再接收键盘。
            popup.after(20, popup.focus_force)  # type: ignore[reportArgumentType] # Tk 接受无参回调，存根对回调类型标注过窄。

    def _help_closed(self, event):
        if event.widget is self.help_popup:
            self.help_popup = None

    def switch_view(self, view):
        if view == self.session.current_view:
            return
        self.pagination.reset_context()
        self.session.set_view(view)
        self._scroll_reset = True
        self.refresh()

    def _search_changed(self, *_args):
        if not self._ending:
            if self.search_var.get().strip() == self.session.search_text:
                return
            self.pagination.reset_context()
            self.session.set_search(self.search_var.get())
            self._scroll_reset = True
            self.refresh()
            if self.session.search_text and self.pagination.rows:
                self.pagination.target = self.pagination.rows[0].row_number

    def _filter_changed(self):
        self.pagination.reset_context()
        self.session.set_only_unhandled(self.only_var.get())
        self._scroll_reset = True
        self.refresh()

    def _show_all(self):
        self.pagination.reset_context()
        self.session.show_all.add(self.session.current_view)
        self._scroll_reset = True
        self.refresh()

    def locate_next(self):
        number = self.session.next_unhandled()
        if number is None:
            self.footer.configure(text="当前筛选无未处理记录")
            return
        page = self.pagination.page_of(number)
        if page is not None and page != self.pagination.page:
            self._change_page(page, target=number)
        elif number not in self.row_locations:
            self.pagination.target = number  # 本页尚在插入时，将定位留给渲染完成回调兑现。
        else:
            self._highlight(number)

    def _highlight(self, number):
        location = self.row_locations.get(number)
        if location is not None:
            tree, item, _row = location
            tree.selection_set(item)
            tree.focus(item)
            self._selected(tree)
            if not tree.winfo_exists():
                return  # 行试算可能刷新当前页，位置由新渲染完成后恢复。
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
                    button.configure(text="确认覆盖此映射", command=lambda: save(True))  # type: ignore[reportOptionalMemberAccess] # 本回调只经 button 的 command 触发，届时已绑定。
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
        if self.on_confirm and self.on_confirm(result) is False:
            return  # 写入失败的回调保留原预览和决定，供用户修正或重试。
        self.accepted_result = result
        self.destroy()

    def _destroyed_event(self, event):
        if event.widget is self:
            self._stop()

    def _stop(self):
        if self._ending:
            return
        self._ending = True
        self.pagination.close()
        self._render_id = self.pagination.token
        for job in (self._poll_job, self._render_job):
            if job:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass  # 原生父窗先销毁时任务可能已被所属窗口清理。
        self._poll_job = self._render_job = None
        if self.help_popup is not None:
            self.help_popup.destroy()
        if hasattr(self, "_search_trace"):
            try:
                self.search_var.trace_remove("write", self._search_trace)
            except tk.TclError:
                pass  # 原生销毁的解释器可能已清除了 Tcl 变量。
        self.session.close()

    def destroy(self):
        self._stop()
        super().destroy()
