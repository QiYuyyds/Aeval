"""End-to-end test: mini suite through MockRunner (task 6.2).

3 tasks × 3 trials covering the four framework behaviors:
- t_timeout:  trial timeout isolation (per_trial_timeout) → invalid
- t_transient: TransientError exponential-backoff retry
- t_deps:     dependency-based grader skip + environment leak detection

Asserts RunSummary pass@k / pass^k / saturation semantics under the corrected
statistics caliber (valid-trial denominators).
"""

import logging

import pytest

from agent_eval.core.runner import EvalRunner
from agent_eval.core.types import (
    EvalSuite,
    EvalTask,
    GraderConfig,
    GraderResult,
    GraderType,
    InvalidReason,
    TrialVerdict,
)
from agent_eval.examples.mock_runner import MockAgentRunner, MockTraceProvider
from agent_eval.storage.memory import MemoryStorage

FAST = (0.0, 0.01)


class LeakEnvironment:
    """t_deps 的 trial 结束后报告环境泄漏, 其余干净"""

    def __init__(self):
        self.current_task: str | None = None
        self.restore_calls = 0

    async def setup(self, task: EvalTask) -> None:
        self.current_task = task.id

    async def teardown(self, task: EvalTask) -> None:
        return None

    async def snapshot(self) -> dict:
        return {"base": True}

    async def verify_clean(self, baseline: dict) -> dict:
        if self.current_task == "t_deps":
            return {"clean": False, "differences": ["leftover.tmp"]}
        return {"clean": True, "differences": []}

    async def restore(self, baseline: dict) -> None:
        self.restore_calls += 1


def _grader(name: str, **config) -> GraderConfig:
    return GraderConfig(type=GraderType.CODE, name=name, config=config)


# MockAgentRunner 的 transcript 恒含该片段 → 一条真实判据 (无判据现在记 invalid)
MOCK_TRANSCRIPT_CHECK = {"type": "contains", "value": "Mock response", "target": "transcript"}
MOCK_OUTCOME_CHECK = {"type": "contains", "value": "art_", "target": "outcome"}


def build_mini_suite() -> EvalSuite:
    return EvalSuite(
        name="mini-e2e",
        tasks=[
            EvalTask(
                id="t_timeout",
                prompt="hang forever",
                graders=[_grader("code_based", checks=[MOCK_TRANSCRIPT_CHECK])],
                max_trials=3,
            ),
            EvalTask(
                id="t_transient",
                prompt="flaky network",
                graders=[_grader("code_based", checks=[MOCK_TRANSCRIPT_CHECK])],
                max_trials=3,
            ),
            EvalTask(
                id="t_deps",
                prompt="leaky task",
                graders=[
                    # 依赖的 code_based 放后面 → 验证拓扑排序先跑依赖
                    GraderConfig(
                        type=GraderType.CODE,
                        name="always_pass",
                        dependencies=["code_based"],
                    ),
                    _grader(
                        "code_based",
                        checks=[{"type": "contains", "value": "art_", "target": "outcome"}],
                    ),
                ],
                max_trials=3,
            ),
        ],
    )


def build_agent() -> MockAgentRunner:
    # 每个 trial 依次消耗脚本项; 3 trials × 最多 3 次 attempt
    return MockAgentRunner(
        latency_range=FAST,
        timeout_duration=2.0,
        script={
            "t_timeout": ["timeout"],
            "t_transient": ["transient", "transient", "success"] * 3,
            "t_deps": ["failure"] * 9,
        },
    )


class AlwaysPassGrader:
    name = "always_pass"

    async def grade(self, trial, spans, task, context=None):
        from agent_eval.core.types import GraderResult

        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.CODE,
            score=1.0,
            passed=True,
            explanation="pass",
        )


@pytest.fixture
def leak_env() -> LeakEnvironment:
    return LeakEnvironment()


@pytest.fixture
def agent() -> MockAgentRunner:
    return build_agent()


