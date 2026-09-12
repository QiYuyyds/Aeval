"""Run 级可比性前置与基线门判定 (CLI / API / pytest 插件三处同源)。

分工 (勿混, 不出第二套比较逻辑):

- :func:`runs_comparable` — **run 级前置**: 统计口径版本 + 证据边界 (含环境
  身份) + 缺边界/缺口径历史行的兜底, 决定两个 run 之间允不允许出现任何
  方向性结论。compare API、CLI compare 与基线门都调它。
- :meth:`agent_eval.core.types.EvidenceBoundary.compare_with` — **边界级比较**:
  只回答两份边界记录自身是否一致, 不看统计口径, 也不定义「一侧整条记录
  缺失」的历史行语义 — 那是 run 级前置的事。
- :func:`compare_baseline` — **基线门判定**: 先过 run 级前置, 可比时以套件
  实测 ``pass@1`` 的 95% 区间判显著变差 (区间不重叠 **且** 方向向下)。

本模块只依赖 core 层; CLI 与 pytest 插件从这里取判定与文案, 不从 API 路由
导入 (装配方向: API 依赖 core, 不是反过来)。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from agent_eval.core.types import PassKEstimate, RunResult, RunSummary, TaskSummary


class BaselineVerdict(str, Enum):
    """基线比较的结论分类 (design D4 + 缺实测区间的不可判兜底)。

    - ``significantly_worse``: 可比且新 run 区间整体低于基线 (不重叠且向下);
    - ``not_significant``: 可比但区间重叠 — 差异落在噪声内, **不等于没有变化**;
    - ``improved``: 可比且新 run 区间整体高于基线;
    - ``not_comparable``: run 级前置不过 (口径/边界/历史行), 拒绝给任何方向;
    - ``undecidable``: 可比但缺实测区间 (全外推/无有效样本), 外推值不参与门判定。
    """

    SIGNIFICANTLY_WORSE = "significantly_worse"
    NOT_SIGNIFICANT = "not_significant"
    IMPROVED = "improved"
    NOT_COMPARABLE = "not_comparable"
    UNDECIDABLE = "undecidable"


#: 门禁视角下的失败结论 (宁可红不可哑: 不可比/不可判都不许静默放行)
BASELINE_GATE_FAILURES = frozenset(
    {
        BaselineVerdict.SIGNIFICANTLY_WORSE,
        BaselineVerdict.NOT_COMPARABLE,
        BaselineVerdict.UNDECIDABLE,
    }
)


class BaselineTaskDelta(BaseModel):
    """逐 task 的新旧 pass@1 对照 — 诊断量, 不参与门判定 (design D2)。"""

    task_id: str
    baseline_pass1: float | None = Field(None, description="None = 该侧无有效样本")
    new_pass1: float | None = None
    baseline_ci: tuple[float, float] | None = None
    new_ci: tuple[float, float] | None = None
    direction: Literal["worse", "better", "flat"] | None = Field(
        None, description="按点估计读方向; 任一侧无值时为 None (不猜方向)"
    )
    significant: bool | None = Field(
        None, description="两侧区间是否不重叠; 任一侧缺区间时为 None (无法判定)"
    )


class BaselineComparison(BaseModel):
    """基线比较结论对象: CLI 退出码与 pytest terminal summary 消费同一结构。"""

    verdict: BaselineVerdict
    reason: str | None = Field(None, description="结论依据; not_comparable 时为不可比原因")
    new_run_id: str
    baseline_run_id: str
    baseline_pass1: float | None = None
    new_pass1: float | None = None
    baseline_ci: tuple[float, float] | None = None
    new_ci: tuple[float, float] | None = None
    task_deltas: list[BaselineTaskDelta] = Field(
        default_factory=list, description="逐 task 对照 (诊断呈现, 不进门判定)"
    )


# ─── run 级可比性前置 (自 api/routes/runs.py 纯移动, 行为零变化) ─────────────


def runs_comparable(run_a: RunResult, run_b: RunResult) -> tuple[bool, str | None]:
    """两个 run 之间是否允许出现任何方向性结论 (run 级前置)。

    覆盖: 统计口径版本一致、证据边界一致 (含环境身份), 以及两类历史行兜底 —
    缺统计口径版本或缺证据边界记录的 run 一律不可比 (宁可红不可哑)。

    与 :meth:`EvidenceBoundary.compare_with` 的分工见模块 docstring: 本函数
    是 run 级前置 (输入两个完整 run), ``compare_with`` 是边界级比较 (输入两份
    边界记录, 只回答记录自身是否一致)。可比性判定以本函数为唯一入口。
    """
    version_a = getattr(run_a, "statistics_version", None)
    version_b = getattr(run_b, "statistics_version", None)
    evidence_a = getattr(run_a, "evidence", None)
    evidence_b = getattr(run_b, "evidence", None)
    same_caliber = version_a is not None and version_a == version_b
    same_boundary, boundary_reason = _compare_evidence_boundaries(evidence_a, evidence_b)
    comparable = same_caliber and same_boundary
    return comparable, _not_comparable_reason(
        comparable, same_caliber, version_a, version_b, boundary_reason
    )


def _compare_evidence_boundaries(
    evidence_a: Any, evidence_b: Any
) -> tuple[bool, str | None]:
    """两个 run 的证据边界是否一致 (缺记录即无法判定可比)。"""
    if evidence_a is None and evidence_b is None:
        return False, "两个 run 均未记录证据采集边界 (历史 run), 无法判定可比性"
    if evidence_a is None or evidence_b is None:
        missing = "a" if evidence_a is None else "b"
        return False, f"run_{missing} 未记录证据采集边界 (历史 run), 无法判定可比性"
    return evidence_a.compare_with(evidence_b)


def _not_comparable_reason(
    comparable: bool,
    same_caliber: bool,
    version_a: str | None,
    version_b: str | None,
    boundary_reason: str | None,
) -> str | None:
    if comparable:
        return None
    if not same_caliber:
        return (
            "两个 run 的统计口径版本不同"
            if version_a is not None and version_b is not None
            else "至少一个 run 未记录统计口径版本 (历史 run), 无法判定可比性"
        )
    return boundary_reason or "两个 run 的证据采集边界不同"


# ─── 基线比较 helper (CLI run --baseline 与 pytest 插件共用, design D4) ──────


def _estimates_map(summary: RunSummary | TaskSummary | None) -> dict[int, PassKEstimate]:
    """pass@k 估计字典 → {int k: est} (容错落盘往返产生的 str key)。"""
    raw = (getattr(summary, "estimates", None) or {}) if summary else {}
    mapped: dict[int, PassKEstimate] = {}
    for k, est in raw.items():
        try:
            mapped[int(k)] = est
        except (TypeError, ValueError):
            continue
    return mapped


def measured_pass1(
    summary: RunSummary | TaskSummary | None,
    side: str,
) -> tuple[float | None, tuple[float, float] | None, str | None]:
    """实测 pass@1 及其 95% 区间; 不可用作门判定时返回 (None, None, 原因)。

    外推值与无有效样本都算「没有实测区间」— 门判定的量必须是观测, 不收
    二项外推的推论值 (缺失 ≠ 0 的同一条纪律)。
    """
    if summary is None:
        return None, None, f"{side} run 无汇总 (未完成), 无法判定"
    est = _estimates_map(summary).get(1)
    if est is None:
        return None, None, f"{side} run 未记录 pass@1 估计"
    if est.value is None:
        return None, None, f"{side} run 的 pass@1 无有效样本 (insufficient_data)"
    if est.extrapolated or est.method == "extrapolated":
        return est.value, None, (
            f"{side} run 的 pass@1 是外推值 (k 超出实测样本), 外推值不参与门判定"
        )
    if est.p_lower_bound is None or est.p_upper_bound is None:
        return est.value, None, f"{side} run 的 pass@1 无 95% 区间"
    return est.value, (est.p_lower_bound, est.p_upper_bound), None


def _task_pass1(ts: TaskSummary) -> tuple[float | None, tuple[float, float] | None]:
    value, ci, _ = measured_pass1(ts, "task")
    return value, ci


def _task_deltas(
    baseline_summary: RunSummary | None, new_summary: RunSummary | None
) -> list[BaselineTaskDelta]:
    """两侧都有的 task 的新旧 pass@1 对照 (诊断量, 升降不进门判定)。"""
    base_map = {ts.task_id: ts for ts in (baseline_summary.task_summaries if baseline_summary else [])}
    new_map = {ts.task_id: ts for ts in (new_summary.task_summaries if new_summary else [])}
    deltas: list[BaselineTaskDelta] = []
    for task_id in sorted(set(base_map) & set(new_map)):
        base_value, base_ci = _task_pass1(base_map[task_id])
        new_value, new_ci = _task_pass1(new_map[task_id])
        direction: Literal["worse", "better", "flat"] | None = None
        if base_value is not None and new_value is not None:
            if new_value < base_value:
                direction = "worse"
            elif new_value > base_value:
                direction = "better"
            else:
                direction = "flat"
        significant: bool | None = None
        if base_ci is not None and new_ci is not None:
            significant = base_ci[1] < new_ci[0] or new_ci[1] < base_ci[0]
        deltas.append(
            BaselineTaskDelta(
                task_id=task_id,
                baseline_pass1=base_value,
                new_pass1=new_value,
                baseline_ci=base_ci,
                new_ci=new_ci,
                direction=direction,
                significant=significant,
            )
        )
    return deltas


def compare_baseline(new_run: RunResult, baseline_run: RunResult) -> BaselineComparison:
    """基线比较: 先过 run 级可比性前置, 再按套件实测 pass@1 的 95% 区间判定。

    判定语义 (design D2, 与 compare 同一条): 区间不重叠 **且** 新值更低才算
    ``significantly_worse``; 区间重叠 → ``not_significant`` (不显著不等于没有
    变化); 新值更高且不重叠 → ``improved``。逐 task 升降只进诊断
    (:class:`BaselineTaskDelta`), 不参与门判定 — 门判套件级, 与绝对阈值门同量。

    不可比 → ``not_comparable`` + 原因 (拒绝硬比); 可比但任一侧缺实测区间
    (全外推/无有效样本) → ``undecidable`` + 原因 (外推值不参与门判定)。
    """
    comparable, reason = runs_comparable(new_run, baseline_run)
    if not comparable:
        return BaselineComparison(
            verdict=BaselineVerdict.NOT_COMPARABLE,
            reason=reason,
            new_run_id=new_run.run_id,
            baseline_run_id=baseline_run.run_id,
        )

    baseline_value, baseline_ci, base_unavailable = measured_pass1(
        baseline_run.summary, "基线"
    )
    new_value, new_ci, new_unavailable = measured_pass1(new_run.summary, "新")
    unavailable = [u for u in (base_unavailable, new_unavailable) if u]
    if unavailable:
        return BaselineComparison(
            verdict=BaselineVerdict.UNDECIDABLE,
            reason="; ".join(unavailable),
            new_run_id=new_run.run_id,
            baseline_run_id=baseline_run.run_id,
            baseline_pass1=baseline_value,
            new_pass1=new_value,
            baseline_ci=baseline_ci,
            new_ci=new_ci,
            task_deltas=_task_deltas(baseline_run.summary, new_run.summary),
        )

    assert baseline_ci is not None and new_ci is not None  # 上一分支已排除
    if new_ci[1] < baseline_ci[0]:
        verdict = BaselineVerdict.SIGNIFICANTLY_WORSE
        reason = (
            f"新 run 实测 pass@1 {new_value:.1%} [95% CI {new_ci[0]:.1%}..{new_ci[1]:.1%}] "
            f"整体低于基线 {baseline_value:.1%} "
            f"[95% CI {baseline_ci[0]:.1%}..{baseline_ci[1]:.1%}] "
            "(区间不重叠且方向向下)"
        )
    elif new_ci[0] > baseline_ci[1]:
        verdict = BaselineVerdict.IMPROVED
        reason = (
            f"新 run 实测 pass@1 {new_value:.1%} [95% CI {new_ci[0]:.1%}..{new_ci[1]:.1%}] "
            f"整体高于基线 {baseline_value:.1%} "
            f"[95% CI {baseline_ci[0]:.1%}..{baseline_ci[1]:.1%}] "
            "(区间不重叠且方向向上)"
        )
    else:
        verdict = BaselineVerdict.NOT_SIGNIFICANT
        reason = (
            f"两 run 的 pass@1 95% 区间重叠 "
            f"(基线 {baseline_value:.1%} [{baseline_ci[0]:.1%}..{baseline_ci[1]:.1%}], "
            f"新 {new_value:.1%} [{new_ci[0]:.1%}..{new_ci[1]:.1%}]) — "
            "差异落在噪声内, 不构成显著变差; 「不显著」不等于「没有变化」"
        )

    return BaselineComparison(
        verdict=verdict,
        reason=reason,
        new_run_id=new_run.run_id,
        baseline_run_id=baseline_run.run_id,
        baseline_pass1=baseline_value,
        new_pass1=new_value,
        baseline_ci=baseline_ci,
        new_ci=new_ci,
        task_deltas=_task_deltas(baseline_run.summary, new_run.summary),
    )


# ─── 统一文案 (CLI 与 pytest 插件复用, design D4) ────────────────────────────


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _ci_text(ci: tuple[float, float] | None) -> str:
    if ci is None:
        return "n/a"
    return f"[95% CI {ci[0] * 100:.1f}%..{ci[1] * 100:.1f}%]"


def format_baseline_report(comparison: BaselineComparison) -> list[str]:
    """基线比较结论的统一文案 (CLI run --baseline 与 pytest 插件 terminal
    summary 打印同一份; 纯字符串, 不做任何 I/O)。"""
    lines = [
        f"Baseline: new {comparison.new_run_id} vs baseline {comparison.baseline_run_id}",
        (
            f"  pass@1: baseline {_pct(comparison.baseline_pass1)} "
            f"{_ci_text(comparison.baseline_ci)}  →  new {_pct(comparison.new_pass1)} "
            f"{_ci_text(comparison.new_ci)}"
        ),
        f"  verdict: {comparison.verdict.value} — {comparison.reason or 'n/a'}",
    ]
    if comparison.task_deltas:
        lines.append("  Task deltas (diagnostic only, not part of the gate):")
        for delta in comparison.task_deltas:
            if delta.significant is None:
                significance = "significance undetermined"
            elif delta.significant:
                significance = "significant"
            else:
                significance = "not significant"
            direction = delta.direction or "direction unknown"
            lines.append(
                f"    - {delta.task_id}: "
                f"baseline {_pct(delta.baseline_pass1)} {_ci_text(delta.baseline_ci)} → "
                f"new {_pct(delta.new_pass1)} {_ci_text(delta.new_ci)}  "
                f"{direction} ({significance})"
            )
    return lines
