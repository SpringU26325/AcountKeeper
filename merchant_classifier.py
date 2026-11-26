"""根据预览中的交易对方，为可入账行生成本地标签建议。"""

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class MerchantRule:
    merchant: str
    direction: str
    tag: str
    verified: bool = False


@dataclass(frozen=True)
class MerchantSuggestion:
    key: str = ""
    tag: str = ""
    confidence: str = "低"
    reason: str = ""
    learned: bool = False

    @property
    def prefill(self) -> str:
        return self.tag if self.confidence == "高" else ""


# 只把全角 ASCII 和全角空格折算，避免把有意义的门店标记一并转换。
_WIDTHS = {code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)}
_WIDTHS[0x3000] = 0x20
_CHANNELS = frozenset(("微信", "微信支付", "财付通", "支付宝", "银联", "云闪付", "网联"))
_RULES = (
    # 常见名称仅用于首批建议，真实账单对账前不进入自动预填档。
    MerchantRule("麦当劳", "支出", "餐饮"),
    MerchantRule("肯德基", "支出", "餐饮"),
    MerchantRule("星巴克", "支出", "餐饮"),
)


def _merchant_key(value: str) -> str:
    return " ".join(value.translate(_WIDTHS).split())


def classify_merchant(
    sources: tuple[str, ...],
    direction: str,
    available_tags: frozenset[str],
    *,
    rules: tuple[MerchantRule, ...] = _RULES,
    learned: Mapping[tuple[str, str], str] | None = None,
) -> MerchantSuggestion:
    keys = set(filter(None, map(_merchant_key, sources)))
    if not keys:
        return MerchantSuggestion(reason="可在字段映射中选择交易对方列")
    if len(keys) != 1:
        return MerchantSuggestion(reason="多个来源不一致，商户不明确")
    key = next(iter(keys))
    if key in _CHANNELS:
        return MerchantSuggestion(reason="交易对方为支付渠道，可手动选择标签")
    remembered = learned.get((key, direction)) if learned is not None else None
    if remembered is not None:
        # 用户映射优先。目标标签失效时保留问题供核对，避免内置规则偷偷替换用户决定。
        if remembered not in available_tags:
            return MerchantSuggestion(key, reason=f"已记住的标签「{remembered}」不在常用标签中")
        return MerchantSuggestion(key, remembered, "高", "匹配已记住的商户和收支方向", learned=True)
    # 仅精确匹配同方向的商户；多个规则给出不同标签时，由用户判断。
    matches = tuple(rule for rule in rules if rule.merchant == key and rule.direction == direction)
    tags = {rule.tag for rule in matches}
    if not tags:
        return MerchantSuggestion(key, reason="没有匹配的商户规则")
    if len(tags) > 1:
        return MerchantSuggestion(key, reason="商户规则给出不同标签")
    tag = next(iter(tags))
    if tag not in available_tags:
        return MerchantSuggestion(key, reason="匹配标签不在常用标签中")
    confidence = "高" if any(rule.verified for rule in matches) else "中"
    return MerchantSuggestion(key, tag, confidence, "商户名称和收支方向均精确匹配")
