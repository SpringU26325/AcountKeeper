"""导入预览表格、组标题及滚动；不管理会话决定或异步调度。"""

from dataclasses import dataclass
from tkinter import ttk

import customtkinter as ctk

from import_preview_controls import PreviewControls, PreviewSelect


@dataclass(frozen=True)
class HeaderState:
    value: str | None
    state: str
    explicit: bool
    actions: tuple
    enabled: bool
    ready: bool
    count: int


class ImportPreviewTable:
    def __init__(self, window, details, font, *, on_select, on_choose, on_confirm, on_clear, header_state):
        self.window, self.details, self.font = window, details, font
        self.controls = PreviewControls(font)
        self.on_select, self.on_choose = on_select, on_choose
        self.on_confirm, self.on_clear, self.header_state = on_confirm, on_clear, header_state
        self.closed, self._wheel_remainder = False, 0.0
        self.row_locations, self.group_controls, self.pair_controls = {}, {}, {}
        self._sections, self._trees = {}, {}
        self._style_table()

    def _style_table(self):
        # 只配置预览专属样式；复制 clam 表头元素，与主页面一致且不切换全局主题。
        style = ttk.Style(self.window)
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

    def reset(self):
        # 每次上下文重建都清空控件索引，不把旧 Treeview 项带入新页。
        for child in self.details.winfo_children():
            child.destroy()
        self.row_locations, self.group_controls, self.pair_controls = {}, {}, {}
        self._sections, self._trees = {}, {}

    def close(self):
        self.closed = True  # 关闭后滚轮不得访问正在销毁的控件树。

    def _tree_for(self, row, historical, *, pending, source, continuation):
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
                ids = source.groups.get(group, ())
                members = set(ids)  # 整组成员只建一次，不能为每个对重复扫完整组。
                pairs = [k for k in source.pairs if set(k) <= members]
                single = len(pairs) == 1 and len(ids) == 2
                title = f"{group} · {len(ids)} 笔" + (f" / {len(pairs)} 对" if pairs else "")
                if single:
                    title += f" · 原始第 {pairs[0][0]}、{pairs[0][1]} 行"
                    title += " · 续（本页继续核对）" if continuation(pairs[0]) else ""
                self._processing_header(section, title, group, pairs[0] if single else None, historical)
            elif group == "用户选择跳过":
                self.controls.label(section, "用户选择跳过 · 由用户明确决定", anchor="w").pack(fill="x")
        section = self._sections[group]
        if pair and not (len(source.groups.get(group, ())) == 2 and len(source.pairs) == 1):
            title = f"退款对 · 原始第 {pair[0]}、{pair[1]} 行"
            title += " · 续（本页继续核对）" if continuation(pair) else ""
            self._processing_header(section, title, None, pair, historical)
        tree = ttk.Treeview(section, columns=("row", "date", "amount", "note", "result"),
                            show="headings", height=1, style="ImportPreview.Treeview", selectmode="browse")
        for key, title, width in zip(tree["columns"], ("原始行", "本地日期", "带符号金额", "备注", "处理结果"), (65, 95, 95, 160, 220)):
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=45, stretch=True)
        tree.tag_configure("obsolete", foreground="#84909E", background="#F0F4F8")
        tree.tag_configure("handled", foreground="#64748B", background="#F0F4F8")
        tree.pack(fill="x", pady=3)
        tree.bind("<<TreeviewSelect>>", lambda event, t=tree: self.on_select(t))
        tree.bind("<MouseWheel>", lambda event: self._wheel(event))
        self._trees[tree_key] = tree
        return tree

    def _processing_header(self, parent, title, group, pair, historical):
        frame = ctk.CTkFrame(parent, fg_color="#F0F4F8")
        frame.pack(fill="x", pady=3)
        frame.grid_columnconfigure(0, weight=1)
        caption = self.controls.label(frame, title, anchor="w", wraplength=200, justify="left")
        if "续（" in title:
            caption.configure(text_color="#64748B")  # 续标题同时用文字与弱化颜色区分。
        caption.grid(row=0, column=0, sticky="w", padx=8, pady=3)
        action_area = ctk.CTkFrame(frame, fg_color="transparent")
        action_area.grid(row=0, column=1, sticky="e", padx=6, pady=3)
        selection = self.header_state(group, pair, historical)
        actions = selection.actions
        values = list(actions)
        menu = PreviewSelect(action_area, values=values, width=150, font=self.font,
            command=lambda action: self.on_choose(action, group, pair))
        menu.set(selection.value or "选择处理方式")
        menu.pack(side="left", padx=3)
        confirm = self.controls.button(action_area, "确认本对" if pair else "确认处理本组",
            lambda: self.on_confirm(group, pair), width=100, primary=True,
            fg_color="#9B6B13" if selection.state == "已选择未确认" and selection.explicit else "#2F80ED")
        confirm.pack(side="left", padx=3)
        status = "已作废" if historical else "✓ 已确认" if selection.state == "已确认" else "待确认" if selection.explicit else "默认选项未确认" if pair else "未选择"
        status_label = self.controls.label(frame, status, anchor="w", wraplength=260, justify="left")
        status_label.grid(row=1, column=0, sticky="w", padx=8)
        enabled = selection.enabled
        menu.configure(state="normal" if enabled else "disabled")
        ready = selection.ready
        self.controls.button_state(confirm, enabled and (pair or ready and selection.value))
        confirm.configure(text="核对本对" if pair and not ready else "确认本对" if pair else "确认处理本组")
        hint = ("已作废，仅供回看" if historical else "✓ 已确认" if selection.state == "已确认" else
                "默认选项未确认，两笔仍待确认" if pair and not selection.explicit else
                "尚未生效，请确认" if ready else "正在生成结果预览" if selection.explicit else "请先选择处理方式")
        status_label.configure(text=hint, text_color="#25835B" if not historical and selection.state == "已确认" else "#9B6B13")
        clear = None
        if group:
            clear = self.controls.button(frame, "清除本组决定", lambda: self.on_clear(group), width=115)
            clear.grid(row=1, column=1, sticky="e", padx=9, pady=2)
            clear.configure(state="normal" if enabled else "disabled")
        controls = {"menu": menu, "confirm": confirm, "clear": clear, "status": status,
                    "status_label": status_label, "caption": caption}
        (self.pair_controls if pair else self.group_controls)[pair or group] = controls
        if group:
            explanation = {"疑似退款对": "相似记录金额相反，请核对两笔", "规则冲突": "不同信息给出了不一致的处理结果，请核对原因",
                           "已识别退款": "请确认是否将退款作为收入记入账本", "未知状态": "无法自动决定是否入账，请查看原始内容"}.get(group, "")
            count = selection.count
            self.controls.label(parent, f"{explanation}\n批量处理本组（{count} 笔）：包含已处理及筛选隐藏的记录。",
                        anchor="w", justify="left", wraplength=570).pack(fill="x", padx=6)

    def append_rows(self, rows, *, historical, pending, source, display_values, continuation):
        # 只插入门面已核验令牌的当前批次，本组件不注册异步任务。
        for row in rows:
            tree = self._tree_for(row, historical, pending=pending, source=source, continuation=continuation)
            values = display_values(row)
            result = values[-1]
            if historical:
                result = "已作废（映射已变，决定不能应用） · " + result
            elif pending and row.decision:
                result = "✓ 已处理 · " + result
            elif row.reason == "用户选择跳过":
                result = "用户选择 · " + result
            tag = "obsolete" if historical else "handled" if pending and row.decision else "normal"
            item = tree.insert("", "end", values=(*values[:4], result), tags=(tag,))
            tree.configure(height=len(tree.get_children()))
            self.row_locations[row.row_number] = (tree, item, row)

    def highlight(self, number):
        location = self.row_locations.get(number)
        if location is not None:
            tree, item, _row = location
            tree.selection_set(item)
            tree.focus(item)
            self.on_select(tree)
            if not tree.winfo_exists():
                return  # 行试算可能刷新当前页，位置由新渲染完成后恢复。
            tree.see(item)
            bounds = tree.bbox(item)
            if bounds:
                canvas = self.details._parent_canvas
                y = tree.winfo_rooty() - self.details.winfo_rooty() + bounds[1]
                height = max(1, self.details.winfo_height())
                canvas.yview_moveto(y / height)

    def _wheel(self, event):
        if self.closed or event.widget.winfo_toplevel() is not self.window:
            return
        widget = event.widget
        while widget is not None and widget is not self.details._parent_frame:
            widget = getattr(widget, "master", None)
        if widget is None:
            return  # 字段区、备注文本、下拉弹层不借用明细的滚动速度。
        row_height = int(ttk.Style(self.window).lookup("ImportPreview.Treeview", "rowheight"))
        distance = self._wheel_remainder - event.delta * row_height * 3 / 120
        pixels = int(distance)
        self._wheel_remainder = distance - pixels  # 小幅连续滚动累积，不因取整而丢失。
        self.details._parent_canvas.yview_scroll(pixels, "units")
        return "break"

