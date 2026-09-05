"""终止原因与预算的端到端矩阵 (specs/orchestration, task 3.6)。

MockRunner 跑通「为什么这次 trial 结束了」的每一种取值, 并验证归类分野
(design D7): 超时属评测没跑完 → invalid 不占分母; 预算触顶属任务约束未达成 →
计未通过但单列; 未声明类别的报错 → 需人工判定, 框架不猜。
"""

from agent_eval.core.metrics import termination_distribution
from agent_eval.core.pricing import PriceTable, TokenPrices
from agent_eval.core.runner import EvalRunner
from agent_eval.core.types import (
    EvalSuite,
    EvalTask,
    GraderConfig,
    GraderType,
    InvalidReason,
    TerminationReason,
    TrialVerdict,
)
from agent_eval.examples.mock_runner import MockAgentRunner, MockTraceProvider
from agent_eval.storage.memory import MemoryStorage

FAST = (0.0, 0.01)

# 默认 mock trace: 1 次工具调用 + 150 token (输入 100 / 输出 50)
TRANSCRIPT_CHECK = {"type": "contains", "value": "Mock response", "target": "transcript"}


def passing_grader() -> GraderConfig:
    return GraderConfig(type=GraderType.CODE, name="code_based",
                        config={"checks": [TRANSCRIPT_CHECK]})


def make_task(task_id: str, **overrides) -> EvalTask:
    defaults = dict(
        id=task_id,
        prompt=f"prompt for {task_id}",
        max_trials=1,
        graders=[passing_grader()],
    )
    defaults.update(overrides)
    return EvalTask(**defaults)


def make_suite(*tasks: EvalTask) -> EvalSuite:
    return EvalSuite(name="termination-suite", version="1.0.0", tasks=list(tasks))


def make_agent(behavior: str, **extra: list[str]) -> MockAgentRunner:
    script = {t: [behavior] for t in extra.pop("tasks", [])} or {}
    script.update(extra)
    return MockAgentRunner(latency_range=FAST, timeout_duration=5.0, script=script)


async def run_suite(
    suite: EvalSuite,
    agent: MockAgentRunner,
    *,
    price_table: PriceTable | None = None,
    per_trial_timeout: float = 30.0,
) -> tuple:
    runner = EvalRunner(
        agent_runner=agent,
        trace_provider=MockTraceProvider(),
        storage=MemoryStorage(),
        per_trial_timeout=per_trial_timeout,
        retry_base_delay=0.01,
        price_table=price_table,
    )
    run = await runner.run_suite(suite)
    return run, runner


