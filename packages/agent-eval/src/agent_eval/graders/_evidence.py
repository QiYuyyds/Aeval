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
    GraderConfig,
    GraderResult,
    GraderType,
    InvalidReason,
    ObservedBy,
    TrialEvidence,
    TrialVerdict,
)
from agent_eval.trace.mapping import AttributeMapping
from agent_eval.trace.normalize import normalize_spans
from agent_eval.trace.observations import NormalizedTrace, is_missing

# 评分器没有自我声明时的默认可消费级别 (不含被评方自报)
DEFAULT_LEVELS = (ObservedBy.HARNESS, ObservedBy.RUNNER)


def declared_levels(grader: Any) -> frozenset[ObservedBy]:
    """评分器自己声明会消费哪几级来源; 没声明按默认两级。"""
    declared = getattr(grader, "evidence_levels", None)
    if not declared:
        return frozenset(DEFAULT_LEVELS)
    return frozenset(ObservedBy(level) for level in declared)


def policy_levels(config: GraderConfig | None) -> frozenset[ObservedBy]:
    """套件侧对这一个判据的取信声明 (``allow_subject`` 是唯一的放行入口)。"""
    if config is None:
        return frozenset(DEFAULT_LEVELS)
    allowed = frozenset(ObservedBy(level) for level in config.evidence)
    if config.allow_subject:
        allowed |= {ObservedBy.SUBJECT}
    return allowed


def effective_levels(config: GraderConfig | None, grader: Any) -> frozenset[ObservedBy]:
    """两边都同意才算数: 套件没允许的评分器读不到, 评分器没声明的套件也不算喂给它。"""
    return policy_levels(config) & declared_levels(grader)


def implementation_version_of(grader: Any) -> str:
    """判分实现版本 —— 随每次判定落盘, 使「翻判是因为哪一版变了」可回答。"""
    return str(getattr(grader, "implementation_version", "unversioned"))


def channel_levels(
    evidence: TrialEvidence | None, *channels: str
) -> list[ObservedBy]:
    """指定通道里**实际有值**的读数级别 (按可信度降序; 缺失读数不算支撑)。"""
    if evidence is None:
        return []
    present = {
        obs.observed_by
        for channel in channels
        for obs in getattr(evidence, channel, [])
        if not obs.is_absent
    }
    return sorted(present, key=lambda level: -level.strength)


def consulted_levels(
    evidence: TrialEvidence | None, *channels: str
) -> list[ObservedBy]:
    """本结论依据了哪几级: 有带来源的证据就读它, 否则只能按适配层交付级记。

    没有证据对象意味着这是第三方直接调用评分器 (旧签名路径) —— 它读到的
    transcript/outcome 是适配层交上来的, 记成 ``runner`` 而不是凭空宣称 harness。
    """
    return channel_levels(evidence, *channels) or [ObservedBy.RUNNER]


def evidence_view(
    context: EvalContext | None, config: GraderConfig | None, grader: Any = None
) -> TrialEvidence | None:
    """该判据实际被允许阅读的那份证据 (None = 本次判定没有带来源的证据)。"""
    if context is None or context.evidence is None:
        return None
    return context.evidence.permitted(
        effective_levels(config, grader) if grader is not None else policy_levels(config)
    )


def consultable_levels(config: GraderConfig | None) -> list[ObservedBy]:
    """本判据被允许阅读的来源级别, 按可信度从高到低排列。"""
    return sorted(policy_levels(config), key=lambda level: -level.strength)


def enforce_evidence_policy(
    result: GraderResult,
    config: GraderConfig | None,
    grader: Any = None,
) -> GraderResult:
    """D4 的两条默认规则 —— 分级不立默认规则就只是元数据。

    1. 只由被评方自报证据支撑的通过 → invalid(``subject_only_evidence``);
    2. 结论依据了本判据未声明的来源级别 → invalid(``evidence_level_mismatch``)。

    两条的原因文案互相区分, 因为该修的东西不同: 一条要补取证通道, 一条要改声明。
    """
    if result.verdict is not TrialVerdict.VALID:
        return result
    used = frozenset(ObservedBy(level) for level in result.evidence_levels)
    if not used:
        return result

    allowed = (
        effective_levels(config, grader) if grader is not None else policy_levels(config)
    )
    allow_subject = bool(config.allow_subject) if config is not None else False
    declared = list(
        sorted((level.value for level in allowed), key=lambda name: -ObservedBy(name).strength)
    )

    if used <= {ObservedBy.SUBJECT}:
        if allow_subject:
            # 逃生开关生效: 结论保留, 但这条「弱证据判定」必须自己站出来
            return result.model_copy(
                update={
                    "subject_only": True,
                    "explanation": result.explanation
                    + " [弱证据: 仅由被评方自报证据支撑 (allow_subject 已显式放行)]",
                }
            )
        return _rejected(
            result,
            InvalidReason.SUBJECT_ONLY_EVIDENCE,
            "结论只由被评方自报证据支撑, 没有任何评测侧取证或适配层交付的读数支撑它; "
            f"本判据声明可消费的是 {declared}",
        )

    offending = sorted(
        (level.value for level in used - allowed), key=lambda name: -ObservedBy(name).strength
    )
    if offending:
        return _rejected(
            result,
            InvalidReason.EVIDENCE_LEVEL_MISMATCH,
            f"结论依据了未声明的来源级别 {offending}; 本判据声明可消费的是 {declared}",
        )
    return result


def _rejected(
    result: GraderResult, reason: InvalidReason, explanation: str
) -> GraderResult:
    """把违规结论改写成 invalid —— 不照原样给分, 也不折算成 agent 未通过。"""
    return result.model_copy(
        update={
            "verdict": TrialVerdict.INVALID,
            "invalid_reason": reason,
            "passed": False,
            "score": 0.0,
            "explanation": f"{explanation} (不采信该判定, 不占通过率分母)",
            "details": {
                **result.details,
                "rejected_explanation": result.explanation,
                "rejected_score": result.score,
                "evidence_levels": [level.value for level in result.evidence_levels],
            },
        }
    )


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
