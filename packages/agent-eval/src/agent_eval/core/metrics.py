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
    AgreementReport,
    EvidenceGap,
    InvalidReason,
    PassKEstimate,
    PresentationOperatorReport,
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


#: 默认置信水平 (95%) 下的双侧 z 值 — 功效输出自陈公式时引用, 不在展示层重算
Z_SCORE_AT_DEFAULT_CONFIDENCE = _z_score(DEFAULT_CONFIDENCE_LEVEL)


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


# ─── Power analysis (样本量规划; 全闭式, 零新依赖, design D3) ─────────────────


def wilson_half_width(
    p: float,
    n: int,
    confidence: float = DEFAULT_CONFIDENCE_LEVEL,
) -> float:
    """Wilson 区间在比例 p、样本 n 下的半宽 (未做 0/1 截断的纯宽度轴)。

    与 :func:`wilson_interval` 同一公式: spread = (z/denom)·√(p(1-p)/n + z²/(4n²))，
    denom = 1 + z²/n。``wilson_interval`` 会把区间端点截到 [0, 1]，反解需要
    单调连续的宽度函数，所以这里返回截断前的 spread。对 n 单调下降。
    """
    z = _z_score(confidence)
    denom = 1.0 + z * z / n
    return (z / denom) * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))


def wilson_sample_size(
    p: float,
    delta: float,
    confidence: float = DEFAULT_CONFIDENCE_LEVEL,
) -> int:
    """最小有效 trial 数 N 使 Wilson 区间半宽 ≤ delta ("±δ 内分辨通过率"问法)。

    公式: 由 spread(p, n)² ≤ δ² 展开为二次方程 ``a·n² + b·n + c ≥ 0``
    (a = δ², b = z²(2δ² − p(1−p)), c = z⁴(δ² − ¼))，取其正根向上取整得初值，
    再用 :func:`wilson_half_width` 正向校验并按需 +1，吸收浮点误差。

    适用条件: 通过率的区间宽度规划，要求 0 < δ < 0.5（半宽超过 0.5 无意义）；
    p 是假设的基线通过率（规划用途，缺省取 0.5 最保守 — 真实 p 越极端区间
    越窄）。与 :func:`wilson_interval` 同源同测：算出的 N 反代回去半宽必 ≤ δ。
    """
    if not 0.0 < delta < 0.5:
        raise ValueError(f"delta 必须在 (0, 0.5) 开区间内, 得到 {delta!r}")
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"p 必须在 [0, 1] 内, 得到 {p!r}")
    z = _z_score(confidence)
    a = delta * delta
    b = z * z * (2.0 * a - p * (1.0 - p))
    c = z**4 * (a - 0.25)
    root = (-b + math.sqrt(b * b - 4.0 * a * c)) / (2.0 * a)
    n = max(1, math.floor(root))
    while wilson_half_width(p, n, confidence) > delta:
        n += 1
    return n


def normal_two_sample_size(
    d: float,
    sigma: float,
    alpha: float = 0.05,
) -> int:
    """分辨两版本分数差异 d 所需的每组样本数 (双样本正态近似, 闭式)。

    公式: ``n = 2·(z_{α/2}·σ/d)²``，其中 z_{α/2} = Φ⁻¹(1 − α/2)（α=0.05 时
    z ≈ 1.960）。这是「差异 d 恰好触及显著性边界」的最小样本（等价于功效
    50% 的读法）；要以更高概率检出，需另加 z_β 项，本框架不引入。

    适用条件与局限: 连续分数、近似正态、两版本样本量相等；**小样本与偏态
    分布下偏乐观** — 输出的 N 应当被当作下限并留余量。σ 取已落盘 run 的
    实测分数标准差 (:class:`ScoreDistribution.std_dev`)。
    """
    if d <= 0.0:
        raise ValueError(f"d 必须为正, 得到 {d!r}")
    if sigma < 0.0:
        raise ValueError(f"sigma 不能为负, 得到 {sigma!r}")
    z = _z_score(1.0 - alpha)
    return max(1, math.ceil(2.0 * (z * sigma / d) ** 2))


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


# ─── Inter-rater agreement (κ / α, spec: statistics) ─────────────────────────

