"""证据来源分级的类型系统 (change ③ task 1.5)。

同一条观测分别以三级来源表达时, 必须在类型与序列化结果上都区分得出来 —— 否则
分级只是文档里的约定。缺失与空值的区别沿用 ② 的语义, 不另造第二套表示。
"""

from __future__ import annotations

import pytest

from agent_eval.core.types import (
    CaptureDecision,
    CapturePolicy,
    EvidenceKind,
    GraderConfig,
    GraderType,
    JudgmentMoment,
    Observation,
    ObservedBy,
    TrialEvidence,
    TrialResult,
    weakest_level,
)
from agent_eval.trace.observations import AbsentReason

SAME_READING = {"files": {"a.py": "print(1)"}, "db_records": []}


def _at(level: ObservedBy, *, at: float = 1_700_000_000_000.0) -> Observation:
    return Observation(
        kind=EvidenceKind.STATE, observed_by=level, observed_at=at, value=dict(SAME_READING)
    )


# ── 1.1 三级来源 + 采集时刻 ──────────────────────────────────────────────────


def test_same_reading_is_distinguishable_across_levels():
    dumped = [_at(level).model_dump() for level in ObservedBy]
    assert {d["observed_by"] for d in dumped} == {"harness", "runner", "subject"}
    # 值完全相同: 唯一区分得出来的就是来源与时刻这一对
    assert len({d["observed_by"] for d in dumped}) == 3
    assert all(d["observed_at"] == 1_700_000_000_000.0 for d in dumped)


def test_observed_at_defaults_to_collection_time():
    before = Observation(kind=EvidenceKind.BUDGET, observed_by=ObservedBy.HARNESS)
    assert before.observed_at > 0


def test_levels_are_ordered_by_strength():
    assert ObservedBy.HARNESS.strength > ObservedBy.RUNNER.strength > ObservedBy.SUBJECT.strength
    assert weakest_level(["subject", "harness"]) is ObservedBy.SUBJECT
    assert weakest_level(["harness"]) is ObservedBy.HARNESS
    assert weakest_level([]) is None


def test_default_declaration_excludes_subject():
    assert ObservedBy.default_declaration() == (ObservedBy.HARNESS, ObservedBy.RUNNER)


# ── 缺失 ≠ 空值 (② 的语义在此延续) ───────────────────────────────────────────


def test_absent_reading_differs_from_empty_reading():
    absent = Observation.absent(
        EvidenceKind.STATE,
        AbsentReason.PROVIDER_UNAVAILABLE.value,
        channel="fs_listdir",
        detail="探针未接入",
    )
    empty = Observation(
        kind=EvidenceKind.STATE, observed_by=ObservedBy.HARNESS, value={"files": {}}
    )
    assert absent.is_absent and not empty.is_absent
    assert absent.render()["missing"] is True
    assert absent.render()["reason"] == "provider_unavailable"
    assert absent.render()["observed_by"] == "harness"
    # 空读数原样呈现: 环境里确实没有文件, 与「没去看」不是一回事
    assert empty.render() == {"files": {}}


def test_absent_readings_are_not_merged_into_state_payload():
    evidence = TrialEvidence(
        subject_state=[
            Observation.absent(
                EvidenceKind.STATE,
                AbsentReason.PROVIDER_UNAVAILABLE.value,
                observed_by=ObservedBy.RUNNER,
            )
        ]
    )
    assert evidence.state_payload([ObservedBy.RUNNER]) == {}
    window = evidence.state_window(ObservedBy.RUNNER)
    assert not window.usable
    assert window.reason == AbsentReason.PROVIDER_UNAVAILABLE.value


# ── 1.2 TrialEvidence 的通道划分 ─────────────────────────────────────────────


def test_two_state_channels_coexist_and_contradict():
    """被评方自报「已创建 a.py」而取证清单里没有: 两条读数都保留, 各带来源。"""
    evidence = TrialEvidence(
        subject_state=[_at(ObservedBy.SUBJECT)],
        harness_state=[
            Observation(
                kind=EvidenceKind.STATE,
                observed_by=ObservedBy.HARNESS,
                observed_at=1_700_000_000_500.0,
                value={"files": {}},
            )
        ],
    )
    assert evidence.state_payload([ObservedBy.SUBJECT])["files"] == {"a.py": "print(1)"}
    assert evidence.state_window(ObservedBy.HARNESS).payload == {"files": {}}
    round_tripped = TrialEvidence.model_validate(evidence.model_dump())
    assert round_tripped == evidence


