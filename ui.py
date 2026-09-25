"""CustomTkinter user interface for AccountKeeper."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import os
import random
import re
import subprocess
import sys
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from typing import cast
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
from PIL import Image

from config import (
    DATA_DIR,
    DEFAULT_SNAIL_MESSAGE,
    SNAIL_IMAGE_CANDIDATES,
    SNAIL_IMAGE_PATH,
    SNAIL_MESSAGES_PATH,
)
from store import Account, AccountStore
class AccountKeeperApp(ctk.CTk):
    """The desktop interface for viewing and managing expense records."""

    def __init__(self, store: AccountStore) -> None:
        super().__init__()
        self.store = store
        self.title("AccountKeeper 本地记账")
        self.geometry("960x680")
        self.minsize(820, 560)
        self.configure(fg_color="#F0F4F8")
        self.snail_animation_id: str | None = None
        self.snail_x = 0
        self.snail_started = False
        self.snail_paused = False
        self._bubble_window: tk.Toplevel | None = None
        self._build_widgets()
        self.refresh_records()
        self._setup_snail()

    def _setup_snail(self) -> None:
        """在标题左侧加载 40 像素蜗牛图标，避免遮挡业务控件。"""
        try:
            image = self._prepare_snail_image()
            image.thumbnail((40, 40), Image.Resampling.LANCZOS)
            self.snail_photo = ctk.CTkImage(
                light_image=image,
                dark_image=image,
                size=image.size,
            )
        except (OSError, ValueError) as error:
            print(f"警告：无法加载蜗牛图片：{error}")
            self.snail_photo = None
            return

        self.snail_label = ctk.CTkLabel(
            self,
            image=self.snail_photo,
            text="",
            cursor="hand2",
            fg_color="transparent",
        )
        self.update_idletasks()
        self.snail_x = self.winfo_width()
        self.snail_label.place(x=self.snail_x, y=15, anchor="nw")
        self.snail_label.bind("<Button-1>", self._snail_clicked)
        self._animate_snail()

    def _prepare_snail_image(self) -> Image.Image:
        """处理白底、等比缩放，并加深蜗牛线条颜色。"""
        image_path = next(
            (path for path in SNAIL_IMAGE_CANDIDATES if path.exists()),
            SNAIL_IMAGE_PATH,
        )
        image = Image.open(image_path).convert("RGBA")
        image.thumbnail((100, 100), Image.Resampling.LANCZOS)
        for y in range(image.height):
            for x in range(image.width):
                red, green, blue, alpha_value = cast(
                    tuple[int, int, int, int], image.getpixel((x, y))
                )
                if red > 230 and green > 230 and blue > 230:
                    image.putpixel((x, y), (red, green, blue, 0))
                elif blue > red + 20 and blue > green + 5:
                    image.putpixel((x, y), (30, 58, 138, alpha_value))
        return image

    def _animate_snail(self) -> None:
        """让蜗牛从右向左爬行，遇到标题和左边界时传送。"""
        if self.snail_paused:
            self.snail_animation_id = self.after(30, self._animate_snail)
            return
        if self.winfo_width() <= 1 or not self.winfo_ismapped():
            self.after(100, self._animate_snail)
            return
        if not self.snail_label.winfo_exists():
            return

        window_width = self.winfo_width()
        snail_width = self.snail_label.winfo_width()
        if not self.snail_started:
            self.snail_x = window_width
            self.snail_started = True
        text_left = self.title_block.winfo_rootx() - self.winfo_rootx()
        text_right = text_left + self.title_block.winfo_width()

        self.snail_x -= 2
        if self.snail_x <= text_right and self.snail_x + snail_width > text_left:
            self.snail_x = text_left - snail_width
        if self.snail_x < 0:
            self.snail_x = window_width

        self.snail_label.place_configure(x=self.snail_x, y=15)
        self.snail_animation_id = self.after(30, self._animate_snail)

    def _destroy_active_bubble(self, restore_pause: bool = True) -> None:
        """销毁当前存在的气泡窗口，并在需要时恢复蜗牛动画。"""
        if restore_pause:
            self.snail_paused = False
        if self._bubble_window is not None:
            if self._bubble_window.winfo_exists():
                self._bubble_window.destroy()
            self._bubble_window = None

    def _show_speech_bubble(self, message: str) -> None:
        """在蜗牛上方弹出一个纯透明背景的圆角气泡。"""
        self._destroy_active_bubble(restore_pause=False)
        if not hasattr(self, "snail_label") or not self.snail_label.winfo_exists():
            return

        snail_x = self.snail_label.winfo_rootx()
        snail_y = self.snail_label.winfo_rooty()
        snail_width = self.snail_label.winfo_width()
        snail_height = self.snail_label.winfo_height()
        snail_center_x = snail_x + snail_width / 2
        snail_center_y = snail_y + snail_height / 2

        bubble = tk.Toplevel(self)
        bubble.overrideredirect(True)
        bubble.attributes("-topmost", True)
        bubble.configure(bg="magenta")
        bubble.wm_attributes("-transparentcolor", "magenta")

        bubble_font = ("Microsoft YaHei UI", 11)
        temp_font = tkfont.Font(family="Microsoft YaHei UI", size=11)
        padding_x = 18
        padding_y = 12
        bubble_width = max(120, temp_font.measure(message) + padding_x * 2)
        bubble_height = max(40, temp_font.metrics("linespace") + padding_y * 2)

        bubble_x = int(snail_center_x - bubble_width / 2)
        bubble_y = int(snail_center_y - snail_height / 2 - bubble_height - 12)
        screen_width = self.winfo_screenwidth()
        margin = 12
        if bubble_x < margin:
            bubble_x = margin
        if bubble_x + bubble_width > screen_width - margin:
            bubble_x = screen_width - margin - bubble_width
        if bubble_y < margin:
            bubble_y = max(margin, int(snail_center_y + snail_height / 2 + 12))

        canvas = tk.Canvas(
            bubble,
            width=bubble_width,
            height=bubble_height + 12,
            bg="magenta",
            highlightthickness=0,
        )
        canvas.pack()

        bubble.geometry(f"{bubble_width}x{bubble_height + 12}+{bubble_x}+{bubble_y}")

        radius = min(14, bubble_width // 4, bubble_height // 2)
        rounded_body = [
            (radius, 0),
            (bubble_width - radius, 0),
            (bubble_width, radius),
            (bubble_width, bubble_height - radius),
            (bubble_width - radius, bubble_height),
            (radius, bubble_height),
            (0, bubble_height - radius),
            (0, radius),
        ]
        canvas.create_polygon(
            rounded_body,
            fill="#FFF7D8",
            outline="#FFF7D8",
            smooth=True,
        )

        tail_x = int(snail_center_x - bubble_x)
        tail_points = [
            (tail_x - 10, bubble_height - 1),
            (tail_x + 10, bubble_height - 1),
            (tail_x, bubble_height + 12),
        ]
        canvas.create_polygon(
            tail_points,
            fill="#FFF7D8",
            outline="#FFF7D8",
            smooth=True,
        )

        cx = bubble_width / 2
        cy = bubble_height / 2
        canvas.create_text(
            cx,
            cy,
            text=message,
            fill="#3E4A5A",
            font=bubble_font,
            justify="center",
        )
        bubble.bind("<Button-1>", lambda _event: self._destroy_active_bubble())
        self._bubble_window = bubble
        bubble.after(3000, self._destroy_active_bubble)

    def _snail_clicked(self, _event: tk.Event) -> None:
        """点击蜗牛时随机显示一条 JSON 消息气泡，并暂停蜗牛动画。"""
        self.snail_paused = True
        message = DEFAULT_SNAIL_MESSAGE
        try:
            with SNAIL_MESSAGES_PATH.open("r", encoding="utf-8") as file:
                messages = json.load(file)
            if not isinstance(messages, list) or not messages:
                raise ValueError("消息列表为空或格式错误")
            text_messages = [item for item in messages if isinstance(item, str) and item]
            if not text_messages:
                raise ValueError("消息列表中没有有效文本")
            message = random.choice(text_messages)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            print(f"警告：无法读取蜗牛消息，使用默认提示：{error}")
        self._show_speech_bubble(message)

    def _build_widgets(self) -> None:
        font_regular = ("Microsoft YaHei UI", 12)
        font_small = ("Microsoft YaHei UI", 11)
        font_title = ("Microsoft YaHei UI", 16, "bold")

        self.header = ctk.CTkFrame(
            self, height=58, fg_color="transparent", corner_radius=0
        )
        self.header.pack(fill="x", padx=24, pady=(10, 5))
        self.header.pack_propagate(False)
        self.title_block = ctk.CTkFrame(
            self, fg_color="transparent", corner_radius=0
        )
        self.title_block.place(relx=0.5, rely=0.05, anchor="center")
        ctk.CTkLabel(
            self.title_block,
            text="AccountKeeper",
            font=font_title,
            text_color="#243447",
        ).pack(anchor="center")
        ctk.CTkLabel(
            self.title_block,
            text="本地收支记录",
            font=font_small,
            text_color="#607D8B",
        ).pack(anchor="center")

        input_frame = ctk.CTkFrame(
            self,
            corner_radius=14,
            fg_color="#FFFFFF",
            border_width=1,
            border_color="#D8E1EA",
        )
        input_frame.pack(fill="x", padx=24, pady=(0, 8))
        ctk.CTkLabel(
            input_frame,
            text="添加记录",
            font=("Microsoft YaHei UI", 13, "bold"),
            text_color="#243447",
        ).grid(row=0, column=0, columnspan=8, padx=16, pady=(12, 4), sticky="w")

        self.date_var = tk.StringVar(value=date.today().isoformat())
        self.amount_var = tk.StringVar()
        self.category_var = tk.StringVar()
        self.note_var = tk.StringVar()
        fields = (
            ("日期", self.date_var, 13),
            ("金额", self.amount_var, 12),
            ("类别", self.category_var, 14),
            ("备注", self.note_var, 28),
        )
        for column, (label, variable, width) in enumerate(fields):
            ctk.CTkLabel(
                input_frame,
                text=label,
                font=font_small,
                text_color="#455A64",
            ).grid(
                row=1, column=column * 2, padx=(16, 6), pady=(4, 14), sticky="w"
            )
            ctk.CTkEntry(
                input_frame,
                textvariable=variable,
                width=width * 8,
                height=36,
                font=font_regular,
                corner_radius=9,
                border_width=1,
                border_color="#C6D4DF",
                fg_color="#F8FAFC",
            ).grid(
                row=1,
                column=column * 2 + 1,
                padx=(0, 10),
                pady=(4, 14),
                sticky="ew",
            )
        ctk.CTkButton(
            input_frame,
            text="添加记录",
            command=self.add_record,
            width=108,
            height=36,
            corner_radius=10,
            fg_color="#2F80ED",
            hover_color="#256AC4",
            font=font_small,
        ).grid(row=1, column=8, padx=(4, 16), pady=(4, 14), sticky="e")
        for column in (1, 3, 5, 7):
            input_frame.columnconfigure(column, weight=1)

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill="x", padx=24, pady=(0, 10))
        button_config = {
            "height": 34,
            "corner_radius": 9,
            "font": font_small,
        }
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self.filter_records)
        # 注意：CustomTkinter 6.0.0 的占位符在绑定 textvariable 时不会激活，
        # 因此这里不传 textvariable，改为按键时把内容同步到 search_var。
        self.search_entry = ctk.CTkEntry(
            toolbar,
            width=200,
            height=34,
            corner_radius=9,
            border_width=1,
            border_color="#C6D4DF",
            fg_color="#FFFFFF",
            font=font_small,
            placeholder_text="🔍 搜索日期/类别/备注",
        )
        self.search_entry.pack(side="left", padx=(0, 10))
        self.search_entry.bind(
            "<KeyRelease>",
            lambda _event: self.search_var.set(self.search_entry.get()),
        )
        ctk.CTkButton(
            toolbar,
            text="刷新",
            command=self.refresh_records,
            width=78,
            fg_color="#607D8B",
            hover_color="#4F6873",
            **button_config,
        ).pack(side="left")
        ctk.CTkButton(
            toolbar,
            text="删除选中记录",
            command=self.delete_record,
            width=132,
            fg_color="#E76F51",
            hover_color="#C9573D",
            **button_config,
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            toolbar,
            text="月度统计",
            command=self.show_stats,
            width=108,
            fg_color="#5B8E7D",
            hover_color="#477564",
            **button_config,
        ).pack(side="left")
        ctk.CTkButton(
            toolbar,
            text="导出为CSV",
            command=self.export_csv,
            width=110,
            fg_color="#7B6D8D",
            hover_color="#635775",
            **button_config,
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            toolbar,
            text="查看图表",
            command=self.show_chart,
            width=108,
            fg_color="#4A90D9",
            hover_color="#3679BA",
            **button_config,
        ).pack(side="left")
        ctk.CTkButton(
            toolbar,
            text="打开数据目录",
            command=self.open_data_folder,
            width=132,
            fg_color="#607D8B",
            hover_color="#4F6873",
            **button_config,
        ).pack(side="left", padx=8)
        self.summary_var = tk.StringVar()
        ctk.CTkLabel(
            toolbar,
            textvariable=self.summary_var,
            font=font_small,
            text_color="#546E7A",
        ).pack(side="right")

        table_frame = ctk.CTkFrame(
            self,
            corner_radius=14,
            fg_color="#FFFFFF",
            border_width=1,
            border_color="#D8E1EA",
        )
        table_frame.pack(fill="both", expand=True, padx=24, pady=(0, 0))
        table_inner = ctk.CTkFrame(table_frame, fg_color="transparent")
        table_inner.pack(fill="both", expand=True, padx=10, pady=10)
        columns = ("id", "date", "amount", "category", "note")
        style = ttk.Style(self)
        style.configure(
            "Account.Treeview",
            background="#FFFFFF",
            fieldbackground="#FFFFFF",
            foreground="#263238",
            rowheight=34,
            font=font_small,
            borderwidth=0,
        )
        style.configure(
            "Account.Treeview.Heading",
            background="#E8F0F6",
            foreground="#37474F",
            font=("Microsoft YaHei UI", 11, "bold"),
            relief="flat",
        )
        style.map("Account.Treeview", background=[("selected", "#D7E9FC")])
        self.tree = ttk.Treeview(
            table_inner,
            columns=columns,
            show="headings",
            style="Account.Treeview",
        )
        headings = (
            ("id", "ID", 70),
            ("date", "日期", 120),
            ("amount", "金额", 120),
            ("category", "类别", 140),
            ("note", "备注", 300),
        )
        for column, title, width in headings:
            self.tree.heading(column, text=title)
            self.tree.column(
                column,
                width=width,
                anchor="center" if column != "note" else "w",
            )
        scrollbar = ttk.Scrollbar(table_inner, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Double-1>", self.edit_record)
        table_inner.rowconfigure(0, weight=1)
        table_inner.columnconfigure(0, weight=1)

    def refresh_records(self) -> None:
        self.filter_records()

    def open_data_folder(self) -> None:
        try:
            if sys.platform.startswith("win"):
                os.startfile(DATA_DIR)
            elif sys.platform == "darwin":
                subprocess.run(["open", str(DATA_DIR)])
            else:
                subprocess.run(["xdg-open", str(DATA_DIR)])
        except Exception as error:
            messagebox.showinfo("数据目录", f"数据目录位于：\n{DATA_DIR}")

    def filter_records(self, *_args: str) -> None:
        keyword = self.search_var.get().strip().lower()
        for item in self.tree.get_children():
            self.tree.delete(item)
        filtered_records = []
        for record in sorted(
            self.store.records,
            key=lambda item: (item.record_date, item.record_id),
            reverse=True,
        ):
            searchable_text = (
                record.record_date.lower(),
                record.category.lower(),
                record.note.lower(),
            )
            if keyword and not any(keyword in text for text in searchable_text):
                continue
            filtered_records.append(record)
            self.tree.insert(
                "",
                "end",
                iid=str(record.record_id),
                values=(
                    record.record_id,
                    record.record_date,
                    f"{record.amount:.2f}",
                    record.category,
                    record.note,
                ),
            )
        income = sum(
            (record.amount for record in filtered_records if record.amount > 0),
            Decimal("0"),
        )
        expense = sum(
            (-record.amount for record in filtered_records if record.amount < 0),
            Decimal("0"),
        )
        self.summary_var.set(
            f"收: {income:.2f} | 支: {expense:.2f}"
        )

    def add_record(self) -> None:
        try:
            record_date = datetime.strptime(self.date_var.get().strip(), "%Y-%m-%d").date()
            amount = Decimal(self.amount_var.get().strip())
        except (ValueError, InvalidOperation):
            messagebox.showerror("输入错误", "日期格式应为 YYYY-MM-DD，金额必须是数字。")
            return
        category = self.category_var.get().strip()
        if amount == 0 or not category:
            messagebox.showerror(
                "输入错误", "金额不能为 0；正数表示收入，负数表示支出，类别不能为空。"
            )
            return
        self.store.add(record_date.isoformat(), amount, category, self.note_var.get())
        self.amount_var.set("")
        self.category_var.set("")
        self.note_var.set("")
        self.refresh_records()

    def delete_record(self) -> None:
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("删除记录", "请先选择一条记录。")
            return
        record_id = int(selected[0])
        if not messagebox.askyesno("确认删除", "确定要删除这条记录吗？"):
            return
        if not self.store.delete(record_id):
            messagebox.showinfo("删除记录", "找不到该记录")
            return
        self.refresh_records()

    def edit_record(self, event: tk.Event) -> None:
        """双击记录行时打开编辑窗口。"""
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return
        try:
            record_id = int(item_id)
        except ValueError:
            return
        record = next(
            (item for item in self.store.records if item.record_id == record_id),
            None,
        )
        if record is None:
            return

        edited_values = self.ask_edit_record(record)
        if edited_values is None:
            return
        record_date, amount, category, note = edited_values
        if not self.store.update(record_id, record_date, amount, category, note):
            messagebox.showerror("编辑失败", "找不到该记录或记录更新失败。")
            return
        self.refresh_records()

    def export_csv(self) -> None:
        month = self.ask_month("导出账单", "请输入要导出的月份（格式：YYYY-MM）")
        if month is None:
            return

        if not any(record.record_date.startswith(month) for record in self.store.records):
            messagebox.showinfo("无法导出", "该月没有记录，无法导出")
            return

        save_path_str = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=f"account_export_{month}.csv",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        if not save_path_str:
            return

        save_path = Path(save_path_str)
        try:
            export_path = self.store.export_month_csv(month, save_path)
        except OSError as error:
            messagebox.showerror("导出失败", f"无法写入导出文件：{error}")
            return

        messagebox.showinfo(
            "导出成功",
            f"导出成功！文件已保存到：\n{export_path}",
        )

    def ask_month(self, title: str, prompt: str) -> str | None:
        """显示自定义月份输入框，并返回通过校验的月份。"""
        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        dialog.geometry("420x240")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.configure(fg_color="#F0F4F8")

        dialog_font = ("Microsoft YaHei UI", 11)
        title_font = ("Microsoft YaHei UI", 13, "bold")
        result: list[str | None] = [None]

        content = ctk.CTkFrame(dialog, fg_color="transparent")
        content.pack(fill="both", expand=True)
        ctk.CTkLabel(
            content,
            text=title,
            font=title_font,
            text_color="#243447",
        ).pack(anchor="w", padx=24, pady=(20, 0))
        ctk.CTkLabel(
            content,
            text=prompt,
            font=dialog_font,
            text_color="#455A64",
        ).pack(anchor="w", padx=24, pady=(12, 8))

        month_var = tk.StringVar()
        entry = ctk.CTkEntry(
            content,
            textvariable=month_var,
            font=dialog_font,
            height=38,
            corner_radius=9,
            border_width=1,
            border_color="#C6D4DF",
            fg_color="#FFFFFF",
        )
        entry.pack(fill="x", padx=24)
        error_var = tk.StringVar()
        ctk.CTkLabel(
            content,
            textvariable=error_var,
            text_color="#C62828",
            font=("Microsoft YaHei UI", 10),
        ).pack(
            anchor="w", padx=24, pady=(5, 0)
        )

        buttons = ctk.CTkFrame(content, fg_color="transparent")
        buttons.pack(anchor="e", padx=24, pady=(14, 0))

        def cancel() -> None:
            dialog.destroy()

        def confirm() -> None:
            month = month_var.get().strip()
            try:
                valid_format = re.fullmatch(r"\d{4}-\d{2}", month) is not None
                if not valid_format:
                    raise ValueError
                datetime.strptime(month, "%Y-%m")
            except ValueError:
                error_var.set("格式错误，请输入有效的 YYYY-MM 月份。")
                entry.focus_set()
                return
            result[0] = month
            dialog.destroy()

        ctk.CTkButton(
            buttons,
            text="取消",
            command=cancel,
            width=88,
            height=34,
            corner_radius=9,
            fg_color="#90A4AE",
            hover_color="#78909C",
            font=dialog_font,
        ).pack(side="right")
        ctk.CTkButton(
            buttons,
            text="确定",
            command=confirm,
            width=88,
            height=34,
            corner_radius=9,
            fg_color="#2F80ED",
            hover_color="#256AC4",
            font=dialog_font,
        ).pack(side="right", padx=(0, 8)
        )
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.bind("<Return>", lambda _event: confirm())
        dialog.bind("<Escape>", lambda _event: cancel())
        entry.focus_set()
        dialog.grab_set()
        self.wait_window(dialog)
        return result[0]

    def ask_edit_record(
        self, record: Account
    ) -> tuple[str, Decimal, str, str] | None:
        """显示预填记录编辑框，并返回通过校验的字段。"""
        dialog = ctk.CTkToplevel(self)
        dialog.title("编辑记录")
        dialog.geometry("460x360")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.configure(fg_color="#F0F4F8")

        dialog_font = ("Microsoft YaHei UI", 11)
        title_font = ("Microsoft YaHei UI", 13, "bold")
        result: list[tuple[str, Decimal, str, str] | None] = [None]
        date_var = tk.StringVar(value=record.record_date)
        amount_var = tk.StringVar(value=f"{record.amount:.2f}")
        category_var = tk.StringVar(value=record.category)
        note_var = tk.StringVar(value=record.note)

        content = ctk.CTkFrame(dialog, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=24, pady=20)
        ctk.CTkLabel(
            content,
            text="编辑记录",
            font=title_font,
            text_color="#243447",
        ).pack(anchor="w")

        fields = (
            ("日期", date_var),
            ("金额", amount_var),
            ("类别", category_var),
            ("备注", note_var),
        )
        entries: list[ctk.CTkEntry] = []
        for label, variable in fields:
            ctk.CTkLabel(
                content,
                text=label,
                font=dialog_font,
                text_color="#455A64",
            ).pack(anchor="w", pady=(10, 3))
            entry = ctk.CTkEntry(
                content,
                textvariable=variable,
                font=dialog_font,
                height=34,
                corner_radius=9,
                border_width=1,
                border_color="#C6D4DF",
                fg_color="#FFFFFF",
            )
            entry.pack(fill="x")
            entries.append(entry)

        error_var = tk.StringVar()
        ctk.CTkLabel(
            content,
            textvariable=error_var,
            text_color="#C62828",
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w", pady=(5, 0))

        buttons = ctk.CTkFrame(content, fg_color="transparent")
        buttons.pack(anchor="e", pady=(10, 0))

        def cancel() -> None:
            dialog.destroy()

        def confirm() -> None:
            try:
                parsed_date = datetime.strptime(
                    date_var.get().strip(), "%Y-%m-%d"
                ).date()
                parsed_amount = Decimal(amount_var.get().strip())
            except (ValueError, InvalidOperation):
                error_var.set("日期格式应为 YYYY-MM-DD，金额必须是数字。")
                return
            parsed_category = category_var.get().strip()
            if parsed_amount == 0 or not parsed_category:
                error_var.set(
                    "金额不能为 0；正数表示收入，负数表示支出，类别不能为空。"
                )
                return
            result[0] = (
                parsed_date.isoformat(),
                parsed_amount,
                parsed_category,
                note_var.get().strip(),
            )
            dialog.destroy()

        ctk.CTkButton(
            buttons,
            text="取消",
            command=cancel,
            width=88,
            height=34,
            corner_radius=9,
            fg_color="#90A4AE",
            hover_color="#78909C",
            font=dialog_font,
        ).pack(side="right")
        ctk.CTkButton(
            buttons,
            text="确定",
            command=confirm,
            width=88,
            height=34,
            corner_radius=9,
            fg_color="#2F80ED",
            hover_color="#256AC4",
            font=dialog_font,
        ).pack(side="right", padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.bind("<Return>", lambda _event: confirm())
        dialog.bind("<Escape>", lambda _event: cancel())
        entries[0].focus_set()
        dialog.grab_set()
        self.wait_window(dialog)
        return result[0]

    def show_stats(self) -> None:
        month = self.ask_month("选择统计月份", "请输入要统计的月份（格式：YYYY-MM）")
        if month is None:
            return
        month_records = [
            record
            for record in self.store.records
            if record.record_date.startswith(month + "-")
        ]
        total_income = sum(
            (record.amount for record in month_records if record.amount > 0),
            Decimal("0"),
        )
        total_expense = sum(
            (-record.amount for record in month_records if record.amount < 0),
            Decimal("0"),
        )
        balance = total_income - total_expense
        category_totals: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for record in month_records:
            if record.amount < 0:
                category_totals[record.category] -= record.amount
        if not month_records:
            details = "该月份没有记账记录"
        elif total_expense == 0:
            details = "该月份只有收入，没有支出"
        else:
            expense_details = "\n".join(
                f"{category}: {amount:.2f} 元"
                for category, amount in sorted(category_totals.items())
            )
            details = f"支出分类明细：\n{expense_details}"
        summary = (
            f"{month} 月度统计\n"
            f"总收入：{total_income:.2f} 元\n"
            f"总支出：{total_expense:.2f} 元\n"
            f"结余：{balance:.2f} 元"
        )
        messagebox.showinfo("月度统计", f"{summary}\n\n{details}")

    def show_chart(self) -> None:
        """按月份汇总各分类收入和支出并显示图表。"""
        month = self.ask_month("查看图表", "请输入要查看的月份（格式：YYYY-MM）")
        if month is None:
            return

        category_totals: defaultdict[str, dict[str, Decimal]] = defaultdict(
            lambda: {"income": Decimal("0"), "expense": Decimal("0")}
        )
        for record in self.store.records:
            if not record.record_date.startswith(month + "-"):
                continue
            if record.amount >= 0:
                category_totals[record.category]["income"] += record.amount
            else:
                category_totals[record.category]["expense"] += record.amount

        if not category_totals:
            messagebox.showinfo("无法生成图表", "该月没有记录，无法生成图表")
            return
        self._render_chart_window(month, category_totals)

    def _render_chart_window(
        self,
        month: str,
        category_totals: defaultdict[str, dict[str, Decimal]],
    ) -> None:
        """在独立窗口中绘制月份收入与支出分类柱状图。"""
        import matplotlib

        matplotlib.use("TkAgg")
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
        plt.rcParams["axes.unicode_minus"] = False

        chart_window = tk.Toplevel(self)
        chart_window.title(f"{month} 支出统计")
        width, height = 700, 550
        screen_width = chart_window.winfo_screenwidth()
        screen_height = chart_window.winfo_screenheight()
        position_x = (screen_width - width) // 2
        position_y = (screen_height - height) // 2
        chart_window.geometry(f"{width}x{height}+{position_x}+{position_y}")

        keys = list(category_totals.keys())
        income_values = [
            float(category_totals[key]["income"])
            for key in keys
        ]
        expense_values = [
            float(category_totals[key]["expense"])
            for key in keys
        ]
        fig = plt.Figure(figsize=(7, 5))
        ax = fig.add_subplot(111)
        positions = list(range(len(keys)))
        bar_width = 0.38
        ax.bar(
            [position - bar_width / 2 for position in positions],
            income_values,
            width=bar_width,
            color="#4CAF50",
            label="收入",
        )
        ax.bar(
            [position + bar_width / 2 for position in positions],
            expense_values,
            width=bar_width,
            color="#E76F51",
            label="支出",
        )
        ax.set_xticks(positions)
        ax.set_xticklabels(keys, rotation=30, ha="right")
        ax.axhline(0, color="#455A64", linewidth=0.8)
        ax.set_ylabel("金额（元）")
        ax.set_title(f"{month} 收入与支出统计")
        ax.legend()
        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, master=chart_window)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)

        def close_chart() -> None:
            canvas.get_tk_widget().destroy()
            plt.close(fig)
            chart_window.destroy()

        chart_window.protocol("WM_DELETE_WINDOW", close_chart)


