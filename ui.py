"""Main AccountKeeper window and application-level business callbacks."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

import dialogs
from chart_window import show_chart_window
from config import DATA_DIR
from snail import SnailManager
from store import Account, AccountStore
from widgets import InputFrame, RecordTableFrame, ToolbarFrame


class AccountKeeperApp(ctk.CTk):
    """The desktop interface for viewing and managing expense records."""

    def __init__(self, store: AccountStore) -> None:
        super().__init__()
        # store 由外部注入，方便测试时替换成临时数据库，避免测试污染真实账本。
        self.store = store
        self.title("AccountKeeper 本地记账")
        self.geometry("960x680")
        # 设置最小尺寸，防止用户把窗口拖得过小导致输入区和表格控件被挤成一团。
        self.minsize(820, 560)
        self.configure(fg_color="#F0F4F8")
        self._build_widgets()
        # 先渲染一次数据，保证窗口一出现就能看到历史记录。
        self.refresh_records()
        # 蜗牛必须等 title_block 建好之后再创建，因为它需要以标题位置作为爬行边界。
        self.snail_manager = SnailManager(self, self.title_block)
        self.snail_manager.start()

    def _build_widgets(self) -> None:
        """组装标题、输入区、工具栏和记录表格。"""
        font_small = ("Microsoft YaHei UI", 11)
        font_title = ("Microsoft YaHei UI", 16, "bold")

        self.header = ctk.CTkFrame(
            self,
            height=58,
            fg_color="transparent",
            corner_radius=0,
        )
        self.header.pack(fill="x", padx=24, pady=(10, 5))
        self.header.pack_propagate(False)
        # 标题区用 place 而非 pack 定位：一是保证窗口缩放时标题始终水平居中，
        # 二是给蜗牛动画提供一个固定、可查询的矩形范围（蜗牛从标题两侧穿过）。
        self.title_block = ctk.CTkFrame(
            self,
            fg_color="transparent",
            corner_radius=0,
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

        self.input_frame = InputFrame(self, self.add_record)
        self.input_frame.pack(fill="x", padx=24, pady=(0, 8))
        # 把这些控件引用提升到主窗口，方便各回调直接读取/清空；控件本身仍归 InputFrame 所有。
        self.date_var = self.input_frame.date_var
        self.amount_var = self.input_frame.amount_var
        self.amount_entry = self.input_frame.amount_entry
        self.category_var = self.input_frame.category_var
        self.note_var = self.input_frame.note_var

        # 工具栏只负责界面，把具体业务动作（筛选/删除/统计/导出/图表）回调给主窗口实现。
        self.toolbar = ToolbarFrame(
            self,
            filter_callback=self.filter_records,
            refresh_callback=self.refresh_records,
            delete_callback=self.delete_record,
            stats_callback=self.show_stats,
            export_callback=self.export_csv,
            # 图表依赖 matplotlib，用 lambda 延迟到实际点击时才导入，加快启动速度。
            chart_callback=lambda: show_chart_window(self),
            open_folder_callback=self.open_data_folder,
        )
        self.toolbar.pack(fill="x", padx=24, pady=(0, 10))
        self.search_var = self.toolbar.search_var
        self.search_entry = self.toolbar.search_entry
        self.summary_var = self.toolbar.summary_var

        # 表格是唯一始终占据剩余空间的区域，用 expand=True 保证窗口拉大时表格跟着变大。
        self.table_frame = RecordTableFrame(self, self.edit_record)
        self.table_frame.pack(fill="both", expand=True, padx=24, pady=(0, 0))
        self.tree = self.table_frame.tree

    def refresh_records(self) -> None:
        # 刷新按钮与新增/删除/编辑后的刷新都收敛到这一处，避免多份重复的渲染逻辑。
        self.filter_records()

    def open_data_folder(self) -> None:
        try:
            # 三个平台调用系统文件管理器的方式各不相同，这里按平台分派。
            if sys.platform.startswith("win"):
                os.startfile(DATA_DIR)
            elif sys.platform == "darwin":
                subprocess.run(["open", str(DATA_DIR)])
            else:
                subprocess.run(["xdg-open", str(DATA_DIR)])
        except Exception as error:
            # 打不开资源管理器也不能让程序崩溃，退而求其次把路径打印给用户。
            messagebox.showinfo("数据目录", f"数据目录位于：\n{DATA_DIR}")

    def filter_records(self, *_args: str) -> None:
        # 用 *_args 吸收事件对象/回调参数，使同一函数既能当事件回调也能被直接调用。
        # 直接读输入框内容而不是 search_var：粘贴（尤其右键粘贴）只改控件内容、不产生按键事件，
        # 依赖变量会读到过期值，表现为"粘进去的文字筛不出结果"。
        keyword = self.search_entry.get().strip().lower()
        # Treeview 不支持增量更新，只能先清空再按当前条件重新插入。
        for item in self.tree.get_children():
            self.tree.delete(item)
        # 单独收集"通过筛选"的记录，用它们（而不是全量数据）计算汇总，保证汇总与列表一致。
        filtered_records = []
        # 按日期倒序 + ID 倒序排列，让最新记录总是出现在最上面。
        for record in sorted(
            self.store.records,
            key=lambda item: (item.record_date, item.record_id),
            reverse=True,
        ):
            # 统一转小写做不区分大小写的模糊匹配，日期/类别/备注任一命中即可。
            searchable_text = (
                record.record_date.lower(),
                record.category.lower(),
                record.note.lower(),
            )
            if keyword and not any(keyword in text for text in searchable_text):
                continue
            filtered_records.append(record)
            # iid 直接用 record_id，这样双击/删除时能由选中项反推出数据库主键。
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
        # 约定：正数金额为收入、负数金额为支出，因此支出侧取相反数再求和得到正数展示值。
        # 比较前必须先判 is_finite()：Decimal("NaN") 参与 > / < 比较会抛 InvalidOperation，
        # 一旦库里存在历史脏数据（旧版本校验缺失时写入的 "NaN"），界面会在启动时就崩溃。
        # 这类记录仍然照常显示在表格里，方便用户定位后删除或改正，只是不计入收支合计。
        income = sum(
            (
                record.amount
                for record in filtered_records
                if record.amount.is_finite() and record.amount > 0
            ),
            Decimal("0"),
        )
        expense = sum(
            (
                -record.amount
                for record in filtered_records
                if record.amount.is_finite() and record.amount < 0
            ),
            Decimal("0"),
        )
        # 汇总随筛选结果实时变化，用户搜索某个类别时即可看到该类别的收支小计；
        # 末尾附带条数，让用户一眼确认当前列表里有多少条记录（筛选后即为命中条数）。
        self.summary_var.set(
            f"收: {income:.2f} | 支: {expense:.2f} | 记录数: {len(filtered_records)}"
        )

    def add_record(self) -> None:
        try:
            # 日期必须严格符合 YYYY-MM-DD，strptime 失败会直接跳到 except 分支。
            record_date = datetime.strptime(
                self.date_var.get().strip(), "%Y-%m-%d"
            ).date()
            # 直接读控件内容：金额框用的是 placeholder_text 而不是 textvariable，
            # 且粘贴不会触发按键事件，读 StringVar 会漏掉粘贴（含右键粘贴）进来的数字。
            raw_amount = self.amount_entry.get().strip()
            # 空字符串不是合法的 Decimal，这里主动抛 ValueError 走统一的错误提示分支。
            if not raw_amount:
                raise ValueError
            # 先取绝对值拿到"金额大小"，正负号完全交给下面的类型开关决定。
            amount = abs(Decimal(raw_amount))
            # Decimal("nan") / Decimal("Infinity") 都能正常解析、也能正常入库（存成 "NaN"），
            # 但之后任何"金额 > 0"这类大小比较都会抛 InvalidOperation，直接把界面渲染炸掉
            # （这就是账本里那条 NaN 记录导致程序一启动就崩溃的原因）。
            # 因此在入口处用 is_finite() 显式拦掉，保证入库的金额一定可比较、可汇总。
            if not amount.is_finite():
                raise ValueError
        except (ValueError, InvalidOperation):
            messagebox.showerror("输入错误", "日期格式应为 YYYY-MM-DD，金额必须是数字。")
            return

        # 根据支出/收入切换按钮，自动为金额加上正负号。
        # 之所以把符号转换放在这里而不是控件里，是为了让 UI 层只关心"用户意图"，
        # 数据层始终按统一的"正数=收入、负数=支出"约定存储。
        amount_type = self.input_frame.amount_type_var.get()
        if amount_type == "支出":
            amount = -amount

        category = self.category_var.get().strip()
        # 0 元记录在统计中没有意义，类别为空则无法分类，两者都视为非法输入。
        if amount == 0 or not category:
            messagebox.showerror(
                "输入错误",
                "金额不能为 0；正数表示收入，负数表示支出，类别不能为空。",
            )
            return
        # store.add 内部还会再校验一次金额（数据层最后防线）。正常流程下这里不会触发，
        # 但万一上层校验被改动绕过，也只会弹出提示而不会让程序崩溃。
        try:
            self.store.add(
                record_date.isoformat(), amount, category, self.note_var.get()
            )
        except ValueError:
            messagebox.showerror("输入错误", "日期格式应为 YYYY-MM-DD，金额必须是数字。")
            return
        # 清空金额/类别/备注，但保留日期，方便用户连续录入同一天的流水。
        self.amount_var.set("")
        # 金额框使用的是 placeholder_text 而非 textvariable，控件内容必须单独清空，
        # 否则下一次提交会把上一次的金额重复带进去（上面的 StringVar 只是顺手重置）。
        self.amount_entry.delete(0, "end")
        self.category_var.set("")
        self.note_var.set("")
        self.refresh_records()

    def delete_record(self) -> None:
        selected = self.tree.selection()
        # 没有任何选中行时给出提示而不是静默忽略，避免用户以为按钮失效。
        if not selected:
            messagebox.showinfo("删除记录", "请先选择一条记录。")
            return
        # iid 就是 record_id（见 filter_records），因此可直接转成主键。
        record_id = int(selected[0])
        # 删除属于不可逆操作，必须先弹自定义确认框。
        if not dialogs.confirm_delete(self):
            return
        # 数据库里已不存在该行（例如被其它窗口删掉），提示用户而不是假装成功。
        if not self.store.delete(record_id):
            messagebox.showinfo("删除记录", "找不到该记录")
            return
        self.refresh_records()

    def edit_record(self, event: tk.Event) -> None:
        """双击记录行时打开编辑窗口。"""
        # identify_row 会把双击的像素坐标映射为行 ID；点在空白处时返回空串。
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return
        try:
            record_id = int(item_id)
        except ValueError:
            return
        # 从内存缓存里找到对应的完整记录，用于给编辑框预填当前值。
        record = next(
            (item for item in self.store.records if item.record_id == record_id),
            None,
        )
        if record is None:
            return

        # 用户取消编辑时返回 None，此时保持原样不做任何改动。
        edited_values = dialogs.ask_edit_record(self, record)
        if edited_values is None:
            return
        record_date, amount, category, note = edited_values
        # 主键不参与修改，编辑只更新内容字段（需求 3.5.1）。
        if not self.store.update(record_id, record_date, amount, category, note):
            messagebox.showerror("编辑失败", "找不到该记录或记录更新失败。")
            return
        self.refresh_records()

    def export_csv(self) -> None:
        # 先让用户输入要导出的月份，取消则直接返回。
        month = dialogs.ask_month(
            self,
            "导出账单",
            "请输入要导出的月份（格式：YYYY-MM）",
        )
        if month is None:
            return

        # 该月一条记录都没有时不必生成空文件，直接告知用户更友好。
        if not any(record.record_date.startswith(month) for record in self.store.records):
            messagebox.showinfo("无法导出", "该月没有记录，无法导出")
            return

        # 保存对话框里预填文件名，用户在"另存为"时就能看清导出的是哪个月份。
        save_path_str = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=f"account_export_{month}.csv",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        # 用户点了取消，返回空串。
        if not save_path_str:
            return

        save_path = Path(save_path_str)
        try:
            export_path = self.store.export_month_csv(month, save_path)
        except OSError as error:
            # 磁盘写满、文件被占用、没有写入权限等都属于 OSError，提示后返回即可。
            messagebox.showerror("导出失败", f"无法写入导出文件：{error}")
            return

        messagebox.showinfo(
            "导出成功",
            f"导出成功！文件已保存到：\n{export_path}",
        )

    def show_stats(self) -> None:
        month = dialogs.ask_month(
            self,
            "选择统计月份",
            "请输入要统计的月份（格式：YYYY-MM）",
        )
        if month is None:
            return
        # 只保留该月份的记录；要求存储的日期带前导零，所以前缀匹配是安全的（需求 3.4）。
        month_records = [
            record
            for record in self.store.records
            if record.record_date.startswith(month + "-")
        ]
        total_income = sum(
            (
                record.amount
                for record in month_records
                if record.amount.is_finite() and record.amount > 0
            ),
            Decimal("0"),
        )
        total_expense = sum(
            (
                -record.amount
                for record in month_records
                if record.amount.is_finite() and record.amount < 0
            ),
            Decimal("0"),
        )
        # 结余 = 收入 - 支出；支出转成正数后相减，避免出现"负数减负数"的歧义。
        balance = total_income - total_expense
        # 只统计支出分类，因为收入不分摊分类、用途是"花了多少钱在什么类别上"。
        category_totals: defaultdict[str, Decimal] = defaultdict(
            lambda: Decimal("0")
        )
        for record in month_records:
            # 同样跳过非有限金额：它既无法比较大小，参与减法还会把整个分类合计污染成 NaN。
            if record.amount.is_finite() and record.amount < 0:
                category_totals[record.category] -= record.amount
        # 分三种情况给出结论，避免展示一个"只有标题没有内容"的空明细。
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

    def ask_month(self, title: str, prompt: str) -> str | None:
        """保留旧接口，实际对话框由 dialogs 模块负责。"""
        return dialogs.ask_month(self, title, prompt)

    def ask_edit_record(
        self,
        record: Account,
    ) -> tuple[str, Decimal, str, str] | None:
        """保留旧接口，实际对话框由 dialogs 模块负责。"""
        return dialogs.ask_edit_record(self, record)
