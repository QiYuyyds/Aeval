"""④ agent 指标目录 — 角色 / 门 / κ-α 的运行时与端到端测试。

覆盖:
- specs/llm-metrics: 轨迹指标默认仅诊断 (诊断块不进分母 / 显式升格进判分)
- specs/graders: reward_basis 乘性安全门 (塌缩 / 不冒充生效 / additive 不变)
- specs/statistics: κ/α 对照表 + 多评分者按 trial 对齐 + 单评分者不可计算
- 历史 run 汇总回读兼容 (无新字段读为空 / None, 不报错)
"""

import pytest
from pydantic import ValidationError

from agent_eval.core.metrics import (
    MIN_ALIGNED_RATINGS_FOR_AGREEMENT,
    agreement_report,
    cohen_kappa,
    krippendorff_alpha,
)
from agent_eval.core.runner import EvalRunner
from agent_eval.core.types import (
    AgreementReport,
    DiagnosticMetricSummary,
    EvalSuite,
    EvalTask,
    GateSpec,
    GraderConfig,
    GraderResult,
    GraderType,
    InvalidReason,
    JudgeDefinition,
    MeasurementContext,
    MetricRole,
    ObservedBy,
    RunResult,
    RunSummary,
    ScoreStrategy,
    TrialResult,
    TrialVerdict,
)
from agent_eval.examples.mock_runner import MockAgentRunner, MockTraceProvider
from agent_eval.metrics.base import Metric, MetricResult
from agent_eval.storage.memory import MemoryStorage


def make_runner(**kwargs) -> EvalRunner:
    defaults = dict(
        agent_runner=MockAgentRunner(latency_range=(0.0, 0.01)),
        trace_provider=MockTraceProvider(),
        storage=MemoryStorage(),
    )
    defaults.update(kwargs)
    return EvalRunner(**defaults)


class TurnCountDiagnostic(Metric):
    """轨迹诊断指标 (确定性, 无 LLM): transcript 消息数 ≥3 记 1.0, 否则 0.5"""

    name = "turn_count"
    threshold = 0.5
    evidence_levels = ("transcript",)
    role = MetricRole.DIAGNOSTIC

    async def measure(self, ctx: MeasurementContext) -> MetricResult:
        n = len(ctx.messages())
        return MetricResult(
            name=self.name,
            score=1.0 if n >= 3 else 0.5,
            reason=f"{n} messages",
            threshold=self.threshold,
        )


def code_task(**overrides) -> EvalTask:
    fields = dict(
        id="t1",
        prompt="What files are in the workspace?",
        max_trials=3,
        graders=[GraderConfig(
            type=GraderType.CODE,
            name="code_based",
            config={"checks": [{"type": "contains", "target": "transcript",
                                "value": "Mock response"}]},
        )],
    )
    fields.update(overrides)
    return EvalTask(**fields)


# ─── 3.x 诊断 / 判分角色轴 (端到端) ───────────────────────────────────────────


