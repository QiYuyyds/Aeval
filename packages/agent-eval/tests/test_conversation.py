"""多轮会话、运行中事件与离线重放的测试 (change ⑤: 组 1 / 3 / 4)。

覆盖三条 spec 主线:
- 会话轮次挂在 TrialSession 上, 一次多轮 trial 仍是**一次** run() 调用,
  轮数不进分母 (orchestration);
- 声明轮数未消费完即会话结束 → invalid 并点名适配器与消费到第几轮;
- 注入事件/人工介入/模拟话术带时刻以 harness 来源进证据, 用户侧输入序列
  构成可重放脚本 (重放被评系统调用次数为零)。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_eval.core.contract import SimulatorContext, SimulatorReply, TrialSession
from agent_eval.core.runner import EvalRunner
from agent_eval.core.simulator import (
    GOAL_END_SENTINEL,
    GoalDrivenUserSimulator,
    ScriptedUserSimulator,
)
from agent_eval.core.suite import SuiteLoadError, load_suite
from agent_eval.core.types import (
    EVENT_CHANNEL,
    HUMAN_MESSAGE_CHANNEL,
    SIMULATED_USER_CHANNEL,
    EvidenceKind,
    GraderConfig,
    GraderType,
    InvalidReason,
    JudgmentMoment,
    ObservedBy,
    TrialVerdict,
)
from agent_eval.examples.mock_runner import MockAgentRunner
from agent_eval.storage.memory import MemoryStorage

FAST = {"latency_range": (0.0, 0.01)}


# ─── 测试用适配器 ────────────────────────────────────────────────────────────


class TurnConsumerRunner:
    """按会话行事的适配器: 消费首轮 + 全部后续用户消息, 一次 run() 跑完。"""

    def __init__(self, *, stop_after: int | None = None):
        self.calls = 0
        self.stop_after = stop_after  # 消费几条后续消息后早退 (None = 全部消费)
        self.consumed: list[str] = []

    async def run(self, view, session) -> Any:  # noqa: ANN001 — 测试桩
        self.calls += 1
        messages = [{"role": "user", "content": view.prompt}]
        taken = 0
        while True:
            reply = await session.next_user_message()
            if reply is None:
                break
            taken += 1
            self.consumed.append(reply)
            messages.append({"role": "user", "content": reply})
            messages.append({"role": "assistant", "content": f"reply {taken}"})
            if self.stop_after is not None and taken >= self.stop_after:
                break  # 早退: 声明轮数未消费完
        evidence = TrialEvidenceBuilder.build(trace_id=f"trace_{view.id}", messages=messages)
        return evidence


class TrialEvidenceBuilder:
    @staticmethod
    def build(*, trace_id: str, messages: list[dict[str, Any]]) -> Any:
        from agent_eval.core.types import TrialEvidence

        return TrialEvidence.runner_reported(
            trace_id=trace_id,
            transcript=messages,
            state={"files": {"output.py": "def hello(): pass\n"}},
        )


class EventAwareRunner(TurnConsumerRunner):
    """在第 2 轮之后 (消费 2 条后续消息后) 请求一次探针读数。"""

    async def run(self, view, session) -> Any:
        evidence = await super().run(view, session)
        # 事件之后: 框架经会话注入的事件已发生, 这里再取一次评测侧读数
        mid = await session.harness_probe("workspace_listing")
        evidence.harness_state.extend(mid)
        return evidence


# ─── 构造辅助 ────────────────────────────────────────────────────────────────


def code_grader(**config) -> GraderConfig:
    cfg = {"checks": [{"type": "contains", "value": "reply", "target": "transcript"}], **config}
    return GraderConfig(type=GraderType.CODE, name="code_based", config=cfg)


def state_grader(**config) -> GraderConfig:
    cfg = {"expectations": [{"type": "file_exists", "path": "output.py"}], **config}
    return GraderConfig(
        type=GraderType.STATE, name="state_check", config=cfg, judgment_moment=cfg.get("moment", JudgmentMoment.AT_END)
    )


def conv_task(task_id: str, graders, **conversation) -> Any:
    from agent_eval.core.types import EvalTask

    return EvalTask(
        id=task_id,
        prompt=f"first turn of {task_id}",
        graders=graders,
        conversation=conversation or None,
        max_trials=2,
    )


def make_runner(agent, **kwargs) -> EvalRunner:
    from agent_eval.core.types import Observation

    async def probe(channel: str):
        return [
            Observation(
                kind=EvidenceKind.STATE,
                observed_by=ObservedBy.HARNESS,
                channel=channel or "workspace_listing",
                value={"files": {"output.py": "def hello(): pass\n"}},
            )
        ]

    from agent_eval.examples.mock_runner import MockTraceProvider

    defaults = dict(
        agent_runner=agent,
        trace_provider=MockTraceProvider(),
        storage=MemoryStorage(),
        environment=_ProbeEnvironment(),
    )
    defaults.update(kwargs)
    return EvalRunner(**defaults)


class _ProbeEnvironment:
    """最小环境: 提供探针与快照, 记录 setup/teardown 次数 (per-trial)。"""

    environment_id = "test-env"
    environment_version = "v1"

    def __init__(self):
        self.setup_count = 0
        self.teardown_count = 0

    async def setup(self, task) -> None:
        self.setup_count += 1

    async def teardown(self, task) -> None:
        self.teardown_count += 1

    async def probe(self, channel: str = ""):
        from agent_eval.core.types import EvidenceKind, Observation, ObservedBy

        return [
            Observation(
                kind=EvidenceKind.STATE,
                observed_by=ObservedBy.HARNESS,
                channel=channel or "workspace_listing",
                value={"files": {"output.py": "def hello(): pass\n"}},
            )
        ]

    async def snapshot(self):
        return {}

    async def verify_clean(self, baseline, harness_readings=None):
        return {"clean": True, "differences": []}

    async def restore(self, baseline) -> None:
        return None


# ─── 会话执行循环 (组 3) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scripted_conversation_one_run_call_zero_model():
    """预写话术跑完整会话: 一次 run() 调用、零模型调用、分母为一个 trial。"""
    agent = TurnConsumerRunner()
    llm_calls: list[tuple[str, str]] = []

    async def llm_fn(system: str, user: str) -> str:
        llm_calls.append((system, user))
        return "unused"

    runner = make_runner(agent, llm_fn=llm_fn)
    task = conv_task(
        "t_multi",
        [code_grader()],
        turns=["第二句话术", "第三句话术"],
    )
    from agent_eval.core.types import EvalSuite

    suite = EvalSuite(name="s", tasks=[task])
    run = await runner.run_suite(suite)

    assert agent.calls == 2  # max_trials=2 → 每 trial 一次 run() 调用
    assert agent.consumed == ["第二句话术", "第三句话术"] * 2  # 每 trial 各消费一轮序列
    assert llm_calls == []  # 预写话术零模型调用
    assert run.statistics_version == "2"
    assert run.summary.total_trials == 2  # 分母为 trial 数, 不乘轮数
    assert run.summary.valid_trials == 2
    for trial in run.trials["t_multi"]:
        assert trial.verdict is TrialVerdict.VALID


@pytest.mark.asyncio
async def test_scripted_user_messages_are_harness_evidence_with_moments():
    """每条模拟用户消息以 harness 来源 + 时刻进 transcript, 与普通消息可区分。"""
    agent = TurnConsumerRunner()
    runner = make_runner(agent)
    task = conv_task("t_evidence", [code_grader()], turns=["轮2"])
    from agent_eval.core.types import EvalSuite

    run = await runner.run_suite(EvalSuite(name="s", tasks=[task]))
    trial = run.trials["t_evidence"][0]
    evidence = trial.evidence

    simulated = [o for o in evidence.transcript if o.channel == SIMULATED_USER_CHANNEL]
    assert len(simulated) == 1
    for obs in simulated:
        assert obs.observed_by is ObservedBy.HARNESS
        assert obs.observed_at > 0
        assert obs.value["simulated"] is True
        assert obs.value["content"] == "轮2"
    # 首轮输入也在用户侧输入序列里, 带时刻
    assert evidence.user_inputs[0].channel == "user_prompt"
    assert evidence.user_inputs[0].value["content"] == task.prompt
    assert evidence.user_inputs[1].channel == SIMULATED_USER_CHANNEL


@pytest.mark.asyncio
async def test_early_exit_adapter_is_invalid_with_config_reason():
    """声明 3 轮只消费 1 轮即返回 → invalid, 文案点名适配器与消费到第几轮。"""
    agent = TurnConsumerRunner(stop_after=1)
    runner = make_runner(agent)
    task = conv_task("t_early", [code_grader()], turns=["r2", "r3", "r4"])
    from agent_eval.core.types import EvalSuite

    run = await runner.run_suite(EvalSuite(name="s", tasks=[task]))
    for trial in run.trials["t_early"]:
        assert trial.verdict is TrialVerdict.INVALID
        assert trial.invalid_reason is InvalidReason.CONVERSATION_NOT_CONSUMED
        assert "TurnConsumerRunner" in trial.error
        assert "3 轮" in trial.error and "1 轮" in trial.error
    assert run.summary.valid_trials == 0  # 不进通过率分母


@pytest.mark.asyncio
async def test_cancel_mid_conversation_invalid_but_evidence_archived():
    """会话中途取消: 证据照常落盘、标注终止原因, 不计入通过率分母。"""
    agent = TurnConsumerRunner()
    runner = make_runner(agent)
    run_id = "run_cancel_test"

    # 第 2 轮消费后触发取消 (适配器向框架要下一轮前, 取消旗标生效)
    original_next = TrialSession.next_user_message

    async def cancel_after_first(self):
        result = await original_next(self)
        if result is not None:
            runner._cancel_flags[run_id] = True
        return result

    TrialSession.next_user_message = cancel_after_first
    try:
        task = conv_task("t_cancel", [code_grader()], turns=["r2", "r3"])
        from agent_eval.core.types import EvalSuite

        run = await runner.run_suite(EvalSuite(name="s", tasks=[task]), run_id=run_id)
    finally:
        TrialSession.next_user_message = original_next

    trial = run.trials["t_cancel"][0]
    assert trial.verdict is TrialVerdict.INVALID
    assert trial.invalid_reason is InvalidReason.TRIAL_CANCELLED
    assert trial.termination_reason.value == "cancelled"
    assert trial.evidence is not None
    assert trial.evidence.transcript  # 已采集证据照常落盘
    assert run.summary.valid_trials == 0


@pytest.mark.asyncio
async def test_conversation_denominator_comparable_with_single_turn_suite():
    """同一多轮任务 N 个 trial: statistics_version=2、分母 N; 与单轮对照逐字段可比。"""
    multi_agent = TurnConsumerRunner()
    runner = make_runner(multi_agent)
    task = conv_task("t_cmp", [code_grader()], turns=["r2"])
    from agent_eval.core.types import EvalSuite

    multi_run = await runner.run_suite(EvalSuite(name="s", tasks=[task]))
    assert multi_run.statistics_version == "2"
    assert multi_run.summary.total_trials == 2

    single_agent = MockAgentRunner(success_rate=1.0, **FAST)
    single_runner = make_runner(single_agent)
    single_task = conv_task("t_cmp", [code_grader()])
    single_run = await single_runner.run_suite(EvalSuite(name="s", tasks=[single_task]))

    a, b = multi_run.summary, single_run.summary
    assert a.pass_at_k.keys() == b.pass_at_k.keys()
    assert a.total_trials == b.total_trials  # 分母不因轮数改变


# ─── 事件注入与「事件之后」判定 (组 4) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_declared_event_injected_after_turn_with_moment():
    """声明的事件在第 N 轮消费后注入, 带时刻进 transcript、来源 harness。"""
    agent = EventAwareRunner()
    runner = make_runner(agent)
    task = conv_task(
        "t_event",
        [code_grader()],
        turns=["r2", "r3"],
        events=[{"after_turn": 1, "message": "文件被外部改动"}],
    )
    from agent_eval.core.types import EvalSuite

    run = await runner.run_suite(EvalSuite(name="s", tasks=[task]))
    evidence = run.trials["t_event"][0].evidence

    events = [o for o in evidence.transcript if o.channel == EVENT_CHANNEL]
    assert len(events) == 1
    assert events[0].observed_by is ObservedBy.HARNESS
    assert events[0].value["event"] == "文件被外部改动"
    assert events[0].observed_at > 0
    # 事件确实落在第 1 轮之后: transcript 中事件位于首轮与第 2 轮话术之间
    assert evidence.user_inputs[0].channel == "user_prompt"
    assert evidence.user_inputs[2].channel == EVENT_CHANNEL
    # 会话诊断块记录了事件数 (不进分母)
    assert run.trials["t_event"][0].session_diagnostics["events_injected"] == 1


@pytest.mark.asyncio
async def test_after_last_event_moment_judges_only_post_event_readings():
    """「事件之后成立」: 判定只依据事件之后的读数。"""
    agent = EventAwareRunner()
    runner = make_runner(agent)
    task = conv_task(
        "t_after_event",
        [state_grader(moment=JudgmentMoment.AFTER_LAST_EVENT)],
        turns=["r2"],
        events=[{"after_turn": 1, "message": "外部改动"}],
    )
    from agent_eval.core.types import EvalSuite

    run = await runner.run_suite(EvalSuite(name="s", tasks=[task]))
    trial = run.trials["t_after_event"][0]
    result = next(g for g in trial.grader_results if g.grader_name == "state_check")
    assert result.verdict is TrialVerdict.VALID
    assert result.judgment_moment is JudgmentMoment.AFTER_LAST_EVENT
    assert "事件" in result.explanation or result.details["judgment_moment"] == "after_last_event"


@pytest.mark.asyncio
async def test_after_last_event_without_any_event_is_insufficient():
    """声明「事件之后」而该 trial 无注入事件 → 报证据不足, 不静默退化为结束时。"""
    agent = TurnConsumerRunner()
    runner = make_runner(agent)
    task = conv_task(
        "t_no_event",
        [state_grader(moment=JudgmentMoment.AFTER_LAST_EVENT)],
        turns=["r2"],
    )
    from agent_eval.core.types import EvalSuite

    run = await runner.run_suite(EvalSuite(name="s", tasks=[task]))
    trial = run.trials["t_no_event"][0]
    result = next(g for g in trial.grader_results if g.grader_name == "state_check")
    assert result.verdict is TrialVerdict.INVALID
    assert result.invalid_reason is InvalidReason.EVIDENCE_UNAVAILABLE
    assert "no_event_injected" in result.explanation


@pytest.mark.asyncio
async def test_human_message_recorded_as_harness_evidence():
    """人工介入消息带时刻落盘, 来源 harness, 与普通消息可区分。"""
    seen: dict[str, Any] = {}

    class HumanInjectingRunner(TurnConsumerRunner):
        async def run(self, view, session) -> Any:
            session.human_message("请改用另一种方案")
            return await super().run(view, session)

    runner = make_runner(HumanInjectingRunner())
    task = conv_task("t_human", [code_grader()], turns=["r2"])
    from agent_eval.core.types import EvalSuite

    run = await runner.run_suite(EvalSuite(name="s", tasks=[task]))
    evidence = run.trials["t_human"][0].evidence
    humans = [o for o in evidence.transcript if o.channel == HUMAN_MESSAGE_CHANNEL]
    assert len(humans) == 1
    assert humans[0].observed_by is ObservedBy.HARNESS
    assert humans[0].value["human"] is True
    seen["count"] = len(evidence.user_inputs)
    # 用户侧输入序列包含人工介入 (可重放脚本)
    assert any(o.channel == HUMAN_MESSAGE_CHANNEL for o in evidence.user_inputs)


# ─── 离线重放 (组 4.3 / 4.4) ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_user_inputs_form_replayable_script_and_regrade_makes_zero_calls():
    """用户侧输入序列随证据落盘; 库层重评分被评系统调用次数为零。"""
    agent = TurnConsumerRunner()
    runner = make_runner(agent)
    task = conv_task(
        "t_replay",
        [code_grader()],
        turns=["r2", "r3"],
        events=[{"after_turn": 2, "message": "事件A"}],
    )
    from agent_eval.core.types import EvalSuite

    run = await runner.run_suite(EvalSuite(name="s", tasks=[task]))
    assert agent.calls == 2
    run_id = run.run_id

    # 库层重评分: 不重新运行被评系统
    calls_before = agent.calls
    regraded = await runner.regrade_run(run_id)
    assert agent.calls == calls_before  # 被评系统调用次数为零
    trial = regraded.trials["t_replay"][0]
    assert trial.grader_results  # 重新出了结论
    # 被评方输出不因重放而改写 (证据原样)
    assert [o.value for o in trial.evidence.transcript] == [
        o.value for o in run.trials["t_replay"][0].evidence.transcript
    ]
    # 重放脚本: 首轮 + 2 轮话术 + 1 事件, 按时间升序
    script = trial.evidence.user_inputs
    assert [o.channel for o in script] == [
        "user_prompt",
        SIMULATED_USER_CHANNEL,
        SIMULATED_USER_CHANNEL,
        EVENT_CHANNEL,
    ]
    moments = [o.observed_at for o in script]
    assert moments == sorted(moments)


# ─── 目标驱动模拟器 (组 6) ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_goal_driven_without_llm_is_unavailable_not_crash():
    """缺 LLM 配置: 带原因的不可用结论, trial 不崩溃不折成 agent 失败。"""
    agent = TurnConsumerRunner()
    runner = make_runner(agent, llm_fn=None)
    task = conv_task("t_no_llm", [code_grader()], goal="拿到单元测试通过")
    from agent_eval.core.types import EvalSuite

    run = await runner.run_suite(EvalSuite(name="s", tasks=[task]))
    for trial in run.trials["t_no_llm"]:
        assert trial.verdict is TrialVerdict.INVALID
        assert trial.invalid_reason is InvalidReason.SIMULATOR_UNAVAILABLE
        assert "llm_fn" in trial.error
    assert run.summary.valid_trials == 0  # 不占分母


class StubLLM:
    """确定性 stub 回调: 返回预排话术, 最后一句为纯 [END] 收尾信号。"""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.replies.pop(0) if self.replies else GOAL_END_SENTINEL


@pytest.mark.asyncio
async def test_goal_driven_stub_generates_and_framework_ends_session():
    """目标驱动: stub 生成两句话术后 [END]; 收尾判定归框架 (end_reason 落盘)。"""
    llm = StubLLM(["请问预算多少？", "第二问", GOAL_END_SENTINEL])
    agent = TurnConsumerRunner()
    runner = make_runner(agent, llm_fn=llm)
    task = conv_task(
        "t_goal",
        [code_grader()],
        goal="完成采购咨询",
    )
    from agent_eval.core.types import EvalSuite

    run = await runner.run_suite(EvalSuite(name="s", tasks=[task]))
    trial = run.trials["t_goal"][0]
    assert trial.verdict is TrialVerdict.VALID
    diag = trial.session_diagnostics
    assert diag["goal"] == "完成采购咨询"
    assert diag["end_reason"] == "goal_achieved"
    assert diag["turns_consumed"] == 2  # [END] 不多消费一轮
    # 每句话术连所用提示词进证据 (供事后复核)
    evidence = trial.evidence
    simulated = [o for o in evidence.transcript if o.channel == SIMULATED_USER_CHANNEL]
    assert len(simulated) == 2
    assert all("simulator_prompt" in o.value for o in simulated)
    assert llm.calls  # 生成确实经 LLMFn


@pytest.mark.asyncio
async def test_goal_driven_prompt_redacted_and_view_has_no_answer_keys():
    """模拟器输入视图不含期望输出/答案键; 提示词过脱敏钩子。"""
    captured: list[str] = []

    class RecordingRedactor:
        def redact(self, value: Any) -> Any:
            return f"<redacted:{len(str(value))}>"

    llm = StubLLM(["好的", GOAL_END_SENTINEL])

    class InspectingSimulator(GoalDrivenUserSimulator):
        async def next_message(self, context: SimulatorContext) -> SimulatorReply | None:
            captured.append(str(context.__dict__))
            return await super().next_message(context)

    class SimRunner(TurnConsumerRunner):
        pass

    task = conv_task("t_barrier", [code_grader()], goal="目标G")
    # 直接对协议做裁剪断言: 构造 context 的 runner 只给 description/goal/history
    session = TrialSession(
        simulator=InspectingSimulator("目标G", llm_fn=llm, redactor=RecordingRedactor()),
        conversation=task.conversation,
        first_user_message="first",
    )
    reply = await session.next_user_message()
    assert reply == "好的"  # next_user_message 返回话术文本
    recorded = session.injected[0].value
    assert recorded["simulator_prompt"].startswith("<redacted:")  # 提示词经脱敏钩子
    ctx = session._history
    assert ctx  # 历史在会话侧, 不含判据
    # SimulatorContext 字段面上没有期望输出/答案键的位置
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(SimulatorContext)}
    assert not field_names & {
        "expected_output",
        "answer_key",
        "graders",
        "config",
        "criteria",
    }


# ─── 套件校验 (组 1.5) ───────────────────────────────────────────────────────


def test_conversation_turns_and_goal_mutex_rejected_with_field_path(tmp_path):
    """turns 与 goal 并存 → 加载失败并指出字段路径。"""
    suite_yaml = """
