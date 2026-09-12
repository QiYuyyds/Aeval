"""基线比较 helper 单测 (变更⑥ tasks 2.1–2.3)。

四条 verdict 路径用已知区间构造 (spec: statistics 的场景区间直接入测);
缺实测区间 (全外推/无有效样本) 按不可判处理并给原因; 历史 run (无证据
边界记录) 与今日 run 比较一律 not_comparable 且原因点名「未记录」。
"""

from __future__ import annotations

from agent_eval.core.comparison import (
    BASELINE_GATE_FAILURES,
    BaselineVerdict,
    compare_baseline,
    format_baseline_report,
    runs_comparable,
)
from agent_eval.core.types import (
    STATISTICS_VERSION,
    EvidenceBoundary,
    PassKEstimate,
    RunResult,
    RunSummary,
    TaskSummary,
)

# ── 构造器: 手写已知区间的 run (不跑真套件, 判定逻辑纯函数可直测) ───────────


def make_boundary(**overrides) -> EvidenceBoundary:
    fields = dict(
        capture_tool_arguments=False,
        capture_model_content=False,
        spec_version="test-spec-1",
        mapping_version="test-mapping-1",
        redactor_identifier="default",
        redactor_version="1",
        environment_identity="none",
    )
    fields.update(overrides)
    return EvidenceBoundary(**fields)


def make_est(k: int, value: float | None, ci=None, *, extrapolated=False) -> PassKEstimate:
    if value is None:
        return PassKEstimate(k=k, n=0, successes=0, value=None)
    low, high = ci if ci else (max(0.0, value - 0.1), min(1.0, value + 0.1))
    return PassKEstimate(
        k=k,
        n=10,
        successes=round(value * 10),
        value=value,
        method="extrapolated" if extrapolated else "measured",
        extrapolated=extrapolated,
        p_point=value,
        p_lower_bound=low,
        p_upper_bound=high,
    )


def make_run(
    run_id: str,
    *,
    pass1: float | None = 0.7,
    ci=(0.62, 0.85),
    extrapolated=False,
    statistics_version: str | None = STATISTICS_VERSION,
    evidence: EvidenceBoundary | None = make_boundary(),
    tasks: dict[str, tuple[float | None, tuple[float, float] | None]] | None = None,
) -> RunResult:
    estimates = {1: make_est(1, pass1, ci, extrapolated=extrapolated)}
    task_summaries = [
        TaskSummary(task_id=tid, total_trials=10, estimates={1: make_est(1, value, task_ci)})
        for tid, (value, task_ci) in (tasks or {}).items()
    ]
    return RunResult(
        run_id=run_id,
        suite_name="suite",
        status="completed",
        statistics_version=statistics_version,
        evidence=evidence,
        summary=RunSummary(
            total_tasks=len(task_summaries),
            total_trials=10 * len(task_summaries),
            pass_at_k={1: pass1},
            estimates=estimates,
            task_summaries=task_summaries,
        ),
    )


# ── 四条 verdict 路径 (已知区间, spec: statistics 场景值入测) ─────────────────


