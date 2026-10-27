"""导入预览门面；协调原生会话、分页令牌、用户决定及所属异步任务。"""

import tkinter as tk

from dialog_lifecycle import ManagedToplevel
from import_dialog_view import ImportDialogView
from import_pagination import ImportPagination
from import_preview_table import HeaderState, ImportPreviewTable
from import_session import Selection


class ImportDialog(ManagedToplevel):
    def __init__(self, parent, session, *, on_confirm=None, mapping_path=None):
        super().__init__(parent, fg_color="#F0F4F8")
        self.session, self.on_confirm, self.mapping_path = session, on_confirm, mapping_path
        self.accepted_result = None
        self.title("导入外部账单")
        self.geometry("1220x740")
        self.minsize(980, 660)
        self.font = ("Microsoft YaHei UI", 12)
        self._poll_job = self._render_job = None
        self._render_id = self._mapping_key = 0
        self._ending = False
        self.view = self.table = None  # 构建异常也能按已建组件逐项清理。
        self._preview_target = self._preview_ready = None
        self._preview_result = None
        self.pagination = ImportPagination()
        self._preview_meta = None
        self._selection_number = None
        self._scroll_reset = False
        self._advance_after_render = False
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Destroy>", self._destroyed_event, add="+")
        try:
            self._build()
            self.finish_setup()
            self.refresh()
            self._ensure_poll()
        except Exception:
            # 构建失败也必须释放线程及 Tk 任务，不能留下半个预览窗。
            self.finish_setup()
            self.destroy()
            raise

    def _build(self):
        # 视图只持有 Tk 父级与事件回调，业务对象及令牌留在当前门面。
        actions = dict(view=self.switch_view, save=self.save_mapping,
            search=self._search_changed, filter=self._filter_changed,
            locate=self.locate_next, all=self._show_all, row=self._row_action,
            duplicate=self._duplicate_changed, close=self.destroy, submit=self.submit, page=self._change_page,
            page_delta=lambda delta: self._change_page(self.pagination.page + delta),
            wheel=lambda event: self.table._wheel(event),
            rebuild_fields=self._build_fields,
            role=lambda role, indices: self._run(lambda: self.session.update_role(role, indices)),
            note=lambda option, value: self._run(lambda: self.session.update_note_options(**{option: value})),
            apply_mapping=lambda mapping: self._run(lambda: self.session.apply_saved_mapping(mapping)),
            delete_mapping=self._delete_mapping, save_mapping=self._save_mapping)
        self.view = ImportDialogView(self, self.font, actions, file_name=self.session.bill.file_name,
            search_text=self.session.search_text, only_unhandled=self.session.only_unhandled)
        self.view.build()
        self.footer = self.view.footer  # 门面保留提交入口使用的错误展示句柄，控件仍归视图持有。
        self.table = ImportPreviewTable(self, self.view.details, self.font,
            on_select=self._selected, on_choose=self._choose, on_confirm=self._confirm_selection,
            on_clear=lambda group: self._run(lambda: self.session.clear_group(group)),
            header_state=self._header_state)
        self._build_fields()

    def _build_fields(self):
        self.view.build_fields(self.session.headers(), self.session.bill.rows,
            self.session.mapping, self.session.options)

    def _header_state(self, group, pair, historical):
        source = self.session.obsolete if historical else self.session
        selection = source.pair_choices.get(pair, Selection("两笔都跳过")) if pair else source.group_choices.get(group, Selection())
        actions = ("两笔都跳过", "只记支出", "两笔都记", "只记退款") if pair else self.session.group_actions(group)
        ready = self._preview_current() and self._preview_ready == (
            "pair" if pair else "group", pair or group, self.session.version)
        return HeaderState(selection.value, selection.state, selection.explicit, actions,
            not historical and not self.session.busy, ready, len(source.groups.get(group, ())))

    def _run(self, operation):
        try:
            operation()
        except (ValueError, OSError) as error:
            self.view.footer.configure(text=str(error))  # 保留现场及当前选择，不把失败伪装成功。
            return False
        self.refresh()
        self._ensure_poll()
        return True

    def _change_page(self, page, *, target=None):
        if self._ending:
            return
        try:
            if self._selection_number is not None:
                self.pagination.remember(self._selection_number)
            self.pagination.move(page)
        except ValueError as error:
            self.view.footer.configure(text=str(error))
            return
        self.pagination.target = target
        self._preview_ready = None  # 就绪资格只属于已核对过的当前页，业务候选继续保留。
        self._scroll_reset = True
        self._render(self.session.result is None and self.session.obsolete is not None)

    def _preview_choice(self):
        kind, target, _version = self._preview_target  # type: ignore[reportGeneralTypeIssues] # 调用方与 _candidate_valid 已保证 _preview_target 非空。
        choices = self.session.pair_choices if kind == "pair" else self.session.group_choices
        return choices.get(target, Selection("两笔都跳过" if kind == "pair" else None)).value if kind != "row" else "入账"

    def _request_preview(self, operation):
        def request():
            operation()
            # 请求号与源结果共同保护同代次内的异步试算；页号仅保护展示，不废弃业务结果。
            self._preview_meta = (self.session, self.session.version, self.session.request_id,
                self.session.result, self._preview_target, self._preview_choice(),
                (self.pagination.epoch, self.pagination.page))
        return self._run(request)

    def _candidate_valid(self):
        meta = self._preview_meta
        return bool(meta and self._preview_target and meta[:3] == (
            self.session, self.session.version, self.session.request_id) and
            meta[3] is self.session.result and meta[4] == self._preview_target and
            meta[5] == self._preview_choice())

    def _bind_preview_page(self):
        if self._candidate_valid():
            self._preview_meta = (*self._preview_meta[:-1], (self.pagination.epoch, self.pagination.page))  # type: ignore[reportOptionalSubscript] # 仅当 _candidate_valid() 为真（meta 非空）时才进入。

    def _preview_current(self):
        return self._candidate_valid() and self._preview_meta[-1] == (self.pagination.epoch, self.pagination.page)  # type: ignore[reportOptionalSubscript] # 左值 _candidate_valid() 已保证 meta 非空。

    def _ensure_poll(self):
        if not self._ending and self.session.busy and self._poll_job is None:
            self._poll_job = self.after(50, self._poll)  # type: ignore[reportArgumentType] # Tk 接受无参回调，存根对回调类型标注过窄。

    def _poll(self):
        self._poll_job = None
        if self._ending:
            return
        if self.session.poll():
            self.refresh()
            if self._preview_target is not None and not self.session.busy:
                self._finish_preview()
        self._ensure_poll()

    def refresh(self):
        if self._ending:
            return
        session = self.session
        if self._preview_result is not session.result:
            # 规则结果/决定生效后，旧试算不再代表当前选择；筛选切换仍保留有效候选。
            self._preview_target = self._preview_ready = None
            self._preview_meta = None
            self._preview_result = session.result
        historical = session.result is None and session.obsolete is not None
        self.view.refresh_status(session, historical)
        key = (session.version, id(session.mapping))
        if key != self._mapping_key:
            self._mapping_key = key
            self._build_fields()
            self._saved_candidates()
        self._render(historical)

    def _render(self, historical):
        # 每次筛选生成独立渲染编号；销毁旧表后旧批次不能继续插入。
        source = self.session.obsolete if historical else self.session.result
        context = (id(self.session), self.session.version, historical, self.session.current_view,
                   self.session.search_text, self.session.only_unhandled)
        summary = (self.session.current_view in ("规则跳过", "非交易行") and
                   self.session.current_view not in self.session.show_all and not self.session.search_text)
        self.pagination.bind(context, source, lambda: self.session.visible_rows(
            historical=historical, summary=False), summary)
        self._render_id = self.pagination.new_render()
        token, version = self._render_id, self.session.version
        if self._render_job:
            self.after_cancel(self._render_job)
            self._render_job = None
        self._scroll_fraction = 0 if self._scroll_reset else self.view.details._parent_canvas.yview()[0]
        self._scroll_reset = False
        self.table.reset()
        self._selection_number = None
        self._restore_selection = self.pagination.selections.get(self.pagination.page)
        self.view.inspect_title.configure(text="单笔核对 · 点击上方记录查看")
        self.view.set_raw("选择一笔记录查看日期、金额、备注和原始内容。\n需要批量处理时，请使用对应组标题处的操作。")
        self._set_row_state(None, historical)
        self.view.show_inspection(False)
        rows, full = self.pagination.page_rows, self.pagination.rows
        suffix = " · 历史回看（已作废）" if historical else ""
        self.view.view_title.configure(text=f"{self.session.current_view} · 共 {len(full)} 笔{suffix}" +
            (f"，当前展示前 5 笔" if summary and len(full) > 5 else ""))
        self.view.all_button.configure(state="normal" if summary and len(full) > 5 else "disabled")
        self.view.update_pager(self.pagination)
        if not rows:
            self.view.controls.label(self.view.details, "未找到匹配记录" if self.session.search_text else "当前视图无记录").grid(row=0, column=0)
        self._render_job = self.after(0, lambda: self._render_batch(rows, 0, token, version, historical))  # type: ignore[reportArgumentType] # Tk 接受无参回调，存根对回调类型标注过窄。

    def _render_batch(self, rows, start, token, version, historical):
        if self._ending or token != self._render_id or version != self.session.version:
            return
        self._render_job = None
        source = self.session.obsolete if historical else self.session
        self.table.append_rows(rows[start:start + 20], historical=historical,
            pending=self.session.current_view == "待确认", source=source,
            display_values=self.session.display_values, continuation=self.pagination.continuation)
        if start + 20 < len(rows):
            self._render_job = self.after(1, lambda: self._render_batch(rows, start + 20, token, version, historical))  # type: ignore[reportArgumentType] # Tk 接受无参回调，存根对回调类型标注过窄。
        else:
            self.view.details._parent_canvas.yview_moveto(self._scroll_fraction)
            target, self.pagination.target = self.pagination.target, None
            location = self.table.row_locations.get(target or self._restore_selection)
            if location and not self._advance_after_render:
                self.table.highlight(target or self._restore_selection)
                if token != self._render_id:
                    return  # 选择触发试算及 refresh 后不能继续操作已销毁控件。
            if self._advance_after_render and not historical and not self.session.busy:
                self._advance_after_render = False
                if not self.session.error and self.session.current_view == "待确认" and self.session.only_unhandled:
                    self.locate_next()
            # 默认退款对也自动准备核对内容，不要求用户先猜到要点某笔明细。
            if not historical and not self.session.busy and not self._selection_number:
                if self._candidate_valid() and self._preview_meta[-1] == (self.pagination.epoch, self.pagination.page):  # type: ignore[reportOptionalSubscript] # 左值 _candidate_valid() 已保证 meta 非空。
                    self._finish_preview()
                elif self.table.pair_controls:
                    pair = next(iter(self.table.pair_controls))
                    if self._preview_target == ("pair", pair, self.session.version) and self._candidate_valid():
                        self._bind_preview_page()
                        self._finish_preview()
                    else:
                        self._prepare_pair(pair)

    def _choose(self, action, group, pair):
        self._preview_target = ("pair" if pair else "group", pair or group, self.session.version)
        self._preview_ready = None
        self._selection_number = None  # 组核对不能被旧行的选择恢复回调抢占。
        self.pagination.remember(None)
        def operation():
            if pair:
                self.session.select_pair(pair, action)
                self.session.request_preview(key=pair, action=action)
            else:
                self.session.select_group(group, action)
                self.session.request_group_preview(group)
        self._request_preview(operation)

    def _confirm_selection(self, group, pair):
        ready = self._preview_ready == ("pair" if pair else "group", pair or group, self.session.version)
        if not ready or not self._preview_current():
            if pair:
                self._prepare_pair(pair)
            else:
                self.view.footer.configure(text="请先选择处理方式并核对本组预览")
            return
        version = self.session.version
        self._advance_after_render = True
        self._preview_target = self._preview_ready = None
        self._run(lambda: self.session.confirm_pair(pair, version=version) if pair else
                  self.session.confirm_group(group, version=version))

    def _prepare_pair(self, pair):
        self._selection_number = None
        self.pagination.remember(None)
        self._preview_target, self._preview_ready = ("pair", pair, self.session.version), None
        action = self.session.pair_choices.get(pair, Selection("两笔都跳过")).value
        self._request_preview(lambda: self.session.request_preview(key=pair, action=action))

    def _row_action(self, action):
        number = self._selection_number
        if number is None:
            return
        if action == "跳过":
            self._advance_after_render = True
            self._run(lambda: self.session.confirm_row(number, action))
        else:
            if self._preview_ready != ("row", number, self.session.version):
                self.view.footer.configure(text="请先核对最终入账预览")
                return
            self._advance_after_render = True
            self._preview_target = self._preview_ready = None
            self._run(lambda: self.session.confirm_row(number, action))

    def _finish_preview(self):
        kind, target, version = self._preview_target  # type: ignore[reportGeneralTypeIssues] # 调用前已由 _candidate_valid 保证 _preview_target 非空。
        if version != self.session.version or not self._candidate_valid():
            self._preview_target = self._preview_ready = None
            return
        if self._preview_meta[-1] != (self.pagination.epoch, self.pagination.page):  # type: ignore[reportOptionalSubscript] # 上方 _candidate_valid() 已保证 meta 非空。
            return  # 业务结果留在会话中；旧页回传不能填写当前核对区。
        if self.session.preview_error or self.session.preview is None:
            self.view.footer.configure(text=self.session.preview_error or "无法生成最终入账预览")
            self.view.row_hint.configure(text=self.session.preview_error or "无法生成结果，请核对字段或选择跳过。")
            return
        ids = (target,) if kind == "row" else target if kind == "pair" else self.session.groups[target]
        self._preview_ready = self._preview_target
        if self.session.current_view != "待确认" or not any(number in self.table.row_locations for number in ids):
            return  # 跨视图/搜索保留候选，但不把别处的批量核对内容填入当前详情。
        # 最终内容在只读核对区呈现，原确认按钮就是明确决定，不额外再弹确认框。
        members = set(ids)
        rows = [row for row in self.session.preview.rows if row.row_number in members]
        self.view.show_inspection(True)
        self.view.inspect_title.configure(text=f"正在处理：原始第 {target} 行" if kind == "row" else f"最终处理预览 · {'本对' if kind == 'pair' else '本组'}共 {len(ids)} 笔")
        self.view.set_raw(self._inspection_text(rows, final=True))
        self.view.row_hint.configure(text="核对入账后内容，再点「此笔入账」；不记这笔可点「跳过此笔」。" if kind == "row" else
                                "尚未生效：核对以上内容后，点击对应标题处的确认按钮。")
        controls = self.table.pair_controls.get(target) if kind == "pair" else self.table.group_controls.get(target) if kind == "group" else None
        if controls:
            self.view.controls.button_state(controls["confirm"], True)
            controls["confirm"].configure(text="确认本对" if kind == "pair" else "确认处理本组")
            if controls["status"] != "✓ 已确认":
                controls["status_label"].configure(text="默认选项未确认，两笔仍待确认" if controls["status"] == "默认选项未确认" else "尚未生效，请确认")
            else:
                self.view.row_hint.configure(text="当前处理方式已确认；改选其他方式后，需再次确认。")
        if kind == "row" and self._selection_number == target:
            self.view.controls.button_state(self.view.income_button, True)

    def _inspection_text(self, rows, *, final=False):
        blocks = []
        headers = self.session.headers()
        for row in rows:
            number, date, amount, note, result = self.session.display_values(row)
            heading = "入账后内容" if final and row.category == "将导入" else "处理后内容" if final else "当前内容"
            raw = "\n".join(f"  {headers[i] if i < len(headers) and headers[i] else '第 ' + str(i + 1) + ' 列'}：{value}"
                            for i, value in enumerate(row.raw_values))
            blocks.append(f"{heading} · 原始第 {number} 行\n日期：{date}    金额：{amount}（收入 + / 支出 −）\n备注：{note}\n处理结果：{result}\n\n原始内容（只读）\n{raw}")
        return "\n\n────────────\n\n".join(blocks)

    def _selected(self, tree):
        selected = tree.selection()
        if not selected:
            return
        match = next(((n, r) for n, (t, item, r) in self.table.row_locations.items() if t is tree and item == selected[0]), None)
        if match is None:
            return
        number, row = match
        self._selection_number = number
        self.pagination.remember(number)
        self.session.located_row = number
        historical = self.session.result is None
        notice = "已作废（映射已变，决定不能应用）\n" if historical else ""
        self.view.show_inspection(True)
        self.view.inspect_title.configure(text=f"{'历史回看' if historical else '正在处理'}：原始第 {number} 行")
        self.view.set_raw(notice + self._inspection_text((row,)))
        self._set_row_state(row, historical)
        if not historical and not self.session.busy and self.session.current_view == "待确认" and row.row_number in {n for ids in self.session.groups.values() for n in ids}:
            if row.pair_key or row.can_import and row.group != "疑似退款对":
                target = ("pair", row.pair_key, self.session.version) if row.pair_key else ("row", number, self.session.version)
                if self._preview_target == target and self._candidate_valid() and self.session.preview is not None:
                    self._preview_target = target
                    self._bind_preview_page()
                    self._finish_preview()
                elif self._preview_target != target or self.session.preview is None:
                    self._preview_target, self._preview_ready = target, None
                    action = self.session.pair_choices.get(row.pair_key, Selection("两笔都跳过")).value
                    self._request_preview(lambda: self.session.request_preview(key=row.pair_key, action=action) if row.pair_key else
                              self.session.request_preview(number=number))

    def _set_row_state(self, row, historical):
        enabled = row is not None and not historical and not self.session.busy
        origins = {n for ids in self.session.groups.values() for n in ids}
        ordinary = enabled and self.session.current_view == "待确认" and row.row_number in origins and row.pair_key is None  # type: ignore[reportOptionalMemberAccess] # enabled 已包含 row is not None，静态无法从布尔别名推断。
        ready = row is not None and self._preview_ready == ("row", row.row_number, self.session.version)
        self.view.controls.button_state(self.view.income_button, ordinary and ready and row.can_import and row.group != "疑似退款对")  # type: ignore[reportOptionalMemberAccess] # ordinary/ready 已隐含 row 非空。
        self.view.controls.button_state(self.view.skip_button, ordinary)
        self.view.duplicate_var.set(bool(row and row.duplicate_released))
        self.view.duplicate_check.configure(state="normal" if enabled and row.duplicate_of is not None else "disabled")  # type: ignore[reportOptionalMemberAccess] # enabled 已包含 row is not None。
        self.view.row_hint.configure(text="旧决定已作废，仅供回看；请修正字段重试或取消。" if historical else
            "点击上方一笔记录查看原值；批量处理请使用组标题处的操作。" if row is None else
            "正在生成结果预览，请稍候。" if self.session.busy else
            "此笔已有处理决定，可切到「待确认」并取消「仅看未处理」后修改。" if row.row_number in origins and self.session.current_view != "待确认" else
            "这两笔需要一起决定，请使用退款对标题右侧的操作。" if row.pair_key else
            "此笔暂不能入账：" + row.reason + "；可跳过或修正字段。" if ordinary and not row.can_import else
            "勾选「仍要导入」仅解除重复限制，还需通过其他规则。" if row.duplicate_of is not None else
            "请核对结果，再选择此笔入账或跳过。" if ordinary else "当前记录无需处理，可核对原始内容。")

    def _duplicate_changed(self):
        number = self._selection_number
        if number is not None:
            self._run(lambda: self.session.release_duplicate(number, self.view.duplicate_var.get()))

    def switch_view(self, view):
        if view == self.session.current_view:
            return
        self.pagination.reset_context()
        self.session.set_view(view)
        self._scroll_reset = True
        self.refresh()

    def _search_changed(self, *_args):
        if not self._ending:
            if self.view.search_var.get().strip() == self.session.search_text:
                return
            self.pagination.reset_context()
            self.session.set_search(self.view.search_var.get())
            self._scroll_reset = True
            self.refresh()
            if self.session.search_text and self.pagination.rows:
                self.pagination.target = self.pagination.rows[0].row_number

    def _filter_changed(self):
        self.pagination.reset_context()
        self.session.set_only_unhandled(self.view.only_var.get())
        self._scroll_reset = True
        self.refresh()

    def _show_all(self):
        self.pagination.reset_context()
        self.session.show_all.add(self.session.current_view)
        self._scroll_reset = True
        self.refresh()

    def locate_next(self):
        number = self.session.next_unhandled()
        if number is None:
            self.view.footer.configure(text="当前筛选无未处理记录")
            return
        page = self.pagination.page_of(number)
        if page is not None and page != self.pagination.page:
            self._change_page(page, target=number)
        elif number not in self.table.row_locations:
            self.pagination.target = number  # 本页尚在插入时，将定位留给渲染完成回调兑现。
        else:
            self.table.highlight(number)

    def _delete_mapping(self, fingerprint):
        if self._run(lambda: self.session.delete_mapping(fingerprint, self.mapping_path)):
            self._saved_candidates()  # 删除只收缩列表，不自动套用其他项。

    def _saved_candidates(self):
        try:
            candidates = self.session.mapping_candidates(self.mapping_path)
        except (ValueError, OSError) as error:
            self.view.show_saved_candidates(error=error)  # 失败原样展示，不当作空映射列表。
            return
        self.view.show_saved_candidates(candidates)

    def _save_mapping(self, name, overwrite):
        self.session.save_mapping(name, overwrite=overwrite, path=self.mapping_path)
        self.view.footer.configure(text="映射已保存")
        self._saved_candidates()

    def save_mapping(self):
        self.view.show_save_mapping(self.session.headers())

    def show_help(self):
        self.view.show_help()

    def submit(self):
        try:
            result = self.session.export_result()
        except ValueError as error:
            self.footer.configure(text=str(error))
            return
        # 没有回调也可作为独立预览使用；这里绝不执行 store 的任何操作。
        if self.on_confirm and self.on_confirm(result) is False:
            return  # 写入失败的回调保留原预览和决定，供用户修正或重试。
        self.accepted_result = result
        self.destroy()

    def _destroyed_event(self, event):
        if event.widget is self:
            self._stop()

    def _stop(self):
        if self._ending:
            return
        self._ending = True
        self.pagination.close()
        self._render_id = self.pagination.token
        for job in (self._poll_job, self._render_job):
            if job:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass  # 原生父窗先销毁时任务可能已被所属窗口清理。
        self._poll_job = self._render_job = None
        if self.table is not None:
            self.table.close()
        if self.view is not None:
            self.view.close()
        self.session.close()

    def destroy(self):
        self._stop()
        super().destroy()
