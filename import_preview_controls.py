"""导入预览控件和可复用的选择框；仅管理控件自身的弹层。"""

import sys
import tkinter as tk

import customtkinter as ctk

from dialog_common import _parent_work_area
from dialog_lifecycle import ManagedToplevel
from popup_common import anchored_position, make_toggle_button


class _SelectPopup(ManagedToplevel):
    def _windows_set_titlebar_color(self, color_mode: str) -> None:
        # 无标题栏弹层跳过 CTk 的隐藏重绘，避免展开时把焦点交还给主窗。
        return


class _SelectOption(ctk.CTkButton):
    def _on_release(self, event=None):
        # 选中会立即销毁弹层；截断已处理的松开事件，避免 Tk 随后调用已销毁的绑定。
        picked = self._mouse_inside and self.cget("state") != tk.DISABLED
        super()._on_release(event)
        return "break" if picked else None


class PreviewSelect(ctk.CTkFrame):
    """统一实心三角与候选面板，展示名称和回填值可分别设置。"""

    def __init__(self, master, *, values, command, font, width=180, display=str, group=None):
        super().__init__(master, width=width, height=34, corner_radius=8,
                         fg_color="#F8FAFC", border_color="#D8E1EA", border_width=1)
        self.values, self.command, self.font = tuple(values), command, font
        self._display = display
        self._group = group
        self.value, self.state, self.popup = self.values[0], "normal", None
        self._entry_pressed = False
        self.grid_columnconfigure(0, weight=1)
        self.label = ctk.CTkButton(self, text=display(self.value), command=self.open, font=font,
            width=max(60, width - 40), height=26, anchor="w", fg_color="transparent",
            hover_color="#D2DEE9", text_color="#243447", corner_radius=7)
        # 子按钮底层仍是矩形画布；四周留足圆角空间，避免遮住外框描边。
        self.label.grid(row=0, column=0, sticky="ew", padx=(4, 0), pady=4)
        self.toggle = make_toggle_button(self, self.open, height=26)
        self.toggle.grid(row=0, column=1, padx=(0, 4), pady=4)
        tk.Misc.bind(self, "<Destroy>", self._anchor_hidden, add="+")
        tk.Misc.bind(self, "<Unmap>", self._anchor_hidden, add="+")
        # 单个键盘焦点覆盖整个选择框，回车/空格展开，上下键在弹层内移动。
        self.label._canvas.configure(takefocus=1)
        for sequence in ("<Return>", "<space>", "<Down>"):
            self.label._canvas.bind(sequence, lambda event: self.open())

    def set(self, value):
        self.value = value
        self.label.configure(text=self._display(value))

    def get(self):
        return self.value

    def set_values(self, values):
        values = tuple(values)
        if values != self.values:
            self.close()  # 候选有变化时收起旧面板，保持编辑器中尚未提交的原值。
            self.values = values

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
        if self.state == "disabled" or not self.values:
            return
        if self.popup is not None:
            self.close()
            return
        x, y, width, height = self._popup_geometry()
        # Windows 用专用底色露出圆角，列表和描边都使用实际项目配色。
        mask = "#F000F1" if sys.platform == "win32" else "white"
        popup = self.popup = _SelectPopup(self, fg_color=mask)
        popup.withdraw()
        popup.overrideredirect(True)
        if sys.platform == "win32":
            popup.attributes("-transparentcolor", mask)
        self.previous_grab = self.grab_current()
        area = ctk.CTkScrollableFrame(popup, fg_color="white", bg_color=mask, corner_radius=8,
            border_width=1, border_color="#D8E1EA", scrollbar_fg_color="white", scrollbar_button_color="#C6D4DF")
        area.pack(fill="both", expand=True)
        self.items, self.area = [], area
        self.active = self.values.index(self.value) if self.value in self.values else 0
        for index, value in enumerate(self.values):
            if self._group is not None and index == self._group[0]:
                ctk.CTkLabel(area, text=self._group[1], font=self.font, anchor="w", height=24,
                             text_color="#64748B").pack(fill="x", padx=8)
            item = _SelectOption(area, text=("✓ " if value == self.value else "   ") + self._display(value), width=1,
                command=lambda i=index: self._pick(i), font=self.font, height=32,
                corner_radius=5, fg_color="#F0F6FE" if value == self.value else "white", hover_color="#E8F1FC",
                text_color="#243447", anchor="w")
            # 保留检查标记和滚动条的留白，长名称可在行内显示到末尾。
            item._text_label.configure(wraplength=max(1, width - round(56 * self._get_widget_scaling())), justify="left")
            item.pack(fill="x", pady=1)
            self.items.append(item)
        popup.bind("<Down>", lambda event: self._move(1))
        popup.bind("<Up>", lambda event: self._move(-1))
        popup.bind("<Home>", lambda event: self._move(-self.active))
        popup.bind("<End>", lambda event: self._move(len(self.items) - 1 - self.active))
        popup.bind("<Return>", lambda event: self._pick(self.active))
        popup.bind("<Escape>", lambda event: self.close())
        popup.bind("<MouseWheel>", self._scroll_options)
        popup.bind("<ButtonPress-1>", self._outside, add="+")
        popup.bind("<ButtonRelease-1>", self._entry_released, add="+")
        popup.bind("<FocusOut>", lambda event: popup.after_idle(self._focus_left), add="+")
        popup.finish_setup()
        if popup.closing or self.popup is not popup:
            return
        popup.wm_geometry(f"{width}x{height}+{x}+{y}")
        popup.deiconify()
        popup.grab_set()
        popup.focus_force()
        self.toggle.configure(text="▲")
        popup.update_idletasks()
        if self.popup is popup and not popup.closing:
            self._move(0)

    def _popup_geometry(self):
        # 统一使用物理坐标。保持左沿贴入口，缺空间时先翻转，再缩短名单视口。
        x, y, anchor_height = self.winfo_rootx(), self.winfo_rooty(), self.winfo_height()
        try:
            _, work, _ = _parent_work_area(self.winfo_toplevel())
            left, top, right, bottom = work.left, work.top, work.right, work.bottom
        except (AttributeError, OSError):
            left, top, right, bottom = 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()
        scale = self._get_widget_scaling()
        width = max(1, min(max(self.winfo_width(), round(245 * scale)), right - left - 16))
        group_height = 24 if self._group is not None and self._group[0] < len(self.values) else 0
        wanted = round(min(320, len(self.values) * 34 + 20 + group_height) * scale)
        height = max(1, min(wanted, max(y - top, bottom - y - anchor_height) - 14))
        offset_x, offset_y = anchored_position((x - left, y - top, anchor_height), (width, height),
                                               (right - left, bottom - top))
        return left + offset_x, top + offset_y, width, height

    def _move(self, delta):
        previous = self.active
        self.active = (self.active + delta) % len(self.items)
        self.items[previous].configure(fg_color="#F0F6FE" if self.values[previous] == self.value else "white")
        item = self.items[self.active]
        item.configure(fg_color="#DCEBFB")  # 焦点底色与当前项的勾选、浅底是两个独立状态。
        canvas = self.area._parent_canvas
        top, bottom = item.winfo_y(), item.winfo_y() + item.winfo_height()
        first, last = canvas.canvasy(0), canvas.canvasy(canvas.winfo_height())
        if top < first or bottom > last:
            extent = canvas.bbox("all")
            if extent and extent[3] > 0:
                canvas.yview_moveto((top if top < first else bottom - canvas.winfo_height()) / extent[3])
        return "break"

    def _scroll_options(self, event):
        # 在弹层内消费滚轮，外层预算列表不会跟着滚动而带走锚点。
        canvas = self.area._parent_canvas
        extent = canvas.bbox("all")
        if extent and extent[3] > 0:
            distance = event.delta * self._get_widget_scaling() * 102 / 120
            canvas.yview_moveto((canvas.canvasy(0) - distance) / extent[3])
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

    def _anchor_hidden(self, event):
        if event.widget is self:
            self.close()

    def destroy(self):
        self.close()
        super().destroy()


