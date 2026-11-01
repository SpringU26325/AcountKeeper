"""协调主窗布局任务；跟随几何变化，重计算仅在尺寸稳定后执行。"""

import tkinter as tk


class LayoutCoordinator:
    def __init__(self, table):
        self.table, self.tree = table, table.tree
        self.root = self.tree.winfo_toplevel()
        self.version = 0
        self.closed = False
        self.tasks = {}
        self.bindings = []
        self.signature = None
        self.settle_job = None
        self.chips = None
        self.suspended = False
        self.deferred = {}  # 隐藏期间只保留每槽最新意图，不保留Tk定时器。
        for widget in (self.root, self.tree):
            for event in ("<Configure>", "<Map>"):
                binding = tk.Misc.bind(widget, event, self.changed, add="+")
                self.bindings.append((widget, event, binding))
        binding = tk.Misc.bind(self.tree, "<Destroy>", self.destroy, add="+")
        self.bindings.append((self.tree, "<Destroy>", binding))
        for widget in (self.root, self.tree):
            binding = tk.Misc.bind(widget, "<Unmap>", self.changed, add="+")
            self.bindings.append((widget, "<Unmap>", binding))

    def suspend(self):
        if self.suspended:
            return
        self.suspended = True
        self.version += 1  # 已出队的闭包也必须失效，取消句柄本身不够。
        pending = dict(self.tasks)
        for owner, slot in pending:
            self.cancel(owner, slot)
        self.deferred.update(pending)

    def resume(self):
        self.suspended = False
        self.signature = self.geometry()
        self.version += 1
        pending, self.deferred = self.deferred, {}
        layers = (self.table.amount_colors, self.table.tag_badges)
        for owner, slot, callback, delay in pending.values():
            # 金额的旧重试/校验一律丢弃，两个覆盖层只按最新数据合并重画。
            if owner not in layers and owner is not self:
                self.schedule(owner, slot, callback, delay)
        self.schedule(self, "settle_job", self.settle, 80)
        self.table.request_overlays()

    def geometry(self):
        chips = self.chips
        return (self.root.state(), self.root.winfo_width(), self.root.winfo_height(),
                self.tree.winfo_width(), self.tree.winfo_height(),
                tuple(self.tree.column(c, "width") for c in self.tree["columns"]),
                None if chips is None else (chips.winfo_width(), chips.winfo_height()))

    def changed(self, event=None):
        if self.closed or (event is not None and event.widget not in (self.root, self.tree)):
            return  # 顶层 bindtag 会收到 Canvas 定位事件，不能据此续期。
        if not self.tree.winfo_viewable():
            self.suspend()
            return
        if self.suspended:
            self.resume()
            return
        signature = self.geometry()
        if signature == self.signature:
            return
        self.signature = signature
        self.version += 1
        # 取消并重新登记仍有效的工作；已取出的旧闭包另有版本/身份双重检查。
        pending = list(self.tasks.values())
        for owner, slot, callback, delay in pending:
            self.cancel(owner, slot)
            self.schedule(owner, slot, callback, delay)
        self.schedule(self, "settle_job", self.settle, 80)
        self.table.request_overlays()

    def schedule(self, owner, slot, callback, delay=None):
        if self.closed:
            return
        self.cancel(owner, slot)
        if not self.tree.winfo_viewable():
            self.suspend()
            self.version += 1  # 即使尺寸未变，隐藏期的数据请求也使旧版本失效。
            self.deferred[(owner, slot)] = (owner, slot, callback, delay)
            return
        version = self.version
        key = (owner, slot)

        def run():
            if self.closed or version != self.version or getattr(owner, slot, None) != job:
                return  # 旧任务不能清掉新任务句柄，更不能拿旧几何落笔。
            if not self.tree.winfo_viewable():
                self.suspend()
                return
            if self.geometry() != self.signature:
                self.changed()
                return  # Configure 尚未派发时也不能使用上一版布局。
            self.tasks.pop(key, None)
            setattr(owner, slot, None)
            if slot == "idle_job" and owner in (self.table.amount_colors, self.table.tag_badges):
                # 两层在同一回调内完成，不插入 update，期间 Tk 不会派发新的布局事件。
                for layer in (self.table.amount_colors, self.table.tag_badges):
                    self.cancel(layer, "idle_job")
                    if version == self.version and not layer.closed:
                        layer.render()
            else:
                callback()

        job = self.tree.after_idle(run) if delay is None else self.tree.after(delay, run)
        setattr(owner, slot, job)
        self.tasks[key] = (owner, slot, callback, delay)

    def cancel(self, owner, slot):
        self.deferred.pop((owner, slot), None)
        self.tasks.pop((owner, slot), None)
        job = getattr(owner, slot, None)
        if job is not None:
            setattr(owner, slot, None)
            try:
                self.tree.after_cancel(job)
            except tk.TclError:
                pass  # 销毁期间 Tk 可能已取消同一任务。

    def settle(self):
        if self.geometry() != self.signature:
            self.changed()
            return  # 只由真实变化续期，不建立空闲轮询循环。
        self.table.tag_tooltip._reflow()
        self.table.request_overlays()

    def destroy(self, event=None):
        if self.closed or (event is not None and event.widget is not self.tree):
            return
        self.closed = True
        for owner, slot, _callback, _delay in list(self.tasks.values()):
            self.cancel(owner, slot)
        for widget, sequence, binding in self.bindings:
            try:
                tk.Misc.unbind(widget, sequence, binding)
            except tk.TclError:
                pass  # 父控件先销毁时仍继续清理其他绑定。
        self.bindings.clear()
        self.deferred.clear()