# 一致性可计算的最少对齐样本数 (同一 trial 上 ≥2 个评分者都有有效判定)。
# 与饱和判定的最小有效样本同量级: 单 run 的 trial 数是个位数到几十, 少于这个
# 数的 κ/α 是噪声, 标 insufficient_data 而不是给一个可被误读的数值。
MIN_ALIGNED_RATINGS_FOR_AGREEMENT = 5


def cohen_kappa(labels_a: list[str], labels_b: list[str]) -> float:
    """未加权 Cohen's κ (两评分者, 二值/多类通用; 文献 Cohen 1960)。

    κ = (p_o - p_e) / (1 - p_e); p_o = 观察一致率, p_e = 边缘分布独立假设下的
    期望一致率。两序列必须等长且非空; p_e == 1 (两人都只判同一类) 时定义无
    意义, 返回 1.0 (完全一致是唯一合理读法)。

    Examples:
        >>> # 经典 2x2 表 [[20, 5], [10, 15]] (行=评分者A, 列=评分者B) → κ = 0.4
        >>> a = ["yes"]*20 + ["yes"]*5 + ["no"]*10 + ["no"]*15
        >>> b = ["yes"]*20 + ["no"]*5 + ["yes"]*10 + ["no"]*15
        >>> round(cohen_kappa(a, b), 10)
        0.4
        >>> cohen_kappa(["a", "b"], ["a", "b"])
        1.0
    """
    if len(labels_a) != len(labels_b):
        raise ValueError("κ 要求两个评分者的标签序列等长")
    n = len(labels_a)
    if n == 0:
        raise ValueError("κ 要求至少一个对齐样本")
    agree = sum(1 for a, b in zip(labels_a, labels_b, strict=True) if a == b)
    p_o = agree / n
    categories = set(labels_a) | set(labels_b)
    p_e = sum(
        (labels_a.count(c) / n) * (labels_b.count(c) / n) for c in categories
    )
    if p_e == 1.0:
        return 1.0
    return (p_o - p_e) / (1.0 - p_e)


def krippendorff_alpha(
    units: list[list[str | None]],
    level: str = "nominal",
) -> float:
    """Krippendorff's α (nominal / ordinal, 容忍缺失; Krippendorff 2011)。

    ``units`` 是逐单元 (trial) 的评分者取值列表, ``None`` = 该评分者本单元
    缺失 (不冒充一致或分歧)。只有观测值 ≥2 的单元参与 (α 的定义域)。

    α = 1 - D_o / D_e; D_o = 巧合对里的观测分歧率, D_e = 边缘分布下的期望
    分歧率。nominal: δ(c,k) = [c != k]; ordinal: δ(c,k) 用累积边缘的平方差
    (序数距离)。全一致 → 1.0; 无任何可配对单元 → insufficient (返回 None
    的判定由调用方做, 这里 raise)。
    """
    if level not in ("nominal", "ordinal"):
        raise ValueError(f"未知的 α 测量层级: {level!r} (可选 nominal / ordinal)")

    # 巧合矩阵 (coincidence matrix): 每个单元内取值两两配对, 按 1/(m_u-1) 计权
    coincidence: dict[tuple[str, str], float] = {}
    for values in units:
        observed = [v for v in values if v is not None]
        m_u = len(observed)
        if m_u < 2:
            continue
        counts: dict[str, int] = {}
        for value in observed:
            counts[value] = counts.get(value, 0) + 1
        for c, n_c in counts.items():
            for k, n_k in counts.items():
                pair_total = n_c * n_k - (n_c if c == k else 0)
                if pair_total:
                    key = (c, k)
                    coincidence[key] = coincidence.get(key, 0.0) + pair_total / (m_u - 1)

    n_prime = sum(coincidence.values())
    if n_prime == 0:
        raise ValueError("α 要求至少一个单元有 ≥2 个评分者的有效判定")

    # 边缘 (行和): n_c = Σ_k o_ck
    categories = sorted({c for c, _ in coincidence})
    marginals = {
        c: sum(coincidence.get((c, k), 0.0) for k in categories) for c in categories
    }

    if level == "nominal":
        observed_disagreement = sum(
            weight for (c, k), weight in coincidence.items() if c != k
        ) / n_prime
        expected_disagreement = sum(
            marginals[c] * marginals[k]
            for c in categories
            for k in categories
            if c != k
        ) / (n_prime * (n_prime - 1.0))
    else:
        # ordinal: δ(c,k) = (Σ_{c ≤ g < k} n_g)²  (按类别自然序, 相邻累积边缘)
        order = {c: i for i, c in enumerate(categories)}
        cumulative = [0.0]
        for c in categories:
            cumulative.append(cumulative[-1] + marginals[c])

        def delta(c: str, k: str) -> float:
            lo, hi = order[c], order[k]
            if lo == hi:
                return 0.0
            lo, hi = min(lo, hi), max(lo, hi)
            return (cumulative[hi] - cumulative[lo]) ** 2

        observed_disagreement = sum(
            weight * delta(c, k) for (c, k), weight in coincidence.items() if c != k
        ) / n_prime
        expected_disagreement = sum(
            marginals[c] * marginals[k] * delta(c, k)
            for c in categories
            for k in categories
        ) / (n_prime * (n_prime - 1.0))

    if expected_disagreement == 0.0:
        return 1.0  # 全体评分者对全部单元给出同一取值: 完全一致
    return 1.0 - observed_disagreement / expected_disagreement


