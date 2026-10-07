"""Treeview标签cell的完整前缀与浅蓝余数徽标；不参与账目数据转换。"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from types import SimpleNamespace


def tag_layout(tags: tuple[str, ...], width: int, font: tkfont.Font, scale: float) -> tuple[str, int]:
    full = "、".join(tags) if tags else "—"
    if not tags or font.measure(full) <= width:
        return full, 0
    # 余数胶囊的两侧内边距及前缀间距也占宽，N不能沿用旧的纯文本预算。
    for count in range(len(tags) - 1, 0, -1):
        prefix, rest = "、".join(tags[:count]), len(tags) - count
        need = font.measure(prefix) + round(6 * scale) + font.measure(f"+{rest}") + 2 * round(6 * scale)
        if need <= width:
            return prefix, rest
    return "", len(tags)  # 名称不截断，极窄列仍保留完整计数交给绘制视口裁切。


class TagBadgeRenderer:
    def __init__(self, table, edit_callback) -> None:
        self.table, self.tree, self.tip = table, table.tree, table.tag_tooltip
        self.edit_callback = edit_callback
        self.canvases: list[tk.Canvas] = []
        self.bindings = []
        self.idle_job: str | None = None
        self.closed = False
        for sequence, callback in (("<<TreeviewSelect>>", self.request), ("<Destroy>", self.destroy)):
            self.bindings.append((self.tree, sequence, tk.Misc.bind(self.tree, sequence, callback, add="+")))

    def request(self, _event=None) -> None:
        if not self.closed and self.idle_job is None:
            self.idle_job = self.tree.after_idle(self.render)  # 滚动/选择的一批通知只重绘一次。

    def clear(self) -> None:
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
            # 每个可见槽位只绑定一次；滚动只替换内容，不不断积累订阅和控件。
            for sequence, callback in (("<Motion>", self._motion), ("<Leave>", self.tip._leave),
                                       ("<ButtonPress>", self._click), ("<Double-1>", self._double),
                                       ("<MouseWheel>", self._wheel)):
                self.bindings.append((canvas, sequence, tk.Misc.bind(canvas, sequence, callback, add="+")))
        return self.canvases[index]

    def _event(self, event):
        # 所有入口回到同一个Treeview坐标/iid口径，Canvas不能成为第二套记录定位。
        return SimpleNamespace(**{**vars(event), "widget": self.tree,
                                 "x": event.x + event.widget.winfo_x(),
                                 "y": event.y + event.widget.winfo_y()})

    def _motion(self, event) -> None:
        self.tip._motion(self._event(event))

    def _click(self, event) -> None:
        self.tip.hide()
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
            self.tree.focus(iid)
            self.tree.focus_set()

    def _double(self, event) -> None:
        self._click(event)
        self.edit_callback(self._event(event))  # 交付原编辑回调，不另开编辑会话。

    def _wheel(self, event) -> str:
        self.tip.hide()
        point = self._event(event)
        self.tree.event_generate("<MouseWheel>", x=point.x, y=point.y, delta=event.delta, state=event.state)
        return "break"  # 已让原生Treeview处理滚轮，阻止宿主再处理一次。

    def render(self) -> None:
        self.idle_job = None
        if self.closed or not self.tree.winfo_viewable():
            return
        height, index, y = self.tree.winfo_height(), 0, 0
        scale = self.table._get_widget_scaling()
        while y < height:
            iid = self.tree.identify_row(y)
            if not iid:
                y += 1  # 仅跳过表头，随后按bbox跨到下一行，不遍历全量记录。
                continue
            box = self.tree.bbox(iid, "tags")
            if not box:
                break
            y = max(y + 1, box[1] + box[3])
            prefix, rest = tag_layout(self.table.record_tags.get(iid, ()),
                                      box[2] - round(10 * scale), self.table.tag_font, scale)
            if not rest or box[0] >= self.tree.winfo_width():
                continue
            canvas = self._canvas(index)
            index += 1
            selected = iid in self.tree.selection()
            background = "#D7E9FC" if selected else ("#F7FAFD" if "stripe" in self.tree.item(iid, "tags") else "#FFFFFF")
            canvas.configure(background=background)
            edge = 1 if box[2] > 2 else 0  # 极窄列不能把覆盖层移到cell之外，否则无法悬停。
            canvas.place(x=box[0] + edge, y=box[1], width=max(1, box[2] - edge * 2), height=box[3])
            tk.Misc.lift(canvas)  # Canvas.lift是图元抬升，覆盖窗口必须调用Misc的窗口抬升。
            canvas.delete("all")
            font, text, padding = self.table.tag_font, f"+{rest}", round(6 * scale)
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
        limit = height // int(self.tree.tk.call("ttk::style", "lookup", "Account.Treeview", "-rowheight")) + 2
        for canvas in self.canvases[limit:]:
            self._unbind(canvas)
            canvas.destroy()  # 窗口缩小后回收高水位槽位，池大小继续受当前视口约束。
        del self.canvases[limit:]

    def _unbind(self, owner) -> None:
        owned = [entry for entry in self.bindings if entry[0] is owner]
        self.bindings[:] = [entry for entry in self.bindings if entry[0] is not owner]
        for widget, sequence, binding in owned:
            try:
                tk.Misc.unbind(widget, sequence, binding)
            except tk.TclError:
                pass  # 原生销毁已移走子控件时，继续清理其余存活订阅。

    def destroy(self, _event=None) -> None:
        if self.closed:
            return
        self.closed = True
        self.clear()
        for owner in (self.tree, *self.canvases):
            self._unbind(owner)
        for canvas in self.canvases:
            if canvas.winfo_exists():
                canvas.destroy()
        self.canvases.clear()