class TestVerdictPaths:
    def test_disjoint_intervals_and_lower_is_significantly_worse(self):
        """Scenario: 区间不重叠且新值更低 [0.62, 0.85] → [0.30, 0.55]。"""
        baseline = make_run("run_base", pass1=0.7, ci=(0.62, 0.85))
        new = make_run("run_new", pass1=0.4, ci=(0.30, 0.55))
        comparison = compare_baseline(new, baseline)
        assert comparison.verdict is BaselineVerdict.SIGNIFICANTLY_WORSE
        assert comparison.baseline_ci == (0.62, 0.85)
        assert comparison.new_ci == (0.30, 0.55)
        assert "不重叠" in comparison.reason and "向下" in comparison.reason
        assert comparison.verdict in BASELINE_GATE_FAILURES

    def test_overlapping_intervals_reject_direction(self):
        """Scenario: 区间重叠 ([0.10, 0.90] 对 [0.34, 1.0]) → 不构成显著变差。"""
        baseline = make_run("run_base", pass1=0.5, ci=(0.10, 0.90))
        new = make_run("run_new", pass1=0.7, ci=(0.34, 1.0))
        comparison = compare_baseline(new, baseline)
        assert comparison.verdict is BaselineVerdict.NOT_SIGNIFICANT
        assert comparison.verdict not in BASELINE_GATE_FAILURES
        # 「不显著」不被读成「没有变化」: 两区间如实呈现
        assert comparison.baseline_ci == (0.10, 0.90)
        assert comparison.new_ci == (0.34, 1.0)
        assert "噪声" in comparison.reason

    def test_disjoint_intervals_and_higher_is_improved(self):
        baseline = make_run("run_base", pass1=0.4, ci=(0.30, 0.55))
        new = make_run("run_new", pass1=0.7, ci=(0.62, 0.85))
        comparison = compare_baseline(new, baseline)
        assert comparison.verdict is BaselineVerdict.IMPROVED
        assert comparison.verdict not in BASELINE_GATE_FAILURES
        assert "向上" in comparison.reason

    def test_not_comparable_when_statistics_version_differs(self):
        old_caliber = make_run("run_old", statistics_version="1")
        comparison = compare_baseline(make_run("run_new"), old_caliber)
        assert comparison.verdict is BaselineVerdict.NOT_COMPARABLE
        assert "统计口径版本不同" in comparison.reason
        assert comparison.baseline_ci is None and comparison.new_ci is None
        assert comparison.verdict in BASELINE_GATE_FAILURES
        # 反向也要一致 (前置与方向无关)
        assert compare_baseline(old_caliber, make_run("run_new")).verdict is BaselineVerdict.NOT_COMPARABLE

    def test_not_comparable_when_environment_identity_differs(self):
        baseline = make_run("run_base", evidence=make_boundary(environment_identity="env-a"))
        new = make_run("run_new", evidence=make_boundary(environment_identity="env-b"))
        comparison = compare_baseline(new, baseline)
        assert comparison.verdict is BaselineVerdict.NOT_COMPARABLE
        assert "环境身份不同" in comparison.reason


class TestUndecidablePaths:
    def test_extrapolated_value_does_not_enter_the_gate(self):
        """外推值不参与门判定: 按不可判处理并给原因 (tasks 2.2)。"""
        baseline = make_run("run_base", pass1=0.7, ci=(0.62, 0.85))
        new = make_run("run_new", pass1=0.9, ci=(0.8, 1.0), extrapolated=True)
        comparison = compare_baseline(new, baseline)
        assert comparison.verdict is BaselineVerdict.UNDECIDABLE
        assert "外推" in comparison.reason
        # 结论对象仍如实带上两侧量, 但不给方向
        assert comparison.new_pass1 == 0.9
        assert comparison.verdict in BASELINE_GATE_FAILURES

    def test_no_valid_samples_is_undecidable(self):
        baseline = make_run("run_base", pass1=None, ci=None)
        comparison = compare_baseline(make_run("run_new"), baseline)
        assert comparison.verdict is BaselineVerdict.UNDECIDABLE
        assert "基线" in comparison.reason and "insufficient_data" in comparison.reason

    def test_undecidable_on_both_sides_lists_both_reasons(self):
        baseline = make_run("run_base", pass1=None, ci=None)
        new = make_run("run_new", pass1=None, ci=None)
        comparison = compare_baseline(new, baseline)
        assert comparison.verdict is BaselineVerdict.UNDECIDABLE
        assert comparison.reason.count("insufficient_data") == 2


