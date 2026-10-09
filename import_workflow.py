"""主页面导入流程：文件读取、批次事务调用及报告；UI 不执行 SQL。"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from config import RESOURCE_DIR
from dialog_lifecycle import ManagedToplevel
import dialogs
from import_dialog import ImportDialog
from import_reader import read_bill_file
from import_rules import load_vocabulary
from import_session import ImportSession
from store import ImportRecord


def _read_import(path):
    # 工作线程只读文件/词表；任何 Tk 操作和账本缓存更新均留在主线程。
    return read_bill_file(path), load_vocabulary(RESOURCE_DIR / "import_vocab")


def _alive(window):
    return window is not None and not window.closing


class FlowWindow(ManagedToplevel):
    """报告和批次窗口共用本项目的颜色、字体及受保护窗口生命周期。"""

    def __init__(self, parent, title, size="680x440"):
        super().__init__(parent, fg_color="#F0F4F8")
        self.title(title)
        self.geometry(size)
        self.transient(parent)
        self.font = ("Microsoft YaHei UI", 12)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _event: self.destroy())

    def label(self, parent, text, **kwargs):
        return ctk.CTkLabel(parent, text=text, font=self.font,
                           text_color="#243447", **kwargs)

    def button(self, parent, text, command, *, danger=False, primary=False):
        color = "#B84035" if danger else "#2F80ED" if primary else "#E8EEF5"
        hover = "#A3342B" if danger else "#256BC7" if primary else "#DDE7F2"
        return ctk.CTkButton(parent, text=text, command=command, height=34,
                            fg_color=color, hover_color=hover, corner_radius=8,
                            text_color="white" if danger or primary else "#475569", font=self.font)

    def show(self):
        self.finish_setup()
        if not self.closing:
            dialogs.center_dialog_on_parent(self.master, self)


class ImportReport(FlowWindow):
    def __init__(self, workflow, file_name, counts, batch, refresh_error=""):
        super().__init__(workflow.app, "导入完成")
        self.workflow, self.batch = workflow, batch
        self.undone = False
        self.label(self, "导入已完成", height=40).pack(fill="x", padx=24, pady=(15, 5))
        card = ctk.CTkFrame(self, fg_color="white", corner_radius=10)
        card.pack(fill="both", expand=True, padx=24, pady=6)
        # 零笔没有批次信息；报告不展示不存在的备份路径或外部单号。
        items = [("文件名", file_name), ("导入笔数", counts["将导入"]),
                 ("跳过笔数", counts["规则跳过"]), ("非交易行数", counts["非交易行"])]
        if batch is not None:
            items.append(("批次 id", batch.batch_id))
        for label, value in items:
            self.label(card, f"{label}：{value}", anchor="w", wraplength=610).pack(fill="x", padx=18, pady=4)
        self.status = self.label(self, refresh_error or ("本次零笔导入，未生成批次。" if batch is None else "可在导入批次中查看或撤销。"),
                                 wraplength=610, justify="left")
        self.status.pack(fill="x", padx=24, pady=6)
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=24, pady=(5, 18))
        self.undo_button = None
        if batch is not None:
            self.undo_button = self.button(actions, "撤销本次导入", lambda: workflow.undo_batch(batch, self), danger=True)
            self.undo_button.pack(side="left", padx=(0, 8))
        self.button(actions, "打开数据目录", workflow.app.open_data_folder).pack(side="left")
        self.retry_button = self.button(actions, "重试刷新列表", self.retry_refresh)
        if refresh_error:
            self.retry_button.pack(side="left", padx=8)
        self.button(actions, "关闭", self.destroy).pack(side="right")
        self.show()

    def mark_undone(self, message):
        self.undone = True
        if self.undo_button is not None:
            self.undo_button.configure(state="disabled", fg_color="#E5EBF1")
        self.status.configure(text=message)

    def retry_refresh(self):
        # 仅重读/渲染，不重放已提交的写入或撤销。
        error = self.workflow.reload_main()
        self.status.configure(text=error or ("本次导入已撤销" if self.undone else "导入已完成，列表已刷新。"))
        if not error:
            self.retry_button.pack_forget()


class BatchList(FlowWindow):
    def __init__(self, workflow):
        super().__init__(workflow.app, "导入批次", "900x500")
        self.minsize(760, 360)
        self.workflow = workflow
        self.status = self.label(self, "导入笔数是导入时快照；撤销会删除该批次的全部现存记录。", wraplength=840)
        self.status.pack(fill="x", padx=18, pady=10)
        # 固定列头配可滚动列表，不在 UI 按当前账本记录数重新推算批次计数。
        headings = ctk.CTkFrame(self, fg_color="#E8F1FC")
        headings.pack(fill="x", padx=18)
        widths = (170, 230, 80, 80, 90, 100)
        for i, (title, width) in enumerate(zip(("导入时间", "文件名", "导入笔数", "跳过笔数", "非交易行数", "操作"), widths)):
            self.label(headings, title, width=width).grid(row=0, column=i)
        headings.columnconfigure(1, weight=1)
        self.body = ctk.CTkScrollableFrame(self, fg_color="white", corner_radius=8)
        self.body.pack(fill="both", expand=True, padx=18, pady=(5, 10))
        self.body.columnconfigure(1, weight=1)
        self.undo_buttons = {}
        self.button(self, "刷新批次", self.refresh).pack(pady=(0, 12))
        self.refresh()
        self.show()

    def refresh(self):
        try:
            batches = self.workflow.store.list_import_batches()
        except Exception as error:
            self.status.configure(text=f"读取批次失败：{error}")
            return  # 读失败保留旧列表，不能伪称数据库没有批次。
        for child in tuple(self.body.winfo_children()):
            child.destroy()
        self.undo_buttons = {}
        if not batches:
            self.label(self.body, "暂无导入批次").grid(row=0, column=0, columnspan=6, pady=30)
        for i, batch in enumerate(batches):
            values = (batch.imported_at.replace("T", " "), batch.file_name,
                      batch.imported_count, batch.skipped_count, batch.non_transaction_count)
            for col, (value, width) in enumerate(zip(values, (170, 230, 80, 80, 90))):
                self.label(self.body, str(value), width=width, wraplength=width - 10).grid(row=i, column=col, sticky="ew", pady=5)
            button = self.button(self.body, "撤销", lambda b=batch: self.workflow.undo_batch(b, self), danger=True)
            button.configure(width=90)
            button.grid(row=i, column=5, padx=5, pady=5)
            self.undo_buttons[batch.batch_id] = button


class ImportWorkflow:
    """批次事务只调用 store 的公开接口，读入与显示错误不能冒充事务回滚。"""

    def __init__(self, app):
        self.app, self.store = app, app.store
        self.closed, self.writing = False, False
        self.preview = self.loading = self.batch_list = self.failure = None
        self.reports = []
        self.future = self.poll_job = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bill-reader")
        self.last_batch, self.last_error = None, ""

    def notice(self, title, message):
        window = FlowWindow(self.app, title, "620x230")
        window.label(window, message, wraplength=570, justify="left").pack(fill="both", expand=True, padx=24, pady=15)
        window.button(window, "关闭", window.destroy).pack(pady=12)
        window.show()
        return window

    def choose_file(self):
        if self.closed:
            return
        if _alive(self.preview):
            self.preview.lift()
            return
        if self.future is not None:
            self.loading.lift()
            return
        path = filedialog.askopenfilename(parent=self.app, title="选择外部账单",
            filetypes=(("账单文件", "*.csv *.xlsx"), ("CSV 账单", "*.csv"), ("Excel 账单", "*.xlsx")))
        if path and not self.closed:
            self.open_file(Path(path))

    def open_file(self, path):
        if self.closed or self.future is not None or _alive(self.preview):
            return
        if not self.store.import_batches_available:
            self.notice("无法导入", "导入批次数据库尚未就绪，请先解决数据库升级问题。")
            return
        self.loading = FlowWindow(self.app, "读取账单", "520x180")
        self.loading.label(self.loading, "正在读取账单，尚未写入账本。", wraplength=470).pack(pady=30)
        self.loading.button(self.loading, "取消", self.cancel_read).pack()
        self.loading.protocol("WM_DELETE_WINDOW", self.cancel_read)
        self.loading.bind("<Escape>", lambda _event: self.cancel_read())
        self.loading.show()
        if self.closed or not _alive(self.loading):
            return
        self.future = self.executor.submit(_read_import, Path(path))
        self.poll_job = self.app.after(50, self.poll_read)

    def cancel_read(self):
        if self.poll_job is not None:
            self.app.after_cancel(self.poll_job)
            self.poll_job = None
        if self.future is not None:
            self.future.cancel()  # 运行中只放弃回传，不从工作线程强行中断读取器。
            self.future = None
        if _alive(self.loading):
            self.loading.destroy()

    def poll_read(self):
        self.poll_job = None
        if self.closed or self.future is None:
            return
        if not self.future.done():
            self.poll_job = self.app.after(50, self.poll_read)
            return
        future, self.future = self.future, None
        if _alive(self.loading):
            self.loading.destroy()
        session = None
        try:
            bill, vocabulary = future.result()
            session = ImportSession(bill, vocabulary)
            self.preview = ImportDialog(self.app, session, on_confirm=self.submit)
        except Exception as error:
            if session is not None:
                session.close()
            self.last_error = str(error)
            if not self.closed:
                self.notice("无法打开账单", f"{error}\n账本未写入，请检查文件后重新选择。")

    def reload_main(self):
        try:
            self.store.load()
            if not self.closed:
                self.app.refresh_records()  # 一次全量 load 和一次渲染，避免逐笔 CRUD 放大 #19。
        except Exception as error:
            return f"账本操作已完成，列表刷新失败：{error}。请重试刷新列表。"
        return ""

    def submit(self, result):
        if self.closed or self.writing:
            return False
        self.writing = True
        try:
            # 从完整分析结果取入账集合，绝不从搜索后的可见行取数，也不保存外部单号。
            records = tuple(ImportRecord(row.record_date, row.amount, row.note) for row in result.to_import)
            try:
                if not result.can_confirm:
                    raise ValueError("仍有待确认记录或必需字段缺失，不能导入。")
                batch = self.store.import_batch(records, self.preview.session.bill.file_name,
                    result.counts["规则跳过"], result.counts["非交易行"])
            except Exception as error:
                self.last_error = str(error)
                self.show_failure(error)
                return False  # store 事务保证全部回滚；原预览及决定保留。
            self.last_batch = batch
            error = self.reload_main() if batch is not None else ""
            # 此边界以后绝不重试写入；报告建窗异常也不能让原预览再次提交。
            try:
                if not self.closed:
                    report = ImportReport(self, self.preview.session.bill.file_name, result.counts, batch, error)
                    self.reports = [item for item in self.reports if _alive(item)]
                    self.reports.append(report)
            except Exception as display_error:
                if not self.closed:
                    messagebox.showerror("导入已完成", f"账本已写入，报告显示失败：{display_error}。请在导入批次中查看，勿再次导入。", parent=self.app)
            return True
        finally:
            self.writing = False

    def show_failure(self, error):
        window = self.failure = FlowWindow(self.preview, "导入失败", "620x260")
        window.label(window, f"导入失败，整批未写入：\n{error}", wraplength=570, justify="left").pack(fill="both", expand=True, padx=24, pady=15)
        actions = ctk.CTkFrame(window, fg_color="transparent")
        actions.pack(pady=15)
        window.button(actions, "返回预览", window.destroy, primary=True).pack(side="left", padx=8)

        def cancel():
            window.destroy()
            if _alive(self.preview):
                self.preview.destroy()

        window.button(actions, "取消导入", cancel).pack(side="left", padx=8)
        window.show()
        if not window.closing:
            window.grab_set()
            self.app.wait_window(window)  # 等待处理事件，writing 防止模态回调二次提交。

    def show_batches(self):
        if self.closed:
            return
        if _alive(self.batch_list):
            self.batch_list.refresh()
            self.batch_list.lift()
        else:
            self.batch_list = BatchList(self)

    def undo_batch(self, batch, origin):
        if self.closed or self.writing:
            return False
        self.writing = True
        try:
            if not dialogs.confirm_undo_import(origin, batch) or self.closed or origin.closing:
                return False
            try:
                removed = self.store.undo_import_batch(batch.batch_id)
            except Exception as error:
                origin.status.configure(text=f"撤销失败，记录保持原状：{error}")
                return False  # 两个入口共享同一事务与失败路径，不逐笔 delete。
            refresh_error = self.reload_main() if removed else ""
            message = "本次导入已撤销" if removed else "该批次已不存在"
            for report in self.reports:
                if _alive(report) and report.batch is not None and report.batch.batch_id == batch.batch_id:
                    report.mark_undone(message)
                    if refresh_error:
                        report.status.configure(text=f"{message}；{refresh_error}")
                        report.retry_button.pack(side="left", padx=8)
            if _alive(self.batch_list):
                self.batch_list.refresh()
                self.batch_list.status.configure(text=f"{message}；{refresh_error}" if refresh_error else message)
            return removed
        finally:
            self.writing = False

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.cancel_read()
        self.executor.shutdown(wait=False, cancel_futures=True)
        for window in (self.preview, self.batch_list, self.failure, *self.reports):
            if _alive(window):
                window.destroy()