class TestTerminationReasons:
    async def test_normal_completion(self):
        """Scenario: 正常结束 → agent_completed 并进入正常评分。"""
        task = make_task("t_ok", graders=[passing_grader()])
        run, _ = await run_suite(make_suite(task), make_agent("success", tasks=["t_ok"]))

        trial = run.trials["t_ok"][0]
        assert trial.termination_reason is TerminationReason.AGENT_COMPLETED
        assert trial.verdict is TrialVerdict.VALID
        assert trial.success is True
        assert trial.grader_results  # 正常进入评分
        assert run.summary.termination_reasons == {"agent_completed": 1}

    async def test_step_budget_stops_trial_even_when_agent_claims_done(self):
        """Scenario: 被评测方自称完成但已触顶 → 不采信自报完成。"""
        task = make_task("t_steps", step_budget=1)
        agent = make_agent("success", tasks=["t_steps"])
        run, _ = await run_suite(make_suite(task), agent)

        trial = run.trials["t_steps"][0]
        assert agent.call_counts["t_steps"] == 1  # agent 确实跑完并宣告成功
        assert trial.outcome["success"] is True
        assert trial.termination_reason is TerminationReason.STEP_BUDGET_EXCEEDED
        assert trial.success is False
        # 立即停止该 trial: 不再花评分成本
        assert trial.grader_results == []

    async def test_step_budget_exceeded_counts_as_failure_not_invalid(self):
        """预算触顶 = 任务约束未达成 → 占分母且判未通过, 与超时不同通道。"""
        task = make_task("t_steps", step_budget=1)
        run, _ = await run_suite(make_suite(task), make_agent("success", tasks=["t_steps"]))

        assert run.summary.valid_trials == 1
        assert run.summary.invalid_trials == 0
        assert run.summary.pass_at_k[1] == 0.0
        assert run.summary.termination_reasons == {"step_budget_exceeded": 1}

    async def test_token_budget(self):
        """Scenario: token 上限触顶 → 停止且单列计数。"""
        task = make_task("t_tokens", token_budget=100)  # 默认 trace 有 150 token
        run, _ = await run_suite(make_suite(task), make_agent("success", tasks=["t_tokens"]))

        trial = run.trials["t_tokens"][0]
        assert trial.termination_reason is TerminationReason.TOKEN_BUDGET_EXCEEDED
        assert trial.success is False
        assert run.summary.termination_reasons == {"token_budget_exceeded": 1}

    async def test_cost_budget_requires_price_table(self):
        """成本预算仅在单价可算时生效; 未配置单价 → 报缺口而非当作 0。"""
        task = make_task("t_cost", cost_budget=0.0001)
        run, _ = await run_suite(make_suite(task), make_agent("success", tasks=["t_cost"]))
        trial = run.trials["t_cost"][0]
        # 无单价表: 成本预算无从判定, trial 正常完成并把缺口说出来
        assert trial.termination_reason is TerminationReason.AGENT_COMPLETED
        assert any(
            gap.field == "budget.cost_budget" for gap in trial.evidence_gaps
        )

        priced = PriceTable(default=TokenPrices(
            input_per_mtok=1000.0, output_per_mtok=1000.0
        ))
        run2, _ = await run_suite(
            make_suite(make_task("t_cost", cost_budget=0.0001)),
            make_agent("success", tasks=["t_cost"]),
            price_table=priced,
        )
        trial2 = run2.trials["t_cost"][0]
        assert trial2.metrics["cost_usd"] > 0.0001
        assert trial2.termination_reason is TerminationReason.COST_BUDGET_EXCEEDED

    async def test_timeout_goes_to_invalid_channel(self):
        """超时 = 评测没跑完 → invalid, 不占分母。"""
        task = make_task("t_slow")
        run, _ = await run_suite(
            make_suite(task), make_agent("timeout", tasks=["t_slow"]), per_trial_timeout=0.05
        )

        trial = run.trials["t_slow"][0]
        assert trial.termination_reason is TerminationReason.TIMEOUT
        assert trial.verdict is TrialVerdict.INVALID
        assert trial.invalid_reason is InvalidReason.TRIAL_TIMEOUT
        assert run.summary.valid_trials == 0
        assert run.summary.invalid_trials == 1
        assert run.summary.pass_at_k[1] is None  # 分母为空, 不是 0%

    async def test_timeout_and_budget_exceeded_are_distinguishable(self):
        """Scenario: 同一套件里既有超时也有 token 触顶 → 两者计数不同、通道不同。"""
        suite = make_suite(
            make_task("t_slow", token_budget=None),
            make_task("t_tokens", token_budget=100),
        )
        agent = MockAgentRunner(
            latency_range=FAST,
            timeout_duration=5.0,
            script={"t_slow": ["timeout"], "t_tokens": ["success"]},
        )
        run, _ = await run_suite(suite, agent, per_trial_timeout=0.05)

        reasons = run.summary.termination_reasons
        assert reasons == {"timeout": 1, "token_budget_exceeded": 1}
        # 超时不占分母, 触顶占 —— 两者在汇总里可辨
        assert run.summary.valid_trials == 1
        assert run.summary.invalid_trials == 1
        assert run.summary.task_summaries[1].failures == [0]

    async def test_undeclared_agent_error_needs_human_judgement(self):
        """Scenario: runner 只抛通用异常 → 记为需人工判定的无效结论。"""
        task = make_task("t_crash")
        run, _ = await run_suite(make_suite(task), make_agent("error", tasks=["t_crash"]))

        trial = run.trials["t_crash"][0]
        assert trial.termination_reason is TerminationReason.AGENT_ERROR
        assert trial.verdict is TrialVerdict.INVALID
        assert trial.invalid_reason is InvalidReason.UNCLASSIFIED_AGENT_ERROR
        assert "需人工判定" in trial.error
        assert run.summary.task_summaries[0].invalid_reasons == {
            "0": "unclassified_agent_error"
        }

    async def test_declared_agent_defect_counts_as_failure(self):
        """接入方声明属 agent 缺陷 → 未通过, 但仍是一个关于 agent 的有效结论。"""
        task = make_task("t_defect")
        run, _ = await run_suite(make_suite(task), make_agent("defect", tasks=["t_defect"]))

        trial = run.trials["t_defect"][0]
        assert trial.termination_reason is TerminationReason.AGENT_ERROR
        assert trial.verdict is TrialVerdict.VALID
        assert trial.success is False
        assert run.summary.valid_trials == 1
        assert run.summary.invalid_trials == 0

    async def test_declared_external_fault_is_not_agent_fault(self):
        task = make_task("t_ext")
        run, _ = await run_suite(make_suite(task), make_agent("external", tasks=["t_ext"]))

        trial = run.trials["t_ext"][0]
        assert trial.termination_reason is TerminationReason.AGENT_ERROR
        assert trial.verdict is TrialVerdict.INVALID
        assert trial.invalid_reason is InvalidReason.EXTERNAL_DEPENDENCY_UNAVAILABLE

    async def test_cancelled_run_records_cancelled_trials(self):
        """Scenario: 取消生效后的 task 留下 cancelled 痕迹, 而不是悄悄少样本。"""
        suite = make_suite(
            make_task("t1", max_trials=2),
            make_task("t2", max_trials=2),
        )
        agent = make_agent("success", tasks=["t1", "t2"])
        runner = EvalRunner(
            agent_runner=agent,
            trace_provider=MockTraceProvider(),
            storage=MemoryStorage(),
            per_trial_timeout=30.0,
        )

        async def callback(event: str, data: dict) -> None:
            if event == "task_complete" and data.get("task_id") == "t1":
                await runner.cancel_run("run_matrix")

        run = await runner.run_suite(suite, callback=callback, run_id="run_matrix")

        assert run.status == "cancelled"
        assert agent.call_counts.get("t2", 0) == 0  # 第二个 task 根本没跑
        cancelled = run.trials["t2"]
        assert [t.termination_reason for t in cancelled] == [
            TerminationReason.CANCELLED,
            TerminationReason.CANCELLED,
        ]
        assert all(t.verdict is TrialVerdict.INVALID for t in cancelled)
        assert run.summary.termination_reasons == {
            "agent_completed": 2,
            "cancelled": 2,
        }

    async def test_cancellation_share_is_visible_next_to_pass_rate(self):
        """Scenario: 40% 被取消 → 汇总显式呈现, 通过率旁给出有效样本数。"""
        suite = make_suite(make_task("t1", max_trials=3), make_task("t2", max_trials=2))
        agent = make_agent("success", tasks=["t1", "t2"])
        runner = EvalRunner(
            agent_runner=agent,
            trace_provider=MockTraceProvider(),
            storage=MemoryStorage(),
        )

        async def callback(event: str, data: dict) -> None:
            if event == "trial_complete" and data.get("trial_index") == 0:
                await runner.cancel_run("run_share")

        run = await runner.run_suite(suite, callback=callback, run_id="run_share")

        summary = run.summary
        assert summary.total_trials == 5
        assert summary.termination_reasons == {
            "agent_completed": 1,
            "cancelled": 4,
        }
        # 大面积取消呈现为「评测没跑完」, 而不是分母悄悄变小
        assert summary.valid_trials == 1
        assert summary.invalid_trials == 4


