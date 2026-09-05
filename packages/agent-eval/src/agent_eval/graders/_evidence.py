"""
grader 侧的证据入口 (capability: graders)。

内置评分器只读归一化观测: 词汇差异全部留在翻译表里, 换宿主不改评分器。
每个结论都自陈实际消费了哪些证据、哪些字段当时缺失, 复核时无需猜依据。
"""

from __future__ import annotations

from typing import Any

from agent_eval.core.contract import EvalContext
from agent_eval.core.types import (
    EvidenceGap,
    GraderResult,
    GraderType,
    InvalidReason,
    TrialVerdict,
)
from agent_eval.trace.mapping import AttributeMapping
from agent_eval.trace.normalize import normalize_spans
from agent_eval.trace.observations import NormalizedTrace, is_missing


def observations_for(
    spans: list[dict[str, Any]],
    context: EvalContext | None,
    *,
    mapping: AttributeMapping | None = None,
) -> NormalizedTrace:
    """取本次评分该读的标准观测 (runner 已归一化则直接复用, 否则就地翻译)。"""
    if context is not None and context.observations is not None:
        return context.observations
    return normalize_spans(spans, mapping=mapping)


def render(value: Any) -> Any:
    """把 Missing 变成可落盘/可呈现的结构 (不压成字符串以免丢原因)。"""
    if is_missing(value):
        return {"missing": True, "reason": value.reason.value, "detail": value.detail}
    return value


def evidence_report(observations: NormalizedTrace) -> dict[str, Any]:
    """评分结果里附见的证据账本: 看到了什么、缺了什么、按哪个口径翻译的。"""
    return {
        "tool_calls_observed": render(observations.tool_call_count),
        "llm_calls_observed": render(observations.llm_call_count),
        "artifacts_observed": len(observations.artifacts),
        "source_status": observations.source_status,
        "missing_fields": dict(observations.missing_fields),
        "unrecognized_attributes": list(observations.unrecognized_attributes),
        "spec_version": observations.spec_version,
        "mapping_version": observations.mapping_version,
    }


def evidence_unavailable_result(
    grader_name: str,
    grader_type: GraderType,
    missing: list[tuple[str, str]],
    details: dict[str, Any] | None = None,
) -> GraderResult:
    """证据不足的结论: 说清缺哪一项及为什么, 不折算成 agent 未通过。

    verdict=invalid 使该结论既不占通过率分子也不占分母 (口径见 fix-stats-and-denominators)。
    """
    listing = "; ".join(f"{field} ({reason})" for field, reason in missing)
    return GraderResult(
        grader_name=grader_name,
        grader_type=grader_type,
        score=0.0,
        passed=False,
        explanation=f"证据不可用, 无法判定: {listing}" if listing else "证据不可用, 无法判定",
        details={
            **(details or {}),
            "evidence_gaps": [EvidenceGap(field=f, reason=r).model_dump() for f, r in missing],
        },
        verdict=TrialVerdict.INVALID,
        invalid_reason=InvalidReason.EVIDENCE_UNAVAILABLE,
    )


def gaps_of(observations: NormalizedTrace, fields: tuple[str, ...]) -> list[tuple[str, str]]:
    """挑出本次判定所依赖、但当时缺失的字段。"""
    return [(field, observations.missing_fields[field]) for field in fields if
            field in observations.missing_fields]
