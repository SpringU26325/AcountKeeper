"""导入预览的数据会话；不持有 Tk，不写账本，规则计算只走公开接口。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from queue import Empty, Queue
from threading import get_ident
from types import MappingProxyType

from import_reader import BillFile, RawCell, RawRow
from import_rules import AnalysisResult, FieldMapping, NoteOptions, analyze_bill, apply_decisions
from merchant_classifier import MerchantSuggestion, classify_merchant
from store import ImportMerchant, ImportRecord, MerchantMapping, MerchantMappingChange
from tag_prefs import build_tag_candidates


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
    result: AnalysisResult
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


@dataclass(frozen=True)
class ImportSubmission:
    records: tuple[ImportRecord, ...]
    expectations: tuple[MerchantMapping, ...]
    changes: tuple[MerchantMappingChange, ...]
    replacements: tuple[tuple[MerchantMapping, MerchantMappingChange], ...]


class ImportSession:
    def __init__(self, bill: BillFile, vocabulary, mapping=None, *, options=None, merchant_mappings=()):
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
        self.merchant_tags = tuple(build_tag_candidates())
        self._tag_candidates = frozenset(self.merchant_tags)
        self._merchants: dict[int, tuple[MerchantSuggestion, str, tuple[str, ...]]] = {}
        self._merchant_groups: dict[tuple[str, str], list[int]] = {}
        self._tag_choices: dict[int, str] = {}
        self._remember: set[int] = set()
        self._mapping_snapshot = self._index_merchant_mappings(merchant_mappings)
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
        self._submit("analysis", lambda: self._with_merchants(
            analyze_bill(rows, vocab, mapping, options=options)))
        self.revision += 1

    def update_mapping(self, mapping):
        self._main()
        if self.mapping is None or (mapping.header_row, mapping.columns) != (
                self.mapping.header_row, self.mapping.columns):
            self._tag_choices.clear()  # 同一行号在另一份字段映射下可能表示不同交易。
            self._remember.clear()
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
                self.baseline = self.result = self._adopt_merchants(value)
                self.mapping, self.obsolete = self.result.mapping, None
                self._index(self.result)
                if self._initial_view:
                    self.current_view = "待确认" if self.result.counts["待确认"] else "将导入"
                    self._initial_view = False
            elif kind == "decision":
                semantic, classified = value
                self.result = self._adopt_merchants(classified)
                (self.row_actions, self.pair_actions, self.released_duplicates,
                 self.group_choices, self.pair_choices) = metadata
                self._index(semantic)
            elif kind == "merchants":
                self.result = self._adopt_merchants(value)
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

    @staticmethod
    def _index_merchant_mappings(mappings):
        return frozen_map({(item.merchant.key, item.direction): item for item in mappings})

    def refresh_merchant_context(self, mappings, tags):
        self._guard()
        latest = self._index_merchant_mappings(mappings)
        available = frozenset(tags)
        # 标签仍在池中就保留本笔选择；只有其对应映射变化时才撤销学习同意。
        self._tag_choices = {n: tag for n, tag in self._tag_choices.items() if not tag or tag in available}
        for number in tuple(self._remember):
            suggestion, direction, _ = self._merchants[number]
            key = suggestion.key, direction
            if number not in self._tag_choices or latest.get(key) != self._mapping_snapshot.get(key):
                self._remember.remove(number)
        self.merchant_tags, self._tag_candidates = tuple(tags), available
        self._mapping_snapshot = latest
        result = self.result
        self._submit("merchants", lambda: self._with_merchants(result))

    def _with_merchants(self, result):
        merchants, groups, cache = {}, {}, {}
        available = self._tag_candidates
        learned = {key: item.tag for key, item in self._mapping_snapshot.items()}
        # 分类消费最终可入账结果，退款和重复项的试算不会提前产生标签草稿。
        for row in result.to_import:
            assert row.amount is not None  # 交易规则已排除缺少最终金额或方向的行。
            direction = "收入" if row.amount > 0 else "支出"
            source = (row.merchant_sources, direction)
            suggestion = cache.get(source)
            if suggestion is None:
                suggestion = cache[source] = classify_merchant(*source, available, learned=learned)
            merchants[row.row_number] = (suggestion, direction, row.merchant_sources)
            if suggestion.key:
                groups.setdefault((suggestion.key, direction), []).append(row.row_number)
        return result, merchants, groups

    def _adopt_merchants(self, classified):
        result, merchants, groups = classified
        # 只有行仍可入账且来源和方向未变时，才能保留手选和主动清空。
        self._tag_choices = {number: tag for number, tag in self._tag_choices.items()
                             if number in merchants and merchants[number][1:] == self._merchants[number][1:]}
        self._remember.intersection_update(self._tag_choices)
        self._merchants, self._merchant_groups = merchants, groups
        return result

    def merchant_preview(self, number):
        if self.result is None or number not in self._merchants:
            return None
        suggestion, direction, _ = self._merchants[number]
        return suggestion, direction, self._tag_choices.get(number, suggestion.prefill), number in self._tag_choices

    def merchant_scope(self, number):
        preview = self.merchant_preview(number)
        if preview is None or not preview[0].key:
            return 0, 0
        members = self._merchant_groups[(preview[0].key, preview[1])]
        return len(members), sum(member in self._tag_choices for member in members)

    def export_import_submission(self, result) -> ImportSubmission:
        if result is not self.export_result():
            raise ValueError("预览已变化，请重新核对。")
        records, expectations, requests, changes, replacements = [], {}, {}, {}, []
        for row in result.to_import:
            assert row.amount is not None and row.record_date is not None
            suggestion, direction, sources = self._merchants[row.row_number]
            merchant = ImportMerchant(suggestion.key, sources[0].strip()) if suggestion.key else None
            key = suggestion.key, direction
            prior = self._mapping_snapshot.get(key)
            tag = self._tag_choices.get(row.row_number, suggestion.prefill)
            records.append(ImportRecord(row.record_date, row.amount, row.note, (tag,) if tag else (), merchant))
            if prior and suggestion.learned and row.row_number not in self._tag_choices:
                expectations[key] = prior
            if row.row_number not in self._remember or not merchant:
                continue
            if key in requests:
                if requests[key] != tag:
                    raise ValueError(f"「{merchant.name}」的{direction}学习标签有冲突，请统一标签或取消记忆。")
                continue
            requests[key] = tag
            # 同一商户同方向只写一次；原映射等于当前标签时仍核对修订号。
            change = MerchantMappingChange(merchant, direction, tag, prior.revision if prior else None)
            if prior and prior.tag == tag:
                expectations[key] = prior
            else:
                changes[key] = change
                if prior:
                    replacements.append((prior, change))
        return ImportSubmission(tuple(records), tuple(expectations.values()), tuple(changes.values()), tuple(replacements))

    def merchant_remembered(self, number) -> bool:
        return number in self._remember

    def revoke_merchant_memory(self, number, *, version, request) -> bool:
        self._guard(version)
        if request != self.request_id or number not in self._remember:
            return False
        # 取消授权立即影响提交快照，已应用的本笔标签仍然有效。
        self._remember.remove(number)
        self.revision += 1
        return True

    def choose_merchant_tag(self, number, tag, *, version, request, batch=False, remember=False):
        self._guard(version)
        preview = self.merchant_preview(number)
        if request != self.request_id or preview is None:
            raise ValueError("交易结果已变化，请重新选择该笔记录")
        if tag and tag not in self._tag_candidates:
            raise ValueError("请选择常用标签")
        if remember and (not tag or not preview[0].key):
            raise ValueError("记住标签需要可识别商户和非空标签。")
        if batch:
            if not preview[0].key:
                raise ValueError("商户不明确，不能批量应用")
            members = [member for member in self._merchant_groups[(preview[0].key, preview[1])]
                       if member not in self._tag_choices]
            if remember and not members:
                raise ValueError("本组标签均已有人工决定，请从单笔操作记住标签。")
            if remember and number not in members and self._tag_choices[number] != tag:
                raise ValueError("本笔与批量标签不同，请从使用该标签的记录勾选记住。")
            self._tag_choices.update(dict.fromkeys(members, tag))
        else:
            self._tag_choices[number] = tag  # 空串表示用户主动留空，必须覆盖自动预填。
        # 批量授权也归当前勾选的行，用户回到这行就能撤销。
        self._remember.discard(number)
        if remember:
            self._remember.add(number)
        self.revision += 1

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
            result = apply_decisions(semantic, row_actions=row_actions, pair_actions=pair_actions)
            return semantic, self._with_merchants(result)

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
        origins = {n for ids in groups.values() for n in ids}  # type: ignore[reportAttributeAccessIssue] # groups 由 frozen_map 生成，语法上取值为 object。
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
            origin = next((r for r in self.result.rows if r.row_number == self.located_row), None)  # type: ignore[reportOptionalMemberAccess] # 调用前的可见行与待处理计数已保证结果存在。
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
        self._merchants.clear()
        self._merchant_groups.clear()
        self._tag_choices.clear()
        self._remember.clear()