class TestDiagnosticRoleAxis:
    async def test_diagnostic_block_present_and_denominators_unchanged(self):
        """启用诊断指标: 分值进诊断块; 通过率/pass^k/分母与未启用逐字段一致"""
        runner_with = make_runner(metrics_registry={"turn_count": TurnCountDiagnostic()})
        runner_without = make_runner(metrics_registry={"turn_count": TurnCountDiagnostic()})

        suite_with = EvalSuite(name="s", tasks=[code_task(diagnostic_metrics=["turn_count"])])
        suite_without = EvalSuite(name="s", tasks=[code_task()])

        run_with = await runner_with.run_suite(suite_with)
        run_without = await runner_without.run_suite(suite_without)

        s_with, s_without = run_with.summary, run_without.summary
        # 分母与通过率逐字段一致 (诊断量不进分母 —— 结构性保证)
        assert s_with.valid_trials == s_without.valid_trials
        assert s_with.invalid_trials == s_without.invalid_trials
        assert s_with.pass_at_k == s_without.pass_at_k
        assert s_with.pass_power_k == s_without.pass_power_k
        assert s_with.avg_score == s_without.avg_score
        assert s_with.score_distribution.mean == s_without.score_distribution.mean
        assert s_with.failures == s_without.failures

        # 诊断块: 分值 + 分布摘要
        block = s_with.diagnostic_metrics
        assert [d.name for d in block] == ["turn_count"]
        d = block[0]
        assert d.n == 3  # 每 trial 一个有效分值
        assert d.avg is not None and d.min is not None and d.max is not None
        assert d.p50 is not None
        assert d.errors == 0
        # 未启用的 run 诊断块为空
        assert s_without.diagnostic_metrics == []

    async def test_promoted_metric_moves_to_judging(self):
        """升格: 判据引用指标为判分量 → 经 grader 适配进判分, 结论携带取信声明"""
        registry = {"turn_count": TurnCountDiagnostic()}
        runner = make_runner(metrics_registry=registry)
        task = code_task(
            max_trials=1,
            graders=[
                GraderConfig(
                    type=GraderType.METRIC,
                    name="turn_count",
                    config={"threshold": 0.5},
                ),
                GraderConfig(
                    type=GraderType.CODE,
                    name="code_based",
                    config={"checks": [{"type": "contains", "target": "transcript",
                                        "value": "Mock response"}]},
                ),
            ],
        )
        run = await runner.run_suite(EvalSuite(name="s", tasks=[task]))

        trial = run.trials["t1"][0]
        metric_results = [g for g in trial.grader_results if g.grader_name == "turn_count"]
        assert len(metric_results) == 1
        result = metric_results[0]
        assert result.grader_type is GraderType.METRIC
        # 结论携带指标的角色与取信声明 (spec: 升格后按同一套声明与门机制运作)
        assert result.details["metric_role"] == "diagnostic"
        assert result.details["metric_evidence_declaration"] == ["transcript"]
        # 升格后不再出现在诊断块 (判分块取而代之)
        assert run.summary.diagnostic_metrics == []

    async def test_unknown_diagnostic_metric_recorded_as_error(self):
        """诊断块记录未知指标为计算失败 (不冒充 0 分, 也不 crash run)"""
        runner = make_runner(metrics_registry={})
        run = await runner.run_suite(
            EvalSuite(name="s", tasks=[code_task(max_trials=1, diagnostic_metrics=["ghost"])])
        )
        block = run.summary.diagnostic_metrics
        assert [d.name for d in block] == ["ghost"]
        assert block[0].n == 0 and block[0].avg is None
        assert block[0].errors == 1

    def test_history_summary_readback_compat(self):
        """历史 run 汇总回读: 无诊断块/κ/α/门字段 → 空与 None, 不报错"""
        old = RunSummary.model_validate({
            "total_tasks": 1,
            "total_trials": 3,
            "pass_at_k": {"1": 1.0},
            "pass_power_k": {"1": 1.0},
        })
        assert old.diagnostic_metrics == []
        assert old.agreement == {}
        # 旧结论读回: 门字段缺省为 None/False
        old_result = GraderResult.model_validate({
            "grader_name": "g", "grader_type": "code", "score": 1.0, "passed": True,
        })
        assert old_result.gate_factor is None
        assert old_result.gate_applied is False
        assert old_result.rater_scores == {}
        old_trial = TrialResult.model_validate({
            "trial_index": 0, "grader_results": [], "success": True,
        })
        assert old_trial.diagnostic_results == []


# ─── 4.x reward_basis 乘性安全门 ─────────────────────────────────────────────