class PreviewControls:
    def __init__(self, font):
        self.font = font

    def label(self, parent, text="", **options):
        options.setdefault("height", 22)
        options.setdefault("text_color", "#243447")
        return ctk.CTkLabel(parent, text=text, font=self.font, **options)

    def check(self, parent, **options):
        return ctk.CTkCheckBox(parent, font=self.font, fg_color="#2F80ED", hover_color="#256BC7",
                              border_color="#90A4AE", text_color="#243447", **options)

    def button(self, parent, text, command, **options):
        primary = options.pop("primary", False)
        options.setdefault("fg_color", "#2F80ED" if primary else "#F0F4F8")
        options.setdefault("hover_color", "#256BC7" if primary else "#D2DEE9")
        options.setdefault("text_color", "white" if primary else "#243447")
        button = ctk.CTkButton(parent, text=text, command=command, font=self.font,
                              corner_radius=8, **options)
        button.enabled_color = options["fg_color"]  # type: ignore[reportAttributeAccessIssue] # 运行时附加的禁用底色，供 button_state 切换。
        return button

    def button_state(self, button, enabled):
        # CTk 默认只灰掉文字；同时弱化底色，避免禁用的蓝按钮看起来仍能提交。
        button.configure(state="normal" if enabled else "disabled",
                         fg_color=button.enabled_color if enabled else "#E5EBF1")

