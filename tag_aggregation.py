"""统计与图表共用的纯聚合：只遍历调用方给定记录，不查库、不修改记录或标签偏好。

月份筛选归调用方；本模块每笔只计一次总额，多标签则给各标签分别计入整笔金额。
支出结果保存正数大小，向下柱形等符号表现交给图表层；全程用 Decimal 避免精度损失。
"""

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
    """单个标签的收入与支出大小，均非负；不代表带符号的账本金额。"""
    income: Decimal
    expense: Decimal


@dataclass(frozen=True)
class RecordTotals:
    """全体总额及逐标签明细；多标签重复计入使明细之和可能超过总额。"""
    total_income: Decimal
    total_expense: Decimal
    by_tag: dict[str, TagTotals]


def aggregate_records_by_tag(records: Iterable[Account]) -> RecordTotals:
    """按完整 tags 聚合当前输入；不按摘要统计，也不在这里筛选日期或排序标签。"""
    total_income = Decimal("0")
    total_expense = Decimal("0")
    # 桶内两项依次为收入、支出大小，结果组装也按此顺序；不能直接累加带符号金额相抵。
    buckets: dict[str, list[Decimal]] = {}

    for record in records:
        amount = record.amount
        # 历史脏数据可能含 NaN / Infinity，不能参与比较或污染任何汇总。
        if not amount.is_finite():
            continue

        # 总额在标签循环外只算一次；把它放进循环会让多标签记录夸大整期收支。
        if amount > 0:
            total_income += amount
        elif amount < 0:
            total_expense -= amount

        # 无标签记录只能进入虚拟桶；多标签记录则对每个标签分别计入整笔金额。
        # 「未分类」仅是本次结果的分组名，不补写 record.tags、record_tags 或 tags.json。
        tags = record.tags if record.tags else ("未分类",)
        # 正常写库已去重，此处再防御调用方构造的重复标签，避免同名桶重复计入一笔。
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
