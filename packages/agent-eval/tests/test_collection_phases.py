"""三相编排、取证探针与分级取信 (change ③ tasks 2.2 / 2.4 / 3.1 / 3.5 / 4.x)。

这里验的是本变更真正改变了行为的那几件事:

- 被评方拿不到答案键 (任务视图在类型上就没有判据);
- 取证发生在环境停止之前, 评分发生在停止之后;
- 「建完又删掉」在默认口径下不成立, 只有显式判「任一时刻」且真有运行中窗口时才算;
- 未接入探针时报证据不足, 而不是拿空读数判通过;
- 同一份证据在「只 harness / 默认 / 允许 subject」三种声明下给出三种不同结论。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agent_eval.core.contract import EvalContext, TrialSession
from agent_eval.core.runner import EvalRunner
from agent_eval.core.types import (
    EvalSuite,
    EvalTask,
    EvidenceKind,
    GraderConfig,
    GraderResult,
    GraderType,
    InvalidReason,
    JudgmentMoment,
    ObservedBy,
    TaskView,
    TrialEvidence,
    TrialResult,
    TrialVerdict,
)
from agent_eval.examples.mock_runner import MockAgentRunner, MockTraceProvider
from agent_eval.graders.state_check import StateCheckGrader
from agent_eval.storage.memory import MemoryStorage

FAST = (0.0, 0.01)


class ProbingEnvironment:
    """可编程的取证环境: 每次探针返回脚本里的下一个读数 (最后一次即结束态)。"""

    def __init__(self, series: list[dict[str, Any]] | None = None, *, raises: bool = False):
        self.series = series or []
        self.raises = raises
        self.events: list[str] = []
        self.probe_channels: list[str] = []

    async def setup(self, task: EvalTask) -> None:
        self.events.append("setup")

    async def teardown(self, task: EvalTask) -> None:
        self.events.append("teardown")

    async def snapshot(self) -> dict[str, Any]:
        return {"files": {}}

    async def probe(self, channel: str = "") -> list[ObservationReading]:
        self.events.append(f"probe:{channel or 'all'}")
        self.probe_channels.append(channel)
        if self.raises:
            raise RuntimeError("probe backend down")
        if not self.series:
            return [{"files": {}}]
        index = min(len(self.probe_channels) - 1, len(self.series) - 1)
        return [self.series[index]]

    async def verify_clean(
        self, baseline: dict[str, Any], harness_readings: list[Any] | None = None
    ) -> dict[str, Any]:
        self.events.append("verify_clean")
        # 泄漏判定读的是取证读数, 不是被评方自报 —— 这里把它记下来供断言
        self.verified_from = [
            obs.observed_by.value for obs in (harness_readings or []) if not obs.is_absent
        ]
        return {"clean": True, "differences": []}

    async def restore(self, baseline: dict[str, Any]) -> None:
        self.events.append("restore")


ObservationReading = dict[str, Any]


class RecordingGrader:
    """把评分发生的时刻记进环境事件流, 用于验证三相顺序。"""

    name = "recording"
    evidence_levels = (ObservedBy.HARNESS, ObservedBy.RUNNER, ObservedBy.SUBJECT)
    implementation_version = "1"

    def __init__(self, events: list[str], *, passed: bool = True):
        self.events = events
        self.passed = passed

    async def grade(self, trial, spans, task, context=None) -> GraderResult:
        self.events.append("grade")
        levels = [
            obs.observed_by
            for obs in (context.evidence.harness_state if context and context.evidence else [])
            if not obs.is_absent
        ]
        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.STATE,
            score=1.0 if self.passed else 0.0,
            passed=self.passed,
            explanation="recorded",
            evidence_levels=levels or [ObservedBy.RUNNER],
        )


def state_task(task_id: str, expectations: list[dict], **grader_kwargs) -> EvalTask:
    return EvalTask(
        id=task_id,
        prompt="p",
        max_trials=1,
        graders=[
            GraderConfig(
                type=GraderType.STATE,
                name="state_check",
                config={"expectations": expectations},
                **grader_kwargs,
            )
        ],
    )


def make_runner(agent, *, environment=None, graders=None, **kwargs) -> EvalRunner:
    return EvalRunner(
        agent_runner=agent,
        trace_provider=MockTraceProvider(default_spans=[]),
        storage=MemoryStorage(),
        environment=environment,
        graders=graders or [],
        **kwargs,
    )


def suite_of(*tasks: EvalTask) -> EvalSuite:
    return EvalSuite(name="phase-suite", version="1.0.0", tasks=list(tasks))


async def grade_state(evidence_state: dict, **grader_kwargs) -> GraderResult:
    """把一份证据直接交给 state_check, 再套用两条默认规则 (不走编排)。"""
    from agent_eval.graders._evidence import enforce_evidence_policy

    task = state_task("t1", [{"type": "file_exists", "path": "a.py"}], **grader_kwargs)
    grader_config = task.graders[0]
    evidence = TrialEvidence.model_validate(evidence_state)
    trial = TrialResult(
        trial_index=0, outcome=evidence.state_payload([
            ObservedBy.HARNESS, ObservedBy.RUNNER, ObservedBy.SUBJECT
        ])
    )
    result = await StateCheckGrader().grade(
        trial,
        [],
        task,
        EvalContext(
            run_id="r",
            task=task,
            trial=trial,
            evidence=evidence.permitted(
                [ObservedBy(level) for level in grader_config.evidence]
                + ([ObservedBy.SUBJECT] if grader_config.allow_subject else [])
            ),
            grader_config=grader_config,
        ),
    )
    return enforce_evidence_policy(result, grader_config, StateCheckGrader())


def state_evidence(**by_level: dict[str, Any]) -> dict[str, Any]:
    """把 (级别 → 状态字典) 组成证据对象; 未给出的级别就是「没取到」。"""
    subject_state = [
        {
            "kind": "state",
            "observed_by": level.value,
            "observed_at": 1_000.0 + rank,
            "channel": f"{level.value}_report",
            "value": payload,
        }
        for rank, (level, payload) in enumerate(by_level.items())
        if level is not ObservedBy.HARNESS
    ]
    harness_state = [
        {
            "kind": "state",
            "observed_by": "harness",
            "observed_at": 1_000.0,
            "channel": "end_state",
            "value": by_level[ObservedBy.HARNESS],
        }
    ] if ObservedBy.HARNESS in by_level else [
        {
            "kind": "state",
            "observed_by": "harness",
            "observed_at": 1_000.0,
            "channel": "end_state",
            "absent_reason": "provider_unavailable",
            "detail": "未接入探针",
        }
    ]
    return {"subject_state": subject_state, "harness_state": harness_state}


# ── 2.2 任务视图不含答案键 ───────────────────────────────────────────────────


def test_task_view_carries_no_graders_or_answer_keys():
    task = EvalTask(
        id="t1",
        prompt="写一个文件",
        graders=[
            GraderConfig(
                type=GraderType.CODE,
                name="code_based",
                config={
                    "checks": [{"type": "contains", "value": "def main", "target": "outcome"}],
                    "expected_output": "def main(): ...",
                },
            ),
            GraderConfig(
                type=GraderType.METRIC,
                name="faithfulness",
                config={"expected_output": "参考产物"},
            ),
        ],
        env={"workspace": "/tmp/x"},
    )

    view = TaskView.of(task)
    text = json.dumps(view.model_dump(), ensure_ascii=False)

    assert not hasattr(view, "graders")
    assert {"id", "description", "prompt", "env"} == set(view.model_dump())
    # 期望值、判据、阈值一概不在递给被评方的请求里
    for leak in ("def main", "参考产物", "expected_output", "checks", "threshold"):
        assert leak not in text
    # 而评分阶段仍拿得到完整的任务定义
    assert task.graders[0].config["expected_output"] == "def main(): ..."


async def test_agent_runner_receives_only_the_view():
    seen: list[tuple[Any, Any]] = []

    class WatchingAgent:
        async def run(self, view, session):
            seen.append((view, session))
            return TrialEvidence.runner_reported(trace_id="t", transcript=[])

    runner = make_runner(WatchingAgent())
    await runner.run_suite(suite_of(state_task("t1", [{"type": "file_exists", "path": "a"}])))

    view, session = seen[0]
    assert isinstance(view, TaskView)
    assert isinstance(session, TrialSession)
    assert view.prompt == "p"


# ── 2.4 mock 的两条接入路径 ──────────────────────────────────────────────────


async def test_simple_path_needs_no_session():
    """只用返回值也算合法接入: 会话句柄一次没碰, 证据仍然完整可评。"""
    agent = MockAgentRunner(success_rate=1.0, latency_range=FAST, script={"t1": ["success"]})
    env = ProbingEnvironment([{"files": {"output.py": ""}}])
    runner = make_runner(agent, environment=env)

    run = await runner.run_suite(suite_of(state_task("t1", [{"type": "file_exists", "path": "output.py"}])))
    trial = run.trials["t1"][0]

    assert agent.sessions_seen == []  # 适配层没碰过句柄
    assert trial.transcript, "返回值里的 transcript 进了证据"
    assert list(trial.outcome["files"]) == ["output.py"]
    # 结束态读数由框架发起, 与被评方上报分开
    assert [obs.channel for obs in trial.evidence.harness_state] == ["end_state"]
    assert trial.evidence.harness_state[0].observed_by is ObservedBy.HARNESS
    assert trial.grader_results[0].evidence_levels == [ObservedBy.HARNESS]


async def test_session_path_pushes_and_probes_mid_run():
    """递进接入: emit 推送 + 运行中探针; 探针读数一律记成 harness 级。"""
    agent = MockAgentRunner(success_rate=1.0, latency_range=FAST, script={"t1": ["push"]})
    env = ProbingEnvironment([{"files": {"tmp.py": ""}}, {"files": {}}])
    runner = make_runner(agent, environment=env)

    run = await runner.run_suite(suite_of(state_task("t1", [{"type": "file_exists", "path": "x"}])))
    evidence = run.trials["t1"][0].evidence

    assert agent.probe_calls == ["workspace_listing"]  # 适配层确实请求过当场取证
    assert {obs.channel for obs in evidence.harness_state} == {
        "workspace_listing",
        "end_state",
    }
    assert all(obs.observed_by is ObservedBy.HARNESS for obs in evidence.harness_state)
    # agent 自述与适配层交付各自留在被评侧通道, 没有被并进取证
    assert {obs.observed_by for obs in evidence.subject_state} == {
        ObservedBy.SUBJECT,
        ObservedBy.RUNNER,
    }
    assert evidence.observed_window is True


async def test_emit_only_path_lands_in_the_same_channels():
    """只推不返 (返回空证据) 也必须得到同一份证据 —— 两条写法不该分叉。"""
    class PushOnlyAgent:
        async def run(self, view, session):
            session.emit(EvidenceKind.TRANSCRIPT, {"role": "user", "content": view.prompt})
            session.emit(
                EvidenceKind.STATE,
                {"files": {"a.py": ""}},
                observed_by=ObservedBy.RUNNER,
            )
            return TrialEvidence(trace_id="trace_push")

    runner = make_runner(PushOnlyAgent())
    run = await runner.run_suite(suite_of(state_task("t1", [{"type": "file_exists", "path": "a.py"}])))
    trial = run.trials["t1"][0]

    assert trial.transcript == [{"role": "user", "content": "p"}]
    assert trial.outcome["files"] == {"a.py": ""}
    assert trial.grader_results[0].passed is True


# ── 3.1 / 3.2 三相顺序与探针缺失 ─────────────────────────────────────────────


async def test_lifecycle_order_is_setup_run_probe_teardown_grade():
    events: list[str] = []
    agent = MockAgentRunner(success_rate=1.0, latency_range=FAST, script={"t1": ["success"]})
    env = ProbingEnvironment([{"files": {}}])
    grader = RecordingGrader(events)
    runner = make_runner(agent, environment=env, graders=[grader])
    task = state_task("t1", [{"type": "file_exists", "path": "x"}])
    task.graders.append(
        GraderConfig(type=GraderType.STATE, name="recording", config={})
    )

    await runner.run_suite(suite_of(task))

    recorded = env.events + events
    order = [
        step
        for step in ("setup", "probe:end_state", "teardown", "verify_clean", "grade")
        if any(step in event for event in recorded)
    ]
    assert order == ["setup", "probe:end_state", "teardown", "verify_clean", "grade"], recorded


class BlindEnvironment(ProbingEnvironment):
    """只提供生命周期动作、不实现取证探针的环境。"""

    probe = None  # 显式没有探针: 框架 getattr(...) 要拿不到它


async def test_end_state_reading_exists_without_probe_implementation():
    """没接入探针 → 结束态是一条「没取到」的读数, 不是空读数。"""
    agent = MockAgentRunner(success_rate=1.0, latency_range=FAST, script={"t1": ["success"]})
    runner = make_runner(agent, environment=BlindEnvironment())

    run = await runner.run_suite(suite_of(state_task("t1", [{"type": "file_exists", "path": "a"}])))
    readings = run.trials["t1"][0].evidence.harness_state

    assert len(readings) == 1
    assert readings[0].is_absent is True
    assert readings[0].absent_reason == "provider_unavailable"


async def test_broken_probe_never_becomes_a_pass():
    """探针坏了: 有适配层终态就据它判失败, 什么都没有就报证据不足 —— 都不判通过。"""
    agent = MockAgentRunner(success_rate=1.0, latency_range=FAST, script={"t1": ["success"]})
    runner = make_runner(agent, environment=ProbingEnvironment(raises=True))

    run = await runner.run_suite(suite_of(state_task("t1", [{"type": "file_exists", "path": "a"}])))
    trial = run.trials["t1"][0]
    reading = trial.evidence.harness_state[-1]

    assert reading.is_absent and "probe backend down" in reading.detail
    result = trial.grader_results[0]
    assert result.passed is False
    # 适配层交付的终态说「没有这个文件」→ 这是关于 agent 的有效结论 (判失败)
    assert result.verdict is TrialVerdict.VALID
    assert result.evidence_levels == [ObservedBy.RUNNER]


async def test_no_trusted_reading_at_all_is_insufficient_evidence():
    """探针坏 + 适配层也没交状态 → 没有任何可信读数, 判证据不足而不是失败。"""
    class NoStateAgent:
        async def run(self, view, session):
            return TrialEvidence(trace_id="trace_none")

    runner = make_runner(NoStateAgent(), environment=ProbingEnvironment(raises=True))

    run = await runner.run_suite(suite_of(state_task("t1", [{"type": "file_exists", "path": "a"}])))
    trial = run.trials["t1"][0]
    result = trial.grader_results[0]

    assert result.verdict is TrialVerdict.INVALID
    assert result.invalid_reason is InvalidReason.EVIDENCE_UNAVAILABLE
    assert "harness" in result.explanation and "runner" in result.explanation
    # 评测没判成 → 不占通过率分母
    assert trial.verdict is TrialVerdict.INVALID
    assert run.summary.task_summaries[0].valid_trials == 0
    assert run.summary.task_summaries[0].pass_at_k[1] is None


async def test_leak_check_reads_harness_readings_not_self_report():
    agent = MockAgentRunner(success_rate=1.0, latency_range=FAST, script={"t1": ["success"]})
    env = ProbingEnvironment([{"files": {}}])
    runner = make_runner(agent, environment=env)

    await runner.run_suite(suite_of(state_task("t1", [{"type": "file_exists", "path": "a"}])))

    assert env.verified_from == ["harness", "harness"] or env.verified_from == ["harness"]


# ── 3.5 / 4.5 判定时刻 ───────────────────────────────────────────────────────


def _created_then_deleted_runner() -> EvalRunner:
    """每次调用都给一套全新的 agent/环境: 探针脚本按调用次数推进, 不能共用。"""
    agent = MockAgentRunner(success_rate=1.0, latency_range=FAST, script={"t1": ["push"]})
    return make_runner(agent, environment=ProbingEnvironment([
        {"files": {"tmp.py": ""}},
        {"files": {}},
    ]))


async def test_created_then_deleted_fails_at_end_and_passes_any_time():
    """中途建完又删掉: 默认「结束时」不成立, 显式「任一时刻」才成立。"""
    expectation = [{"type": "file_exists", "path": "tmp.py"}]

    at_end = await _created_then_deleted_runner().run_suite(
        suite_of(state_task("t1", expectation, judgment_moment=JudgmentMoment.AT_END))
    )
    any_time = await _created_then_deleted_runner().run_suite(
        suite_of(state_task("t1", expectation, judgment_moment=JudgmentMoment.ANY_TIME))
    )

    end_trial = at_end.trials["t1"][0]
    time_trial = any_time.trials["t1"][0]
    assert end_trial.grader_results[0].passed is False
    assert end_trial.grader_results[0].judgment_moment is JudgmentMoment.AT_END
    assert time_trial.grader_results[0].passed is True
    assert time_trial.grader_results[0].judgment_moment is JudgmentMoment.ANY_TIME
    # 所用时刻随结论可见
    assert "at_end" in end_trial.grader_results[0].explanation
    assert "any_time" in time_trial.grader_results[0].explanation
    # 默认口径下这条结论不依赖任何自报内容 (它读的是取证 + 适配层交付的终态)
    assert ObservedBy.SUBJECT not in end_trial.grader_results[0].evidence_levels
    assert time_trial.grader_results[0].evidence_levels == [ObservedBy.HARNESS]
    assert end_trial.success is False
    assert time_trial.success is True


async def test_any_time_without_mid_run_probe_is_insufficient():
    """只有结束态一次取证时, 「任一时刻」判不了 —— 不拿结束态冒充全时段观测。"""
    agent = MockAgentRunner(success_rate=1.0, latency_range=FAST, script={"t1": ["success"]})
    env = ProbingEnvironment([{"files": {"tmp.py": ""}}])
    runner = make_runner(agent, environment=env)

    run = await runner.run_suite(
        suite_of(state_task("t1", [{"type": "file_exists", "path": "tmp.py"}],
                            judgment_moment=JudgmentMoment.ANY_TIME))
    )
    result = run.trials["t1"][0].grader_results[0]

    assert result.verdict is TrialVerdict.INVALID
    assert result.invalid_reason is InvalidReason.EVIDENCE_UNAVAILABLE


async def test_not_at_end_asserts_absence_at_end():
    agent = MockAgentRunner(success_rate=1.0, latency_range=FAST, script={"t1": ["push"]})
    env = ProbingEnvironment([{"files": {"tmp.py": ""}}, {"files": {}}])
    runner = make_runner(agent, environment=env)

    run = await runner.run_suite(
        suite_of(state_task("t1", [{"type": "file_exists", "path": "tmp.py"}],
                            judgment_moment=JudgmentMoment.NOT_AT_END))
    )
    assert run.trials["t1"][0].grader_results[0].passed is True


# ── 4.1 / 4.2 / 4.3 / 4.6 分级取信与两条默认规则 ─────────────────────────────


@pytest.mark.parametrize(
    "grader_kwargs,expect_verdict,expect_reason,expect_passed,expect_levels",
    [
        # 收紧到只认评测侧取证: 只有自报读数可用 → 缺证, 不照原样打分
        (
            {"evidence": ["harness"]},
            TrialVerdict.INVALID,
            InvalidReason.EVIDENCE_UNAVAILABLE,
            False,
            [],
        ),
        # 默认两级: 适配层交付的终态说没有这个文件 → 有效判失败, 不采信自报
        (
            {},
            TrialVerdict.VALID,
            None,
            False,
            [ObservedBy.RUNNER],
        ),
        # 显式放行: 自报可以单独支撑通过, 但结论被标成弱证据
        (
            {"allow_subject": True, "evidence": ["harness", "runner", "subject"]},
            TrialVerdict.VALID,
            None,
            True,
            [ObservedBy.SUBJECT],
        ),
    ],
    ids=["harness-only", "default", "allow-subject"],
)
async def test_same_evidence_three_declarations(
    grader_kwargs, expect_verdict, expect_reason, expect_passed, expect_levels
):
    """同一份证据 (只有被评侧上报) 在三种声明下产生三种不同结论。"""
    from agent_eval.graders._evidence import enforce_evidence_policy

    evidence = state_evidence(
        **{
            ObservedBy.RUNNER: {"files": {}},          # 适配层交付: 没有 a.py
            ObservedBy.SUBJECT: {"files": {"a.py": ""}},  # agent 自报: 有
        }
    )
    task = state_task("t1", [{"type": "file_exists", "path": "a.py"}], **grader_kwargs)
    grader_config = task.graders[0]
    trial = TrialResult(trial_index=0, outcome={"files": {"a.py": ""}})
    context = EvalContext(
        run_id="r",
        task=task,
        trial=trial,
        evidence=TrialEvidence.model_validate(evidence).permitted(
            [
                ObservedBy(level) for level in grader_config.evidence
            ] + ([ObservedBy.SUBJECT] if grader_config.allow_subject else [])
        ),
        grader_config=grader_config,
    )

    result = await StateCheckGrader().grade(trial, [], task, context)
    result = enforce_evidence_policy(result, grader_config, StateCheckGrader())

    assert result.verdict is expect_verdict
    assert result.invalid_reason is expect_reason
    assert result.passed is expect_passed
    assert list(result.evidence_levels) == expect_levels
    if expect_reason is InvalidReason.EVIDENCE_UNAVAILABLE:
        assert "harness" in result.explanation
    if expect_reason is InvalidReason.SUBJECT_ONLY_EVIDENCE:
        assert "自报" in result.explanation
    if result.subject_only:
        assert "弱证据" in result.explanation


async def test_subject_only_pass_without_escape_switch_is_invalid():
    """声明里带 subject 但没开 allow_subject → 自报支撑的通过判无效, 文案说明缺哪一级。"""
    result = await grade_state(
        state_evidence(**{ObservedBy.SUBJECT: {"files": {"a.py": ""}}}),
        evidence=["harness", "runner", "subject"],
    )

    assert result.verdict is TrialVerdict.INVALID
    assert result.invalid_reason is InvalidReason.SUBJECT_ONLY_EVIDENCE
    assert "只由被评方自报证据支撑" in result.explanation
    # 被改写的原结论保留在 details 里: 无效不等于什么都没发生
    assert result.details["rejected_score"] == 1.0
    assert result.details["evidence_levels"] == ["subject"]


async def test_grader_reading_undeclared_level_is_rejected_with_distinct_reason():
    """结论依据了未声明级别 → invalid(evidence_level_mismatch), 与规则 1 文案可区分。"""
    from agent_eval.core.types import GraderResult
    from agent_eval.graders._evidence import enforce_evidence_policy

    config = state_task("t1", [], evidence=["harness"]).graders[0]
    result = GraderResult(
        grader_name="state_check",
        grader_type=GraderType.STATE,
        score=1.0,
        passed=True,
        explanation="自报清单里有这个文件",
        evidence_levels=[ObservedBy.RUNNER],
    )

    rejected = enforce_evidence_policy(result, config, StateCheckGrader())

    assert rejected.verdict is TrialVerdict.INVALID
    assert rejected.invalid_reason is InvalidReason.EVIDENCE_LEVEL_MISMATCH
    assert "未声明" in rejected.explanation
    assert rejected.details["rejected_score"] == 1.0
    # 与规则 1 的文案区分: 一个说「只由自报支撑」, 一个说「读了没声明的级别」
    subject_only = enforce_evidence_policy(
        result.model_copy(update={"evidence_levels": [ObservedBy.SUBJECT]}), config, None
    )
    assert subject_only.invalid_reason is InvalidReason.SUBJECT_ONLY_EVIDENCE
    assert "自报" in subject_only.explanation


async def test_allow_subject_is_recorded_on_the_run():
    """逃生开关随 run 落盘, 事后能直接回答「当时谁被允许只凭自报判通过」。"""
    agent = MockAgentRunner(success_rate=1.0, latency_range=FAST, script={"t1": ["success"]})
    runner = make_runner(agent)
    suite = suite_of(
        state_task("t1", [{"type": "file_exists", "path": "output.py"}], allow_subject=True)
    )

    run = await runner.run_suite(suite)

    assert run.evidence is not None
    assert run.evidence.subject_allowed == {"t1/state_check": True}
    assert run.summary.task_summaries[0].subject_only_trials in ([0], [])


async def test_conclusions_disclose_their_weakest_level():
    """同样是 1.0, 靠取证与靠适配层交付在汇总里分量不同。"""
    harness_agent = MockAgentRunner(success_rate=1.0, latency_range=FAST, script={"t1": ["success"]})
    probed = ProbingEnvironment([{"files": {"output.py": ""}}])
    runner = make_runner(harness_agent, environment=probed)
    suite = suite_of(state_task("t1", [{"type": "file_exists", "path": "output.py"}]))

    run = await runner.run_suite(suite)
    trial = run.trials["t1"][0]

    assert trial.weakest_evidence is ObservedBy.HARNESS
    assert run.summary.task_summaries[0].evidence_levels == {"harness": 1}
    assert run.summary.evidence_levels == {"harness": 1}

    # 没有取证通道时同一判据落到 runner 级 (被评侧上报), 报告要能区分
    runner_no_probe = make_runner(
        MockAgentRunner(success_rate=1.0, latency_range=FAST, script={"t1": ["success"]}),
        environment=ProbingEnvironment([{"files": {}}]),
    )
    second = await runner_no_probe.run_suite(suite)
    assert second.trials["t1"][0].weakest_evidence is ObservedBy.RUNNER
    assert second.summary.task_summaries[0].evidence_levels == {"runner": 1}