class ScriptedGrader:
    """可编排通过/失败/无效的确定性评分器 (门与多评分者测试用)"""

    implementation_version = "1"
    evidence_levels = (ObservedBy.HARNESS, ObservedBy.RUNNER)

    def __init__(self, name: str, script: dict):
        self.name = name
        self.script = script  # judge_key → 判定序列 (末条重复)
        self.calls: list[str] = []

    async def grade(self, trial, spans, task, context=None):
        key = self.name
        grader_name = self.name
        if context is not None and context.grader_config is not None:
            key = context.grader_config.config.get("judge_key", self.name)
            grader_name = context.grader_config.name
        self.calls.append(key)
        outcomes = self.script[key]
        outcome = outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
        if outcome == "invalid":
            return GraderResult(
                grader_name=grader_name,
                grader_type=GraderType.CODE,
                score=0.0,
                passed=False,
                explanation="证据不可用, 无法判定",
                verdict=TrialVerdict.INVALID,
                invalid_reason=InvalidReason.EVIDENCE_UNAVAILABLE,
            )
        passed = outcome == "pass"
        return GraderResult(
            grader_name=grader_name,
            grader_type=GraderType.CODE,
            score=0.9 if passed else 0.1,
            passed=passed,
            explanation="scripted",
        )


def gate_task(*, basis, gate_factor, strategy=ScoreStrategy.WEIGHTED,
              max_trials: int = 1) -> EvalTask:
    return EvalTask(
        id="t1",
        prompt="p",
        max_trials=max_trials,
        score_strategy=strategy,
        reward_basis=basis,
        graders=[
            GraderConfig(
                type=GraderType.CODE,
                name="base",
                weight=1.0,
            ),
            GraderConfig(
                type=GraderType.CODE,
                name="safety",
                gate=GateSpec(factor=gate_factor),
            ),
        ],
    )


async def run_gate(script: dict, *, basis, gate_factor, strategy=ScoreStrategy.WEIGHTED):
    base = ScriptedGrader("base", {"base": list(script["base"])})
    safety = ScriptedGrader("safety", {"safety": list(script["safety"])})
    runner = make_runner(graders=[base, safety])
    run = await runner.run_suite(
        EvalSuite(name="s", tasks=[gate_task(basis=basis, gate_factor=gate_factor,
                                             strategy=strategy)])
    )
    trial = run.trials["t1"][0]
    return trial, run


