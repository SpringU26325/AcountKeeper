"""表格标签列的摘要测量与浅蓝余数徽标，只处理显示。

widgets.RecordTableFrame 提供完整标签映射、原生 Treeview、测量字体及 tooltip。
tag_layout 同时供原生摘要和 Canvas 绘制使用，避免两套宽度预算得出不同的 N。
Canvas 只覆盖溢出 cell；搜索和编辑仍读取 ui 持有的 store.records，不能解析摘要还原标签。
相关验收入口为 _probe/probe_table_visuals.py；真实鼠标与系统 DPI 效果仍需人工确认。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from types import SimpleNamespace


def tag_layout(tags: tuple[str, ...], width: int, font: tkfont.Font, scale: float) -> tuple[str, int]:
    """返回（完整标签前缀，未显示数量）；余数为 0 表示由原生 cell 全量显示。"""
    # width 是调用方已扣除 cell 外侧留白的物理宽度；font 必须与实际表格绘制一致。
    # 只把胶囊内边距和前缀间隔乘 scale，不能再次缩放列宽或字体测量结果。
    full = "、".join(tags) if tags else "—"
    if not tags or font.measure(full) <= width:
        return full, 0
    # 从最大候选 N 向下试，首个能放下的就是最大完整前缀；始终为余数胶囊预留宽度。
    for count in range(len(tags) - 1, 0, -1):
        prefix, rest = "、".join(tags[:count]), len(tags) - count
        need = font.measure(prefix) + round(6 * scale) + font.measure(f"+{rest}") + 2 * round(6 * scale)
        if need <= width:
            return prefix, rest
    return "", len(tags)  # 名称不截断，极窄列仍保留完整计数交给绘制视口裁切。


class TagBadgeRenderer:
    """管理可见溢出 cell 的 Canvas 槽位池；记录身份始终由 Treeview 决定。"""

    def __init__(self, table, edit_callback) -> None:
        self.table, self.tree, self.tip = table, table.tree, table.tag_tooltip
        self.edit_callback = edit_callback
        # 槽位不固定绑定某个 iid，滚动后按当前可见顺序复用，不能据槽位索引编辑记录。
        self.canvases: list[tk.Canvas] = []
        # 每项保存（注册控件，事件，绑定 ID），精确解绑才能保留 CTk 和其他订阅者。
        self.bindings = []
        self.idle_job: str | None = None  # 合并绘制任务归 tree 注册，由 clear/destroy 取消。
        self.closed = False  # 永久退出标记；普通刷新只 clear，之后仍可 request。
        for sequence, callback in (("<<TreeviewSelect>>", self.request), ("<Destroy>", self.destroy)):
            self.bindings.append((self.tree, sequence, tk.Misc.bind(self.tree, sequence, callback, add="+")))

    def request(self, _event=None) -> None:
        # ui 插入完成、tooltip 的滚动/列宽处理和原生选择事件都汇入这个重绘入口。
        if not self.closed and self.idle_job is None:
            self.idle_job = self.tree.after_idle(self.render)  # 滚动/选择的一批通知只重绘一次。

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
                # 完整标签映射和 tooltip 由 RecordTableFrame 清理，这里只处理覆盖层。
                canvas.place_forget()

    def _canvas(self, index: int) -> tk.Canvas:
        if index == len(self.canvases):
            canvas = tk.Canvas(self.tree, highlightthickness=0, borderwidth=0, takefocus=0)
            self.canvases.append(canvas)
            # 每个可见槽位只绑定一次；滚动只替换内容，不不断积累订阅和控件。
            for sequence, callback in (("<Motion>", self._motion), ("<Leave>", self.tip._leave),
                                       ("<ButtonPress>", self._click), ("<Double-1>", self._double),
                                       ("<MouseWheel>", self._wheel)):
                self.bindings.append((canvas, sequence, tk.Misc.bind(canvas, sequence, callback, add="+")))
        return self.canvases[index]

    def _event(self, event):
        # 所有入口回到同一个Treeview坐标/iid口径，Canvas不能成为第二套记录定位。
        # Canvas 是 tree 的直接子控件，局部 x/y 只需加槽位偏移；屏幕 x_root/y_root 原样保留。
        return SimpleNamespace(**{**vars(event), "widget": self.tree,
                                 "x": event.x + event.widget.winfo_x(),
                                 "y": event.y + event.widget.winfo_y()})

    def _motion(self, event) -> None:
        self.tip._motion(self._event(event))

    def _click(self, event) -> None:
        self.tip.hide()  # Canvas 挡住原生 cell，点击入口也须取消 tooltip 及其延迟显示任务。
        if event.num != 1:
            return
        event = self._event(event)
        iid = self.tree.identify_row(event.y)
        if iid:
            # browse模式仍支持Ctrl取消选择；不合成按下事件，避免干扰Tk双击识别。
            if event.state & 4 and iid in self.tree.selection():
                self.tree.selection_remove(iid)
            else:
                self.tree.selection_set(iid)
            # Treeview 的焦点行与键盘焦点都要同步，点击覆盖层后方向键才能继续操作同一条记录。
            self.tree.focus(iid)
            self.tree.focus_set()

    def _double(self, event) -> None:
        # 覆盖层收到了双击，主动交付一次已有编辑入口；不再合成原生双击以免重复打开。
        self._click(event)
        self.edit_callback(self._event(event))  # 交付原编辑回调，不另开编辑会话。

    def _wheel(self, event) -> str:
        self.tip.hide()
        point = self._event(event)
        self.tree.event_generate("<MouseWheel>", x=point.x, y=point.y, delta=event.delta, state=event.state)
        return "break"  # 已让原生Treeview处理滚轮，阻止宿主再处理一次。

    def render(self) -> None:
        # 消耗便条时先释放句柄；未映射就跳过，后续显示/布局流程需重新 request。
        self.idle_job = None
        if self.closed or not self.tree.winfo_viewable():
            return
        # y 扫描视口，index 只计需要覆盖的 cell；池大小不随账本总记录数增长。
        height, index, y = self.tree.winfo_height(), 0, 0
        scale = self.table._get_widget_scaling()
        while y < height:
            iid = self.tree.identify_row(y)
            if not iid:
                y += 1  # 表头和记录下方空白均可能未命中；找到记录后再按 bbox 跨行。
                continue
            box = self.tree.bbox(iid, "tags")
            if not box:
                # 已无可绘制边界时停止本轮扫描，余下旧槽位由循环后的收尾统一隐藏。
                break
            y = max(y + 1, box[1] + box[3])  # 即使边界高度异常也保证前进，避免空闲绘制卡死。
            prefix, rest = tag_layout(self.table.record_tags.get(iid, ()),
                                      box[2] - round(10 * scale), self.table.tag_font, scale)
            if not rest or box[0] >= self.tree.winfo_width():
                # 全量放下时保留原生文字；不可见列不分配覆盖槽位。
                continue
            canvas = self._canvas(index)
            index += 1
            selected = iid in self.tree.selection()
            # 覆盖层须复现原生行的选中/条纹底色，否则标签列会看起来像未选中。
            background = "#D7E9FC" if selected else ("#F7FAFD" if "stripe" in self.tree.item(iid, "tags") else "#FFFFFF")
            canvas.configure(background=background)
            edge = 1 if box[2] > 2 else 0  # 极窄列不能把覆盖层移到cell之外，否则无法悬停。
            canvas.place(x=box[0] + edge, y=box[1], width=max(1, box[2] - edge * 2), height=box[3])
            # 覆盖几乎整个 cell，遮住其原生摘要以免重复出现前缀/+N；Treeview 的值本身不改。
            tk.Misc.lift(canvas)  # Canvas.lift是图元抬升，覆盖窗口必须调用Misc的窗口抬升。
            canvas.delete("all")
            font, text, padding = self.table.tag_font, f"+{rest}", round(6 * scale)
            # 与 tag_layout 共用字体和 6px 逻辑间距；改绘制预算时必须同步摘要测量预算。
            badge_width = font.measure(text) + 2 * padding
            gap, prefix_width = (round(6 * scale) if prefix else 0), font.measure(prefix)
            left = max(round(5 * scale) - edge, (box[2] - edge * 2 - prefix_width - gap - badge_width) / 2)
            middle = box[3] / 2
            if prefix:
                canvas.create_text(left, middle, text=prefix, font=font, anchor="w", fill="#243447" if selected else "#334155")
            left += prefix_width + gap
            badge_height = min(box[3] - 2, font.metrics("linespace") + round(4 * scale))
            top, bottom, right = middle - badge_height / 2, middle + badge_height / 2, left + badge_width
            radius = min(badge_height / 2, badge_width / 2)
            # 两端圆弧加中间矩形，只有胶囊背景着色，文字仍使用同一字体测量。
            canvas.create_oval(left, top, left + radius * 2, bottom, fill="#EAF2FF", outline="")
            canvas.create_rectangle(left + radius, top, right - radius, bottom, fill="#EAF2FF", outline="")
            canvas.create_oval(right - radius * 2, top, right, bottom, fill="#EAF2FF", outline="")
            canvas.create_text((left + right) / 2, middle, text=text, font=font, fill="#426B9F")
        for canvas in self.canvases[index:]:
            canvas.place_forget()  # 只隐藏不再使用的槽位，活动cell不反复Unmap造成提示闪烁。
        # 视口行数外加两槽容纳边界处的部分可见行，缩窗后也不保留曾经较大的控件池。
        limit = height // int(self.tree.tk.call("ttk::style", "lookup", "Account.Treeview", "-rowheight")) + 2
        for canvas in self.canvases[limit:]:
            self._unbind(canvas)
            canvas.destroy()  # 窗口缩小后回收高水位槽位，池大小继续受当前视口约束。
        del self.canvases[limit:]

    def _unbind(self, owner) -> None:
        """仅摘指定控件的本组件绑定，供槽位缩减及最终销毁共用。"""
        owned = [entry for entry in self.bindings if entry[0] is owner]
        # 先移出登记表，重复收尾不会再次删除同一命令；其他控件的绑定仍须保留。
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