def test_runner_reported_wraps_every_channel_once():
    evidence = TrialEvidence.runner_reported(
        trace_id="trace_1",
        transcript=[{"role": "user", "content": "hi"}],
        state={"files": {"a.py": ""}},
        steps=[{"tool": "fs_write"}],
        artifacts=[{"type": "code_file"}],
    )
    assert evidence.trace_id == "trace_1"
    assert {obs.observed_by for obs in evidence.transcript} == {ObservedBy.RUNNER}
    assert evidence.messages() == [{"role": "user", "content": "hi"}]
    assert evidence.state_payload()["files"] == {"a.py": ""}
    # 只有被评方自报时, 取证通道是「没取到」而不是「空」
    assert not evidence.state_window(ObservedBy.HARNESS).usable


def test_subject_level_survives_serialisation():
    evidence = TrialEvidence.runner_reported(
        state={"claims": "我已完成"}, observed_by=ObservedBy.SUBJECT
    )
    dumped = evidence.model_dump()
    assert dumped["subject_state"][0]["observed_by"] == "subject"
    restored = TrialEvidence.model_validate(dumped)
    assert restored.subject_state[0].observed_by is ObservedBy.SUBJECT


# ── 1.3 判定时刻: 结束时 / 结束时不成立 / 任一时刻 ───────────────────────────


def _probed(*, mid: dict | None, end: dict) -> TrialEvidence:
    readings = []
    if mid is not None:
        readings.append(
            Observation(
                kind=EvidenceKind.STATE,
                observed_by=ObservedBy.HARNESS,
                observed_at=1_000.0,
                value=mid,
            )
        )
    readings.append(
        Observation(
            kind=EvidenceKind.STATE,
            observed_by=ObservedBy.HARNESS,
            observed_at=2_000.0,
            value=end,
        )
    )
    return TrialEvidence(harness_state=readings)


def test_created_then_deleted_only_passes_any_time():
    evidence = _probed(mid={"files": {"tmp.py": ""}}, end={"files": {}})
    assert evidence.observed_window
    at_end = evidence.state_window(ObservedBy.HARNESS, JudgmentMoment.AT_END)
    any_time = evidence.state_window(ObservedBy.HARNESS, JudgmentMoment.ANY_TIME)
    assert "tmp.py" not in at_end.payload["files"]
    assert "tmp.py" in any_time.payload["files"]
    assert any_time.readings == evidence.harness_state


def test_any_time_never_guessed_from_a_single_end_snapshot():
    evidence = _probed(mid=None, end={"files": {"a.py": ""}})
    assert not evidence.observed_window
    window = evidence.state_window(ObservedBy.HARNESS, JudgmentMoment.ANY_TIME)
    assert not window.usable
    assert "任一时刻" in window.detail


def test_not_at_end_reads_the_same_end_state_inverted():
    evidence = _probed(mid={"files": {"tmp.py": ""}}, end={"files": {}})
    window = evidence.state_window(ObservedBy.HARNESS, JudgmentMoment.NOT_AT_END)
    assert window.invert is True
    assert window.payload["files"] == {}


def test_end_state_merges_every_probe_channel():
    """一次结束前取证跨多通道: 结束态必须是各通道最后读数的合并。

    真实链路验收踩过这个坑: db_dump 排在 workspace_files 之后, 只取序列最后一条
    就让「库里没有 files 键」把文件清单整个顶掉, 于是明明存在的 hello.md 判不成立。
    """
    evidence = TrialEvidence(
        harness_state=[
            Observation(
                kind=EvidenceKind.STATE, observed_by=ObservedBy.HARNESS, observed_at=1_000.0,
                channel="workspace_files", value={"files": {"hello.md": "Hello from Aeval!"}},
            ),
            Observation(
                kind=EvidenceKind.STATE, observed_by=ObservedBy.HARNESS, observed_at=1_000.0,
                channel="db_dump", value={"db_records": [{"id": 1}], "artifact_count": 2},
            ),
        ]
    )
    window = evidence.state_window(ObservedBy.HARNESS, JudgmentMoment.AT_END)
    assert window.usable
    assert window.payload["files"] == {"hello.md": "Hello from Aeval!"}
    assert window.payload["db_records"] == [{"id": 1}]
    assert len(window.readings) == 2