class TestRewardBasisGates:
    async def test_failed_gate_collapses_total_score(self):
        """门失败塌缩总分: 基础 0.9 × 失败硬门 (factor 0) = 0, 与其他维度无关"""
        trial, run = await run_gate(
            {"base": ["pass"], "safety": ["fail"]}, basis="multiplicative", gate_factor=0.0
        )
        safety = next(g for g in trial.grader_results if g.grader_name == "safety")
        assert safety.gate_factor == 0.0
        assert safety.gate_applied is True
        assert "门失败" in safety.gate_reason
        # 塌缩后的总分驱动 success (0 < threshold 0.7)
        assert trial.success is False
        # 汇总读到的加权分也是塌缩后的
        assert run.summary.task_summaries[0].avg_score == 0.0

    async def test_soft_gate_partial_collapse(self):
        """软门 (factor 0.5): 基础 0.9 × 0.5 = 0.45"""
        _, run = await run_gate(
            {"base": ["pass"], "safety": ["fail"]}, basis="multiplicative", gate_factor=0.5
        )
        assert abs(run.summary.task_summaries[0].avg_score - 0.45) < 1e-9

    async def test_passed_gate_does_not_multiply(self):
        """门通过: 因子不乘入, 总分 = 基础分"""
        trial, _ = await run_gate(
            {"base": ["pass"], "safety": ["pass"]}, basis="multiplicative", gate_factor=0.0
        )
        safety = next(g for g in trial.grader_results if g.grader_name == "safety")
        assert safety.gate_applied is False
        assert "门通过" in safety.gate_reason
        assert trial.success is True

    async def test_evidence_shortfall_gate_never_pretends(self):
        """门证据不足不冒充生效: 评测侧无有效判定 → 不乘因子并标注原因"""
        trial, _ = await run_gate(
            {"base": ["pass"], "safety": ["invalid"]},
            basis="multiplicative", gate_factor=0.0,
        )
        safety = next(g for g in trial.grader_results if g.grader_name == "safety")
        assert safety.gate_applied is False
        assert "门未生效" in safety.gate_reason
        assert "证据不足" in safety.gate_reason
        # 证据缺失走 ③ 的同一套 invalid 语义 (不占分母, 不算 agent 失败)
        assert safety.verdict is TrialVerdict.INVALID

    async def test_additive_suite_scores_unchanged_gate_marked_inactive(self):
        """additive 套件分数与 0.2.0 口径一致; 门字段明确标为未启用"""
        trial, run = await run_gate(
            {"base": ["pass"], "safety": ["fail"]}, basis="additive", gate_factor=0.0
        )
        # additive: 门判据按普通判据参与加权 (0.9 + 0.1) / 2 = 0.5
        assert abs(run.summary.task_summaries[0].avg_score - 0.5) < 1e-9
        safety = next(g for g in trial.grader_results if g.grader_name == "safety")
        assert safety.gate_factor == 0.0
        assert safety.gate_applied is False
        assert "additive" in safety.gate_reason

    async def test_gate_all_pass_strategy_multiplicative(self):
        """all_pass + multiplicative: 非门全过基础分 1.0, 失败硬门塌为 0"""
        trial, _ = await run_gate(
            {"base": ["pass"], "safety": ["fail"]},
            basis="multiplicative", gate_factor=0.0, strategy=ScoreStrategy.ALL_PASS,
        )
        assert trial.success is False
        trial_ok, _ = await run_gate(
            {"base": ["pass"], "safety": ["pass"]},
            basis="multiplicative", gate_factor=0.0, strategy=ScoreStrategy.ALL_PASS,
        )
        assert trial_ok.success is True

    async def test_suite_level_reward_basis_inherited(self):
        """suite 级 multiplicative 下渗到未显式声明的 task (run 级声明)"""
        runner = make_runner(graders=[
            ScriptedGrader("base", {"base": ["pass"]}),
            ScriptedGrader("safety", {"safety": ["fail"]}),
        ])
        suite = EvalSuite(
            name="s",
            reward_basis="multiplicative",
            tasks=[EvalTask(
                id="t1", prompt="p", max_trials=1,
                graders=[
                    GraderConfig(type=GraderType.CODE, name="base", weight=1.0),
                    GraderConfig(type=GraderType.CODE, name="safety",
                                 gate=GateSpec(factor=0.0)),
                ],
            )],
        )
        run = await runner.run_suite(suite)
        assert run.summary.task_summaries[0].avg_score == 0.0

    def test_gate_requires_no_subject_escape(self):
        """门判据不得放行被评方自报证据单独支撑 (subject 级证据不得触发门)"""
        with pytest.raises(ValidationError) as exc:
            GraderConfig(
                type=GraderType.CODE, name="safety",
                gate=GateSpec(factor=0.0), allow_subject=True,
            )
        assert "subject" in str(exc.value)

    def test_multiplicative_requires_non_gate_criterion(self):
        with pytest.raises(ValidationError) as exc:
            EvalTask(
                id="t", prompt="p",
                graders=[GraderConfig(type=GraderType.CODE, name="only",
                                      gate=GateSpec(factor=0.5))],
                reward_basis="multiplicative",
            )
        assert "非门判据" in str(exc.value)

    def test_suite_multiplicative_requires_non_gate_per_task(self):
        with pytest.raises(ValidationError) as exc:
            EvalSuite(
                name="s", reward_basis="multiplicative",
                tasks=[EvalTask(
                    id="t", prompt="p",
                    graders=[GraderConfig(type=GraderType.CODE, name="only",
                                          gate=GateSpec(factor=0.5))],
                )],
            )
        assert "非门判据" in str(exc.value)

    def test_factor_range_validated(self):
        with pytest.raises(ValidationError):
            GraderConfig(type=GraderType.CODE, name="g", gate=GateSpec(factor=1.5))
        with pytest.raises(ValidationError):
            GraderConfig(type=GraderType.CODE, name="g", gate=GateSpec(factor=-0.1))

    async def test_gate_uses_same_evidence_machinery(self):
        """门的取信声明走 ③ 的同一套机制: subject-only 的通过不触发门 (4.5)"""
        from agent_eval.graders._evidence import enforce_evidence_policy

        config = GraderConfig(
            type=GraderType.CODE, name="safety",
            gate=GateSpec(factor=0.0),
            evidence=[ObservedBy.HARNESS, ObservedBy.RUNNER],
        )
        grader = ScriptedGrader("safety", {"base": ["pass"]})
        subject_only = GraderResult(
            grader_name="safety", grader_type=GraderType.CODE,
            score=1.0, passed=True, explanation="subject said so",
            evidence_levels=[ObservedBy.SUBJECT],
        )
        enforced = enforce_evidence_policy(subject_only, config, grader)
        # ③ 的默认规则把 subject-only 通过判 invalid → 门未生效, 不乘因子
        assert enforced.verdict is TrialVerdict.INVALID
        annotated = EvalRunner.__new__(EvalRunner)._annotate_gate(
            enforced, config, "multiplicative"
        )
        assert annotated.gate_applied is False
        assert "门未生效" in annotated.gate_reason


