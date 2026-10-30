"""账目表格、完整标签映射、金额着色与悬停提示，只管理表现层状态。

原生摘要与徽标共用 table_tag_badges.tag_layout，不能解析摘要还原业务数据。
金额列由 table_amount_colors 覆盖着色，行底色与原生单元格逐像素对齐。
表格先创建 tooltip，再创建引用它的徽标层和金额层；任务和绑定由各自控制器清理。
验收入口为 _probe/probe_table_visuals.py、_probe/probe_amount_colors_78.py 和 _probe/probe_main_layout.py。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from decimal import Decimal
import tkinter as tk
from typing import Callable
from tkinter import ttk, font as tkfont

import customtkinter as ctk

from table_amount_colors import AmountColorRenderer
from table_tag_badges import TagBadgeRenderer, tag_layout


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
        # 金额着色按 iid 取原始 Decimal：符号来自数据层，不解析展示文本里的 + / -。
        self.record_amounts: dict[str, Decimal] = {}
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
        self.amount_colors = AmountColorRenderer(self, edit_callback)
        self.tree.configure(yscrollcommand=self._on_scroll)
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

    def _on_scroll(self, first, last) -> None:
        # 滚动时两类覆盖层的位置全部失效；提示/滚动条归 tooltip，金额层只补一次重绘请求。
        self.tag_tooltip.scrolled(first, last)
        self.amount_colors.request()

    def request_overlays(self) -> None:
        """插入/刷新完成后统一请求两类覆盖层，避免调用方漏掉其中一层。"""
        self.tag_badges.request()
        self.amount_colors.request()

    def _style_table(self) -> None:
        scale = self._get_widget_scaling()
        # CTk使用逻辑像素，Tk负字号使用物理像素，避免表格被再次按系统点数放大。
        font = ("Microsoft YaHei UI", -max(1, round(12 * scale)))
        # 金额覆盖层直接复用原生字体描述符，字体不同会立刻表现为重影或宽度不一致。
        self.cell_font = font
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
            if hasattr(self, "amount_colors"):
                self.amount_colors.request()  # 字号与行高都变了，旧覆盖层必须按新 bbox 重画。

    def set_record_amount(self, iid: str, amount: Decimal) -> None:
        """登记原始金额供着色层取符号；展示文本仍由 ui 生成，不在这里格式化。"""
        self.record_amounts[iid] = amount

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
        self.amount_colors.clear()
        # 金额映射与标签映射同批失效：复用 iid 时不能把上一轮的符号带进新行。
        self.record_amounts.clear()


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