class TestHistoricalRows:
    def test_historical_run_without_boundary_is_not_comparable(self):
        """历史 run (无证据边界记录) 与今日 run 比较 → not_comparable, 点名「未记录」。"""
        historical = make_run("run_hist", evidence=None)
        comparison = compare_baseline(make_run("run_new"), historical)
        assert comparison.verdict is BaselineVerdict.NOT_COMPARABLE
        assert "未记录" in comparison.reason

    def test_historical_run_without_statistics_version_is_not_comparable(self):
        historical = make_run("run_hist", statistics_version=None)
        comparison = compare_baseline(make_run("run_new"), historical)
        assert comparison.verdict is BaselineVerdict.NOT_COMPARABLE
        assert "未记录" in comparison.reason

    def test_runs_comparable_agrees_with_the_helper(self):
        """前置函数与 helper 在同一输入上结论一致 (不出第二套判定)。"""
        ok_a, ok_b = make_run("a"), make_run("b")
        assert runs_comparable(ok_a, ok_b) == (True, None)
        bad = make_run("c", statistics_version="1")
        comparable, reason = runs_comparable(ok_a, bad)
        assert comparable is False
        assert "统计口径版本不同" in reason


class TestTaskDiagnostics:
    def test_task_deltas_are_diagnostic_only(self):
        baseline = make_run(
            "run_base",
            pass1=0.7,
            ci=(0.62, 0.85),
            tasks={
                "t_worse": (0.7, (0.62, 0.85)),
                "t_better": (0.3, (0.30, 0.55)),
                "t_noise": (0.5, (0.30, 0.70)),
            },
        )
        new = make_run(
            "run_new",
            pass1=0.4,
            ci=(0.30, 0.55),
            tasks={
                "t_worse": (0.3, (0.30, 0.55)),
                "t_better": (0.7, (0.62, 0.85)),
                "t_noise": (0.4, (0.25, 0.55)),
            },
        )
        comparison = compare_baseline(new, baseline)
        # 逐 task 升降不改变套件级门判定: 上例 t_better/t_noise 存在仍判变差
        assert comparison.verdict is BaselineVerdict.SIGNIFICANTLY_WORSE
        by_id = {d.task_id: d for d in comparison.task_deltas}
        assert by_id["t_worse"].direction == "worse" and by_id["t_worse"].significant
        assert by_id["t_better"].direction == "better" and by_id["t_better"].significant
        assert by_id["t_noise"].direction == "worse" and not by_id["t_noise"].significant

    def test_task_missing_on_one_side_is_dropped(self):
        baseline = make_run("run_base", tasks={"t_shared": (0.5, (0.3, 0.7)), "t_only_base": (0.5, (0.3, 0.7))})
        new = make_run("run_new", tasks={"t_shared": (0.5, (0.3, 0.7)), "t_only_new": (0.5, (0.3, 0.7))})
        comparison = compare_baseline(new, baseline)
        assert [d.task_id for d in comparison.task_deltas] == ["t_shared"]


class TestReport:
    def test_format_baseline_report_lines(self):
        baseline = make_run(
            "run_base", pass1=0.7, ci=(0.62, 0.85), tasks={"t1": (0.7, (0.62, 0.85))}
        )
        new = make_run("run_new", pass1=0.4, ci=(0.30, 0.55), tasks={"t1": (0.3, (0.30, 0.55))})
        lines = format_baseline_report(compare_baseline(new, baseline))
        text = "\n".join(lines)
        assert "Baseline: new run_new vs baseline run_base" in text
        assert "baseline 70.0% [95% CI 62.0%..85.0%]" in text
        assert "new 40.0% [95% CI 30.0%..55.0%]" in text
        assert "verdict: significantly_worse" in text
        assert "- t1: baseline 70.0%" in text and "worse (significant)" in text
        assert "diagnostic only, not part of the gate" in text

    def test_not_comparable_report_has_no_task_block(self):
        historical = make_run("run_hist", evidence=None)
        lines = format_baseline_report(compare_baseline(make_run("run_new"), historical))
        assert "verdict: not_comparable" in "\n".join(lines)
        assert not any("Task deltas" in line for line in lines)
