"""导入预览共用控件及样式工厂；仅管理控件自身的弹层。"""

import customtkinter as ctk

from dialog_lifecycle import ManagedToplevel
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

