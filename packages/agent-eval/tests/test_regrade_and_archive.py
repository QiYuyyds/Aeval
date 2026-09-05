"""证据归档、库层重评分与敏感证据留存 (change ③ tasks 5.x / 6.x / 8.x)。

采集与评分一旦分离, 下面这几件事就必须同时成立:

- 证据按 trial 独立落盘并能完整取回 (含来源级别与缺失原因);
- 每次判定记下自己的口径, 重评只追加不覆盖;
- 重评分绝不触碰被评系统, 且证据不齐时整体拒绝而不是产出新结论;
- 未开启采集的正文不落盘, 按 run 删除后不留残留;
- 本变更前落盘的 run 仍可读, 但会被明确挡在重评分之外。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agent_eval.core.runner import (
    EvalRunner,
    IncompleteEvidence,
    RegradeUnavailable,
)
from agent_eval.core.types import (
    CaptureDecision,
    CapturePolicy,
    EvalSuite,
    EvalTask,
    GraderConfig,
    GraderType,
    JudgmentMoment,
    ObservedBy,
    RunResult,
    TerminationReason,
    TrialVerdict,
)
from agent_eval.examples.mock_runner import MockAgentRunner, MockTraceProvider
from agent_eval.storage.memory import MemoryStorage
from agent_eval.storage.sqlite import SqliteStorage
from agent_eval.trace.mapping import (
    FIELD_TOOL_ARGUMENTS,
    FIELD_TOOL_NAME,
    default_mapping,
)
from agent_eval.trace.normalize import UNCAPTURED_MARKER

FAST = (0.0, 0.01)
SECRET = "sk-live-abcdef1234567890"
BODY = "这是模型的正文输出，不该默认落盘"


class ProbeEnv:
    """结束前能取证的环境: 取证结果里带上被测文件, 使判分不依赖任何自报内容。"""

    def __init__(self, files: dict[str, str] | None = None):
        self.files = files if files is not None else {"output.py": "def hello(): pass"}
        self.probe_calls = 0

    async def setup(self, task: EvalTask) -> None:
        return None

    async def teardown(self, task: EvalTask) -> None:
        return None

    async def snapshot(self) -> dict[str, Any]:
        return {"files": {}}

    async def probe(self, channel: str = "") -> list[dict[str, Any]]:
        self.probe_calls += 1
        return [{"files": dict(self.files)}]

    async def verify_clean(self, baseline, harness_readings=None) -> dict[str, Any]:
        self.verified_with_readings = bool(harness_readings)
        return {"clean": True, "differences": []}

    async def restore(self, baseline) -> None:
        return None


def state_task(task_id: str = "t1", **grader_kwargs) -> EvalTask:
    return EvalTask(
        id=task_id,
        prompt="写一个 output.py",
        max_trials=2,
        graders=[
            GraderConfig(
                type=GraderType.STATE,
                name="state_check",
                config={"expectations": [{"type": "file_exists", "path": "output.py"}]},
                **grader_kwargs,
            )
        ],
    )


def make_runner(agent, *, storage=None, environment=None, **kwargs) -> EvalRunner:
    kwargs.setdefault(
        "trace_provider", MockTraceProvider(default_spans=[])
    )
    return EvalRunner(
        agent_runner=agent,
        storage=storage or MemoryStorage(),
        environment=environment,
        **kwargs,
    )


async def collected_run(storage=None) -> tuple[EvalRunner, RunResult]:
    agent = MockAgentRunner(success_rate=1.0, latency_range=FAST, script={"t1": ["success"]})
    runner = make_runner(agent, storage=storage, environment=ProbeEnv())
    run = await runner.run_suite(
        EvalSuite(name="archive-suite", version="1.0.0", tasks=[state_task()])
    )
    return runner, run


def spans_with_content() -> list[dict[str, Any]]:
    table = default_mapping()
    return [
        {
            "name": "tool.call",
            "attributes": {
                table.candidates(FIELD_TOOL_NAME)[0]: "fs_write",
                table.candidates(FIELD_TOOL_ARGUMENTS)[0]: {"token": SECRET},
                "gen_ai.input.messages": BODY,
                "gen_ai.usage.input_tokens": 10,
                "gen_ai.usage.output_tokens": 5,
            },
            "start_time": "2026-09-05T10:00:00Z",
            "end_time": "2026-09-05T10:00:01Z",
            "status": {"status_code": "OK"},
        }
    ]


# ── 5.1 证据独立持久化并可完整取回 ───────────────────────────────────────────


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_evidence_archived_per_trial_and_retrievable(backend, tmp_path):
    storage = SqliteStorage(db_path=str(tmp_path / "e.db")) if backend == "sqlite" else MemoryStorage()
    if backend == "sqlite":
        await storage.initialize()

    _runner, run = await collected_run(storage)

    for trial in run.trials["t1"]:
        archived = await storage.get_trial_evidence(run.run_id, "t1", trial.trial_index)
        assert archived is not None, "每条 trial 的证据都要独立落盘"
        assert archived.trace_id == trial.trace_id
        # 来源级别与缺失原因随读数一起回来
        end = archived.harness_state[-1]
        assert end.observed_by is ObservedBy.HARNESS
        assert end.observed_at > 0
        assert [obs.observed_by for obs in archived.subject_state] == [ObservedBy.RUNNER]
        assert archived.capture == CaptureDecision()
    # run 记录本身不带证据正文 (同一份内容不存两处)
    assert "harness_state" not in json.dumps(run.model_dump(mode="json"))


async def test_run_record_does_not_duplicate_the_evidence_blob(tmp_path):
    storage = SqliteStorage(db_path=str(tmp_path / "e.db"))
    await storage.initialize()
    _runner, run = await collected_run(storage)

    stored = await storage.get_run(run.run_id)
    evidence = await storage.get_trial_evidence(run.run_id, "t1", 0)

    assert stored.trials["t1"][0].evidence is None
    assert evidence is not None and evidence.harness_state
    # 但「可不可重评」这个旗标跟着 run 记录走, 读出来就知道
    assert stored.trials["t1"][0].evidence_archived is True


# ── 5.2 判定历史: 口径随结论落盘, current 指针, 永不覆盖 ──────────────────────


async def test_each_verdict_records_its_caliber():
    runner, run = await collected_run()

    attempts = await runner.storage.list_grade_attempts(run.run_id)
    assert len(attempts) == 2  # 两条 trial 各一次当场判定
    first = attempts[0]
    assert first.is_current is True
    assert first.triggered_by == "run"
    assert first.grader_versions["state_check"] == "2"
    assert first.mapping_version and first.spec_version
    assert first.statistics_version
    assert first.created_at > 0
    assert first.trial.trial_index == 0
    # 历史条目里不重复存证据正文
    assert first.trial.evidence is None


async def test_regrade_appends_and_moves_the_current_pointer():
    runner, run = await collected_run()
    original = (await runner.storage.list_grade_attempts(run.run_id))[0]
    assert original.triggered_by == "run"

    # 判据换口径: 现在只认评测侧取证 (原结论不得消失)
    narrowed = EvalSuite(
        name="archive-suite",
        version="1.0.0",
        tasks=[state_task(evidence=["harness"], judgment_moment=JudgmentMoment.AT_END)],
    )
    regaded = await runner.regrade_run(run.run_id, suite=narrowed)

    attempts = await runner.storage.list_grade_attempts(run.run_id)
    assert [a.triggered_by for a in attempts] == ["run", "run", "regrade", "regrade"]
    assert [a.is_current for a in attempts] == [False, False, True, True]
    # 原结论完整保留, 与新结论并列
    kept = attempts[0]
    assert kept.trial.success is True
    assert regaded.trials["t1"][0].success is True
    assert attempts[2].trial.success is True


# ── 5.3 / 5.4 重评分不回查被评系统, 证据不齐即拒绝 ───────────────────────────


async def test_regrade_never_touches_the_evaluated_system():
    class CountingAgent:
        def __init__(self):
            self.calls = 0

        async def run(self, view, session):
            self.calls += 1
            return TrialEvidence_stub(view)

    from agent_eval.core.types import TrialEvidence

    def TrialEvidence_stub(view):
        return TrialEvidence.runner_reported(
            trace_id=f"trace_{view.id}",
            transcript=[{"role": "user", "content": view.prompt}],
            state={"files": {"output.py": ""}},
        )

    agent = CountingAgent()
    runner = make_runner(agent, environment=ProbeEnv())
    run = await runner.run_suite(
        EvalSuite(name="archive-suite", version="1.0.0", tasks=[state_task()])
    )
    assert agent.calls == 2

    before = await runner.storage.get_run(run.run_id)
    regaded = await runner.regrade_run(run.run_id)
    after = await runner.storage.get_run(run.run_id)

    assert agent.calls == 2, "重评分不得触发任何对被评系统的调用"
    assert [t.success for t in regaded.trials["t1"]] == [
        t.success for t in before.trials["t1"]
    ]
    assert after.summary is not None


async def test_regrade_refuses_when_evidence_is_incomplete():
    runner, run = await collected_run()
    storage = runner.storage
    # 模拟「写入失败/被清理」: 抹掉第二条 trial 的证据
    storage._evidence.pop((run.run_id, "t1", 1))

    with pytest.raises(IncompleteEvidence) as raised:
        await runner.regrade_run(run.run_id)

    assert "t1#1" in str(raised.value)
    # 拒绝得干脆: 原结论一条都没变
    stored = await storage.get_run(run.run_id)
    assert [t.success for t in stored.trials["t1"]] == [True, True]
    assert len(await storage.list_grade_attempts(run.run_id)) == 2


async def test_regrade_requires_the_grader_definitions():
    runner, run = await collected_run()
    await runner.storage.delete_suite("archive-suite")

    with pytest.raises(RegradeUnavailable, match="套件定义"):
        await runner.regrade_run(run.run_id)


async def test_summary_counts_only_the_current_verdict():
    """同一 trial 有多个结论时: current 参与统计, 历史只用于审计与漂移度量。"""
    runner, run = await collected_run()
    storage = runner.storage

    # 手工把第二条 trial 的 current 换成一条「未通过」结论 (模拟重评翻判)
    from agent_eval.core.types import GradeAttempt

    attempts = await storage.list_grade_attempts(run.run_id, "t1", 1)
    flipped = attempts[0].trial.model_copy(update={"success": False})
    await storage.save_grade_attempt(
        GradeAttempt(
            run_id=run.run_id,
            task_id="t1",
            trial_index=1,
            triggered_by="regrade",
            trial=flipped,
        )
    )

    stored = await storage.get_run(run.run_id)
    assert stored.trials["t1"][1].success is True  # run 记录仍指向当场结论

    drift = await runner.verdict_drift(run.run_id)
    assert drift["trials"] == 2
    assert drift["regraded_trials"] == 1
    assert drift["flipped_trials"] == 1
    assert drift["flip_rate"] == pytest.approx(1.0)
    assert drift["attempts"] == 3


# ── 6.x 采集开关: 一套声明, 两个字段 ─────────────────────────────────────────


async def test_content_capture_is_off_by_default_and_not_persisted():
    agent = MockAgentRunner(success_rate=1.0, latency_range=FAST, script={"t1": ["success"]})
    runner = make_runner(
        agent,
        trace_provider=MockTraceProvider(spans_by_task={"t1": spans_with_content()}),
    )
    run = await runner.run_suite(
        EvalSuite(name="capture-suite", version="1.0.0", tasks=[state_task()])
    )

    blob = json.dumps(run.model_dump(mode="json"), default=str)
    archived = await runner.storage.get_trial_evidence(run.run_id, "t1", 0)
    assert SECRET not in blob and BODY not in blob
    assert SECRET not in json.dumps(archived.model_dump(mode="json"))
    assert BODY not in json.dumps(archived.model_dump(mode="json"))
    assert archived.capture == CaptureDecision()
    assert set(archived.stripped_attributes) == {
        "gen_ai.tool.call.arguments",
        "gen_ai.input.messages",
    }
    assert run.evidence.capture_tool_arguments is False
    assert run.evidence.capture_model_content is False


async def test_the_two_switches_stay_independent_and_visible():
    """只开正文不开入参 (及反向): 两者状态各自独立, 结果里都看得见。"""
    content_only = state_task("t1")
    content_only.capture = CapturePolicy(model_content=True)
    args_only = state_task("t2")
    args_only.capture = CapturePolicy(tool_arguments=True)
    suite = EvalSuite(name="split-suite", version="1.0.0", tasks=[content_only, args_only])

    agent = MockAgentRunner(success_rate=1.0, latency_range=FAST)
    runner = make_runner(
        agent,
        trace_provider=MockTraceProvider(
            default_spans=[], spans_by_task={"t1": spans_with_content(), "t2": spans_with_content()}
        ),
    )
    run = await runner.run_suite(suite)

    assert run.evidence.capture_tool_arguments is True   # 有一个 task 开了
    assert run.evidence.capture_model_content is True
    assert run.evidence.capture_by_task == {"t1": False, "t2": True}
    assert run.evidence.capture_content_by_task == {"t1": True, "t2": False}

    t1 = await runner.storage.get_trial_evidence(run.run_id, "t1", 0)
    t2 = await runner.storage.get_trial_evidence(run.run_id, "t2", 0)
    assert t1.capture == CaptureDecision(model_content=True)
    assert t2.capture == CaptureDecision(tool_arguments=True)

    # 开了的那一类落脱敏摘要 (明文不落盘), 没开的那一类落未采集标记
    t1_attrs = t1.source_spans[0]["attributes"]
    t2_attrs = t2.source_spans[0]["attributes"]
    assert t1_attrs["gen_ai.input.messages"].startswith("redacted:sha256:")
    assert t1_attrs["gen_ai.tool.call.arguments"] == UNCAPTURED_MARKER
    t2_arguments = t2_attrs["gen_ai.tool.call.arguments"]
    assert isinstance(t2_arguments, dict) and set(t2_arguments) == {"token"}
    assert t2_arguments["token"].startswith("redacted:sha256:")
    assert t2_attrs["gen_ai.input.messages"] == UNCAPTURED_MARKER
    # 两条摘要在两个 task 上一致: 采集与否互不牵动
    assert t1.stripped_attributes == ["gen_ai.tool.call.arguments"]
    assert t2.stripped_attributes == ["gen_ai.input.messages"]
    for blob in (t1, t2):
        text = json.dumps(blob.model_dump(mode="json"))
        assert SECRET not in text and BODY not in text


async def test_uncaptured_content_yields_evidence_unavailable_not_failure():
    """没开入参采集时, 需要入参的判据报「证据不可用」而不是判 agent 失败。"""
    task = EvalTask(
        id="t1",
        prompt="p",
        max_trials=1,
        graders=[
            GraderConfig(
                type=GraderType.TOOL_CALLS,
                name="tool_calls",
                config={"required_tools": ["fs_write"]},
            )
        ],
    )
    agent = MockAgentRunner(success_rate=1.0, latency_range=FAST, script={"t1": ["success"]})
    runner = make_runner(
        agent,
        trace_provider=MockTraceProvider(default_spans=[], spans_by_task={"t1": spans_with_content()}),
    )

    run = await runner.run_suite(EvalSuite(name="gap-suite", version="1.0.0", tasks=[task]))
    result = run.trials["t1"][0].grader_results[0]

    # 工具名读得到 → 判据仍能给出关于 agent 的有效结论; 入参这一项单独报缺
    assert result.verdict is TrialVerdict.VALID
    assert result.details["tool_calls"][0]["arguments"] == {
        "missing": True,
        "reason": "capture_disabled",
        "detail": "套件未开启工具入参采集",
    }
    assert run.trials["t1"][0].evidence.stripped_attributes == [
        "gen_ai.input.messages",
        "gen_ai.tool.call.arguments",
    ]


async def test_deleting_a_run_leaves_no_evidence_or_attempt(tmp_path):
    storage = SqliteStorage(db_path=str(tmp_path / "del.db"))
    await storage.initialize()
    runner, run = await collected_run(storage)
    assert await storage.get_trial_evidence(run.run_id, "t1", 0) is not None

    assert await storage.delete_run(run.run_id) is True

    assert await storage.get_run(run.run_id) is None
    assert await storage.get_trial_evidence(run.run_id, "t1", 0) is None
    assert await storage.list_grade_attempts(run.run_id) == []
    # 直查底表: 派生内容一行都不留 (开启采集的正文不得以孤儿行形式存活)
    residue = await _raw_rows(storage.db_path)
    assert residue == {"trial_evidence": 0, "grade_attempts": 0, "runs": 0}


async def _raw_rows(db_path: str) -> dict[str, int]:
    import aiosqlite

    async with aiosqlite.connect(db_path) as db:
        out: dict[str, int] = {}
        for table in ("trial_evidence", "grade_attempts", "runs"):
            cursor = await db.execute(f"SELECT COUNT(*) AS n FROM {table}")
            row = await cursor.fetchone()
            out[table] = row[0]
        return out


# ── 8.x 历史 run: 可读、不可重评, 并且与统计口径版本一起显示 ──────────────────


LEGACY_RUN = {
    "run_id": "run_legacy000001",
    "suite_name": "old-suite",
    "status": "completed",
    "started_at": 1_760_000_000_000.0,
    "completed_at": 1_760_000_060_000.0,
    "trials": {
        "t1": [
            {
                "trial_index": 0,
                "trace_id": "trace_old",
                "success": True,
                "grader_results": [
                    {
                        "grader_name": "state_check",
                        "grader_type": "state",
                        "score": 1.0,
                        "passed": True,
                        "explanation": "1/1 state checks passed",
                    }
                ],
                "metrics": {"latency_ms": 1200.0},
                "transcript": [{"role": "assistant", "content": "我建好了 output.py"}],
                "outcome": {"files": {"output.py": ""}},
                "verdict": "valid",
            }
        ]
    },
}


async def test_legacy_run_is_readable_but_not_regradeable():
    storage = MemoryStorage()
    legacy = RunResult.model_validate(LEGACY_RUN)
    await storage.save_run(legacy)

    runner = make_runner(
        MockAgentRunner(success_rate=1.0, latency_range=FAST), storage=storage
    )
    stored = await storage.get_run(legacy.run_id)
    trial = stored.trials["t1"][0]

    # 既有字段照读不误
    assert trial.success is True
    assert trial.grader_results[0].score == 1.0
    assert trial.outcome["files"] == {"output.py": ""}
    # 但它在结构上就被标为「不可重评」
    assert trial.evidence is None
    assert trial.evidence_archived is False
    assert trial.regradeable is False
    assert stored.regradeable is False
    assert stored.evidence is None  # 历史 run 没记证据边界

    with pytest.raises(RegradeUnavailable, match="不可重评分"):
        await runner.regrade_run(legacy.run_id)


async def test_legacy_trials_do_not_gain_verdicts_by_default():
    """历史 trial 缺的字段读为「未知」, 不得被默认值补成一个看起来正常的结论。"""
    trial = RunResult.model_validate(LEGACY_RUN).trials["t1"][0]
    assert trial.termination_reason is None
    assert trial.weakest_evidence is None
    assert trial.evidence_gaps == []
    # 汇总里落进 unknown 桶而不是某个具体终止原因
    counts = {t.termination_reason for t in [trial]}
    assert counts == {None}


async def test_summary_and_run_stay_consistent_after_three_phases():
    """三相推进后统计口径不变: valid/invalid 分母仍按 ① 的规则算。"""
    runner, run = await collected_run()

    summary = run.summary
    ts = summary.task_summaries[0]
    assert ts.total_trials == 2
    assert ts.valid_trials == 2
    assert ts.invalid_trials == 0
    assert ts.pass_at_k[1] == 1.0
    assert ts.termination_reasons == {TerminationReason.AGENT_COMPLETED.value: 2}
    assert run.statistics_version == "2"
    # 证据强度贯通到汇总: 这次靠的是评测侧独立取证
    assert ts.evidence_levels == {"harness": 2}
    assert summary.evidence_levels == {"harness": 2}