def agreement_report(
    raters: list[str],
    units: list[dict[str, str | None]],
) -> AgreementReport:
    """对齐后的多评分者判定 → κ / α 报告 (选择语义见 design D3)。

    - <2 个评分者 → 不可计算 (评分者不足), 不伪造数值;
    - 对齐样本 < MIN_ALIGNED_RATINGS_FOR_AGREEMENT → insufficient_data + 原因;
    - 恰 2 个评分者且无缺失 → Cohen's κ (附一致/分歧计数);
    - ≥2 评分者或存在缺失评分 → Krippendorff's α (nominal, 缺失单元不参与)。
    """
    rated_units = [u for u in units if any(u.get(r) is not None for r in raters)]
    pair_units = [
        u for u in rated_units if sum(1 for r in raters if u.get(r) is not None) >= 2
    ]
    missing_present = any(
        sum(1 for r in raters if u.get(r) is not None) < len(raters)
        for u in rated_units
    )
    if len(raters) < 2:
        return AgreementReport(
            measure=None,
            value=None,
            reason=(
                "insufficient_data: 评分者不足 (κ/α 需要 ≥2 个独立评分者; "
                "单评分者多采样的 confidence 是自一致, 不是评分者间信度)"
            ),
            raters=len(raters),
            aligned_samples=len(pair_units),
        )
    if len(pair_units) < MIN_ALIGNED_RATINGS_FOR_AGREEMENT:
        return AgreementReport(
            measure=None,
            value=None,
            reason=(
                f"insufficient_data: 对齐样本过少 ({len(pair_units)} < "
                f"{MIN_ALIGNED_RATINGS_FOR_AGREEMENT})"
            ),
            raters=len(raters),
            aligned_samples=len(pair_units),
        )

    if len(raters) == 2 and not missing_present:
        first, second = raters
        labels_a = [u[first] for u in pair_units]
        labels_b = [u[second] for u in pair_units]
        agree = sum(1 for a, b in zip(labels_a, labels_b, strict=True) if a == b)
        return AgreementReport(
            measure="cohen_kappa",
            value=cohen_kappa(labels_a, labels_b),
            raters=2,
            aligned_samples=len(pair_units),
            agree=agree,
            disagree=len(pair_units) - agree,
        )

    matrix = [[u.get(r) for r in raters] for u in pair_units]
    return AgreementReport(
        measure="krippendorff_alpha",
        value=krippendorff_alpha(matrix, level="nominal"),
        raters=len(raters),
        aligned_samples=len(pair_units),
    )


# ─── Presentation invariance (spec: statistics「呈现不变性与跨评分者信度不得合成」) ──

PASS_LABEL = "pass"
FAIL_LABEL = "fail"


