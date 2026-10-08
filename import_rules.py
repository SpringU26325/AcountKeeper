"""外部账单纯规则：不读配置、不访问账本、不操作 UI；词表加载为独立边界。"""

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping
import unicodedata

from import_reader import RawCell, RawRow

_ROLES = ("date", "amount", "direction", "status", "trade_type", "counterparty",
          "product", "original_note", "payment", "order_id", "secondary_order_id", "category")
_MULTI = {"counterparty", "product", "original_note", "payment"}
_CATEGORIES = ("将导入", "规则跳过", "待确认", "非交易行")


@dataclass(frozen=True)
class FieldTerm:
    value: str
    roles: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class StatusRule:
    rule_id: str
    value_set: tuple[str, ...]
    field_scope: str | None
    action: str
    reason_category: str
    source: str
    audited: bool = False


@dataclass(frozen=True)
class EmptyMarker:
    value: str
    field_scope: str | None
    source: str


@dataclass(frozen=True)
class Vocabulary:
    fields: tuple[FieldTerm, ...]
    status_rules: tuple[StatusRule, ...]
    empty_markers: tuple[EmptyMarker, ...]


@dataclass(frozen=True)
class FieldMapping:
    header_row: int | None
    columns: Mapping[str, tuple[int, ...]]
    review_roles: tuple[str, ...] = ()

    def __post_init__(self):
        # 拷贝后冻结，调用方修改下拉数据不能暗中改变已产生的分析结果。
        object.__setattr__(self, "columns", MappingProxyType(
            {role: tuple(indices) for role, indices in self.columns.items()}))


@dataclass(frozen=True)
class NoteOptions:
    include_counterparty: bool = True
    include_product: bool = True
    include_original_note: bool = True
    include_time: bool = False
    include_payment: bool = False


@dataclass(frozen=True)
class RowResult:
    row_number: int
    raw_values: tuple[object, ...]
    category: str
    reason: str
    group: str = ""
    record_date: str | None = None
    original_amount: Decimal | None = None
    amount: Decimal | None = None
    note: str = ""
    can_import: bool = False
    warnings: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    rule_ids: tuple[str, ...] = ()
    duplicate_of: int | None = None
    duplicate_released: bool = False
    pair_key: tuple[int, int] | None = None
    decision: str | None = None
    _semantic: tuple[str, str, str] = field(default=("非交易行", "", ""), repr=False)
    _facts: tuple[str, str, str, str] = field(default=("", "", "", ""), repr=False)
    _note_parts: tuple[str, ...] = field(default=(), repr=False)
    _time: str = field(default="", repr=False)
    _refund: bool = field(default=False, repr=False)
    _ambiguous_pair: bool = field(default=False, repr=False)


@dataclass(frozen=True)
class AnalysisResult:
    mapping: FieldMapping
    rows: tuple[RowResult, ...]
    messages: tuple[str, ...]

    @property
    def counts(self):
        result = dict.fromkeys(_CATEGORIES, 0)
        for row in self.rows:
            result[row.category] += 1
        return result

    @property
    def can_confirm(self):
        return (bool(self.mapping.columns.get("date") and self.mapping.columns.get("amount"))
                and not any(row.category == "待确认" for row in self.rows))

    @property
    def to_import(self):
        return tuple(row for row in self.rows if row.category == "将导入")


def _normal(value):
    return "" if value is None else unicodedata.normalize("NFKC", str(value)).strip()


