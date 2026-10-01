"""Pure monthly record aggregation shared by statistics and charts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from store import Account

MULTI_TAG_TOTALS_NOTE = (
    "一条记录有多个标签时会分别计入各项，故各项之和可能大于当期总额；"
    "未分类表示没有打标签的记录。"
)


@dataclass(frozen=True)
class TagTotals:
    income: Decimal
    expense: Decimal


@dataclass(frozen=True)
class RecordTotals:
    total_income: Decimal
    total_expense: Decimal
    by_tag: dict[str, TagTotals]


def aggregate_records_by_tag(records: Iterable[Account]) -> RecordTotals:
    """Count each record once in totals and once per tag in the breakdown."""
    total_income = Decimal("0")
    total_expense = Decimal("0")
    # 桶内保存正数金额；图表需要向下的支出柱时再由展示层转成负数。
    buckets: dict[str, list[Decimal]] = {}

    for record in records:
        amount = record.amount
        # 历史脏数据可能含 NaN / Infinity，不能参与比较或污染任何汇总。
        if not amount.is_finite():
            continue

        if amount > 0:
            total_income += amount
        elif amount < 0:
            total_expense -= amount

        # 无标签记录只能进入虚拟桶；多标签记录则对每个标签分别计入整笔金额。
        tags = record.tags if record.tags else ("未分类",)
        for tag in dict.fromkeys(tags):
            bucket = buckets.setdefault(tag, [Decimal("0"), Decimal("0")])
            if amount > 0:
                bucket[0] += amount
            elif amount < 0:
                bucket[1] -= amount

    return RecordTotals(
        total_income=total_income,
        total_expense=total_expense,
        by_tag={
            tag: TagTotals(income=amounts[0], expense=amounts[1])
            for tag, amounts in buckets.items()
        },
    )