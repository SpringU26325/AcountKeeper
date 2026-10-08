"""导入预览的数据会话；不持有 Tk，不写账本，规则计算只走公开接口。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from queue import Empty, Queue
from threading import get_ident
from types import MappingProxyType

from import_reader import BillFile, RawCell, RawRow
from import_rules import FieldMapping, NoteOptions, analyze_bill, apply_decisions


def frozen_map(values):
    # 必须复制底层字典，否则只读视图仍会跟着调用者的可变字典变化。
    return MappingProxyType(dict(values))


@dataclass(frozen=True)
class Selection:
    value: str | None = None
    confirmed: str | None = None
    explicit: bool = False

    @property
    def state(self):
        if self.value is None:
            return "未选择"
        return "已确认" if self.value == self.confirmed else "已选择未确认"


@dataclass(frozen=True)
class ObsoleteSnapshot:
    version: int
    result: object
    groups: object
    pairs: object
    row_actions: object
    pair_actions: object
    group_choices: object
    pair_choices: object
    released_duplicates: frozenset
    progress: tuple[int, int, int]
    notice: str = "已作废（映射已变，决定不能应用）"

    @property
    def mapping(self):
        return self.result.mapping


class ImportSession:
    def __init__(self, bill: BillFile, vocabulary, mapping=None, *, options=None):
        # 仅支持读取器的不可变标量；外层 frozen 不足以保护任意 object。
        scalar = (str, int, float, bool, date, time, timedelta, Decimal, type(None))
        if any(not isinstance(c.value, scalar) for r in bill.rows for c in r.cells):
            raise ValueError("原始单元格必须是不可变标量")
        self.bill = BillFile(bill.file_name, tuple(RawRow(r.row_number, tuple(
            RawCell(c.value, c.kind, c.number_format) for c in r.cells)) for r in bill.rows),
            bill.encoding, bill.sheet_name)
        self.vocabulary = replace(vocabulary,
            fields=tuple(replace(t, roles=tuple(t.roles)) for t in vocabulary.fields),
            status_rules=tuple(replace(t, value_set=tuple(t.value_set)) for t in vocabulary.status_rules),
            empty_markers=tuple(vocabulary.empty_markers))
        self.mapping = mapping
        self.options = options or NoteOptions()
        self.baseline = self.result = self.obsolete = None
        self.current_view, self.search_text, self.only_unhandled = "将导入", "", True
        self.show_all, self.located_row = set(), None
        self.groups, self.pairs = frozen_map({}), frozen_map({})
        self.row_actions, self.pair_actions = frozen_map({}), frozen_map({})
        self.group_choices, self.pair_choices = frozen_map({}), frozen_map({})
        self.released_duplicates = frozenset()
        self.version = self.request_id = self.revision = 0
        self.error, self.preview, self.preview_error = "", None, ""
        self.closed = self.busy = False
        self._owner, self._queue = get_ident(), Queue()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bill-preview")
        self.task_handle = None
        self._initial_view = True
        self._recompute()

    def _main(self):
        if get_ident() != self._owner:
            raise RuntimeError("会话状态只能由所属主线程修改")

    def _guard(self, version=None):
        self._main()
        if self.closed or version is not None and version != self.version:
            raise ValueError("旧版本已作废，不能修改、清除、应用或提交")
        if self.busy or self.result is None:
            raise ValueError("当前结果不可操作；旧快照仅供只读回看")

    def _submit(self, kind, operation, metadata=None):
        self.request_id += 1
        version, request, outbox = self.version, self.request_id, self._queue
        if self.task_handle is not None:
            self.task_handle.cancel()  # 只能取消尚未运行的任务；运行中的回传由代次拒收。
        self.busy, self.error = True, ""
        future = self._executor.submit(operation)
        self.task_handle = future

        def completed(done):
            try:
                value, error = done.result(), None
            except Exception as caught:
                value, error = None, caught  # 异常也必须携代次，旧失败不能污染新界面。
            outbox.put((version, request, kind, value, error, metadata))

        future.add_done_callback(completed)

    def _recompute(self):
        self._main()
        if self.closed:
            raise ValueError("会话已关闭")
        if self.result is not None:
            self.obsolete = ObsoleteSnapshot(self.version, self.result, self.groups, self.pairs,
                self.row_actions, self.pair_actions, self.group_choices, self.pair_choices,
                self.released_duplicates, self.progress)
        # 归档与当前状态分离，连续失败不覆盖最后一份有效历史。
        self.version += 1
        self.baseline = self.result = None
        self.groups, self.pairs = frozen_map({}), frozen_map({})
        self.row_actions, self.pair_actions = frozen_map({}), frozen_map({})
        self.group_choices, self.pair_choices = frozen_map({}), frozen_map({})
        self.released_duplicates = frozenset()
        self.preview, self.preview_error, self.located_row = None, "", None
        rows, vocab, mapping, options = self.bill.rows, self.vocabulary, self.mapping, self.options
        self._submit("analysis", lambda: analyze_bill(rows, vocab, mapping, options=options))
        self.revision += 1

    def update_mapping(self, mapping):
        self._main()
        self.mapping = FieldMapping(mapping.header_row, mapping.columns, tuple(mapping.review_roles))
        self._recompute()

    def update_role(self, role, indices):
        self._main()
        current = self.mapping or FieldMapping(None, {})
        columns = dict(current.columns)
        columns[role] = tuple(indices)
        self.update_mapping(FieldMapping(current.header_row, columns,
            tuple(r for r in current.review_roles if r != role)))

    def update_note_options(self, **changes):
        self._main()
        self.options = replace(self.options, **changes)
        self._recompute()

    def poll(self):
        self._main()
        changed = False
        while True:
            try:
                version, request, kind, value, error, metadata = self._queue.get_nowait()
            except Empty:
                break
            if self.closed or version != self.version or request != self.request_id:
                continue
            self.busy, self.task_handle = False, None
            changed = True
            if error is not None:
                if kind == "preview":
                    self.preview_error = str(error)
                else:
                    self.error = str(error)
                # 分析失败绝不恢复旧结果；决定失败则保留此前有效结果。
            elif kind == "analysis":
                self.baseline = self.result = value
                self.mapping, self.obsolete = value.mapping, None
                self._index(value)
                if self._initial_view:
                    self.current_view = "待确认" if value.counts["待确认"] else "将导入"
                    self._initial_view = False
            elif kind == "decision":
                semantic, self.result = value
                (self.row_actions, self.pair_actions, self.released_duplicates,
                 self.group_choices, self.pair_choices) = metadata
                self._index(semantic)
            else:
                self.preview = value
            self.revision += 1
        return changed

    def _index(self, semantic):
        groups, pairs = {}, {}
        for row in semantic.rows:
            if row.category == "待确认":
                groups.setdefault(row.group, []).append(row.row_number)
            if row.pair_key:
                pairs[row.pair_key] = row.pair_key
        membership = {n: group for group, ids in groups.items() for n in ids}
        for key in pairs:
            if any(membership.get(n) != "疑似退款对" for n in key):
                raise ValueError("退款对与主要处理组不一致")
        self.groups = frozen_map({g: tuple(ids) for g, ids in groups.items()})
        self.pairs = frozen_map(pairs)

    @property
    def counts(self):
        return self.result.counts if self.result else dict.fromkeys(
            ("将导入", "规则跳过", "待确认", "非交易行"), 0)

    @property
    def progress(self):
        total = sum(len(ids) for ids in self.groups.values())
        processed = len(self.row_actions) + 2 * len(self.pair_actions)
        return total, processed, total - processed

    @property
    def can_submit(self):
        return bool(not self.closed and not self.busy and not self.error
                    and self.result and self.result.can_confirm)

    def export_result(self, *, version=None):
        self._guard(version)
        if not self.can_submit:
            raise ValueError("尚有待确认或必需字段问题，不能提交")
        return self.result  # 只交付整份结果；搜索不能缩小最终提交集合。

    def _change(self, rows=None, pairs=None, released=None, groups=None, choices=None):
        row_actions = frozen_map(self.row_actions if rows is None else rows)
        pair_actions = frozen_map(self.pair_actions if pairs is None else pairs)
        releases = self.released_duplicates if released is None else frozenset(released)
        metadata = (row_actions, pair_actions, releases,
                    frozen_map(self.group_choices if groups is None else groups),
                    frozen_map(self.pair_choices if choices is None else choices))
        base = self.baseline

        def replay():
            semantic = apply_decisions(base, release_duplicates=releases)
            return semantic, apply_decisions(semantic, row_actions=row_actions, pair_actions=pair_actions)

        self.preview, self.preview_error = None, ""
        self._submit("decision", replay, metadata)

    def confirm_row(self, number, action, *, version=None):
        self._guard(version)
        if number not in {n for ids in self.groups.values() for n in ids}:
            raise ValueError("记录不属于当前待确认处理集合")
        rows = dict(self.row_actions)
        rows[number] = action
        groups = dict(self.group_choices)
        group = next(g for g, ids in self.groups.items() if number in ids)
        # 逐笔修改后仅保留真正一致的组状态，不用旧下拉掩盖不同决定。
        uniform = {rows.get(n) for n in self.groups[group]}
        if len(uniform) != 1 or None in uniform:
            groups[group] = Selection()
        self._change(rows=rows, groups=groups)

    def group_actions(self, group):
        ids = self.groups.get(group, ())
        if group == "疑似退款对":
            covered = {n for key in self.pairs for n in key}
            return ("两笔都跳过", "只记支出", "两笔都记", "只记退款") if set(ids) <= covered else ("跳过",)
        by_id = {r.row_number: r for r in self.result.rows} if self.result else {}
        return ("入账", "跳过") if ids and all(by_id[n].can_import for n in ids) else ("跳过",)

    def select_group(self, group, action, *, version=None):
        self._guard(version)
        if group not in self.groups or action not in self.group_actions(group):
            raise ValueError("本组不支持此动作")
        previous = self.group_choices.get(group, Selection())
        self.group_choices = frozen_map({**self.group_choices,
            group: Selection(action, previous.confirmed, True)})
        self.revision += 1

    def confirm_group(self, group, *, version=None):
        self._guard(version)
        rows, pairs, choices, action = self._group_candidate(group)
        self._change(rows, pairs, groups={**self.group_choices,
            group: Selection(action, action, True)}, choices=choices)

    def _group_candidate(self, group):
        selection = self.group_choices.get(group, Selection())
        if selection.value is None:
            raise ValueError("请先选择本组处理方式")
        action = selection.value
        if action not in self.group_actions(group):
            raise ValueError("本组动作已不可用")
        rows, pairs, choices = dict(self.row_actions), dict(self.pair_actions), dict(self.pair_choices)
        ids = self.groups[group]  # 必须用完整索引，绝不能遍历当前搜索可见行。
        if group == "疑似退款对":
            for key in self.pairs:
                pairs[key] = "两笔都跳过" if action == "跳过" else action
                choices[key] = Selection(pairs[key], pairs[key], True)
            paired = {n for key in self.pairs for n in key}
            rows.update({n: "跳过" for n in ids if n not in paired})
        else:
            rows.update({n: action for n in ids})
        return rows, pairs, choices, action

    def select_pair(self, key, action, *, version=None):
        self._guard(version)
        key = tuple(key)
        if key not in self.pairs or action not in ("两笔都跳过", "只记支出", "两笔都记", "只记退款"):
            raise ValueError("退款对或组合无效")
        old = self.pair_choices.get(key, Selection("两笔都跳过"))
        self.pair_choices = frozen_map({**self.pair_choices, key: Selection(action, old.confirmed, True)})
        self.revision += 1

    def confirm_pair(self, key, *, version=None):
        self._guard(version)
        key = tuple(key)
        if key not in self.pairs:
            raise ValueError("退款对不存在")
        choice = self.pair_choices.get(key, Selection("两笔都跳过"))
        confirmed = Selection(choice.value, choice.value, True)
        groups = dict(self.group_choices)
        groups["疑似退款对"] = Selection()
        self._change(pairs={**self.pair_actions, key: choice.value}, groups=groups,
                     choices={**self.pair_choices, key: confirmed})

    def clear_group(self, group, *, version=None):
        self._guard(version)
        if group not in self.groups:
            raise ValueError("本组不存在")
        ids = set(self.groups[group])
        pairs = {key: a for key, a in self.pair_actions.items() if not ids.intersection(key)}
        choices = {key: a for key, a in self.pair_choices.items() if not ids.intersection(key)}
        self._change(rows={n: a for n, a in self.row_actions.items() if n not in ids}, pairs=pairs,
                     groups={g: c for g, c in self.group_choices.items() if g != group}, choices=choices)

    def release_duplicate(self, number, enabled=True, *, version=None):
        self._guard(version)
        releases = set(self.released_duplicates)
        releases.add(number) if enabled else releases.discard(number)
        rows, pairs = dict(self.row_actions), dict(self.pair_actions)
        if not enabled:
            rows.pop(number, None)
            pairs = {k: a for k, a in pairs.items() if number not in k}
        self._change(rows, pairs, releases)

    def request_preview(self, *, number=None, action="入账", key=None, version=None):
        self._guard(version)
        rows, pairs = dict(self.row_actions), dict(self.pair_actions)
        if key is None:
            rows[number] = action
        else:
            pairs[tuple(key)] = action
        self._preview_candidate(rows, pairs)

    def request_group_preview(self, group, *, version=None):
        self._guard(version)
        rows, pairs, _choices, _action = self._group_candidate(group)
        self._preview_candidate(rows, pairs)

    def _preview_candidate(self, rows, pairs):
        base, releases = self.baseline, self.released_duplicates
        rows, pairs = frozen_map(rows), frozen_map(pairs)
        self.preview, self.preview_error = None, ""
        self._submit("preview", lambda: apply_decisions(apply_decisions(base,
            release_duplicates=releases), row_actions=rows, pair_actions=pairs))

    def set_view(self, view):
        self._main()
        if view not in ("将导入", "规则跳过", "待确认", "非交易行"):
            raise ValueError("未知视图")
        self.current_view, self.located_row = view, None
        self.revision += 1

    def set_search(self, text):
        self._main()
        self.search_text, self.located_row = text.strip(), None
        self.revision += 1

    def set_only_unhandled(self, enabled):
        self._main()
        self.only_unhandled, self.located_row = bool(enabled), None
        self.revision += 1

    @staticmethod
    def display_values(row):
        # 精度异常必须保留原有小数，不得在预览中先舍入后掩盖异常。
        money = format(row.amount, "+f" if row.amount.as_tuple().exponent < -2 else "+.2f") if row.amount is not None else "—"
        return (str(row.row_number), row.record_date or "—",
                money, row.note,
                " · ".join((row.reason, *row.warnings)))

    def visible_rows(self, *, historical=False, summary=True):
        source = self.obsolete if historical else None
        result = source.result if source else self.result
        if result is None:
            return ()
        groups = source.groups if source else self.groups
        origins = {n for ids in groups.values() for n in ids}
        view = self.current_view
        rows = [r for r in result.rows if (r.row_number in origins if view == "待确认" else r.category == view)]
        if view == "待确认" and self.only_unhandled and not historical:
            rows = [r for r in rows if r.decision is None]
        ranks = {g: i for i, g in enumerate(("规则冲突", "疑似退款对", "已识别退款", "未知状态"))}
        if view == "将导入":
            rows.sort(key=lambda r: (r.record_date or "", r.row_number))
        elif view == "待确认":
            rows.sort(key=lambda r: (ranks.get(r.group, 9), min(r.pair_key or (r.row_number,)), r.row_number))
        elif view == "规则跳过":
            rows.sort(key=lambda r: (0 if r.reason == "中性交易" else 2 if r.reason == "重复"
                else 3 if r.reason == "用户选择跳过" else 1, r.row_number))
        else:
            rows.sort(key=lambda r: r.row_number)
        if self.search_text:
            rows = [r for r in rows if any(self.search_text in value for value in self.display_values(r))]
        elif summary and view in ("规则跳过", "非交易行") and view not in self.show_all:
            rows = rows[:5]
        return tuple(rows)

    def next_unhandled(self):
        self._guard()
        numbers = [r.row_number for r in self.visible_rows(summary=False) if r.decision is None
                   and r.category == "待确认"]
        if not numbers:
            return None
        index = numbers.index(self.located_row) + 1 if self.located_row in numbers else 0
        if self.located_row is not None and self.located_row not in numbers:
            # 已处理行退出筛选后，从原排序位置继续，而非每次抢回第一笔。
            ranks = {g: i for i, g in enumerate(("规则冲突", "疑似退款对", "已识别退款", "未知状态"))}
            def order(row):
                return ranks.get(row.group, 9), min(row.pair_key or (row.row_number,)), row.row_number
            origin = next((r for r in self.result.rows if r.row_number == self.located_row), None)
            following = [r.row_number for r in self.visible_rows(summary=False)
                         if r.row_number in numbers and origin and order(r) > order(origin)]
            index = numbers.index(following[0]) if following else 0
        self.located_row = numbers[index % len(numbers)]
        return self.located_row

    def headers(self):
        current = self.mapping
        header = next((r for r in self.bill.rows if current and r.row_number == current.header_row), None)
        width = max((len(r.cells) for r in self.bill.rows), default=0)
        return tuple(str(header.cells[i].value or "") if header and i < len(header.cells)
                     else "" for i in range(width))

    def mapping_candidates(self, path=None):
        from import_prefs import find_mapping_candidates
        return find_mapping_candidates(self.headers(), **({"path": path} if path else {}))

    def save_mapping(self, name, *, overwrite=False, path=None):
        from import_prefs import save_mapping
        if self.mapping is None:
            raise ValueError("尚无可保存映射")
        return save_mapping(self.headers(), dict(self.mapping.columns), name, overwrite=overwrite,
                            **({"path": path} if path else {}))

    def apply_saved_mapping(self, saved):
        from import_prefs import apply_saved_mapping
        sources = apply_saved_mapping(saved, self.headers())
        self.update_mapping(FieldMapping(self.mapping.header_row if self.mapping else None, sources))

    def delete_mapping(self, fingerprint, path=None):
        from import_prefs import delete_mapping
        return delete_mapping(fingerprint, **({"path": path} if path else {}))

    def close(self):
        self._main()
        if self.closed:
            return
        self.closed, self.busy = True, False
        self.version += 1
        if self.task_handle:
            self.task_handle.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self.result = self.baseline = self.obsolete = None
