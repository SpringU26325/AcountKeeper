"""金额列的收支着色覆盖层，只改显示，不碰 Treeview 值或账本数据。

record_table.RecordTableFrame 提供行映射、原生字体和视口；本模块按可见行复用 Canvas 池。
覆盖层完整盖住 cell 并复现选中/斑马纹底色，文字直接取原生单元格值，避免重影或口径漂移。
验收入口为 _probe/probe_amount_colors_78.py；真实系统 DPI 与配色观感仍需人工确认。
"""

from __future__ import annotations

import tkinter as tk
from decimal import Decimal
from types import SimpleNamespace

# 支出沿用工具栏危险色，收入用深绿；两者在浅底上对比度足够，且不依赖色相单独区分。
EXPENSE_COLOR = "#B84035"
INCOME_COLOR = "#1E7A46"
# 与 record_table._style_table / table_tag_badges 保持同一组行底色，覆盖层不能自成一套。
SELECTED_BG = "#D7E9FC"
STRIPE_BG = "#F7FAFD"
PLAIN_BG = "#FFFFFF"


class AmountColorRenderer:
    """管理可见行金额 cell 的 Canvas 槽位池；记录身份始终由 Treeview 的 iid 决定。"""

    def __init__(self, table, edit_callback) -> None:
        self.table, self.tree, self.tip = table, table.tree, table.tag_tooltip
        self.edit_callback = edit_callback
        # 槽位不固定绑定某个 iid：滚动后按当前可见顺序复用，不能据槽位索引编辑记录。
        self.canvases: list[tk.Canvas] = []
        # 每项保存（注册控件，事件，绑定 ID），精确解绑才能保留 CTk 和其他订阅者。
        self.bindings = []
        self.idle_job: str | None = None  # 合并绘制任务归 tree 注册，由 clear/destroy 取消。
        self.closed = False  # 永久退出标记；普通刷新只 clear，之后仍可 request。
        for sequence, callback in (("<<TreeviewSelect>>", self.request),
                                   ("<Configure>", self.request),
                                   ("<ButtonRelease-1>", self.request),
                                   ("<Destroy>", self.destroy)):
            self.bindings.append((self.tree, sequence, tk.Misc.bind(self.tree, sequence, callback, add="+")))

    def request(self, _event=None) -> None:
        # ui 插入完成、滚动、选中和列宽变化都汇入这个重绘入口，一批通知只重绘一次。
        if not self.closed and self.idle_job is None:
            self.idle_job = self.tree.after_idle(self.render)

    def clear(self) -> None:
        """取消待绘制任务并隐藏槽位，保留控件与绑定供下一轮记录复用。"""
        if self.idle_job is not None:
            job, self.idle_job = self.idle_job, None
            try:
                self.tree.after_cancel(job)
            except tk.TclError:
                pass  # 父窗可能已先取消所属任务，幂等收尾不重抛。
        for canvas in self.canvases:
            if canvas.winfo_exists():
                canvas.place_forget()

    def _canvas(self, index: int) -> tk.Canvas:
        if index == len(self.canvases):
            canvas = tk.Canvas(self.tree, highlightthickness=0, borderwidth=0, takefocus=0)
            self.canvases.append(canvas)
            # 每个槽位只绑定一次；滚动只替换内容，不积累订阅和控件。
            for sequence, callback in (("<ButtonPress>", self._click), ("<Double-1>", self._double),
                                       ("<MouseWheel>", self._wheel), ("<Leave>", self._leave)):
                self.bindings.append((canvas, sequence, tk.Misc.bind(canvas, sequence, callback, add="+")))
        return self.canvases[index]

    def _event(self, event):
        # 所有入口回到同一个 Treeview 坐标/iid 口径，Canvas 不能成为第二套记录定位。
        return SimpleNamespace(**{**vars(event), "widget": self.tree,
                                 "x": event.x + event.widget.winfo_x(),
                                 "y": event.y + event.widget.winfo_y()})

    def _leave(self, _event) -> None:
        # 覆盖层是独立窗口，移出时原生 Treeview 收不到 Leave，提示必须在这里收掉。
        self.tip.hide()

    def _click(self, event) -> None:
        self.tip.hide()  # Canvas 挡住原生 cell，点击入口也须取消 tooltip 及其延迟显示任务。
        if event.num != 1:
            return
        event = self._event(event)
        iid = self.tree.identify_row(event.y)
        if iid:
            # browse 模式仍支持 Ctrl 取消选择；不合成按下事件，避免干扰 Tk 双击识别。
            if event.state & 4 and iid in self.tree.selection():
                self.tree.selection_remove(iid)
            else:
                self.tree.selection_set(iid)
            # 焦点行与键盘焦点都要同步，点击覆盖层后方向键才能继续操作同一条记录。
            self.tree.focus(iid)
            self.tree.focus_set()

    def _double(self, event) -> None:
        # 覆盖层收到了双击，主动交付一次已有编辑入口；不再合成原生双击以免重复打开。
        self._click(event)
        self.edit_callback(self._event(event))

    def _wheel(self, event) -> str:
        self.tip.hide()
        point = self._event(event)
        self.tree.event_generate("<MouseWheel>", x=point.x, y=point.y, delta=event.delta, state=event.state)
        return "break"  # 已让原生 Treeview 处理滚轮，阻止宿主再处理一次。

    def render(self) -> None:
        # 消耗便条时先释放句柄；未映射就跳过，后续显示/布局流程需重新 request。
        self.idle_job = None
        if self.closed or not self.tree.winfo_viewable():
            return
        # y 扫描视口，index 只计需要着色的 cell；池大小不随账本总记录数增长。
        height, index, y = self.tree.winfo_height(), 0, 0
        while y < height:
            iid = self.tree.identify_row(y)
            if not iid:
                y += 1  # 表头和记录下方空白均可能未命中；找到记录后再按 bbox 跨行。
                continue
            box = self.tree.bbox(iid, "amount")
            if not box:
                # 表头位置可能命中已滚出视口的上一行；跳过它，继续寻找下方可见行。
                y += 1
                continue
            y = max(y + 1, box[1] + box[3])  # 即使边界高度异常也保证前进，避免空闲绘制卡死。
            amount = self.table.record_amounts.get(iid)
            if amount is None:
                # 未登记金额的行（隔离宿主直接插入）保留原生文字，不猜颜色也不遮住内容。
                continue
            canvas = self._canvas(index)
            index += 1
            selected = iid in self.tree.selection()
            # 覆盖层须复现原生行的选中/条纹底色，否则金额格会像未选中或被切出一块白条。
            background = SELECTED_BG if selected else (
                STRIPE_BG if "stripe" in self.tree.item(iid, "tags") else PLAIN_BG)
            canvas.configure(background=background)
            # 严丝合缝盖住整个 cell：差一个像素就会露出原生文字，形成重影。
            canvas.place(x=box[0], y=box[1], width=max(1, box[2]), height=max(1, box[3]))
            tk.Misc.lift(canvas)  # Canvas.lift 是图元抬升，覆盖窗口必须调用 Misc 的窗口抬升。
            canvas.delete("all")
            # 直接复读原生值：覆盖层与单元格必须逐字一致，不在这里重新格式化金额。
            text = self.tree.set(iid, "amount")
            canvas.create_text(box[2] / 2, box[3] / 2, text=text, font=self.table.cell_font,
                               anchor="center", fill=self._color(amount))
        for canvas in self.canvases[index:]:
            canvas.place_forget()  # 只隐藏不再使用的槽位，活动 cell 不反复 Unmap 造成闪烁。
        # 视口行数外加两槽容纳边界处的部分可见行，缩窗后也不保留曾经较大的控件池。
        limit = height // int(self.tree.tk.call("ttk::style", "lookup", "Account.Treeview", "-rowheight")) + 2
        for canvas in self.canvases[limit:]:
            self._unbind(canvas)
            canvas.destroy()
        del self.canvases[limit:]

    @staticmethod
    def _color(amount: Decimal) -> str:
        # 符号是唯一判据：数据层负数=支出、正数=收入，不依赖展示文本里的 + / -。
        return EXPENSE_COLOR if amount < 0 else INCOME_COLOR

    def _unbind(self, owner) -> None:
        """仅摘指定控件的本组件绑定，供槽位缩减及最终销毁共用。"""
        owned = [entry for entry in self.bindings if entry[0] is owner]
        self.bindings[:] = [entry for entry in self.bindings if entry[0] is not owner]
        for widget, sequence, binding in owned:
            try:
                tk.Misc.unbind(widget, sequence, binding)
            except tk.TclError:
                pass  # 原生销毁已移走子控件时，继续清理其余存活订阅。

    def destroy(self, _event=None) -> None:
        """永久停止调度，再取消任务、精确解绑并销毁槽位；允许重复收尾。"""
        if self.closed:
            return
        self.closed = True  # 先封住 request，销毁引起的事件不能重新排入绘制任务。
        self.clear()
        for owner in (self.tree, *self.canvases):
            self._unbind(owner)
        for canvas in self.canvases:
            if canvas.winfo_exists():
                canvas.destroy()
        self.canvases.clear()