name: mutex
version: 1.0.0
tasks:
  - id: t1
    prompt: p
    graders:
      - type: code
        name: code_based
        config: {}
    conversation:
      turns: ["第二句"]
      goal: 目标
"""
    path = tmp_path / "suite.yaml"
    path.write_text(suite_yaml, encoding="utf-8")
    with pytest.raises(SuiteLoadError) as exc:
        load_suite(path)
    assert "conversation.turns" in str(exc.value)
    assert "conversation.goal" in str(exc.value)


def test_suite_without_conversation_loads_byte_identical(tmp_path):
    """不含会话声明的既有套件: 加载结果与本变更前一致, 无空会话段落。"""
    suite_yaml = """
name: legacy
version: 1.0.0
tasks:
  - id: t1
    prompt: p
    graders:
      - type: code
        name: code_based
        config: {}
"""
    path = tmp_path / "suite.yaml"
    path.write_text(suite_yaml, encoding="utf-8")
    suite = load_suite(path)
    assert suite.tasks[0].conversation is None
    assert suite.tasks[0].environment is None
    assert suite.environment is None


def test_scripted_simulator_exhaustion_ends_session():
    """预写话术用尽返回 None; 会话结束原因落盘。"""
    session = TrialSession(
        simulator=ScriptedUserSimulator(["只有一句"]),
        conversation=None,
        first_user_message="first",
    )

    async def drive():
        first = await session.next_user_message()
        second = await session.next_user_message()
        return first, second

    first, second = asyncio.run(drive())
    assert first == "只有一句"
    assert second is None
    assert session.end_reason == "simulator_exhausted"