async def run_mini_suite(
    agent: MockAgentRunner,
    leak_env: LeakEnvironment,
    attribute_mapping=None,
) -> dict:
    """跑一遍 mini suite; attribute_mapping 决定 mock 写哪种词汇、框架按哪种读。"""
    runner = EvalRunner(
        agent_runner=agent,
        trace_provider=MockTraceProvider(mapping=attribute_mapping),
        trace_mapping=attribute_mapping,
        storage=MemoryStorage(),
        environment=leak_env,
        graders=[AlwaysPassGrader()],
        per_trial_timeout=0.1,
        max_trial_retries=2,
        retry_base_delay=0.01,
        enable_grader_cache=False,
    )
    run = await runner.run_suite(build_mini_suite())
    summary = run.summary.model_dump()
    return {"run": run, "summary": summary, "runner": runner}


class TestMiniSuiteE2E:
    async def test_full_run_completes(self, agent, leak_env, attribute_mapping):
        result = await run_mini_suite(agent, leak_env, attribute_mapping)
        run = result["run"]
        assert run.status == "completed"
        assert run.error is None
        assert set(run.trials.keys()) == {"t_timeout", "t_transient", "t_deps"}
        assert all(len(trials) == 3 for trials in run.trials.values())

    async def test_timeout_trials_are_invalid(self, agent, leak_env, attribute_mapping):
        result = await run_mini_suite(agent, leak_env, attribute_mapping)
        trials = result["run"].trials["t_timeout"]
        assert all(not t.success for t in trials)
        assert all("timed out" in (t.error or "") for t in trials)
        # 超时不重试
        assert agent.call_counts["t_timeout"] == 3
        # 超时是评测预算耗尽 → invalid, 不是「评分判定为不通过」
        assert all(t.verdict is TrialVerdict.INVALID for t in trials)
        assert all(t.invalid_reason is InvalidReason.TRIAL_TIMEOUT for t in trials)
        ts = next(
            s for s in result["summary"]["task_summaries"] if s["task_id"] == "t_timeout"
        )
        assert ts["valid_trials"] == 0
        assert ts["invalid_trials"] == 3
        assert ts["pass_at_k"][1] is None
        assert ts["failures"] == []
        assert "t_timeout" not in result["summary"]["failures"]

    async def test_transient_retries_recover(self, agent, leak_env, attribute_mapping):
        result = await run_mini_suite(agent, leak_env, attribute_mapping)
        trials = result["run"].trials["t_transient"]
        assert all(t.success for t in trials)
        # 每个 trial: 2 次瞬态失败 + 1 次成功 = 3 次 × 3 trials
        assert agent.call_counts["t_transient"] == 9

    async def test_dependency_skip(self, agent, leak_env, attribute_mapping):
        result = await run_mini_suite(agent, leak_env, attribute_mapping)
        trials = result["run"].trials["t_deps"]
        for trial in trials:
            by_name = {gr.grader_name: gr for gr in trial.grader_results}
            assert by_name["code_based"].passed is False
            assert by_name["always_pass"].score == 0.0
            assert "依赖未满足" in by_name["always_pass"].explanation

    async def test_leak_detected_and_restored_without_failing_trial(
        self, agent, leak_env, caplog, attribute_mapping
    ):
        with caplog.at_level(logging.WARNING, logger="agent_eval.core.runner"):
            result = await run_mini_suite(agent, leak_env, attribute_mapping)

        assert leak_env.restore_calls == 3  # t_deps 的 3 个 trial 各恢复一次
        assert caplog.text.count("Environment leak detected") == 3
        # 泄漏不判 trial 失败 — t_deps 的失败来自评分, 不是泄漏
        trials = result["run"].trials["t_deps"]
        assert all(
            "leak" not in (t.error or "").lower() for t in trials
        )

    async def test_summary_pass_rates(self, agent, leak_env, attribute_mapping):
        result = await run_mini_suite(agent, leak_env, attribute_mapping)
        summary = result["summary"]

        ts = {t["task_id"]: t for t in summary["task_summaries"]}

        # t_transient: 3/3 成功 → pass@1 = pass@3 = pass^3 = 1.0
        assert ts["t_transient"]["pass_at_k"][1] == 1.0
        assert ts["t_transient"]["pass_power_k"][3] == 1.0
        assert ts["t_transient"]["valid_trials"] == 3

        # t_timeout: 全部 invalid → 证据不足 (不以 0.0 冒充实测)
        assert ts["t_timeout"]["pass_at_k"][1] is None
        # t_deps: 全部有效但评分不通过 → pass^1 = 0.0
        assert ts["t_deps"]["pass_power_k"][1] == 0.0
        assert ts["t_deps"]["valid_trials"] == 3
        assert ts["t_deps"]["invalid_trials"] == 0

        # 全局: 仅两个有有效分母的 task 参与均值 → (1.0 + 0.0) / 2
        assert summary["pass_at_k"][1] == pytest.approx(0.5)
        assert summary["total_tasks"] == 3
        assert summary["total_trials"] == 9
        assert summary["valid_trials"] == 6
        assert summary["invalid_trials"] == 3
        assert summary["pending_trials"] == 0
        assert sorted(summary["failures"]) == ["t_deps"]

    async def test_not_saturated(self, agent, leak_env, attribute_mapping):
        result = await run_mini_suite(agent, leak_env, attribute_mapping)
        sat = result["summary"]["saturation"]
        assert sat["is_saturated"] is False
        assert sat["recommendation"] is None
        # 3 trial/task 低于默认最小样本数 5 → 全部样本不足
        assert sat["eligible_tasks"] == []
        assert sorted(sat["insufficient_sample_tasks"]) == [
            "t_deps", "t_timeout", "t_transient"
        ]

    async def test_saturated_when_all_tasks_pass(self):
        # 全部通过且样本量达标 → 超过半数 task 实测 pass@1 ≥ 0.95 → 饱和告警
        agent = MockAgentRunner(success_rate=1.0, latency_range=FAST)
        runner = EvalRunner(
            agent_runner=agent,
            trace_provider=MockTraceProvider(),
            storage=MemoryStorage(),
            min_valid_trials_for_saturation=3,
        )
        suite = EvalSuite(
            name="saturated",
            tasks=[
                EvalTask(
                    id=f"t{i}",
                    prompt="p",
                    graders=[_grader("code_based", checks=[MOCK_TRANSCRIPT_CHECK])],
                    max_trials=3,
                )
                for i in range(3)
            ],
        )
        run = await runner.run_suite(suite)
        sat = run.summary.saturation
        assert sat["is_saturated"] is True
        assert "更有挑战性" in sat["recommendation"]

    async def test_run_persisted_and_queryable(self, agent, leak_env, attribute_mapping):
        result = await run_mini_suite(agent, leak_env, attribute_mapping)
        run = result["run"]

        # run_suite 的 finally 已把最终状态落盘, 可从存储读回
        stored = await result["runner"].storage.get_run(run.run_id)
        assert stored is not None
        assert stored.status == "completed"
        assert stored.summary is not None
        assert set(stored.trials.keys()) == {"t_timeout", "t_transient", "t_deps"}


