"""统计与图表共用的纯聚合：只遍历调用方给定记录，不查库、不修改记录或标签偏好。

标签聚合汇总传入记录，月度接口负责日期校验与范围筛选；总额每笔一次，多标签各计整笔。
支出结果保存正数大小，向下柱形等符号表现交给图表层；全程用 Decimal 避免精度损失。
"""

from __future__ import annotations

from dataclasses import dataclass
from calendar import monthrange
from datetime import date
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


def aggregate_records_by_tag(records: Iterable[Account | StatsRecord]) -> RecordTotals:
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


@dataclass(frozen=True)
class StatsRecord:
    """从完整缓存复制的只读字段，工作线程不持有可变 Account 或数据库连接。"""
    record_id: int
    record_date: str
    amount: Decimal
    tags: tuple[str, ...]


@dataclass(frozen=True)
class MonthStats:
    """一个请求的完整统计结果，所有金额保持 Decimal。"""
    totals: RecordTotals
    previous: RecordTotals
    average: Decimal | None
    previous_average: Decimal | None
    daily: tuple[Decimal, ...]
    half_year: tuple[tuple[str, Decimal, Decimal], ...]
    largest: StatsRecord | None
    record_count: int
    skipped_count: int
    future_count: int
    months: tuple[str, ...]


def offset_month(month: str, offset: int) -> str:
    year, number = map(int, month.split("-"))
    year, remainder = divmod(year * 12 + number - 1 + offset, 12)
    return f"{year:04d}-{remainder + 1:02d}"


def average_expense(expense: Decimal, month: str, today: date) -> Decimal | None:
    current = today.strftime("%Y-%m")
    if month > current:
        return None  # 未来月份尚无已过天数，不伪造日均或做除零运算。
    year, number = map(int, month.split("-"))
    days = today.day if month == current else monthrange(year, number)[1]
    return expense / Decimal(days)


def describe_change(current: Decimal | None, previous: Decimal | None) -> tuple[str, int]:
    """返回环比文字与数值方向，颜色由 UI 决定，负结余以基数绝对值作分母。"""
    if current is None or previous is None:
        return "—", 0
    difference = current - previous
    direction = (difference > 0) - (difference < 0)
    if not previous:
        # 零基数不能显示无穷大；当前也为零表示持平，而不是新增。
        return ({1: "↑ 新增", -1: "↓ 减少", 0: "→ 0.00%"}[direction], direction)
    # 负结余的基数取绝对值，改善仍显示上涨，不能因分母为负而颠倒方向。
    percent = abs(difference) / abs(previous) * Decimal("100")
    arrow = {1: "↑", -1: "↓", 0: "→"}[direction]
    return f"{arrow} {percent:.2f}%", direction


def build_month_stats(records: Iterable[StatsRecord], month: str, today: date) -> MonthStats:
    """仅聚合传入快照；不查库、不接触控件，不修改旧数据。"""
    first = date.fromisoformat(month + "-01")
    if first.strftime("%Y-%m") != month:
        raise ValueError("月份必须为 YYYY-MM")
    previous_month = offset_month(month, -1)
    months = tuple(offset_month(month, step) for step in range(-5, 1))
    half = {key: [Decimal("0"), Decimal("0")] for key in months}
    selected, previous = [], []
    available = set()
    skipped = future = 0
    largest = None
    for record in records:
        # 日期必须可解析且规范；脏数据只排除于本次统计，绝不补写原账本。
        try:
            record_day = date.fromisoformat(record.record_date)
        except (ValueError, TypeError):
            skipped += 1
            continue
        if (record_day.isoformat() != record.record_date
                or not isinstance(record.amount, Decimal) or not record.amount.is_finite()):
            skipped += 1
            continue
        key = record.record_date[:7]
        available.add(key)
        if key == month:
            selected.append(record)
            future += record_day > today
            if record.amount < 0 and (largest is None or
                    (record.amount, record.record_date, record.record_id) <
                    (largest.amount, largest.record_date, largest.record_id)):
                largest = record  # 同额时按最早日期、再按ID选定，刷新顺序不影响洞察。
        if key == previous_month:
            previous.append(record)
        if key in half:
            # 每笔只计一次总额；不能在标签循环里累加半年收支。
            if record.amount > 0:
                half[key][0] += record.amount
            elif record.amount < 0:
                half[key][1] -= record.amount
    totals = aggregate_records_by_tag(selected)
    before = aggregate_records_by_tag(previous)
    day_count = monthrange(first.year, first.month)[1]
    if month == today.strftime("%Y-%m"):
        # 保留所选整月的实际账目；存在未来日期记录时显式计入，并由 UI 提示。
        day_count = max(today.day, max((int(row.record_date[-2:]) for row in selected), default=0))
    daily = [Decimal("0")] * day_count
    for record in selected:
        if record.amount < 0:
            daily[int(record.record_date[-2:]) - 1] -= record.amount
    return MonthStats(
        totals=totals, previous=before,
        average=average_expense(totals.total_expense, month, today),
        previous_average=average_expense(before.total_expense, previous_month, today),
        daily=tuple(daily), half_year=tuple((key, *half[key]) for key in months),
        largest=largest, record_count=len(selected), skipped_count=skipped,
        future_count=future, months=tuple(sorted(available, reverse=True)),
    )
