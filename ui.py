"""主窗口组装与业务回调：输入校验、收支符号转换及调用 store，不直接执行 SQL。

store.records 是完整账本缓存，搜索和编辑从中取值；表格仅持有当前筛选行的展示状态。
刷新顺序为撤销旧提示/映射/覆盖层、重建 Treeview 行、同步操作按钮、请求徽标重绘。
摘要及字体测量归 widgets/table_tag_badges，不能反解析显示文本来还原标签或主键。
表格交互验收入口为 _probe/probe_table_visuals.py；用隔离宿主，避免连接真实账本。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import os
import sqlite3
import subprocess
import sys
import tkinter as tk
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from backup import create_backup
import tag_prefs
import calendar_picker
import dialogs
import settings
from chart_window import show_chart_window
from config import DATA_DIR, DEFAULT_EXPORT_DIR
from dialog_lifecycle import ManagedToplevel, _cancel_owned_tasks
from dialogs import center_dialog_on_parent
from snail import SnailManager
from store import Account, AccountStore
from tag_aggregation import MULTI_TAG_TOTALS_NOTE, aggregate_records_by_tag
from widgets import InputFrame, RecordTableFrame, ToolbarFrame


class AccountKeeperApp(ctk.CTk):
    """协调收支录入、筛选和编辑；子控件管理自身状态，持久化交给注入的 store。"""

    def __init__(
        self,
        store: AccountStore,
        last_export_dir: Path = DEFAULT_EXPORT_DIR,
    ) -> None:
        super().__init__()
        self._calendar_start_job: str | None = None
        self._calendar_map_binding: str | None = None
        self._calendar_building = 0
        self._calendar_closing = False
        self._calendar_destroyed = False
        self._backup_running = False  # 模态对话框会处理事件，禁止嵌套触发同一次备份。
        # store 由外部注入，方便测试时替换成临时数据库，避免测试污染真实账本。
        self.store = store
        # last_export_dir 由启动流程注入，带默认值保证单独构造窗口时仍可用：
        # 默认值取 config 常量，避免本层再写一份「用哪个目录」的判断（配置读取只归 settings 模块管）。
        # 它只是「另存为」对话框的起始目录；导出成功后会被更新为用户实际所选的目录。
        self.last_export_dir = last_export_dir
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
        # 先让主页面完成首次映射/绘制，再准备隐藏日历；启动构造期间不抢首帧预算。
        self._calendar_map_binding = tk.Misc.bind(self, "<Map>", self._schedule_calendar_prewarm, add="+")
        if self.winfo_ismapped():
            self._schedule_calendar_prewarm()

    def _schedule_calendar_prewarm(self, event: tk.Event | None = None) -> None:
        if self._calendar_closing or (event is not None and event.widget is not self):
            return  # 子控件Map与退出请求不能重复启动预创建。
        if self._calendar_map_binding is None:
            return
        tk.Misc.unbind(self, "<Map>", self._calendar_map_binding)
        self._calendar_map_binding = None  # 从最小化恢复也不再重复准备。

        def queue_start() -> None:
            self._calendar_start_job = None
            if not self._calendar_closing:
                self._calendar_start_job = self.after(1, self._run_calendar_prewarm)

        self._calendar_start_job = self.after_idle(queue_start)

    def _run_calendar_prewarm(self) -> None:
        self._calendar_start_job = None
        if self._calendar_closing:
            return
        calendar_picker.prewarm(self)

    def _begin_calendar_build(self) -> None:
        self._calendar_building += 1  # 对冷打开与隐藏预创建统一保护，防止CTk的update中销毁解释器。

    def _end_calendar_build(self) -> None:
        self._calendar_building -= 1
        if self._calendar_building == 0 and self._calendar_closing:
            self.destroy()  # 最外层日历建窗完成后兑现退出，不靠定时器猜构建完成。

    def destroy(self) -> None:
        if self._calendar_destroyed:
            return
        self._calendar_closing = True
        if self._calendar_start_job is not None:
            self.after_cancel(self._calendar_start_job)
            self._calendar_start_job = None
        if self._calendar_map_binding is not None:
            tk.Misc.unbind(self, "<Map>", self._calendar_map_binding)
            self._calendar_map_binding = None
        calendar_picker.shutdown(self)  # 同时取消后台批次并唤醒建窗期间重入的模态选择。
        if self._calendar_building:
            for child in tuple(self.children.values()):
                if isinstance(child, tk.Toplevel):
                    child.destroy()  # 先唤醒重入的其他模态等待；构建中的ManagedToplevel自行延期销毁。
            return  # 保留主窗及解释器，让CTk建窗完成后安全兑现关闭。
        self.snail_manager.stop()
        _cancel_owned_tasks(self)  # 根窗退出也清理所属CTk/预创建定时器，避免失效脚本留在解释器中。
        super().destroy()
        self._calendar_destroyed = True

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

        # 标签池只初始化一次：tag_prefs 先读旧 categories.json；没有可用旧文件时，
        # 再合并预置标签与数据库历史。tags.json 是否存在是初始化标记，不覆盖用户修改。
        tag_prefs.ensure_tags_initialized(self.store.get_tags())
        self.input_frame = InputFrame(
            self,
            self.add_record,
        )
        self.input_frame.pack(fill="x", padx=24, pady=(0, 8))
        # 把这些控件引用提升到主窗口，方便各回调直接读取/清空；控件本身仍归 InputFrame 所有。
        # 标签刻意不提升：它不是一个 StringVar，而是 chips 那个有序集合，
        # 归 InputFrame 自己管，主窗口只经 collect_tags() / clear_tags() 两个口子打交道。
        # 不把 _tags 提到主窗口，是为了不让两条路径去改同一份标签集合。
        self.date_var = self.input_frame.date_var
        self.amount_entry = self.input_frame.amount_entry
        self.note_var = self.input_frame.note_var

        # 工具栏只负责界面，把具体业务动作（筛选/删除/统计/导出/图表）回调给主窗口实现。
        self.toolbar = ToolbarFrame(
            self,
            filter_callback=self.filter_records,
            refresh_callback=self.refresh_records,
            delete_callback=self.delete_record,
            edit_callback=self.edit_selected_record,
            stats_callback=self.show_stats,
            export_callback=self.export_csv,
            backup_callback=self.backup_data,
            # 图表依赖 matplotlib，用 lambda 延迟到实际点击时才导入，加快启动速度。
            chart_callback=lambda: show_chart_window(self),
            open_folder_callback=self.open_data_folder,
        )
        self.toolbar.pack(fill="x", padx=24, pady=(0, 10))
        self.search_entry = self.toolbar.search_entry

        # 表格是唯一始终占据剩余空间的区域，用 expand=True 保证窗口拉大时表格跟着变大。
        self.table_frame = RecordTableFrame(self, self.edit_record)
        self.table_frame.pack(fill="both", expand=True, padx=24, pady=(0, 8))
        self.tree = self.table_frame.tree
        # 单选、取消选择及重建列表都同步按钮，避免操作上一轮筛选留下的记录。
        self.tree.bind("<<TreeviewSelect>>", self._sync_record_actions, add="+")
        self._sync_record_actions()

    def refresh_records(self) -> None:
        """按当前搜索词重绘缓存，不重新读库，也不清空筛选条件。"""
        # 正常新增/删除/编辑成功后 store 已 load；刷新按钮本身不会读取外部改库结果。
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
        except Exception:
            # 打不开资源管理器也不能让程序崩溃，退而求其次用提示框展示路径。
            messagebox.showinfo("数据目录", f"数据目录位于：\n{DATA_DIR}")

    def filter_records(self, *_args: str) -> None:
        """从完整缓存筛选并重建展示行；不改缓存、原始 tags 元组或记录主键。"""
        # 用 *_args 吸收事件对象/回调参数，使同一函数既能当事件回调也能被直接调用。
        # 搜索框未绑定 textvariable，直接读取控件作为唯一来源；粘贴的触发及延迟读取归 ToolbarFrame。
        keyword = self.search_entry.get().strip().lower()
        # 删除旧行前先取消提示及摘要任务、清映射并隐藏覆盖层，避免复用 iid 时串入上一轮内容。
        self.table_frame.clear_records()
        for item in self.tree.get_children():
            self.tree.delete(item)
        # 按日期倒序 + ID 倒序排列，让最新记录总是出现在最上面。
        visible_row = 0  # 条纹按筛选后顺序计数，不能按主键奇偶给行着色。
        for record in sorted(
            self.store.records,
            key=lambda item: (item.record_date, item.record_id),
            reverse=True,
        ):
            # 始终匹配完整 tags，摘要隐藏的标签乃至只显示计数的行也必须可被搜索。
            # 日期、备注或任一标签单独命中即可；逐标签比较避免拼接符两侧被误当成连续文字。
            matches = (
                keyword in record.record_date.lower()
                or keyword in record.note.lower()
                or any(keyword in tag.lower() for tag in record.tags)
            )
            if keyword and not matches:
                continue
            # ID 不再显示为列，但 iid 仍保存主键，编辑/删除继续按同一个标识定位。
            self.tree.insert(
                "",
                "end",
                iid=str(record.record_id),
                tags=("stripe",) if visible_row % 2 else (),
                values=(
                    record.record_date,
                    f"{record.amount:.2f}",
                    # 插入前登记 iid → 完整 tuple，返回值仅作摘要；tooltip/徽标也不能从摘要反解析。
                    self.table_frame.set_record_tags(str(record.record_id), record.tags),
                    record.note,
                ),
            )
            visible_row += 1

        self._sync_record_actions()  # 删除并重建后原选择已失效，不能保留上一轮的按钮可用状态。

        self.table_frame.tag_badges.request()  # 插入完成后再测量可见行，避免逐行触发布局。

    def _selected_record_id(self) -> int | None:
        """只认当前仍存在的选中行 iid，不按可变行序或单元格文本定位记录。"""
        selected = self.tree.selection()
        if not selected or not self.tree.exists(selected[0]):
            return None
        try:
            return int(selected[0])
        except ValueError:
            return None  # 只接受记录主键，异常行标识不能进入编辑/删除业务。

    def _sync_record_actions(self, _event: tk.Event | None = None) -> None:
        if self._calendar_closing:
            return  # 销毁时仍可能投递选择事件，不能再配置已销毁的按钮。
        # 此处只更新展示状态；实际编辑/删除回调仍重新读取当前选择，不能缓存旧主键。
        record_id = self._selected_record_id()
        self.toolbar.set_record_actions_enabled(record_id is not None)
        self.table_frame.set_selected_record(record_id)

    def add_record(self) -> None:
        try:
            # 按年/月/日解析并在写入时用 isoformat 规范化；解析失败走统一输入提示。
            record_date = datetime.strptime(
                self.date_var.get().strip(), "%Y-%m-%d"
            ).date()
            # 金额框使用 placeholder_text 而未绑定 textvariable，提交时直接读取实际文本。
            raw_amount = self.amount_entry.get().strip()
            # 空字符串不是合法的 Decimal，这里主动抛 ValueError 走统一的错误提示分支。
            if not raw_amount:
                raise ValueError
            # 先取绝对值拿到"金额大小"，正负号完全交给下面的类型开关决定。
            amount = abs(Decimal(raw_amount))
            # NaN/Infinity 可被 Decimal 解析，但 NaN 比较可能抛异常、Infinity 会污染汇总；
            # 入口只接收有限金额，不能把能解析等同于业务合法。
            if not amount.is_finite():
                raise ValueError
        except (ValueError, InvalidOperation):
            messagebox.showerror("输入错误", "日期格式应为 YYYY-MM-DD，金额必须是数字。")
            return

        # 根据支出/收入切换按钮，自动为金额加上正负号。
        # 之所以把符号转换放在提交入口而不是控件里，是为了让输入控件只表达"用户意图"，
        # 数据层始终按统一的"正数=收入、负数=支出"约定存储。
        amount_type = self.input_frame.amount_type_var.get()
        if amount_type == "支出":
            amount = -amount

        # §3.14.4 已确认 0 标签合法，可提交空元组；金额非零是独立约束，不能恢复标签必填。
        if amount == 0:
            messagebox.showerror(
                "输入错误",
                "金额不能为 0；正数表示收入，负数表示支出。",
            )
            return
        # 标签在这里一次取全：collect_tags 会把输入框里残留的文字也冲刷成 chip，
        # 用户打完字直接点「添加」不会漏掉那个词；0 个标签时返回空元组。
        # 保持 tuple 传给 store；裸字符串会被数据层拒绝，不能把整个摘要当成一个标签。
        tags = self.input_frame.collect_tags()
        # store.add 内部还会再校验一次金额（数据层最后防线）。正常流程下这里不会触发，
        # 但万一上层校验被改动绕过，也只会弹出提示而不会让程序崩溃。
        try:
            self.store.add(
                record_date.isoformat(), amount, tags, self.note_var.get()
            )
        except ValueError:
            messagebox.showerror("输入错误", "日期格式应为 YYYY-MM-DD，金额必须是数字。")
            return
        # 清空金额/标签/备注，但保留日期，方便用户连续录入同一天的流水。
        # 金额框使用的是 placeholder_text 而非 textvariable，控件内容必须单独清空，
        # 否则下一次提交会把上一次的金额重复带进去。
        self.amount_entry.delete(0, "end")
        # 标签归 InputFrame 管，主窗口只让它自己复位（连带清掉输入框里的残留文字）。
        # 清空已选 chips 与待提交文本是同一动作，不能在主窗口绕过接口直接改内部 _tags。
        self.input_frame.clear_tags()
        self.note_var.set("")
        # 需求 3.13：这里不再需要「重新查历史标签 + 刷新候选」。
        # 候选改由 tag_prefs 在每次点 ▼ 时现算，弹窗里点「+ 保存」把标签写进
        # tags.json 之后，弹窗自己会重画列表，这一层什么都不用做；同时少了一个
        # 必守的「新增成功后记得刷新」约定，也就少一类「忘了刷新」的 bug。
        self.refresh_records()

    def delete_record(self) -> None:
        record_id = self._selected_record_id()
        # 没有任何选中行时给出提示而不是静默忽略，避免用户以为按钮失效。
        if record_id is None:
            messagebox.showinfo("删除记录", "请先选择一条记录。")
            return
        # 删除属于不可逆操作，必须先弹自定义确认框。
        if not dialogs.confirm_delete(self):
            return
        # 数据库里已不存在该行（例如被其它窗口删掉），提示用户而不是假装成功。
        if not self.store.delete(record_id):
            messagebox.showinfo("删除记录", "找不到该记录")
            return
        self.refresh_records()

    def edit_selected_record(self) -> None:
        """常驻按钮按当前选中主键打开同一编辑流程。"""
        record_id = self._selected_record_id()
        if record_id is None:
            messagebox.showinfo("编辑记录", "请先选择一条记录。")
            return
        self._edit_record_by_id(record_id)

    def edit_record(self, event: tk.Event) -> None:
        """双击记录行时打开编辑窗口。"""
        # identify_row 会把双击的像素坐标映射为行 ID；点在空白处时返回空串。
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return
        try:
            record_id = int(item_id)
        except ValueError:
            return  # 表头/空白及非记录行不应打开编辑窗。
        self._edit_record_by_id(record_id)

    def _edit_record_by_id(self, record_id: int) -> None:
        """按钮和双击共用预填、取消与保存流程，防止业务口径分叉。"""
        # 从完整缓存按主键取记录预填，不能读取 Treeview 的摘要或 Canvas 可复用槽位。
        record = next(
            (item for item in self.store.records if item.record_id == record_id),
            None,
        )
        if record is None:
            messagebox.showerror("编辑失败", "找不到该记录。")
            return

        # 用户取消编辑时返回 None，此时保持原样不做任何改动。
        edited_values = dialogs.ask_edit_record(self, record)
        if edited_values is None:
            return
        # 编辑提交入口已转换收支符号并收集完整标签元组；原样透传，不二次取负或包装标签。
        record_date, amount, tags, note = edited_values
        # 主键不参与修改，编辑只更新内容字段（需求 3.5.1）。
        if not self.store.update(record_id, record_date, amount, tags, note):
            messagebox.showerror("编辑失败", "找不到该记录或记录更新失败。")
            return
        self.refresh_records()

    def _confirm_backup_overwrite(self, save_path: Path) -> bool:
        # 与删除确认共用受管理的 CTkToplevel 生命周期；原生 messagebox 不适合危险操作。
        dialog = ManagedToplevel(self)
        dialog.attributes("-alpha", 0.0)  # 先透明布局，复用删除/编辑的一次居中后显现流程。
        dialog.title("确认覆盖备份")
        dialog.geometry("460x240")
        tk.Wm.resizable(dialog, False, False)  # 不再安排 CTk 标题栏重绘，避免定位后又重入布局。
        dialog.transient(self)
        dialog.configure(fg_color="#F0F4F8")
        try:
            dialog.iconbitmap(self.iconbitmap())
        except tk.TclError:
            pass  # 图标是装饰，失败不能阻断覆盖确认。
        result = [False]
        content = ctk.CTkFrame(dialog, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=24, pady=20)
        ctk.CTkLabel(
            content, text=f"目标文件已存在，确认覆盖吗？\n{save_path.name}",
            wraplength=400, font=("Microsoft YaHei UI", 12), text_color="#243447",
        ).pack(fill="x", expand=True, pady=(0, 16))
        buttons = ctk.CTkFrame(content, fg_color="transparent")
        buttons.pack(fill="x")
        buttons.grid_columnconfigure((0, 1), weight=1)

        def finish(confirmed: bool) -> None:
            if dialog.closing:
                return  # 已收到父窗/标题栏关闭请求时，迟到的回车不能再确认覆盖。
            result[0] = confirmed
            dialog.destroy()

        for column, text, confirmed, color, hover in (
            (0, "取消", False, "#90A4AE", "#78909C"),
            (1, "确认覆盖", True, "#E76F51", "#C9573D"),
        ):
            ctk.CTkButton(
                buttons, text=text, command=lambda value=confirmed: finish(value),
                width=120, height=34, corner_radius=9, fg_color=color,
                hover_color=hover, font=("Microsoft YaHei UI", 11),
            ).grid(row=0, column=column, sticky="ew", padx=(0, 6) if column == 0 else (6, 0))
        dialog.protocol("WM_DELETE_WINDOW", lambda: finish(False))
        dialog.bind("<Escape>", lambda _event: finish(False))
        dialog.bind("<Return>", lambda _event: finish(True))
        dialog.finish_setup()
        if dialog.closing or not center_dialog_on_parent(self, dialog):
            return False
        # grab 和 wait_window 沿用删除确认语义，关闭/取消保持 False，只有确认才允许覆盖。
        dialog.grab_set()
        self.wait_window(dialog)
        return result[0]

    def backup_data(self) -> None:
        if self._backup_running or self._calendar_closing:
            return
        self._backup_running = True
        try:
            chosen = filedialog.asksaveasfilename(
                parent=self, defaultextension=".zip",
                initialfile=f"AccountKeeper_backup_{datetime.now().date().isoformat()}.zip",
                initialdir=str(self.last_export_dir), filetypes=[("ZIP 文件", "*.zip")],
                confirmoverwrite=False,  # 关闭系统覆盖询问，危险操作只走项目 CTk 确认框。
            )
            if not chosen or self._calendar_closing:
                return
            save_path = Path(chosen).expanduser().resolve()
            if save_path.exists() and not self._confirm_backup_overwrite(save_path):
                return
            if self._calendar_closing:
                return  # 模态确认期间主窗可能已退出，不能继续备份或写配置。
            exported = create_backup(self.store, save_path)
            # 只有完整 ZIP 发布成功才记路径；配置失败保留备份成功结果，只打印已有警告。
            if settings.save_settings(exported.parent):
                self.last_export_dir = exported.parent
            messagebox.showinfo("备份成功", f"备份已保存到：\n{exported}")
        except TimeoutError:
            if not self._calendar_closing:
                messagebox.showerror("备份失败", "数据库正忙，请稍后重试")
        except (OSError, sqlite3.Error, zipfile.BadZipFile, ValueError, tk.TclError) as error:
            if not self._calendar_closing:
                messagebox.showerror("备份失败", f"无法备份数据：{error}")
        finally:
            self._backup_running = False  # 成功、取消、异常都归还入口，允许下一次重试。

    def export_csv(self) -> None:
        # 月份由月历弹窗点选（issues #3.2，不再手输），用户取消时返回 None。
        # 第三个实参 prompt 已删：弹窗里不再有说明文字，标题栏 + 月历本身已自解释。
        month = dialogs.ask_month(self, "导出账单")
        if month is None:
            return

        # 该月一条记录都没有时不必生成空文件，直接告知用户更友好。
        # 必须带上结尾的 "-"：只写月前缀时 2024-01 会把 2024-010 这类脏数据也算进来，
        # 与 store.export_month_csv 的 LIKE 'YYYY-MM-%'、下面 show_stats 的口径保持一致。
        if not any(record.record_date.startswith(month + "-") for record in self.store.records):
            messagebox.showinfo("无法导出", "该月没有记录，无法导出")
            return

        # 保存对话框里预填文件名，用户在"另存为"时就能看清导出的是哪个月份。
        save_path_str = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=f"account_export_{month}.csv",
            # 需求 3.11：用「上次导出目录」作起始目录。它由 settings 层保证一定可用，
            # 没有记录或记录已失效时返回值本身就是默认目录，所以这里不需要再判空。
            initialdir=str(self.last_export_dir),
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

        # 需求 3.11：记住用户这次实际选的目录，供下次导出直接复用。
        # 只有写回成功才更新内存值，避免「界面用新目录、磁盘上还是旧记录」的不一致。
        # 写回失败不弹窗：导出本身已经成功，不该因为「记不住目录」这种小事报错。
        chosen_dir = save_path.expanduser().resolve().parent
        if settings.save_settings(chosen_dir):
            self.last_export_dir = chosen_dir

        messagebox.showinfo(
            "导出成功",
            f"导出成功！文件已保存到：\n{export_path}",
        )

    def show_stats(self) -> None:
        # 与 export_csv 同一套口径：月份由月历弹窗点选（issues #3.2），取消返回 None。
        month = dialogs.ask_month(self, "选择统计月份")
        if month is None:
            return
        # 只保留该月份的记录；要求存储的日期带前导零，所以前缀匹配是安全的（需求 3.4）。
        month_records = [
            record
            for record in self.store.records
            if record.record_date.startswith(month + "-")
        ]
        totals = aggregate_records_by_tag(month_records)
        total_income = totals.total_income
        total_expense = totals.total_expense
        # 结余 = 收入 - 支出；支出转成正数后相减，避免出现"负数减负数"的歧义。
        balance = total_income - total_expense
        # 月度统计只展示支出明细，且忽略金额为零的桶以保持旧有显示语义。
        expense_totals = {
            tag: tag_totals.expense
            for tag, tag_totals in totals.by_tag.items()
            if tag_totals.expense > 0
        }
        # 分三种情况给出结论，避免展示一个"只有标题没有内容"的空明细。
        if not month_records:
            details = "该月份没有记账记录"
        elif total_expense == 0:
            details = "该月份只有收入，没有支出"
        else:
            expense_details = "\n".join(
                f"{tag}: {amount:.2f} 元"
                for tag, amount in sorted(expense_totals.items())
            )
            details = (
                f"支出标签明细：\n{expense_details}\n\n"
                f"{MULTI_TAG_TOTALS_NOTE}"
            )
        summary = (
            f"{month} 月度统计\n"
            f"总收入：{total_income:.2f} 元\n"
            f"总支出：{total_expense:.2f} 元\n"
            f"结余：{balance:.2f} 元"
        )
        messagebox.showinfo("月度统计", f"{summary}\n\n{details}")

    def ask_month(
        self,
        title: str,
        initial_month: str | None = None,
        anchor: tk.Misc | None = None,
    ) -> str | None:
        """保留旧接口，实际对话框由 dialogs 模块负责。

        月份选择仍经dialogs转发；本层直接引用calendar_picker仅用于启动预创建及退出清理，
        不在业务回调中另写一套选择逻辑。

        Args:
            title: 窗口标题栏文字，带语境（如「查看图表」）。
            initial_month: 预选月份（YYYY-MM），同时决定初始展示的年份；
                不传或字符串不合法时定位到今天所在月份。
            anchor: 锚点控件（触发月份选择的按钮）：弹窗贴它的左下角弹出，
                不传则屏幕居中。三个工具栏按钮目前没留控件引用，故先按居中走。
        """
        # 可选项一律关键字透传：dialogs.ask_month 的形参顺序将来若有调整，
        # 这里不会静默错位（例如把 initial_month 当 anchor 传下去）。
        return dialogs.ask_month(
            self,
            title,
            initial_month=initial_month,
            anchor=anchor,
        )

    def ask_edit_record(
        self,
        record: Account,
    ) -> tuple[str, Decimal, tuple[str, ...], str] | None:
        """保留旧接口，实际对话框由 dialogs 模块负责。"""
        return dialogs.ask_edit_record(self, record)
