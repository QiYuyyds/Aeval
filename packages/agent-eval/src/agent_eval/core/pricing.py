"""
成本单价表 (capability: statistics — 成本作为与通过率并列的独立轴)。

框架 MUST NOT 内置默认价目: 价格变动远快于套件修订, 内置价只会产出「看起来很
精确的假成本」。单价表是外部配置, 未配置即成本不可计算 (而不是 0)。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# 成本不可计算的原因 (写进 trial 的证据缺口清单)
PRICE_TABLE_NOT_CONFIGURED = "price_table_not_configured"
PRICE_MISSING_FOR_CLASS = "price_missing_for_token_class"
MODEL_PRICE_NOT_COVERED = "model_price_not_covered"
DETAIL_EXCEEDS_PARENT = "detail_tokens_exceed_parent"

_PER_MILLION = 1_000_000.0

# 明细桶与父桶的关系 (subset 为默认; 见 PriceTable.detail_semantics)
DETAIL_IS_DISJOINT = "disjoint"

# 观测字段 → (单价字段, 该字段缺价时的回落单价字段)
_TOKEN_CLASSES: dict[str, tuple[str, str]] = {
    "input_tokens": ("input_per_mtok", ""),
    "output_tokens": ("output_per_mtok", ""),
    "reasoning_tokens": ("reasoning_per_mtok", "output_per_mtok"),
    "cache_read_tokens": ("cache_read_per_mtok", "input_per_mtok"),
}

# 明细桶 → 父桶 (subset 口径下明细已从父桶中扣走, 不重复计费)
_DETAIL_TO_PARENT: dict[str, str] = {
    "cache_read_tokens": "input_tokens",
    "reasoning_tokens": "output_tokens",
}


class TokenPrices(BaseModel):
    """四路 token 的单价 (美元 / 百万 token); 缺价的类别不参与折算。"""

    input_per_mtok: float | None = Field(None, ge=0.0, description="输入 token 单价")
    output_per_mtok: float | None = Field(None, ge=0.0, description="输出 token 单价")
    reasoning_per_mtok: float | None = Field(
        None, ge=0.0, description="推理 token 单价 (未给则按输出价折算)"
    )
    cache_read_per_mtok: float | None = Field(
        None, ge=0.0, description="缓存读取 token 单价 (未给则按输入价折算)"
    )


class PriceTable(BaseModel):
    """按模型分列的单价表 + 一个兜底档 (兜底缺失即该模型成本不可计算)。"""

    by_model: dict[str, TokenPrices] = Field(default_factory=dict)
    default: TokenPrices | None = None
    currency: str = Field("usd", description="记账币种 (当前仅支持单一币种)")
    detail_semantics: Literal["subset", "disjoint"] = Field(
        "subset",
        description=(
            "cache_read / reasoning 是否为 input / output 的子集。"
            "OTel GenAI 与 OpenInference 均为 subset (默认); "
            "Anthropic 风格的 input 不含缓存读时显式改 disjoint"
        ),
    )

    @model_validator(mode="after")
    def _validate_currency(self) -> PriceTable:
        if self.currency.lower() != "usd":
            raise ValueError("当前仅支持 usd 记账币种, 多币种分列未实现")
        return self

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> PriceTable | None:
        """从外部配置构造; 未配置 (None / 空) 返回 None 而不是空表。

        形状: ``{"default": {"input_per_mtok": 2.5}, "by_model": {"gpt-4o": {...}}}``
        """
        if not raw:
            return None
        return cls.model_validate(dict(raw))

    def prices_for(self, model: str | None) -> TokenPrices | None:
        """按观测到的模型取单价档; 模型未知或未单列时用兜底档。"""
        if model:
            exact = self.by_model.get(model)
            if exact is not None:
                return exact
        return self.default

    @staticmethod
    def rate_for(token_class: str, prices: TokenPrices) -> float | None:
        rate, fallback = _TOKEN_CLASSES[token_class]
        value = getattr(prices, rate)
        if value is not None:
            return value
        return getattr(prices, fallback) if fallback else None

    def _billable(self, usage: Mapping[str, float | None]) -> dict[str, float] | str:
        """折算各桶应计费的 token 数; 口径自相矛盾时返回不可计算的原因。

        subset 口径下 ``input`` 已含 ``cache_read``、``output`` 已含 ``reasoning``
        —— 明细按自己的单价计费, 父桶只计剩余部分。四桶直接相加会把高缓存命中
        的 trace 放大数倍成本。
        """
        present = {
            token_class: float(usage.get(token_class) or 0.0)
            for token_class in _TOKEN_CLASSES
        }
        if self.detail_semantics == DETAIL_IS_DISJOINT:
            return present

        billable = dict(present)
        for detail, parent in _DETAIL_TO_PARENT.items():
            if present[detail] > present[parent]:
                return f"{DETAIL_EXCEEDS_PARENT}:{detail}>{parent}"
            billable[parent] = present[parent] - present[detail]
        return billable

    def cost_usd(
        self,
        usage: Mapping[str, float | None],
        model: str | None = None,
    ) -> tuple[float | None, str | None]:
        """按四路分解折算成本 (未参与折算的路径不静默省略)。

        Returns:
            ``(cost_usd, reason)``: 不可计算时 cost 为 None 且 reason 说明缺什么
        """
        prices = self.prices_for(model)
        if prices is None:
            return None, (
                MODEL_PRICE_NOT_COVERED if model else PRICE_TABLE_NOT_CONFIGURED
            )
        billable = self._billable(usage)
        if isinstance(billable, str):
            return None, billable
        total = 0.0
        costed_any = False
        for token_class, tokens in billable.items():
            if not tokens:
                continue
            rate = self.rate_for(token_class, prices)
            if rate is None:
                return None, f"{PRICE_MISSING_FOR_CLASS}:{token_class}"
            total += tokens * rate / _PER_MILLION
            costed_any = True
        if not costed_any:
            return None, PRICE_TABLE_NOT_CONFIGURED
        return total, None