class TestTerminationDistribution:
    async def test_task_and_run_levels_both_report(self):
        suite = make_suite(
            make_task("t1", max_trials=2),
            make_task("t2", step_budget=1),
        )
        agent = MockAgentRunner(
            latency_range=FAST, script={"t1": ["success"], "t2": ["success"]}
        )
        run, _ = await run_suite(suite, agent)

        task_summaries = {ts.task_id: ts for ts in run.summary.task_summaries}
        assert task_summaries["t1"].termination_reasons == {"agent_completed": 2}
        assert task_summaries["t2"].termination_reasons == {"step_budget_exceeded": 1}
        assert run.summary.termination_reasons == {
            "agent_completed": 2,
            "step_budget_exceeded": 1,
        }

    async def test_trials_without_reason_read_as_unknown(self):
        """缺终止原因的历史行读为 unknown, 不臆断为正常完成。"""
        run, _ = await run_suite(
            make_suite(make_task("t1")), make_agent("success", tasks=["t1"])
        )
        assert run.trials["t1"][0].termination_reason is TerminationReason.AGENT_COMPLETED

        run.trials["t1"][0].termination_reason = None  # 模拟本变更之前落盘的行
        assert termination_distribution(run.trials["t1"]) == {"unknown": 1}
        assert termination_distribution([]) == {}