def test_end_state_keeps_the_last_reading_of_each_channel():
    """同一通道被取多次时, 结束态用它自己最后一次读数 (中途那次不算终态)。"""
    evidence = TrialEvidence(
        harness_state=[
            Observation(
                kind=EvidenceKind.STATE, observed_by=ObservedBy.HARNESS, observed_at=1_000.0,
                channel="workspace_files", value={"files": {"tmp.py": ""}},
            ),
            Observation(
                kind=EvidenceKind.STATE, observed_by=ObservedBy.HARNESS, observed_at=2_000.0,
                channel="workspace_files", value={"files": {}},
            ),
            Observation(
                kind=EvidenceKind.STATE, observed_by=ObservedBy.HARNESS, observed_at=2_000.0,
                channel="db_dump", value={"rows": 3},
            ),
        ]
    )
    at_end = evidence.state_window(ObservedBy.HARNESS, JudgmentMoment.AT_END)
    any_time = evidence.state_window(ObservedBy.HARNESS, JudgmentMoment.ANY_TIME)
    assert at_end.payload == {"files": {}, "rows": 3}
    assert any_time.payload["files"] == {"tmp.py": ""}  # 窗口是存在量词


def test_subject_channel_cannot_answer_any_time():
    evidence = TrialEvidence(
        subject_state=[_at(ObservedBy.SUBJECT, at=1.0), _at(ObservedBy.SUBJECT, at=2.0)]
    )
    window = evidence.state_window(ObservedBy.SUBJECT, JudgmentMoment.ANY_TIME)
    assert not window.usable


# ── 1.4 TrialResult 承载证据, 新字段全给默认值 ───────────────────────────────


def test_trial_result_defaults_keep_old_payloads_loadable():
    """变更前落盘的 trial JSON 不含新字段, 仍须能读出来 —— 且标为不可重评。"""
    legacy = TrialResult.model_validate({"trial_index": 0, "trace_id": "t", "success": True})
    assert legacy.evidence is None
    assert legacy.evidence_archived is False
    assert legacy.regradeable is False
    assert legacy.weakest_evidence is None


def test_trial_with_archived_evidence_is_regradeable():
    trial = TrialResult(
        trial_index=0,
        evidence=TrialEvidence(trace_id="t"),
        evidence_archived=True,
        weakest_evidence=ObservedBy.HARNESS,
    )
    assert trial.regradeable

    dumped = trial.model_dump()
    # 证据本体不进 run 记录: 它按 trial 另存一张表, 重评分时单独取回
    assert "evidence" not in dumped
    restored = TrialResult.model_validate(dumped)
    assert restored.evidence is None
    # 「可读」与「可重评」是两件事: 旗标随记录保留, 结论字段也不丢
    assert restored.evidence_archived is True
    assert restored.weakest_evidence is ObservedBy.HARNESS
    assert restored.regradeable is True


# ── 1.5 (graders 侧) 判据的取信声明 ──────────────────────────────────────────


def _config(**kwargs):
    return GraderConfig(
        type=GraderType.STATE, name="state_check", **kwargs
    )


def test_grader_config_defaults_to_trusted_levels():
    config = _config()
    assert config.evidence == [ObservedBy.HARNESS, ObservedBy.RUNNER]
    assert config.allow_subject is False
    assert config.judgment_moment is JudgmentMoment.AT_END


def test_grader_config_can_narrow_to_harness_only():
    assert _config(evidence=["harness"]).evidence == [ObservedBy.HARNESS]


def test_grader_config_rejects_empty_declaration():
    with pytest.raises(ValueError, match="evidence 不能为空"):
        _config(evidence=[])


def test_grader_config_rejects_duplicate_levels():
    with pytest.raises(ValueError, match="重复"):
        _config(evidence=["harness", "harness"])


def test_grade_results_carry_provenance_self_report():
    from agent_eval.core.types import GraderResult

    result = GraderResult(
        grader_name="state_check",
        grader_type=GraderType.STATE,
        score=1.0,
        passed=True,
        evidence_levels=[ObservedBy.HARNESS],
        judgment_moment=JudgmentMoment.ANY_TIME,
    )
    assert result.subject_only is False
    assert result.model_dump()["judgment_moment"] == "any_time"
    assert GraderResult.model_validate(result.model_dump()) == result


# ── 采集声明: 一套语义两个字段 ───────────────────────────────────────────────


def test_capture_policy_inherits_field_by_field():
    suite = CapturePolicy(model_content=True)
    task = CapturePolicy(tool_arguments=False)
    resolved = task.resolve(suite)
    assert resolved == CaptureDecision(tool_arguments=False, model_content=True)


def test_capture_policy_defaults_closed():
    assert CapturePolicy().resolve() == CaptureDecision()


def test_legacy_bool_means_tool_arguments_only():
    """旧的单一开关不得被事后读宽成「正文也采了」。"""
    decision = CaptureDecision.coerce(True)
    assert decision.tool_arguments is True
    assert decision.model_content is False
    assert CaptureDecision.coerce(False) == CaptureDecision()