def presentation_operator_report(
    *,
    operator: str,
    invariant: str,
    presentations: list[str],
    baseline: str,
    units: list[dict[str, str | None]],
    score_shifts: list[float] | None = None,
    unreadable_reasons: list[str] | None = None,
) -> PresentationOperatorReport:
    """一个呈现算子的不变性报告 —— 测的是**结论翻不翻**, 不是分数漂多少。

    每份呈现被当作一个标签序列喂给既有的 κ/α 数学, 但这**不是**跨评分者信度:
    评分者只有一个, 变的是呈现。两类量各占一个类型、各报各的数, 不得合成
    (spec: statistics)。

    翻转与统计口径同源: 只有结论跨过 threshold 才算翻 (design D4)。对齐样本不足
    时复用 ``MIN_ALIGNED_RATINGS_FOR_AGREEMENT`` 语义报不可计算, 不伪造数值。
    """
    if baseline not in presentations:
        raise ValueError(f"基线呈现 {baseline!r} 必须在参与比较的呈现里")
    others = [p for p in presentations if p != baseline]
    shifts = list(score_shifts or ())

    aligned = [
        u for u in units if sum(1 for p in presentations if u.get(p) is not None) >= 2
    ]
    gaps = sum(1 for u in units if any(u.get(p) is None for p in presentations))

    compared = 0
    flipped = 0
    differing: list[str] = []
    for unit in units:
        base = unit.get(baseline)
        readable = [p for p in others if unit.get(p) is not None]
        if base is None or not readable:
            continue
        compared += 1
        disagreed = [p for p in readable if unit[p] != base]
        if disagreed:
            flipped += 1
            differing.extend(disagreed)

    shift_max = max(shifts) if shifts else None
    shift_mean = sum(shifts) / len(shifts) if shifts else None
    flip_rate = flipped / compared if compared else None
    distinct_differing = sorted(set(differing))

    measure: str | None = None
    value: float | None = None
    uncomputable: str | None = None
    if len(presentations) < 2:
        uncomputable = (
            f"不可计算: 该算子在当前判分配置下只构造出 {len(presentations)} 份呈现, "
            "没有可比较的第二份 (不是「没有翻转」)"
        )
    elif not aligned:
        cause = (unreadable_reasons or [""])[0]
        uncomputable = (
            f"不可计算: {len(units)} 个抽中 trial 上没有任何一份呈现读得出结论"
            + (f" (首个原因: {cause})" if cause else "")
            + " —— judge 不可用 (含无凭证) 是没测, 不是不敏感"
        )
    elif len(aligned) < MIN_ALIGNED_RATINGS_FOR_AGREEMENT:
        uncomputable = (
            f"不可计算: 对齐样本过少 ({len(aligned)} < "
            f"{MIN_ALIGNED_RATINGS_FOR_AGREEMENT}); 少于这个数的 κ/α 是噪声"
        )
    elif len(presentations) == 2 and not any(
        u.get(others[0]) is None for u in aligned
    ):
        first, second = baseline, others[0]
        measure = "cohen_kappa"
        value = cohen_kappa([u[first] for u in aligned], [u[second] for u in aligned])
    else:
        measure = "krippendorff_alpha"
        value = krippendorff_alpha(
            [[u.get(p) for p in presentations] for u in aligned], level="nominal"
        )

    if flipped:
        status: str = "sensitive"
        # 翻了几判是直接观察到的事实, 不依赖统计量够不够 —— 样本不足也不能把它读成"没翻"
        reason = (
            f"检出呈现敏感: {flipped}/{compared} 个可比较 trial 的结论随呈现翻转 "
            f"(与基线不同的呈现: {', '.join(distinct_differing)}); "
            f"本条只在「{invariant}」这个不变量内成立"
        )
    elif uncomputable:
        status = "not_computable"
        reason = uncomputable
    else:
        status = "not_detected"
        reason = (
            f"未检出呈现敏感: {len(aligned)} 个对齐 trial 上各呈现结论全同 "
            f"({measure}={value:.4f}); 这是一条结论, 但只在「{invariant}」"
            "这个不变量内成立 —— 把它引用成普适的否证是越界的"
        )

    return PresentationOperatorReport(
        operator=operator,
        invariant=invariant,
        presentations=list(presentations),
        baseline_presentation=baseline,
        status=status,
        measure=measure,
        value=value,
        reason=reason,
        aligned_trials=len(aligned),
        gap_trials=gaps,
        flipped_trials=flipped,
        flip_rate=flip_rate,
        flip_presentations=distinct_differing,
        max_score_shift=shift_max,
        mean_score_shift=shift_mean,
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
