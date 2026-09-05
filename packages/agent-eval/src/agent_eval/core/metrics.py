"""
Statistical metrics for the Aeval evaluation framework.

pass@k / pass^k estimators, validity (denominator) semantics, and the
uncertainty summaries that every aggregate is reported with.

Estimator caliber (design D1):
- ``k <= n``: finite-sample unbiased combinatorial estimates —
  pass@k = 1 - C(n-c, k) / C(n, k), pass^k = C(c, k) / C(n, k)
- ``k > n`` : binomial extrapolation (pass@k = 1-(1-p)^k, pass^k = p^k),
  flagged ``extrapolated`` and accompanied by the Wilson bounds of p = c/n

Only ``valid`` trials enter the denominator; ``invalid`` (evaluation-side
failure) and ``pending`` (awaiting human score) trials are excluded from both
numerator and denominator. An empty denominator yields ``insufficient_data``
(``None``) — never ``0.0``.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Any

from agent_eval.core.pricing import PRICE_TABLE_NOT_CONFIGURED, PriceTable
from agent_eval.core.types import (
    DEFAULT_BOOTSTRAP_ROUNDS,
    DEFAULT_CONFIDENCE_LEVEL,
    EvidenceGap,
    InvalidReason,
    PassKEstimate,
    ResourceSummary,
    RunResult,
    ScoreDistribution,
    TrialClassResources,
    TrialResult,
    TrialVerdict,
)
from agent_eval.trace.mapping import AttributeMapping
from agent_eval.trace.normalize import normalize_spans
from agent_eval.trace.observations import (
    AbsentReason,
    NormalizedTrace,
    is_missing,
)

# 观测字段 → 过程指标键 (token 四分解)
_TOKEN_METRIC_FIELDS: tuple[tuple[str, str], ...] = (
    ("n_input_tokens", "input_tokens"),
    ("n_output_tokens", "output_tokens"),
    ("n_reasoning_tokens", "reasoning_tokens"),
    ("n_cache_read_tokens", "cache_read_tokens"),
)


@dataclass
class ProcessMetrics:
    """一次 trial 的过程指标与其证据缺口 (提取一次, 两处呈现)。"""

    metrics: dict[str, float] = field(default_factory=dict)
    gaps: list[EvidenceGap] = field(default_factory=list)


# ─── Validity / denominators ──────────────────────────────────────────────────

# 兼容历史行: HumanGrader 的 pending 语义在引入 verdict 字段之前只落在
# details.status 上, 缺字段的旧 run 记录仍需被识别为 pending。
_PENDING_DETAIL_STATUSES = frozenset({"pending"})


def grader_verdict(result: Any) -> TrialVerdict:
    """单个 grader 结论的分类 (含历史行的 details.status 兜底)。"""
    # 先查 details.status: 历史行的 verdict 字段缺省为 valid, 会掩盖
    # HumanGrader 落在 details 上的 pending 标记
    details = getattr(result, "details", None) or {}
    if details.get("status") in _PENDING_DETAIL_STATUSES:
        return TrialVerdict.PENDING
    verdict = getattr(result, "verdict", None)
    if isinstance(verdict, str) and verdict in {v.value for v in TrialVerdict}:
        return TrialVerdict(verdict)
    return TrialVerdict.VALID


def classify_trial(trial: TrialResult) -> TrialVerdict:
    """一次 trial 的结论分类 (可重入: 人工评分回传后重算会离开 pending)。

    有 grader 结论时按其推导, 优先级 pending > invalid > valid —— 等待人工评分
    的 trial 不被改写成 invalid (specs/orchestration)。trial 级 verdict 是派生
    状态, 重算时以 grader 为准; 只有没有任何 grader 结论 (如 trial 超时未进入
    评分) 时才采用 trial 级判定。
    """
    if any(grader_verdict(gr) is TrialVerdict.PENDING for gr in trial.grader_results):
        return TrialVerdict.PENDING

    if not trial.grader_results:
        if trial.verdict is TrialVerdict.PENDING:
            return TrialVerdict.PENDING
        if trial.verdict is TrialVerdict.INVALID or trial.invalid_reason is not None:
            return TrialVerdict.INVALID
        return TrialVerdict.VALID

    if trial.verdict is TrialVerdict.INVALID or trial.invalid_reason is not None:
        return TrialVerdict.INVALID
    if any(grader_verdict(gr) is TrialVerdict.INVALID for gr in trial.grader_results):
        return TrialVerdict.INVALID
    return TrialVerdict.VALID


def trial_invalid_reason(trial: TrialResult) -> InvalidReason | None:
    """trial 的评测侧失败原因 (取首个可追溯到原因的 grader)。"""
    if classify_trial(trial) is not TrialVerdict.INVALID:
        return None
    if trial.invalid_reason is not None:
        return trial.invalid_reason
    for gr in trial.grader_results:
        reason = getattr(gr, "invalid_reason", None)
        if reason is not None:
            return reason
    return InvalidReason.GRADER_ERROR


def split_trials_by_verdict(
    trials: list[TrialResult],
) -> dict[TrialVerdict, list[tuple[int, TrialResult]]]:
    """按结论分类 trial, 返回 {verdict: [(原索引, trial)]} (保留原始位置)。"""
    buckets: dict[TrialVerdict, list[tuple[int, TrialResult]]] = {
        TrialVerdict.VALID: [],
        TrialVerdict.INVALID: [],
        TrialVerdict.PENDING: [],
    }
    for index, trial in enumerate(trials):
        buckets[classify_trial(trial)].append((index, trial))
    return buckets


def valid_trials(trials: list[TrialResult]) -> list[TrialResult]:
    """只有有效 trial 参与通过率与平均分 (invalid/pending 均不占分母)。"""
    return [t for t in trials if classify_trial(t) is TrialVerdict.VALID]


def is_insufficient_data(value: float | None) -> bool:
    """聚合量是否为「证据不足」表示。"""
    return value is None


def has_enough_data(n: int, minimum: int = 1) -> bool:
    """分母是否足以支撑结论 —— insufficient_data 判定入口。"""
    return n >= max(1, minimum)


# ─── Uncertainty ──────────────────────────────────────────────────────────────


def _z_score(confidence: float) -> float:
    return NormalDist().inv_cdf((1.0 + confidence) / 2.0)


def wilson_interval(
    successes: int,
    n: int,
    confidence: float = DEFAULT_CONFIDENCE_LEVEL,
) -> tuple[float, float] | None:
    """二项比例的 Wilson score 区间; 分母为 0 时返回 None (insufficient_data)。"""
    if n <= 0:
        return None
    z = _z_score(confidence)
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    spread = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, centre - spread), min(1.0, centre + spread)


def percentile(values: list[float], q: float) -> float | None:
    """线性插值分位数 (q 为 0-100); 空输入返回 None。"""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * (q / 100.0)
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return ordered[low]
    frac = pos - low
    return ordered[low] * (1.0 - frac) + ordered[high] * frac


def p50(values: list[float]) -> float | None:
    return percentile(values, 50.0)


def p95(values: list[float]) -> float | None:
    return percentile(values, 95.0)


def worst_of_n(values: list[float]) -> float | None:
    """可靠性地板: 最差一次的成绩。"""
    return min(values) if values else None


def bootstrap_ci(
    values: list[float],
    rounds: int = DEFAULT_BOOTSTRAP_ROUNDS,
    confidence: float = DEFAULT_CONFIDENCE_LEVEL,
    seed: int | None = None,
) -> tuple[float, float] | None:
    """重采样均值的百分位置信区间; 空输入返回 None。

    seed 可注入以便测试复现。rounds 至少 1000 (spec 下限), 更小值向上取齐。
    """
    if not values:
        return None
    rounds = max(rounds, DEFAULT_BOOTSTRAP_ROUNDS)
    rng = random.Random(seed)
    size = len(values)
    samples = [sum(rng.choices(values, k=size)) / size for _ in range(rounds)]
    alpha = (1.0 - confidence) / 2.0
    return percentile(samples, alpha * 100.0), percentile(samples, (1.0 - alpha) * 100.0)


def summarize_scores(
    values: list[float],
    rounds: int = DEFAULT_BOOTSTRAP_ROUNDS,
    confidence: float = DEFAULT_CONFIDENCE_LEVEL,
    seed: int | None = None,
) -> ScoreDistribution:
    """连续分数的分布摘要: 均值 / SD / worst_of_n / bootstrap 95% 区间。"""
    n = len(values)
    if n == 0:
        return ScoreDistribution(n=0, ci_level=confidence)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    low, high = bootstrap_ci(values, rounds=rounds, confidence=confidence, seed=seed)
    return ScoreDistribution(
        n=n,
        mean=mean,
        std_dev=variance**0.5,
        worst_of_n=min(values),
        ci_low=low,
        ci_high=high,
        ci_level=confidence,
        method="bootstrap",
    )


# ─── pass@k / pass^k ──────────────────────────────────────────────────────────


def _count_successes(trials: list[TrialResult]) -> tuple[int, int]:
    """(n, c): 有效 trial 数与其成功数 (invalid/pending 不占分母)。"""
    counted = valid_trials(trials)
    return len(counted), sum(1 for t in counted if t.success)


def _insufficient(k: int, n: int, confidence: float) -> PassKEstimate:
    return PassKEstimate(k=k, n=n, successes=0, method="insufficient_data", ci_level=confidence)


def pass_at_k(
    trials: list[TrialResult],
    k: int,
    confidence: float = DEFAULT_CONFIDENCE_LEVEL,
) -> PassKEstimate:
    """
    pass@k: k 次尝试中至少成功一次 (能力)。

    - ``k <= n``: 有限样本无偏组合估计 ``1 - C(n-c, k) / C(n, k)``,
      因此 ``pass@1 == c / n``, 且与 trial 顺序无关。
    - ``k > n`` : 二项外推 ``1 - (1-p)^k``, p = c/n; 结果标记 ``extrapolated``
      并附带 p 的 Wilson 区间。
    - ``n == 0``: ``insufficient_data`` (value=None), 不外推。

    Examples:
        >>> pass_at_k([fail, ok, ok], 1).value          # n=3, c=2
        0.6666666666666666
        >>> pass_at_k([fail, ok, ok], 3).value          # 至少一次 (实测区间)
        1.0
        >>> pass_at_k([ok, fail, fail], 5).extrapolated
        True
    """
    n, c = _count_successes(trials)
    if k <= 0 or n == 0:
        return _insufficient(max(k, 0), n, confidence)

    p = c / n
    bounds = wilson_interval(c, n, confidence)
    low, high = bounds if bounds else (None, None)

    if k <= n:
        value = 1.0 - math.comb(n - c, k) / math.comb(n, k)
        method: str = "measured"
        extrapolated = False
    else:
        value = 1.0 if p == 1.0 else (0.0 if p == 0.0 else 1.0 - (1.0 - p) ** k)
        method = "extrapolated"
        extrapolated = True

    return PassKEstimate(
        k=k,
        n=n,
        successes=c,
        value=value,
        method=method,  # type: ignore[arg-type]
        extrapolated=extrapolated,
        p_point=p,
        p_lower_bound=low,
        p_upper_bound=high,
        ci_level=confidence,
    )


def pass_power_k(
    trials: list[TrialResult],
    k: int,
    confidence: float = DEFAULT_CONFIDENCE_LEVEL,
) -> PassKEstimate:
    """
    pass^k: k 次尝试全部成功 (可靠性)。

    - ``k <= n``: ``C(c, k) / C(n, k)``, 表示任意 k 次全部成功的概率,
      与 trial 完成顺序无关 (不截取前 k 个判定)。
    - ``k > n`` : 二项外推 ``p^k``, 标记 ``extrapolated``。
    - ``n == 0``: ``insufficient_data``, 不外推。

    Examples:
        >>> pass_power_k([ok, ok, fail], 2).value       # n=3, c=2
        0.0
        >>> pass_power_k([ok, ok, ok], 3).value
        1.0
    """
    n, c = _count_successes(trials)
    if k <= 0 or n == 0:
        return _insufficient(max(k, 0), n, confidence)

    p = c / n
    bounds = wilson_interval(c, n, confidence)
    low, high = bounds if bounds else (None, None)

    if k <= n:
        value = math.comb(c, k) / math.comb(n, k)
        method: str = "measured"
        extrapolated = False
    else:
        value = 1.0 if p == 1.0 else p**k
        method = "extrapolated"
        extrapolated = True

    return PassKEstimate(
        k=k,
        n=n,
        successes=c,
        value=value,
        method=method,  # type: ignore[arg-type]
        extrapolated=extrapolated,
        p_point=p,
        p_lower_bound=low,
        p_upper_bound=high,
        ci_level=confidence,
    )


def estimates_to_rates(estimates: dict[int, PassKEstimate]) -> dict[int, float | None]:
    """估计结构 → {k: value} (None 表示 insufficient_data)。"""
    return {k: est.value for k, est in estimates.items()}


# ─── Metric Aggregation ───────────────────────────────────────────────────────


def aggregate_metrics(trials: list[TrialResult]) -> dict[str, float]:
    """
    聚合多个 trial 的过程指标。

    对每个指标计算 avg/min/max/p50/p95 (avg/min/max 键名与既有一致)。

    Args:
        trials: trial 结果列表

    Returns:
        聚合后的指标字典，格式: {metric_name_{avg|min|max|p50|p95}: value}
    """
    if not trials:
        return {}

    # 收集所有指标 key
    all_keys: set[str] = set()
    for t in trials:
        all_keys.update(t.metrics.keys())

    result: dict[str, float] = {}
    for key in all_keys:
        values = [t.metrics[key] for t in trials if key in t.metrics]
        if values:
            result[f"{key}_avg"] = sum(values) / len(values)
            result[f"{key}_min"] = min(values)
            result[f"{key}_max"] = max(values)
            result[f"{key}_p50"] = p50(values)
            result[f"{key}_p95"] = p95(values)

    return result


def extract_metrics(
    observations: NormalizedTrace | list[dict[str, Any]],
    tracked_metrics: list[str],
    *,
    price_table: PriceTable | None = None,
    optimal_steps: int | None = None,
    mapping: AttributeMapping | None = None,
) -> dict[str, float]:
    """
    从归一化观测 (或待归一化的 spans) 提取过程指标。

    Returns:
        指标字典; **读不到的指标不出现在结果里** (缺字段即证据缺失, 不以 0 顶替)
    """
    return extract_process_metrics(
        observations,
        tracked_metrics,
        price_table=price_table,
        optimal_steps=optimal_steps,
        mapping=mapping,
    ).metrics


def extract_process_metrics(
    observations: NormalizedTrace | list[dict[str, Any]],
    tracked_metrics: list[str],
    *,
    price_table: PriceTable | None = None,
    optimal_steps: int | None = None,
    mapping: AttributeMapping | None = None,
) -> ProcessMetrics:
    """过程指标 + 逐项证据缺口 (结论自陈「没看到什么、为什么没看到」)。

    计数与 token 分解一律取自归一化观测, 不再按 span 名称子串猜调用类型。
    遗留四项 (n_turns/n_toolcalls/n_total_tokens) 仍受 ``tracked_metrics`` 门控;
    token 四分解与成本是呈现轴, 只要算得出来就报告。
    """
    if isinstance(observations, list):
        observations = normalize_spans(observations, mapping=mapping)

    metrics: dict[str, float] = {}
    gaps: list[EvidenceGap] = []

    def gap(field: str, reason: Any, detail: str = "") -> None:
        gaps.append(EvidenceGap(field=field, reason=_reason_code(reason), detail=detail))

    # ── 计数 (缺失与 0 分开: 0 是「trace 里确实没有」) ──
    if "n_turns" in tracked_metrics:
        _emit_count(metrics, gaps, "n_turns", observations.llm_call_count)
    if "n_toolcalls" in tracked_metrics:
        _emit_count(metrics, gaps, "n_toolcalls", observations.tool_call_count)

    # ── token 四分解 ──
    usage: dict[str, float | None] = {}
    for metric_key, obs_field in _TOKEN_METRIC_FIELDS:
        value = observations.sum_field(obs_field)
        if is_missing(value):
            gap(metric_key, value)
            usage[obs_field] = None
            continue
        usage[obs_field] = float(value)
        metrics[metric_key] = float(value)

    # ── n_total_tokens: 输入+输出的只读派生别名 (不用于计费推导) ──
    if "n_total_tokens" in tracked_metrics:
        alias = _total_tokens_alias(observations, usage)
        if is_missing(alias):
            gap("n_total_tokens", alias)
        else:
            metrics["n_total_tokens"] = float(alias)

    # ── 成本 (独立呈现轴; 无单价表即不可计算) ──
    cost_usd, cost_reason = compute_cost_usd(observations, price_table)
    if cost_usd is None:
        gap("cost_usd", cost_reason or PRICE_TABLE_NOT_CONFIGURED)
    else:
        metrics["cost_usd"] = cost_usd

    # ── 步数效率 (诊断量, 不参与通过判定) ──
    if optimal_steps:
        steps = observations.tool_call_count
        if is_missing(steps) or steps <= 0:
            gap("step_efficiency", steps if is_missing(steps) else AbsentReason.NO_SUCH_CALL)
        else:
            metrics["step_efficiency"] = optimal_steps / float(steps)

    return ProcessMetrics(metrics=metrics, gaps=gaps)


def compute_cost_usd(
    observations: NormalizedTrace,
    price_table: PriceTable | None,
) -> tuple[float | None, str | None]:
    """按观测到的模型解析单价档并折算成本; 返回 (成本, 不可计算的原因)。"""
    if price_table is None:
        return None, PRICE_TABLE_NOT_CONFIGURED
    return price_table.cost_usd(_token_usage(observations), model=_observed_model(observations))


def _token_usage(observations: NormalizedTrace) -> dict[str, float | None]:
    usage: dict[str, float | None] = {}
    for _, attr in _TOKEN_METRIC_FIELDS:
        value = observations.sum_field(attr)
        usage[attr] = None if is_missing(value) else float(value)
    return usage


def _observed_model(observations: NormalizedTrace) -> str | None:
    for call in observations.llm_calls:
        if not is_missing(call.model):
            return str(call.model)
    return None


def _reason_code(value: Any) -> str:
    """把「为什么没读到」归一成字符串 (Missing → 其原因枚举, 字符串原因原样)。"""
    if is_missing(value):
        return value.reason.value
    return getattr(value, "value", None) or str(value)


def _emit_count(
    metrics: dict[str, float],
    gaps: list[EvidenceGap],
    field: str,
    value: Any,
) -> None:
    if is_missing(value):
        gaps.append(EvidenceGap(field=field, reason=_reason_code(value), detail=value.detail))
        return
    metrics[field] = float(value)


def _total_tokens_alias(
    observations: NormalizedTrace,
    usage: dict[str, float | None],
) -> Any:
    """n_total_tokens = 输入 + 输出; 两者都没读到时才认 provider 直报的总数。"""
    input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
    if input_tokens is not None or output_tokens is not None:
        return (input_tokens or 0.0) + (output_tokens or 0.0)
    total = observations.sum_field("total_tokens")
    return total if is_missing(total) else float(total)


# ─── Termination reasons ──────────────────────────────────────────────────────

UNKNOWN_TERMINATION = "unknown"  # 历史 trial 未记录终止原因


def termination_distribution(trials: list[TrialResult]) -> dict[str, int]:
    """终止原因分布计数; 占比不折叠进通过率, 也不因未记录而臆断为正常完成。"""
    counts: dict[str, int] = {}
    for trial in trials:
        reason = (
            trial.termination_reason.value
            if trial.termination_reason is not None
            else UNKNOWN_TERMINATION
        )
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def merge_termination_distributions(
    distributions: list[dict[str, int]],
) -> dict[str, int]:
    """task 级分布合并为 run 级分布。"""
    merged: dict[str, int] = {}
    for distribution in distributions:
        for reason, count in distribution.items():
            merged[reason] = merged.get(reason, 0) + count
    return merged


# ─── Resource / cost axis ─────────────────────────────────────────────────────


def summarize_resources(trials: list[TrialResult]) -> ResourceSummary:
    """分别汇总通过与未通过 trial 的 token 与成本。

    「失败比成功更贵」这类事实必须看得见, 不能被全局均值抹平; 成本读不到的
    trial 单列为 cost_unknown_trials 并按原因标注, 不计为零成本。
    """
    costs = [t.metrics["cost_usd"] for t in trials if "cost_usd" in t.metrics]
    tokens = [
        t.metrics["n_total_tokens"] for t in trials if "n_total_tokens" in t.metrics
    ]
    passed = [t for t in trials if t.success]
    failed = [t for t in trials if not t.success]
    unknown_reasons = [
        gap.reason
        for t in trials
        for gap in t.evidence_gaps
        if gap.field == "cost_usd"
    ]
    return ResourceSummary(
        total_cost_usd=sum(costs) if costs else None,
        avg_cost_usd=(sum(costs) / len(costs)) if costs else None,
        p50_cost_usd=p50(costs),
        p95_cost_usd=p95(costs),
        avg_total_tokens=(sum(tokens) / len(tokens)) if tokens else None,
        passed=_class_resources(passed),
        failed=_class_resources(failed),
        cost_unknown_trials=len(unknown_reasons),
        cost_unknown_reason=(
            max(set(unknown_reasons), key=unknown_reasons.count)
            if unknown_reasons
            else None
        ),
    )


def _class_resources(trials: list[TrialResult]) -> TrialClassResources:
    costs = [t.metrics["cost_usd"] for t in trials if "cost_usd" in t.metrics]
    tokens = [
        t.metrics["n_total_tokens"] for t in trials if "n_total_tokens" in t.metrics
    ]
    return TrialClassResources(
        trials=len(trials),
        avg_total_tokens=(sum(tokens) / len(tokens)) if tokens else None,
        avg_cost_usd=(sum(costs) / len(costs)) if costs else None,
        costed_trials=len(costs),
    )


def cost_trend(runs: list[RunResult]) -> dict[str, Any]:
    """跨 run 成本趋势: 未记录成本轴的 run 被排除并说明原因 (不按零计入)。"""
    series: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for run in runs:
        resources = run.summary.resources if run.summary else None
        if resources is None:
            excluded.append(
                {
                    "run_id": run.run_id,
                    "reason": (
                        "history_run_without_resource_axis"
                        if run.summary is not None
                        else "no_summary"
                    ),
                }
            )
            continue
        if resources.total_cost_usd is None:
            excluded.append(
                {
                    "run_id": run.run_id,
                    "reason": resources.cost_unknown_reason
                    or PRICE_TABLE_NOT_CONFIGURED,
                }
            )
            continue
        series.append(
            {
                "run_id": run.run_id,
                "started_at": run.started_at,
                "total_cost_usd": resources.total_cost_usd,
                "avg_cost_usd": resources.avg_cost_usd,
            }
        )
    return {
        "series": sorted(series, key=lambda item: item["started_at"]),
        "excluded": excluded,
        "comparable": len(series) > 1,
    }