def _validate_vocabulary(vocab):
    ids = set()
    for term in vocab.fields:
        if not isinstance(term.value, str) or not term.roles or any(role not in _ROLES for role in term.roles):
            raise ValueError("字段词条包含未知角色")
    for entry in (*vocab.fields, *vocab.status_rules, *vocab.empty_markers):
        if entry.source not in ("公开资料", "真实样例"):
            raise ValueError("词条 source 必须为公开资料或真实样例")
    for rule in vocab.status_rules:
        if not isinstance(rule.rule_id, str) or not rule.rule_id or rule.rule_id in ids or not rule.value_set:
            raise ValueError("状态规则编号重复或匹配值为空")
        ids.add(rule.rule_id)
        if rule.field_scope not in (None, "status", "trade_type"):
            raise ValueError("状态规则必须引用已支持的字段角色")
        if rule.action not in ("入账", "跳过", "待确认") or type(rule.audited) is not bool:
            raise ValueError("状态动作或对账标记无效")
        if any(not isinstance(v, str) or not _normal(v) for v in rule.value_set):
            raise ValueError("状态匹配值必须为非空字符串")
        if rule.reason_category not in ("中性交易", "失败关闭", "退款", "未知", "未知（关闭语义歧义）"):
            raise ValueError("状态规则原因类别无效")
        # 来源不等于对账通过；注入的词表也须经过此守卫，不能绕过资产加载。
        if rule.action == "入账" and not (rule.source == "真实样例" and rule.audited):
            raise ValueError("入账规则必须来自已逐行对账的真实样例")
    for marker in vocab.empty_markers:
        if not isinstance(marker.value, str):
            raise ValueError("空值标记必须为字符串")
        if marker.field_scope is not None and marker.field_scope not in _ROLES:
            raise ValueError("空值标记引用未知字段角色")


def load_vocabulary(asset_dir=None):
    directory = Path(asset_dir) if asset_dir is not None else Path(__file__).with_name("import_vocab")
    assets = []
    for name in ("fields", "states", "empty"):
        try:
            data = json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
            if type(data.get("version")) is not int or data["version"] != 1:
                raise ValueError("不支持的词表版本")
            if not isinstance(data.get("entries"), list):
                raise ValueError("词表 entries 必须为列表")
            assets.append(data["entries"])
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
            # 加载失败明确停止，不能以空词表静默继续并把未知交易当作成功。
            raise ValueError(f"无法加载 {name} 词表：{exc}") from exc
    try:
        vocab = Vocabulary(
            tuple(FieldTerm(x["value"], tuple(x["roles"]), x["source"]) for x in assets[0]),
            # 手工维护可遗漏对账标记；仅缺字段默认未对账，显式错误类型仍由守卫拒绝。
            tuple(StatusRule(x["rule_id"], tuple(x["value_set"]), x["field_scope"],
                             x["action"], x["reason_category"], x["source"], x.get("audited", False))
                  for x in assets[1]),
            tuple(EmptyMarker(x["value"], x["field_scope"], x["source"]) for x in assets[2]),
        )
        _validate_vocabulary(vocab)
    except (KeyError, TypeError) as exc:
        raise ValueError("词表结构缺项或类型错误") from exc
    return vocab


class _Cleaner:
    def __init__(self, vocab):
        self.markers = defaultdict(set)
        for marker in vocab.empty_markers:
            self.markers[marker.field_scope].add(_normal(marker.value))

    def text(self, cell, role=None):
        if cell is None or cell.kind in ("f", "e"):
            return ""
        text = _normal(cell.value)
        # 仅整格匹配，不删除备注中的斜线，也不删除负金额的符号。
        return "" if text in self.markers[None] or text in self.markers[role] else text


def _cell(row, index):
    return row.cells[index] if index < len(row.cells) else None