class ExplodingGrader:
    name = "exploding"

    async def grade(self, trial, spans, task, context=None):
        raise RuntimeError("grader crashed")


class HangingGrader:
    name = "hanging"

    async def grade(self, trial, spans, task, context=None):
        import asyncio

        await asyncio.sleep(5)
        return GraderResult(
            grader_name=self.name, grader_type=GraderType.CODE,
            score=1.0, passed=True, explanation="never reached",
        )


class TestVerdictMatrix:
    """任务 3.9: 以 specs/orchestration 的 scenario 为准的 MockRunner 端到端矩阵

    五类: grader 异常 / grader 超时 / 无判据 / 依赖跳过 / 全 pending。
    """

    async def _run(self, task, *, graders=None, **kwargs):
        agent = MockAgentRunner(success_rate=1.0, latency_range=FAST)
        runner = EvalRunner(
            agent_runner=agent,
            trace_provider=MockTraceProvider(),
            storage=MemoryStorage(),
            graders=graders or [],
            retry_base_delay=0.01,
            **kwargs,
        )
        suite = EvalSuite(name="verdict-matrix", tasks=[task])
        run = await runner.run_suite(suite)
        assert run.status == "completed"
        return run

    async def test_grader_exception_is_invalid(self):
        task = EvalTask(
            id="t_exc", prompt="p",
            graders=[GraderConfig(type=GraderType.CODE, name="exploding")],
            max_trials=2,
        )
        run = await self._run(task, graders=[ExplodingGrader()])

        ts = run.summary.task_summaries[0]
        assert ts.valid_trials == 0
        assert ts.invalid_trials == 2
        assert ts.pass_at_k[1] is None
        assert ts.failures == []
        assert ts.task_id not in run.summary.failures
        # 原因可查询且原始证据未被丢弃
        assert ts.invalid_reasons == {"0": "grader_error", "1": "grader_error"}
        trial = run.trials["t_exc"][0]
        assert trial.transcript  # 证据保留
        assert trial.metrics["latency_ms"] >= 0
        assert trial.grader_results[0].explanation.startswith("Grader error:")

    async def test_grader_timeout_is_invalid(self):
        task = EvalTask(
            id="t_slow", prompt="p",
            graders=[GraderConfig(type=GraderType.CODE, name="hanging")],
            max_trials=1,
        )
        run = await self._run(task, graders=[HangingGrader()], grader_timeout=0.05)

        ts = run.summary.task_summaries[0]
        assert ts.invalid_trials == 1
        assert ts.invalid_reasons == {"0": "grader_timeout"}
        assert ts.pass_at_k[1] is None

    async def test_no_criteria_is_invalid(self):
        task = EvalTask(
            id="t_empty", prompt="p",
            graders=[_grader("code_based")],  # 无 checks
            max_trials=1,
        )
        run = await self._run(task)

        ts = run.summary.task_summaries[0]
        assert ts.invalid_trials == 1
        assert ts.invalid_reasons == {"0": "no_criteria_configured"}
        assert run.trials["t_empty"][0].grader_results[0].passed is False

    async def test_dependency_skip_stays_valid(self):
        task = EvalTask(
            id="t_dep", prompt="p",
            graders=[
                GraderConfig(
                    type=GraderType.CODE, name="always_pass", dependencies=["code_based"]
                ),
                _grader("code_based", checks=[MOCK_OUTCOME_CHECK]),
            ],
            max_trials=1,
        )
        # success_rate=1.0 → code_based 通过; 换成 failure 脚本则依赖未满足
        agent = MockAgentRunner(
            latency_range=FAST, script={"t_dep": ["failure"]}
        )
        runner = EvalRunner(
            agent_runner=agent,
            trace_provider=MockTraceProvider(),
            storage=MemoryStorage(),
            graders=[AlwaysPassGrader()],
            retry_base_delay=0.01,
        )
        run = await runner.run_suite(EvalSuite(name="verdict-matrix", tasks=[task]))

        trial = run.trials["t_dep"][0]
        assert trial.verdict is TrialVerdict.VALID  # 是关于 agent 的结论
        ts = run.summary.task_summaries[0]
        assert ts.valid_trials == 1
        assert ts.invalid_trials == 0
        assert ts.pass_at_k[1] == 0.0
        assert ts.failures == [0]

    async def test_all_pending_task_is_neither_pass_nor_fail(self):
        task = EvalTask(
            id="t_human", prompt="p",
            graders=[GraderConfig(type=GraderType.CUSTOM, name="human")],
            max_trials=3,
        )
        run = await self._run(task)

        ts = run.summary.task_summaries[0]
        assert ts.pending_trials == [0, 1, 2]
        assert ts.valid_trials == 0
        assert ts.invalid_trials == 0
        assert ts.pass_at_k[1] is None
        assert ts.avg_score is None
        assert ts.failures == []
        assert ts.task_id not in run.summary.failures
        # pending 不得被改写为 invalid
        assert all(
            t.verdict is TrialVerdict.PENDING for t in run.trials["t_human"]
        )