# ─── 5.x 跨评分者一致性 κ/α ──────────────────────────────────────────────────


class TestAgreementPureFunctions:
    def test_cohen_kappa_known_table(self):
        """文献经典 2x2 表 [[20,5],[10,15]] → κ = 0.4 (Wikipedia Cohen's kappa)"""
        a = ["yes"] * 20 + ["yes"] * 5 + ["no"] * 10 + ["no"] * 15
        b = ["yes"] * 20 + ["no"] * 5 + ["yes"] * 10 + ["no"] * 15
        assert round(cohen_kappa(a, b), 10) == 0.4

    def test_cohen_kappa_perfect_and_degenerate(self):
        assert cohen_kappa(["a", "b", "c"], ["a", "b", "c"]) == 1.0
        # 两人都只判同一类: p_e = 1 → 定义无意义, 完全一致读作 1.0
        assert cohen_kappa(["a", "a"], ["a", "a"]) == 1.0

    def test_krippendorff_alpha_with_missing_hand_computed(self):
        """含缺失评分的 α (手算对照: n'=11, D_o=6/11, D_e=28/55 → -1/14)"""
        units = [
            ["a", "a", "b"],
            ["a", "b", "b"],
            ["a", None, "b"],
            ["a", "a", "a"],
        ]
        assert abs(krippendorff_alpha(units, "nominal") - (-1 / 14)) < 1e-12

    def test_krippendorff_alpha_ordinal_hand_computed(self):
        """ordinal α 手算对照 (累积边缘平方差; 单元 [12][23][13][22] → -11/38)"""
        units = [["1", "2"], ["2", "3"], ["1", "3"], ["2", "2"]]
        assert abs(krippendorff_alpha(units, "ordinal") - (-11 / 38)) < 1e-12
        # 同一数据 nominal α = -0.05
        assert abs(krippendorff_alpha(units, "nominal") - (-0.05)) < 1e-12

    def test_alpha_two_categories_ordinal_equals_nominal(self):
        two = [["p", "p"], ["p", "f"], ["f", "f"], ["f", "p"], ["p", "p"]]
        assert krippendorff_alpha(two, "ordinal") == krippendorff_alpha(two, "nominal")

    def test_alpha_perfect_agreement(self):
        assert krippendorff_alpha([["x", "x"], ["y", "y"], ["x", "x"]]) == 1.0

    def test_agreement_report_single_rater_insufficient(self):
        report = agreement_report(raters=["solo"], units=[{"solo": "pass"}])
        assert report.measure is None and report.value is None
        assert "评分者不足" in report.reason

    def test_agreement_report_too_few_aligned_samples(self):
        units = [{"a": "pass", "b": "fail"}] * (MIN_ALIGNED_RATINGS_FOR_AGREEMENT - 1)
        report = agreement_report(raters=["a", "b"], units=units)
        assert report.measure is None and report.value is None
        assert "对齐样本过少" in report.reason
        assert report.aligned_samples == MIN_ALIGNED_RATINGS_FOR_AGREEMENT - 1

    def test_agreement_report_kappa_two_raters(self):
        units = [{"a": "pass", "b": "pass"}, {"a": "fail", "b": "fail"},
                 {"a": "pass", "b": "pass"}, {"a": "fail", "b": "fail"},
                 {"a": "pass", "b": "fail"}]
        report = agreement_report(raters=["a", "b"], units=units)
        assert report.measure == "cohen_kappa"
        assert report.agree == 4 and report.disagree == 1
        expected = cohen_kappa(["pass", "fail", "pass", "fail", "pass"],
                               ["pass", "fail", "pass", "fail", "fail"])
        assert abs(report.value - expected) < 1e-12

    def test_agreement_report_alpha_with_missing(self):
        """含缺失评分的 α: 缺失单元不冒充一致或分歧; 仍有足够对齐样本"""
        units = [
            {"a": "pass", "b": "pass", "c": "pass"},
            {"a": "fail", "b": "fail", "c": None},  # c 缺失, a+b 仍可配对
            {"a": "pass", "b": "pass", "c": "fail"},
            {"a": "fail", "b": "fail", "c": "fail"},
            {"a": "pass", "b": "fail", "c": "pass"},
        ]
        report = agreement_report(raters=["a", "b", "c"], units=units)
        assert report.measure == "krippendorff_alpha"
        assert report.value is not None
        assert report.raters == 3
        assert report.aligned_samples == 5


