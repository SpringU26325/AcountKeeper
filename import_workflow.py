"""主页面导入流程：文件读取、批次事务调用及报告；UI 不执行 SQL。"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import TypeGuard, TypeVar

import customtkinter as ctk

from config import RESOURCE_DIR
from dialog_common import _apply_app_icon, fit_dialog_to_work_area
from dialog_lifecycle import ManagedToplevel
import dialogs
from import_dialog import ImportDialog
from import_reader import read_bill_file
from import_rules import load_vocabulary
from import_session import ImportSession
from store import MerchantMappingConflict
from tag_prefs import build_tag_candidates


def _read_import(path):
    # 工作线程只读文件/词表；任何 Tk 操作和账本缓存更新均留在主线程。
    return read_bill_file(path), load_vocabulary(RESOURCE_DIR / "import_vocab")


_WindowT = TypeVar("_WindowT", bound=ManagedToplevel)


def _alive(window: _WindowT | None) -> TypeGuard[_WindowT]:
    return window is not None and not window.closing


class FlowWindow(ManagedToplevel):
    """报告和批次窗口共用本项目的颜色、字体及受保护窗口生命周期。"""

    def __init__(self, parent, title, size="680x440"):
        super().__init__(parent, fg_color="#F0F4F8")
        self.title(title)
        # 读取、完成及批次窗口共用同一图标，构建时统一设置。
        _apply_app_icon(self)
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
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(pady=(0, 12))
        self.button(actions, "刷新批次", self.refresh).pack(side="left", padx=5)
        self.button(actions, "商户映射", workflow.show_merchant_mappings).pack(side="left", padx=5)
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


class MerchantMappingList(FlowWindow):
    def __init__(self, workflow):
        self._refresh_future = None
        self._can_delete = False
        super().__init__(workflow.app, "商户映射", "700x430")
        self.workflow = workflow
        self.mappings = ()
        self.status = self.label(self, "", wraplength=540)
        self.status.pack(fill="x", padx=12, pady=8)
        body = ctk.CTkFrame(self, fg_color="white")
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)
        # 原生表格保持单选，滚动时不会为每条映射单独创建按钮。
        self.tree = ttk.Treeview(body, columns=("merchant", "direction", "tag"), show="headings",
                                 selectmode="browse")
        for key, title, width in (("merchant", "商户", 260), ("direction", "收支", 72), ("tag", "标签", 120)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=64)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        # 窄窗保留表格列宽，选中项的完整名称在可滚动核对区换行展示。
        self.detail = ctk.CTkTextbox(self, height=64, font=self.font, wrap="word")
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(side="bottom", pady=10)
        self.detail.pack(side="bottom", fill="x", padx=12, pady=(0, 4))
        # 先为核对区和操作按钮分配空间，表格可缩短并通过滚动查看。
        body.pack(fill="both", expand=True, padx=12, pady=6)
        self.refresh_button = self.button(actions, "刷新", self.refresh)
        self.refresh_button.pack(side="left", padx=5)
        self.remove = self.button(actions, "删除选中映射", self._delete_selected, danger=True)
        self.remove.pack(side="left", padx=5)
        self.tree.bind("<<TreeviewSelect>>", self._show_selected)
        self.refresh()
        self.show()
        if not self.closing:
            fit_dialog_to_work_area(workflow.app, self, (480, 270))

    def refresh(self, *, message=""):
        if self.closing or self._refresh_future is not None:
            return  # 等待同一份快照，重复打开或点击刷新不会排队查询。
        self._can_delete = False
        self.remove.configure(state="disabled")
        self.refresh_button.configure(state="disabled")
        self.status.configure(text=message or "正在读取商户映射…")
        # 使用现有读取线程等待 SQLite 锁，窗口可继续操作和关闭。
        self._refresh_future = self.workflow.executor.submit(self.workflow.store.list_merchant_mappings)
        self.after(50, self._poll_refresh, message)

    def _poll_refresh(self, message):
        future = self._refresh_future
        if self.closing or future is None:
            return
        if not future.done():
            self.after(50, self._poll_refresh, message)
            return
        self._refresh_future = None
        self.refresh_button.configure(state="normal")
        try:
            self.mappings = future.result()
        except Exception as error:
            self.status.configure(text=f"读取商户映射失败：{error}")
            return  # 权限或数据库错误不得显示成空列表。
        self.tree.delete(*self.tree.get_children())
        for index, item in enumerate(self.mappings):
            self.tree.insert("", "end", iid=str(index), values=(item.merchant.name, item.direction, item.tag))
        self._can_delete = True
        self._show_selected()
        self.status.configure(text=message or (
            "删除映射只影响今后的导入。" if self.mappings else "暂无已记住的商户映射"))

    def _show_selected(self, _event=None):
        selected = self.tree.selection()
        if selected:
            mapping = self.mappings[int(selected[0])]
            text = f"商户：{mapping.merchant.name}\n收支：{mapping.direction}\n标签：{mapping.tag}"
        else:
            text = "选择一项可查看完整的商户名称和标签。"
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")
        self.remove.configure(state="normal" if selected and self._can_delete else "disabled")

    def _delete_selected(self):
        selected = self.tree.selection()
        if selected and self._can_delete:
            self.workflow.delete_merchant_mapping(self.mappings[int(selected[0])], self)

    def _on_destroy(self, event):
        if event.widget is self and self._refresh_future is not None:
            # 取消尚未执行的读取，已运行的查询由线程池收尾；父类移除本窗轮询。
            self._refresh_future.cancel()
            self._refresh_future = None
        super()._on_destroy(event)


class ImportWorkflow:
    """批次事务只调用 store 的公开接口，读入与显示错误不能冒充事务回滚。"""

    def __init__(self, app):
        self.app, self.store = app, app.store
        self.closed, self.writing = False, False
        self.preview = self.loading = self.batch_list = self.failure = self.merchant_list = None
        self.reports = []
        self.future = self.poll_job = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bill-reader")
        self.last_batch, self.last_error = None, ""
        self._shutdown_thread = None
        self._shutdown_error = None

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
            self.loading.lift()  # type: ignore[reportOptionalMemberAccess] # 读取在途时 loading 必已创建。
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
        if not self.store.merchants_available:
            self.notice("无法导入", "商户数据库尚未就绪，请重启完成数据库升级。")
            return
        self.loading = FlowWindow(self.app, "读取账单", "520x180")
        self.loading.label(self.loading, "正在读取账单及商户映射，尚未写入账本。", wraplength=470).pack(pady=30)
        self.loading.button(self.loading, "取消", self.cancel_read).pack()
        self.loading.protocol("WM_DELETE_WINDOW", self.cancel_read)
        self.loading.bind("<Escape>", lambda _event: self.cancel_read())
        self.loading.show()
        if self.closed or not _alive(self.loading):
            return
        self.future = self.executor.submit(self._load_preview, Path(path))
        self.poll_job = self.app.after(50, self.poll_read)

    def _load_preview(self, path):
        bill, vocabulary = _read_import(path)
        # 独立连接可能等待 SQLite 锁，随账单一起在读取线程完成，加载窗仍可响应取消。
        return bill, vocabulary, self.store.list_merchant_mappings()

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
            bill, vocabulary, merchant_mappings = future.result()
            session = ImportSession(bill, vocabulary, merchant_mappings=merchant_mappings)
            self.preview = ImportDialog(self.app, session, on_confirm=self.submit)
        except Exception as error:
            if session is not None:
                session.close()
            self.last_error = str(error)
            if not self.closed:
                self.notice("无法打开账单", f"{error}\n账本未写入，请检查账单或数据库后重试。")

    def reload_main(self):
        try:
            self.store.load()
            if not self.closed:
                self.app.refresh_records()  # 一次全量 load 和一次渲染，避免逐笔 CRUD 放大 #19。
        except Exception as error:
            return f"账本操作已完成，列表刷新失败：{error}。请重试刷新列表。"
        return ""

    def submit(self, result):
        preview = self.preview
        if self.closed or self.writing or not _alive(preview):
            return False
        self.writing = True
        try:
            session = preview.session
            try:
                payload = session.export_import_submission(result)
            except ValueError as error:
                preview.footer.configure(text=str(error))
                return False  # 同批学习冲突可在当前预览修正，账本事务尚未开启。
            if payload.replacements:
                changes = "\n".join(f"{old.merchant.name}（{old.direction}）：{old.tag} → {new.tag}"
                                    for old, new in payload.replacements)
                revision = session.revision
                if not self._confirm_merchant_action(preview, "确认更新商户映射", changes, "更新映射并导入"):
                    return False
                if self.closed or preview.closing or session.revision != revision:
                    return False  # 确认弹窗让出事件循环，旧载荷不能用于已变化的预览。
            try:
                # 常用标签可能已被另一实例移除，提交前拒绝写入已经失效的预览选择。
                available = set(build_tag_candidates())
                if any(tag not in available for record in payload.records for tag in record.tags):
                    raise MerchantMappingConflict("常用标签已变化，请重新核对预览标签。")
                batch = self.store.import_batch(payload.records, session.bill.file_name,
                    result.counts["规则跳过"], result.counts["非交易行"],
                    expected_mappings=payload.expectations, merchant_changes=payload.changes)
            except MerchantMappingConflict as error:
                self.last_error = str(error)
                self.show_failure(error)
                if not self.closed and not preview.closing:
                    try:
                        preview.refresh_merchant_context(self.store.list_merchant_mappings(), build_tag_candidates())
                    except Exception as refresh_error:
                        preview.footer.configure(text=f"商户映射刷新失败：{refresh_error}")
                return False
            except Exception as error:
                self.last_error = str(error)
                self.show_failure(error)
                return False  # store 事务保证全部回滚；原预览及决定保留。
            self.last_batch = batch
            error = self.reload_main() if batch is not None else ""
            # 此边界以后绝不重试写入；报告建窗异常也不能让原预览再次提交。
            try:
                if not self.closed:
                    report = ImportReport(self, session.bill.file_name, result.counts, batch, error)
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

    def show_merchant_mappings(self):
        if self.closed:
            return
        if not self.store.merchants_available:
            self.notice("无法查看商户映射", "商户数据库尚未就绪，请重启完成数据库升级。")
        elif _alive(self.merchant_list):
            self.merchant_list.refresh()
            self.merchant_list.lift()
        else:
            self.merchant_list = MerchantMappingList(self)

    def _confirm_merchant_action(self, parent, title, details, caption, *, danger=False):
        window = FlowWindow(parent, title, "570x300")
        # 文本框保留长商户名称及所有将替换的映射，不因条数增加把按钮推出窗口。
        content = ctk.CTkTextbox(window, height=165, font=window.font, wrap="word")
        content.pack(fill="both", expand=True, padx=14, pady=12)
        content.insert("1.0", details)
        content.configure(state="disabled")
        actions = ctk.CTkFrame(window, fg_color="transparent")
        actions.pack(pady=(0, 12))
        confirmed = False

        def accept():
            nonlocal confirmed
            confirmed = True
            window.destroy()

        window.button(actions, "取消", window.destroy).pack(side="left", padx=5)
        window.button(actions, caption, accept, danger=danger, primary=not danger).pack(side="left", padx=5)
        window.show()
        if not window.closing and fit_dialog_to_work_area(parent, window, (350, 190)):
            window.grab_set()
            parent.wait_window(window)
        return confirmed

    def delete_merchant_mapping(self, mapping, origin):
        if self.closed or self.writing:
            return
        self.writing = True
        try:
            detail = f"{mapping.merchant.name}（{mapping.direction}） → {mapping.tag}\n\n删除只影响今后的导入。"
            if not self._confirm_merchant_action(origin, "确认删除商户映射", detail, "删除映射", danger=True):
                return
            if self.closed or origin.closing:
                return
            try:
                self.store.delete_merchant_mapping(mapping)
            except Exception as error:
                origin.refresh(message=f"删除映射失败：{error}")
            else:
                origin.refresh()
        finally:
            self.writing = False

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
        for window in (self.preview, self.batch_list, self.merchant_list, self.failure, *self.reports):
            if _alive(window):
                window.destroy()
        # cancel_read会丢弃future引用；必须等线程池真正排空，不能据此误判文件已释放。
        def drain():
            try:
                self.executor.shutdown(wait=True, cancel_futures=True)
            except Exception as error:
                self._shutdown_error = error  # 失败必须交给退出协调者，不能伪报收尾完成。
        self._shutdown_thread = threading.Thread(target=drain, name="bill-reader-shutdown", daemon=False)
        try:
            self._shutdown_thread.start()
        except RuntimeError as error:
            self._shutdown_error = error  # 未启动的线程也is_alive=False，必须阻止假收尾。

    def shutdown_ready(self):
        if self._shutdown_error is not None:
            raise RuntimeError("导入线程池收尾失败") from self._shutdown_error
        # is_alive为假才证明shutdown线程也已退出；主线程不join、不等文件读取。
        return (self.closed and not self.writing and self._shutdown_thread is not None
                and not self._shutdown_thread.is_alive())
