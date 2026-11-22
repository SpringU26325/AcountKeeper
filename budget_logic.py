"""预算额度和月度统计，只处理调用方提供的数据。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import IntEnum
from typing import TYPE_CHECKING

from tag_aggregation import StatsRecord, aggregate_records_by_tag, money_precision, record_tag_keys, valid_record_date

if TYPE_CHECKING:
    from store import Account


def budget_month_start(month: str) -> date:
    # 要求标准年月，避免同一自然月用不同的键分别保存额度。
    first = date.fromisoformat(f"{month}-01")
    if first.isoformat()[:7] != month:
        raise ValueError("预算月份必须为 YYYY-MM。")
    return first


def normalize_budget_tag(tag: str | None) -> str | None:
    if tag is None:
        return None  # None 只代表总预算，空标签不能当成另一份总预算。
    if not isinstance(tag, str) or not tag.strip():
        raise ValueError("预算标签不能为空。")
    return tag.strip()


@dataclass(frozen=True)
class BudgetLimit:
    month: str
    amount: Decimal
    tag: str | None = None

    def __post_init__(self) -> None:
        budget_month_start(self.month)
        if not isinstance(self.amount, Decimal) or not self.amount.is_finite() or self.amount <= 0:
            raise ValueError("预算额度必须为有限的正数。")
        with money_precision():
            try:
                amount = self.amount.quantize(Decimal("0.01"))
            except InvalidOperation as error:
                # 拒绝无法表示成分的输入，避免把错误额度格式化后保存。
                raise ValueError("预算额度超出可用范围。") from error
        if amount != self.amount:
            raise ValueError("预算额度最多支持两位小数。")
        object.__setattr__(self, "amount", amount)
        object.__setattr__(self, "tag", normalize_budget_tag(self.tag))


class BudgetState(IntEnum):
    AVAILABLE = 0
    NEAR_LIMIT = 1
    EXHAUSTED = 2
    OVER_LIMIT = 3


@dataclass(frozen=True)
class BudgetProgress:
    limit: BudgetLimit
    used: Decimal
    remaining: Decimal
    overspent: Decimal
    percent: Decimal
    state: BudgetState
    rows: tuple[StatsRecord, ...]


@dataclass(frozen=True)
class BudgetSnapshot:
    month: str
    progress: tuple[BudgetProgress, ...]
    excluded_amount: Decimal
    excluded_rows: tuple[StatsRecord, ...]
    skipped_count: int


def _progress(limit: BudgetLimit, used: Decimal, rows: tuple[StatsRecord, ...]) -> BudgetProgress:
    balance = limit.amount - used
    # 直接比较金额来判档，显示比例的舍入不能提前触发提醒。
    if balance < 0:
        state = BudgetState.OVER_LIMIT
    elif balance == 0:
        state = BudgetState.EXHAUSTED
    elif used * 5 >= limit.amount * 4:
        state = BudgetState.NEAR_LIMIT
    else:
        state = BudgetState.AVAILABLE
    zero = Decimal("0.00")
    return BudgetProgress(limit, used, max(zero, balance), max(zero, -balance),
                          used * 100 / limit.amount, state, rows)


def build_budget_snapshot(
    records: Iterable[Account], limits: Iterable[BudgetLimit], month: str,
    *, load_skipped_count: int = 0,
) -> BudgetSnapshot:
    """从一个自然月的已入账支出计算用量及明细。"""
    budget_month_start(month)
    configured = {limit.tag: limit for limit in limits if limit.month == month}
    buckets: dict[str | None, list[StatsRecord]] = {tag: [] for tag in configured}
    included, excluded = [], []
    skipped = load_skipped_count  # 加载层已排除的账目不在 records 中，先计入这部分。
    for record in records:
        if valid_record_date(record) is None:
            skipped += 1  # 和财务分析一样统计整个输入快照的异常，让预算页能提示。
            continue
        day, amount = record.record_date, record.amount
        if day[:7] != month or amount >= 0:
            continue  # 预算只累计所选月支出，收入和退款收入不抵扣。
        row = StatsRecord(record.record_id, day, amount, tuple(record.tags), record.note)
        if record.budget_excluded:
            excluded.append(row)
            continue
        included.append(row)
        # 明细用同一个标签分组规则，确保多标签、重复标签和「未分类」都与合计对得上。
        for tag in dict.fromkeys((None, *record_tag_keys(row))):
            if tag in buckets:
                buckets[tag].append(row)
    totals = aggregate_records_by_tag(included)
    used_by_tag: dict[str | None, Decimal] = {tag: total.expense for tag, total in totals.by_tag.items()}
    used_by_tag[None] = totals.total_expense
    with money_precision():
        result = tuple(_progress(limit, used_by_tag.get(tag, Decimal("0")), tuple(buckets[tag]))
                       for tag, limit in configured.items())
    excluded_amount = aggregate_records_by_tag(excluded).total_expense
    return BudgetSnapshot(month, result, excluded_amount, tuple(excluded), skipped)