class TestMultiRaterRunner:
    @staticmethod
    def panel_task(max_trials: int, script: dict) -> tuple[EvalRunner, EvalTask, ScriptedGrader]:
        grader = ScriptedGrader("panel", script)
        runner = make_runner(graders=[grader])
        task = EvalTask(
            id="t1", prompt="p", max_trials=max_trials,
            graders=[GraderConfig(
                type=GraderType.CODE, name="panel",
                config={"judge_key": "a"},
                judges=[
                    JudgeDefinition(name="judge-a", config={"judge_key": "a"}),
                    JudgeDefinition(name="judge-b", config={"judge_key": "b"}),
                ],
            )],
        )
        return runner, task, grader

    async def test_two_raters_report_kappa(self):
        """判据配置两个独立 judge → 汇总报告 κ 与一致/分歧计数 (5.2/5.3)"""
        runner, task, _ = self.panel_task(
            5,
            # judge-a 恒 pass; judge-b 5 个 trial 里 1 个分歧
            {"a": ["pass"] * 5, "b": ["pass", "pass", "pass", "pass", "fail"]},
        )
        run = await runner.run_suite(EvalSuite(name="s", tasks=[task]))

        trial = run.trials["t1"][0]
        result = trial.grader_results[0]
        # 逐评分者按 trial 对齐
        assert set(result.rater_scores) == {"judge-a", "judge-b"}
        assert result.rater_ratings["judge-a"] == "pass"
        assert result.rater_ratings["judge-b"] in {"pass", "fail"}
        # 基准结论 = 首个定义 (judge-a)
        assert result.passed is True

        report = run.summary.agreement.get("panel")
        assert report is not None
        assert report.measure == "cohen_kappa"
        assert report.raters == 2
        assert report.aligned_samples == 5
        assert report.agree is not None and report.disagree is not None
        assert report.agree + report.disagree == 5

    async def test_missing_rater_reports_alpha(self):
        """某评分者本 trial 无有效判定 → 该格缺失 → α 通道 (容忍缺失)"""
        runner, task, _ = self.panel_task(
            6,
            # judge-b 第 1 个 trial 缺失 → 5 个对齐样本, 走 α
            {"a": ["pass"] * 6,
             "b": ["invalid", "pass", "pass", "pass", "fail", "pass"]},
        )
        run = await runner.run_suite(EvalSuite(name="s", tasks=[task]))
        report = run.summary.agreement["panel"]
        assert report.measure == "krippendorff_alpha"
        assert report.value is not None
        assert report.aligned_samples == 5

    async def test_single_rater_no_agreement(self):
        """单评分者: κ/α 不可计算 (agreement 空), confidence 语义为自一致"""
        grader = ScriptedGrader("solo", {"base": ["pass"] * 5})
        runner = make_runner(graders=[grader])
        task = EvalTask(
            id="t1", prompt="p", max_trials=5,
            graders=[GraderConfig(type=GraderType.CODE, name="solo")],
        )
        run = await runner.run_suite(EvalSuite(name="s", tasks=[task]))
        assert run.summary.agreement == {}

    async def test_multi_sample_multi_rater_not_mixed(self):
        """多采样与多评分者不混用: judges 存在时走多评分者路径"""

        class CountingGrader(ScriptedGrader):
            def __init__(self):
                super().__init__("panel", {"a": ["pass"]})
                self.grade_calls = 0

            async def grade(self, trial, spans, task, context=None):
                self.grade_calls += 1
                return await super().grade(trial, spans, task, context)

        grader = CountingGrader()
        runner = make_runner(graders=[grader])
        task = EvalTask(
            id="t1", prompt="p", max_trials=1,
            graders=[GraderConfig(
                type=GraderType.CODE, name="panel", sample_count=3,
                config={"judge_key": "a"},
                judges=[JudgeDefinition(name="judge-a", config={"judge_key": "a"})],
            )],
        )
        await runner.run_suite(EvalSuite(name="s", tasks=[task]))
        # judges 优先: 只按评分者数调用 (1), 不做 3 次多采样
        assert grader.grade_calls == 1