def _date_value(cell, cleaner):
    if cell is None or cell.kind in ("f", "e", "b") or not cleaner.text(cell, "date"):
        return None, ""
    value = cell.value
    if isinstance(value, datetime):
        return value.date().isoformat(), value.strftime("%H:%M")
    if isinstance(value, date):
        return value.isoformat(), ""
    # 只接受年在前的明确日期；纯数字序列不猜 Excel 序号或月日顺序。
    match = re.fullmatch(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?(?:[ T](.*))?", cleaner.text(cell, "date"))
    if not match:
        return None, ""
    try:
        day = date(*(int(match[i]) for i in (1, 2, 3))).isoformat()
    except ValueError:
        return None, ""
    time = ""
    if match[4]:
        try:
            time = datetime.fromisoformat(day + "T" + match[4]).strftime("%H:%M")
        except ValueError:
            # 时间异常不改变合法本地日期，只是不生成可选时间前缀。
            pass
    return day, time


def _amount_value(cell, cleaner):
    if cell is None or cell.kind in ("f", "e", "b") or isinstance(cell.value, bool):
        return None
    if isinstance(cell.value, (int, float, Decimal)):
        # 原生数值可能用科学计数表示；经字符串转 Decimal 避免引入二进制浮点尾差。
        amount = Decimal(str(cell.value))
        return amount if amount.is_finite() else None
    text = cleaner.text(cell, "amount")
    text = re.sub(r"^[¥￥]\s*", "", text)
    text = re.sub(r"\s*元$", "", text)
    # 千分位须完整合法，不把 1,23 的错误金额清洗成 123。
    if not re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", text):
        return None
    try:
        amount = Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None
    return amount if amount.is_finite() else None


def _column_values(rows, index, cleaner, role):
    return [cleaner.text(_cell(row, index), role) for row in rows
            if cleaner.text(_cell(row, index), role)]


def _valid_amount(amount):
    # 逐行写入守卫和整列方向证据共用校验，零值／精度异常不能贡献有效交易符号。
    return amount is not None and amount != 0 and amount.as_tuple().exponent >= -2


def _infer_mapping(rows, vocab, cleaner):
    aliases = defaultdict(set)
    for term in vocab.fields:
        aliases[_normal(term.value)].update(term.roles)
    header = None
    best = (0, 0)
    for row in rows:
        roles = set().union(*(aliases[_normal(c.value)] for c in row.cells)) if row.cells else set()
        rank = (int("date" in roles) + int("amount" in roles), len(roles))
        if len(roles) >= 2 and rank > best:
            best, header = rank, row
    data = tuple(row for row in rows if header is None or row.row_number > header.row_number)
    width = max((len(row.cells) for row in rows), default=0)
    headers = tuple(_normal(c.value) for c in header.cells) if header else ()
    candidates = defaultdict(list)
    for role in _ROLES:
        for index in range(width):
            values = _column_values(data, index, cleaner, role)
            explicit = role in aliases[headers[index]] if index < len(headers) else False
            cells = [_cell(row, index) for row in data if cleaner.text(_cell(row, index), role)]
            content = typed = relation = 0.0
            if role == "date":
                content = sum(_date_value(c, cleaner)[0] is not None for c in cells) / max(1, len(cells))
                typed = sum(c.kind == "d" or bool(re.search(r"[yd]", c.number_format.lower())) for c in cells) / max(1, len(cells))
            elif role == "amount":
                content = sum(_amount_value(c, cleaner) is not None for c in cells) / max(1, len(cells))
                typed = sum(c.kind == "n" for c in cells) / max(1, len(cells))
                directions = [i for i, h in enumerate(headers) if "direction" in aliases[h]]
                related = [(r, _amount_value(_cell(r, index), cleaner)) for r in data]
                comparable = [(r, a) for r, a in related if a is not None and a != 0
                              and any(cleaner.text(_cell(r, i), "direction") in ("收入", "支出") for i in directions)]
                # 关系证据核对金额符号与另一列的收支语义，不把负数本身当字段关系。
                relation = sum(any(cleaner.text(_cell(r, i), "direction") == ("收入" if a > 0 else "支出")
                                   for i in directions) for r, a in comparable) / max(1, len(comparable))
            elif role == "direction":
                content = sum(v in ("支出", "收入", "不计收支") for v in values) / max(1, len(values))
            elif role in ("status", "trade_type"):
                known = {v for r in vocab.status_rules if r.field_scope in (role, None) for v in r.value_set}
                content = sum(v in known for v in values) / max(1, len(values))
            # 表头权重最高，其后是数据吻合、类型格式及关系；无表头时只推断强类型角色。
            if explicit or role in ("date", "amount", "direction") and content == 1.0 and cells:
                evidence = (int(explicit), content, typed, relation)
                level = "低" if cells and role in ("date", "amount", "direction") and content < 0.5 else "高" if explicit and content == 1.0 else "中"
                candidates[role].append((evidence, index, level))
    columns, reviews, used = {}, [], set()
    for role in _ROLES:
        choices = sorted(candidates[role], key=lambda x: (x[0], -x[1]), reverse=True)
        available = [c for c in choices if c[1] not in used or role in ("trade_type", "category")]
        if not available:
            if role in ("date", "amount"):
                reviews.append(role)
            continue
        chosen = [c for c in available if c[0][0]] if role in _MULTI else available[:1]
        chosen = chosen or available[:1]
        columns[role] = tuple(sorted(c[1] for c in chosen))
        used.update(columns[role])
        # 竞争相当或缺少内容佐证时只输出高亮角色，不暴露内部评分和候选集合。
        if chosen[0][2] != "高" or len(available) > 1 and available[0][0] == available[1][0]:
            reviews.append(role)
    classification = set(columns.get("trade_type", ())) | set(columns.get("category", ()))
    if len(classification) == 1:
        index = next(iter(classification))
        values = _column_values(data, index, cleaner, "trade_type")
        known = {_normal(v) for r in vocab.status_rules for v in r.value_set}
        # 单列兼任时以值域判断；无法判断留给交易类型，不从平台或文件名猜语义。
        target = "trade_type" if not values or sum(v in known for v in values) >= len(values) / 2 else "category"
        columns.pop("trade_type", None)
        columns.pop("category", None)
        columns[target] = (index,)
    return FieldMapping(header.row_number if header else None, columns, tuple(dict.fromkeys(reviews)))


def _validate_mapping(mapping, rows):
    numbers = [row.row_number for row in rows]
    if any(type(n) is not int or n < 1 for n in numbers) or numbers != sorted(set(numbers)):
        raise ValueError("原始行号必须唯一且递增")
    if mapping.header_row is not None and mapping.header_row not in numbers:
        raise ValueError("映射表头行不存在")
    width = max((len(row.cells) for row in rows), default=0)
    for role, indices in mapping.columns.items():
        if role not in _ROLES or len(indices) != len(set(indices)):
            raise ValueError("未知角色或重复列下标")
        if role not in _MULTI and len(indices) > 1:
            raise ValueError("单值角色不能绑定多列")
        if any(type(i) is not int or i < 0 or i >= width for i in indices):
            raise ValueError("映射列下标越界")


def _note(row, refund=False):
    return " ".join(p for p in (row._time, "[退款]" if refund else "", *row._note_parts) if p)


def _row_analysis(raw, mapping, vocab, cleaner, mixed_signs, options):
    def cells(role):
        return tuple(_cell(raw, i) for i in mapping.columns.get(role, ()))

    def value(role):
        return " ".join(filter(None, (cleaner.text(c, role) for c in cells(role))))

    day, time = _date_value(next(iter(cells("date")), None), cleaner)
    original = _amount_value(next(iter(cells("amount")), None), cleaner)
    note_parts = tuple(value(role) for role, enabled in (
        ("counterparty", options.include_counterparty), ("product", options.include_product),
        ("original_note", options.include_original_note), ("payment", options.include_payment)) if enabled)
    row = RowResult(raw.row_number, raw.values, "非交易行", "日期或金额无有效值",
                    record_date=day, original_amount=original, _note_parts=note_parts,
                    _time=f"[{time}]" if options.include_time and time else "",
                    _facts=(value("counterparty"), value("product"), value("status"), value("order_id")))
    row = replace(row, note=_note(row))
    if not mapping.columns.get("date") or not mapping.columns.get("amount"):
        return replace(row, category="待确认", reason="缺少必需字段映射", group="规则冲突",
                       _semantic=("待确认", "缺少必需字段映射", "规则冲突"))
    if day is None or original is None:
        return row
    direction = value("direction")
    # copy_abs / copy_negate 不受 Decimal 上下文精度限制，符号转换绝不偷偷舍入。
    amount = original.copy_abs().copy_negate() if direction == "支出" else original.copy_abs() if direction == "收入" else original if mixed_signs else None
    warnings = ("收支方向优先：原金额负号与收入冲突",) if direction == "收入" and original < 0 else ()
    valid_money = _valid_amount(original)
    # 未命中的收支值仍需确认；混合原始符号已确定方向时允许明确确认，不冒充未解决。
    can_import = valid_money and amount is not None and direction != "不计收支"
    row = replace(row, amount=amount, can_import=can_import, diagnostics=warnings)
    matched = []
    for rule in vocab.status_rules:
        scoped = cells(rule.field_scope) if rule.field_scope else raw.cells
        if any(cleaner.text(c, rule.field_scope) in {_normal(v) for v in rule.value_set} for c in scoped):
            matched.append(rule)
    meanings = {(r.action, r.reason_category) for r in matched}
    rule_ids = tuple(r.rule_id for r in matched)
    refund = any(r.reason_category == "退款" for r in matched)
    category, reason, group = "将导入", "有效交易", ""
    if len(meanings) > 1:
        category, reason, group = "待确认", "状态规则冲突", "规则冲突"
    elif direction == "不计收支":
        category, reason = "规则跳过", "中性交易"
    elif meanings:
        action, reason = next(iter(meanings))
        category = {"入账": "将导入", "跳过": "规则跳过", "待确认": "待确认"}[action]
        group = ("已识别退款" if reason == "退款" else "未知状态") if category == "待确认" else ""
    elif mapping.columns.get("status"):
        category, reason, group = "待确认", "未知或缺失状态", "未知状态"
    # 校验始终约束写入；语义跳过无需补方向，但异常金额不能通过决定入账。
    if not valid_money:
        category, reason, group = "待确认", "金额为零或超过两位小数", "规则冲突"
    elif category != "规则跳过" and (amount is None or direction not in ("", "收入", "支出")):
        reason = "金额方向未解决" if amount is None else "未知收支值待确认"
        category, group = "待确认", "规则冲突"
    return replace(row, category=category, reason=reason, group=group, rule_ids=rule_ids,
                   _semantic=(category, reason, group), _refund=refund)


def _mark_relations(rows):
    complete, missing_id, orders, pairs = {}, {}, defaultdict(list), defaultdict(list)
    result = list(rows)
    for index, row in enumerate(rows):
        if row.record_date is None or row.amount is None or not row.can_import:
            continue
        party, product, status, order_id = row._facts
        five = (row.record_date, row.amount, party, product, status)
        if order_id:
            key = (*five, order_id)
            if key in complete:
                result[index] = replace(row, duplicate_of=rows[complete[key]].row_number)
            else:
                complete[key] = index
            orders[order_id].append(index)
        elif five in missing_id:
            result[index] = replace(row, warnings=(*row.warnings, "疑似重复，但无单号佐证：请核对"))
        else:
            missing_id[five] = index
        if row.category in ("将导入", "待确认"):
            pairs[(row.record_date, row.amount.copy_abs(), party, product)].append(index)
    for indices in orders.values():
        variants = {(rows[i].record_date, rows[i].amount, *rows[i]._facts[:3]) for i in indices}
        if len(variants) > 1:
            for i in indices:
                result[i] = replace(result[i], warnings=(*result[i].warnings, "单号相同，字段有差异：请核对"))
    for indices in pairs.values():
        negative = [i for i in indices if rows[i].amount < 0]
        positive = [i for i in indices if rows[i].amount > 0]
        if not negative or not positive:
            continue
        unique = len(negative) == len(positive) == 1
        conflict = any(rows[i].group == "规则冲突" for i in indices)
        pair_key = tuple(sorted(rows[i].row_number for i in indices)) if unique and not conflict else None
        for i in indices:
            row = result[i]
            # 冲突优先，不提供能跨过冲突的组合；歧义配对仍保留待确认。
            if row.group == "规则冲突":
                continue
            reason = "疑似退款对" if unique and not conflict else "疑似退款对：配对不明确"
            result[i] = replace(row, category="待确认", reason=reason, group="疑似退款对",
                                pair_key=pair_key, _ambiguous_pair=pair_key is None,
                                _semantic=("待确认", reason, "疑似退款对"))
    for i, row in enumerate(result):
        # 最终重复跳过覆盖退款／未知语义；底层语义保留，解除后恢复而非强制入账。
        if row.duplicate_of is not None:
            result[i] = replace(row, category="规则跳过", reason="重复", group="")
    return tuple(result)


def analyze_bill(rows, vocab, mapping=None, *, options=None):
    _validate_vocabulary(vocab)
    rows = tuple(rows)
    cleaner = _Cleaner(vocab)
    mapping = mapping if mapping is not None else _infer_mapping(rows, vocab, cleaner)
    _validate_mapping(mapping, rows)
    options = options or NoteOptions()
    header = next((r for r in rows if r.row_number == mapping.header_row), None)
    signature = tuple(_normal(c.value) for c in header.cells) if header else ()
    signs = set()
    date_index = next(iter(mapping.columns.get("date", ())), None)
    amount_index = next(iter(mapping.columns.get("amount", ())), None)
    for row in rows:
        if mapping.header_row is not None and row.row_number <= mapping.header_row:
            continue
        day = _date_value(_cell(row, date_index), cleaner)[0] if date_index is not None else None
        amount = _amount_value(_cell(row, amount_index), cleaner) if amount_index is not None else None
        # 先验证日期和金额，避免残缺行的反向金额让同号交易列被误判为混合符号。
        if day is not None and _valid_amount(amount):
            signs.add(amount > 0)
    results = []
    for row in rows:
        text = tuple(_normal(c.value) for c in row.cells)
        # 表头按完整结构判断，合法交易备注出现“合计”不会被误删。
        leading = mapping.header_row is not None and row.row_number <= mapping.header_row
        if leading or not any(text) or signature and text == signature:
            reason = "表头或前导说明" if leading else "重复表头" if signature and text == signature else "空行"
            results.append(RowResult(row.row_number, row.values, "非交易行", reason))
        else:
            results.append(_row_analysis(row, mapping, vocab, cleaner, len(signs) == 2, options))
    messages = ("本次未识别到状态列",) if not mapping.columns.get("status") else ()
    return AnalysisResult(mapping, _mark_relations(results), messages)


def _decide(row, action, *, refund=False):
    if action not in ("入账", "跳过"):
        raise ValueError("单行决定只接受入账或跳过")
    if action == "入账" and not row.can_import:
        raise ValueError("必要字段、金额精度或方向未解决，不能入账")
    if action == "跳过":
        return replace(row, category="规则跳过", reason="用户选择跳过", decision=action)
    amount = row.original_amount.copy_abs() if refund else row.amount
    return replace(row, category="将导入", reason="用户确认入账", amount=amount,
                   note=_note(row, refund), decision=action)


def apply_decisions(result, *, row_actions=None, pair_actions=None, release_duplicates=()):
    rows = {r.row_number: r for r in result.rows}
    pairs = defaultdict(list)
    for row in result.rows:
        if row.pair_key is not None:
            pairs[row.pair_key].append(row.row_number)
    for number in release_duplicates:
        row = rows.get(number)
        if row is None or row.duplicate_of is None:
            raise ValueError("解除重复的目标不是完全重复行")
        if row.duplicate_released:
            continue
        category, reason, group = row._semantic
        # 解除只执行一次；后续改动决定不能因重复发送解除请求而留下旧决定标记。
        rows[number] = replace(row, category=category, reason=reason, group=group, duplicate_released=True)
    for key, action in (pair_actions or {}).items():
        paired = [rows[n] for n in pairs.get(key, ())]
        if len(paired) != 2 or any(r.category == "规则跳过" and r.decision is None for r in paired):
            raise ValueError("退款配对不存在、尚有重复跳过或配对不明确")
        if action not in ("两笔都跳过", "只记支出", "两笔都记", "只记退款"):
            raise ValueError("未知退款对组合")
        for row in paired:
            incoming = row.amount > 0
            include = action == "两笔都记" or action == ("只记退款" if incoming else "只记支出")
            rows[row.row_number] = _decide(row, "入账" if include else "跳过", refund=incoming)
    for number, action in (row_actions or {}).items():
        row = rows.get(number)
        if row is None or row.pair_key is not None:
            raise ValueError("行不存在，或退款对必须按对决定")
        if row.category != "待确认" and row.decision is None:
            raise ValueError("该行不接受待确认决定；完全重复须先解除")
        # 不明确配对不提供四组合；单行跳过可消除阻塞，但不能自作主张分配退款额。
        if row._ambiguous_pair and action == "入账":
            raise ValueError("退款配对不明确，不能推断入账组合")
        rows[number] = _decide(row, action, refund=row._refund)
    return replace(result, rows=tuple(rows[r.row_number] for r in result.rows))