class TestSelfConsistencyPresentation:
    def test_confidence_documented_as_self_consistency(self):
        """confidence 字段自陈 self-consistency (与 agreement 分块, 不混排)"""
        description = GraderResult.model_fields["confidence"].description
        assert "self-consistency" in description
        assert "RunSummary.agreement" in description

    def test_report_separates_agreement_from_confidence(self):
        """报告: agreement 分块呈现并标注自一致说明; 诊断块默认折叠"""
        from agent_eval.metrics.report import render_run_report

        run = RunResult(
            run_id="run_x", suite_name="s", status="completed",
            trials={"t1": [TrialResult(trial_index=0, success=True)]},
            summary=RunSummary(
                total_tasks=1, total_trials=1,
                pass_at_k={1: 1.0}, pass_power_k={1: 1.0}, valid_trials=1,
                agreement={"panel": AgreementReport(
                    measure="cohen_kappa", value=0.4, raters=2,
                    aligned_samples=5, agree=4, disagree=1,
                )},
                diagnostic_metrics=[DiagnosticMetricSummary(
                    name="turn_count", n=1, avg=1.0, min=1.0, max=1.0, p50=1.0,
                )],
            ),
        )
        folded = render_run_report(run)
        assert "诊断指标: 1 项" in folded
        assert "| turn_count |" not in folded
        assert "跨评分者一致性" in folded
        assert "self-consistency" in folded
        assert "Cohen's κ" in folded

        verbose = render_run_report(run, verbose=True)
        assert "| turn_count |" in verbose

    def test_meta_payload_publishes_new_capabilities(self):
        from agent_eval.api.app import meta_payload

        payload = meta_payload()
        assert payload["capabilities"]["evidence_aware_metrics"] is True
        assert payload["capabilities"]["metric_roles"] == ["diagnostic", "judging"]
        assert payload["capabilities"]["inter_rater_agreement"] == [
            "cohen_kappa", "krippendorff_alpha",
        ]
        assert payload["capabilities"]["reward_basis_gates"] is True
        assert "min_aligned_ratings_for_agreement" in payload["statistics"]
