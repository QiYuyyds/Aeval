"""Unit tests for the six pre-existing built-in graders (coverage for task 6.4)."""

import pytest

from agent_eval.core.types import (
    EvalTask,
    GraderConfig,
    GraderType,
    InvalidReason,
    TrialResult,
    TrialVerdict,
)
from agent_eval.graders.artifact_check import ArtifactCheckGrader
from agent_eval.graders.code_based import CodeBasedGrader
from agent_eval.graders.model_based import ModelBasedGrader
from agent_eval.graders.state_check import StateCheckGrader
from agent_eval.graders.tool_calls import ToolCallsGrader
from agent_eval.graders.transcript import TranscriptGrader


def make_task(grader_name: str, grader_type: GraderType, config: dict) -> EvalTask:
    return EvalTask(
        id="t1",
        prompt="p",
        graders=[GraderConfig(type=grader_type, name=grader_name, config=config)],
    )


def make_trial(outcome: dict | None = None, metrics: dict | None = None) -> TrialResult:
    return TrialResult(
        trial_index=0,
        transcript=[{"role": "user", "content": "hello"}, {"role": "assistant", "content": "world"}],
        outcome=outcome if outcome is not None else {},
        metrics=metrics or {},
    )


# ─── code_based ───────────────────────────────────────────────────────────────


class TestCodeBasedGrader:
    async def test_no_checks_is_invalid_not_auto_pass(self):
        grader = CodeBasedGrader()
        result = await grader.grade(make_trial(), [], make_task("code_based", GraderType.CODE, {}))
        assert result.verdict is TrialVerdict.INVALID
        assert result.invalid_reason is InvalidReason.NO_CRITERIA_CONFIGURED
        assert result.passed is False
        assert "形同未挂载" in result.explanation

    @pytest.mark.parametrize(
        "check,text_ok",
        [
            ({"type": "contains", "value": "world", "target": "transcript"}, True),
            ({"type": "contains", "value": "missing", "target": "transcript"}, False),
            ({"type": "not_contains", "value": "absent", "target": "transcript"}, True),
            ({"type": "not_contains", "value": "world", "target": "transcript"}, False),
            ({"type": "regex", "value": r"w.rld", "target": "transcript"}, True),
            ({"type": "regex", "value": r"\d+", "target": "transcript"}, False),
            ({"type": "unknown_type", "value": "x"}, False),
        ],
    )
    async def test_check_types(self, check, text_ok):
        grader = CodeBasedGrader()
        result = await grader.grade(
            make_trial(), [], make_task("code_based", GraderType.CODE, {"checks": [check]})
        )
        assert (result.score == 1.0) is text_ok

    async def test_exact_match(self):
        grader = CodeBasedGrader()
        task = make_task("code_based", GraderType.CODE, {
            "checks": [{"type": "exact", "value": "42", "target": "outcome"}]
        })
        result = await grader.grade(make_trial(outcome={"result": "42"}), [], task)
        # exact 比较的是整个序列化文本, 而非子串 → 不等于
        assert result.details["checks"][0]["passed"] is False

    async def test_partial_score_and_threshold(self):
        grader = CodeBasedGrader()
        task = make_task("code_based", GraderType.CODE, {
            "checks": [
                {"type": "contains", "value": "hello", "target": "transcript"},
                {"type": "contains", "value": "nope", "target": "transcript"},
            ],
            "threshold": 0.5,
        })
        result = await grader.grade(make_trial(), [], task)
        assert result.score == 0.5
        assert result.passed is True


# ─── model_based ──────────────────────────────────────────────────────────────


class TestModelBasedGrader:
    async def test_scores_from_llm_response(self):
        def llm(system: str, user: str) -> str:
            return '```json\n{"correctness": 0.9, "completeness": 0.7}\n```'

        grader = ModelBasedGrader(llm_fn=llm)
        task = make_task("model_based", GraderType.MODEL, {
            "rubric": "be correct",
            "dimensions": ["correctness", "completeness"],
        })
        result = await grader.grade(make_trial(), [], task)
        assert result.score == pytest.approx(0.8)
        assert result.passed is True
        assert result.details["dimensions"] == {"correctness": 0.9, "completeness": 0.7}

    async def test_llm_failure_is_invalid_not_zero(self):
        def bad_llm(system: str, user: str) -> str:
            raise RuntimeError("api down")

        grader = ModelBasedGrader(llm_fn=bad_llm)
        result = await grader.grade(make_trial(), [], make_task("model_based", GraderType.MODEL, {}))
        assert result.verdict is TrialVerdict.INVALID
        assert result.invalid_reason is InvalidReason.JUDGE_UNAVAILABLE
        assert result.score == 0.0
        assert result.passed is False
        assert "LLM call failed" in result.explanation

    async def test_unparseable_response_is_invalid_not_half(self):
        """Scenario: 解析失败不再给半分 (specs/orchestration)"""
        grader = ModelBasedGrader(llm_fn=lambda s, u: "no json here")
        result = await grader.grade(make_trial(), [], make_task("model_based", GraderType.MODEL, {
            "dimensions": ["quality"],
        }))
        assert result.verdict is TrialVerdict.INVALID
        assert result.invalid_reason is InvalidReason.VERDICT_UNPARSEABLE
        assert result.details["dimensions"] == {}
        assert 0.5 not in result.details["dimensions"].values()

    async def test_partial_dimensions_divide_by_all_configured(self):
        """Scenario: 部分维度缺席 → invalid, 且分母仍为配置的全集维度数"""
        grader = ModelBasedGrader(
            llm_fn=lambda s, u: '{"a": 1.0}'
        )
        task = make_task("model_based", GraderType.MODEL, {
            "dimensions": ["a", "b", "c"],
        })
        result = await grader.grade(make_trial(), [], task)
        assert result.verdict is TrialVerdict.INVALID
        assert result.invalid_reason is InvalidReason.VERDICT_UNPARSEABLE
        assert result.details["missing"] == ["b", "c"]
        # 1.0 / 3 而非 1.0 / 1 (缺席维度不被当作满分也不被忽略)
        assert result.score == pytest.approx(1 / 3)
        assert result.passed is False

    async def test_no_dimensions_configured_is_invalid(self):
        grader = ModelBasedGrader(llm_fn=lambda s, u: "{}")
        result = await grader.grade(make_trial(), [], make_task(
            "model_based", GraderType.MODEL, {"dimensions": []}
        ))
        assert result.verdict is TrialVerdict.INVALID
        assert result.invalid_reason is InvalidReason.NO_CRITERIA_CONFIGURED

    async def test_out_of_range_dimension_scores_are_clamped(self):
        grader = ModelBasedGrader(
            llm_fn=lambda s, u: '{"quality": 7, "tone": -3}'
        )
        task = make_task("model_based", GraderType.MODEL, {
            "dimensions": ["quality", "tone"],
        })
        result = await grader.grade(make_trial(), [], task)
        assert result.details["dimensions"] == {"quality": 1.0, "tone": 0.0}
        assert result.verdict is TrialVerdict.VALID

    async def test_prompt_contains_rubric_and_transcript(self):
        grader = ModelBasedGrader()
        prompt = grader._build_prompt(make_trial(), "must be nice", ["quality"])
        assert "must be nice" in prompt
        assert "hello" in prompt


# ─── state_check ──────────────────────────────────────────────────────────────


class TestStateCheckGrader:
    async def test_no_expectations_is_invalid_not_auto_pass(self):
        grader = StateCheckGrader()
        result = await grader.grade(make_trial(), [], make_task("state_check", GraderType.STATE, {}))
        assert result.verdict is TrialVerdict.INVALID
        assert result.invalid_reason is InvalidReason.NO_CRITERIA_CONFIGURED
        assert "expectations" in result.explanation

    async def test_file_expectations(self):
        outcome = {"files": {"out.py": "def main():\n    pass\n"}}
        grader = StateCheckGrader()
        task = make_task("state_check", GraderType.STATE, {
            "expectations": [
                {"type": "file_exists", "path": "out.py"},
                {"type": "file_contains", "path": "out.py", "value": "def main"},
                {"type": "file_regex", "path": "out.py", "value": r"def \w+\("},
                {"type": "file_exists", "path": "missing.py"},
            ],
        })
        result = await grader.grade(make_trial(outcome=outcome), [], task)
        assert result.score == 0.75
        assert result.details["expectations"][3]["passed"] is False

    async def test_db_record_and_conflict_markers(self):
        outcome = {
            "db_records": [{"id": 1, "name": "a"}],
            "files": {"clean.py": "x = 1"},
        }
        grader = StateCheckGrader()
        task = make_task("state_check", GraderType.STATE, {
            "expectations": [
                {"type": "db_record", "match": {"id": 1}},
                {"type": "db_record", "match": {"id": 99}},
                {"type": "no_conflict_markers", "path": "clean.py"},
            ],
        })
        result = await grader.grade(make_trial(outcome=outcome), [], task)
        assert result.score == pytest.approx(2 / 3)

    async def test_unknown_expectation_type_fails(self):
        grader = StateCheckGrader()
        task = make_task("state_check", GraderType.STATE, {
            "expectations": [{"type": "mystery"}],
        })
        result = await grader.grade(make_trial(), [], task)
        assert result.score == 0.0


# ─── tool_calls ───────────────────────────────────────────────────────────────


class TestToolCallsGrader:
    """两套词汇必须给出同一结论 (spec: 内置评分器只消费归一化观测)。"""

    async def test_required_and_forbidden(self, make_tool_span, make_context):
        grader = ToolCallsGrader()
        task = make_task("tool_calls", GraderType.TOOL_CALLS, {
            "required_tools": ["fs_read", "fs_write"],
            "forbidden_tools": ["bash"],
        })
        spans = [make_tool_span("fs_read"), make_tool_span("fs_write")]
        result = await grader.grade(make_trial(), spans, task, make_context(task, spans))
        assert result.score == 1.0 and result.passed is True
        assert result.details["recall"] == 1.0
        assert result.details["precision"] == 1.0

    async def test_missing_required_tools(self, make_tool_span, make_context):
        grader = ToolCallsGrader()
        task = make_task("tool_calls", GraderType.TOOL_CALLS, {
            "required_tools": ["fs_read", "fs_write"],
        })
        spans = [make_tool_span("fs_read")]
        result = await grader.grade(make_trial(), spans, task, make_context(task, spans))
        # 召回率作为既有消费字段保留; 结论分改由 P/R/F1 给出
        assert result.details["recall"] == 0.5
        assert result.details["missing"] == ["fs_write"]
        assert result.score == pytest.approx(2 / 3)

    async def test_extra_calls_lower_precision(self, make_tool_span, make_context):
        """期望工具全调了, 外加三个无关工具 → 召回 1 而精确率小于 1。"""
        grader = ToolCallsGrader()
        task = make_task("tool_calls", GraderType.TOOL_CALLS, {
            "required_tools": ["fs_write"],
        })
        spans = [make_tool_span(t) for t in ("fs_write", "a", "b", "c")]
        result = await grader.grade(make_trial(), spans, task, make_context(task, spans))
        assert result.details["recall"] == 1.0
        assert result.details["precision"] == 0.25
        assert result.details["f1"] == pytest.approx(0.4)
        assert result.score == pytest.approx(0.4)

    async def test_forbidden_tool_violation_scores_zero(self, make_tool_span, make_context):
        grader = ToolCallsGrader()
        task = make_task("tool_calls", GraderType.TOOL_CALLS, {
            "forbidden_tools": ["bash"],
        })
        spans = [make_tool_span("bash")]
        result = await grader.grade(make_trial(), spans, task, make_context(task, spans))
        assert result.score == 0.0 and result.passed is False
        assert result.details["violated"] == ["bash"]

    async def test_no_criteria_is_invalid_not_pass(self, make_context):
        grader = ToolCallsGrader()
        task = make_task("tool_calls", GraderType.TOOL_CALLS, {})
        result = await grader.grade(make_trial(), [], task, make_context(task, []))
        assert result.verdict is TrialVerdict.INVALID
        assert result.invalid_reason is InvalidReason.NO_CRITERIA_CONFIGURED
        assert result.passed is False

    async def test_span_name_is_not_the_identification_basis(
        self, make_tool_span, make_context
    ):
        """识别依据是标准观测字段, 不是 span 名称子串。"""
        grader = ToolCallsGrader()
        task = make_task("tool_calls", GraderType.TOOL_CALLS, {"required_tools": ["fs_write"]})
        spans = [make_tool_span("fs_write")]
        for span in spans:
            span["name"] = "totally-unrelated-name"
        result = await grader.grade(make_trial(), spans, task, make_context(task, spans))
        assert result.details["used_tools"] == ["fs_write"]
        assert result.score == 1.0

    async def test_unreadable_evidence_is_invalid_not_agent_failure(
        self, make_context, normalize
    ):
        """取证通道不可用 ≠ agent 一次工具都没调 (缺失与零分离)。"""
        grader = ToolCallsGrader()
        task = make_task("tool_calls", GraderType.TOOL_CALLS, {"required_tools": ["fs_write"]})
        context = make_context(task, [])
        context.observations = normalize(
            [], source_status="unavailable", source_detail="trace 后端未安装"
        )
        result = await grader.grade(make_trial(), [], task, context)
        assert result.verdict is TrialVerdict.INVALID
        assert result.invalid_reason is InvalidReason.EVIDENCE_UNAVAILABLE
        assert "证据不可用" in result.explanation

    async def test_genuinely_absent_tool_calls_are_a_real_zero(
        self, make_turn_span, make_context
    ):
        """trace 里确实没有工具调用 → 0 是真实观测, 该判未通过。"""
        grader = ToolCallsGrader()
        task = make_task("tool_calls", GraderType.TOOL_CALLS, {"required_tools": ["fs_write"]})
        spans = [make_turn_span()]
        result = await grader.grade(make_trial(), spans, task, make_context(task, spans))
        assert result.verdict is TrialVerdict.VALID
        assert result.score == 0.0 and result.passed is False


# ─── transcript ───────────────────────────────────────────────────────────────


class TestTranscriptGrader:
    async def test_score_from_normalized_trace(self, make_turn_span, make_tool_span, make_context):
        grader = TranscriptGrader()
        task = make_task("transcript", GraderType.TRANSCRIPT, {
            "max_turns": 10, "max_tokens": 1000, "threshold": 0.9,
        })
        spans = [make_turn_span(input_tokens=300, output_tokens=200), make_tool_span("a")]
        result = await grader.grade(make_trial(), spans, task, make_context(task, spans))
        # turns_score=0.9, tokens_score=0.5, redundancy=0 → (0.9+0.5+1)/3
        assert result.score == pytest.approx((0.9 + 0.5 + 1.0) / 3)
        assert result.passed is False

    async def test_unobserved_components_are_not_scored_as_zero(
        self, make_context, normalize
    ):
        """provider 什么都没给: 三个分量都测不到 → 证据不可用, 而不是给低分。"""
        grader = TranscriptGrader()
        task = make_task("transcript", GraderType.TRANSCRIPT, {})
        context = make_context(task, [])
        context.observations = normalize([], source_status="unavailable")
        result = await grader.grade(make_trial(), [], task, context)
        assert result.verdict is TrialVerdict.INVALID
        assert result.invalid_reason is InvalidReason.EVIDENCE_UNAVAILABLE

    async def test_redundancy_from_normalized_calls(self, make_tool_span, make_context):
        grader = TranscriptGrader()
        task = make_task("transcript", GraderType.TRANSCRIPT, {
            "max_turns": 1000, "max_tokens": 10**9,
        })
        spans = [make_tool_span("a"), make_tool_span("a"), make_tool_span("b")]
        result = await grader.grade(make_trial(), spans, task, make_context(task, spans))
        # 2 unique / 3 total → redundancy = 1/3
        assert result.details["redundancy_score"] == pytest.approx(2 / 3)

    async def test_step_efficiency_is_diagnostic_unless_gated(
        self, make_tool_span, make_context
    ):
        """绕远路但做成: 效率单独报告, 未声明门禁前不影响通过判定。"""
        grader = TranscriptGrader()
        task = make_task("transcript", GraderType.TRANSCRIPT, {
            "max_turns": 1000, "max_tokens": 10**9, "threshold": 0.5,
        })
        task = task.model_copy(update={"optimal_steps": 1})
        spans = [make_tool_span("a"), make_tool_span("b"), make_tool_span("c")]
        result = await grader.grade(make_trial(), spans, task, make_context(task, spans))
        assert result.details["step_efficiency"] == pytest.approx(1 / 3)
        assert result.passed is True

    async def test_declared_step_efficiency_gate_can_fail(
        self, make_tool_span, make_context
    ):
        grader = TranscriptGrader()
        task = make_task("transcript", GraderType.TRANSCRIPT, {
            "max_turns": 1000, "max_tokens": 10**9, "threshold": 0.9,
            "step_efficiency_threshold": 0.9,
        })
        task = task.model_copy(update={"optimal_steps": 3})
        spans = [make_tool_span(t) for t in ("a", "b", "c", "d", "e", "f")]
        result = await grader.grade(make_trial(), spans, task, make_context(task, spans))
        assert result.details["step_efficiency"] == pytest.approx(0.5)
        assert result.passed is False


# ─── artifact_check ───────────────────────────────────────────────────────────


class TestArtifactCheckGrader:
    async def test_no_artifacts_scores_zero_and_stays_valid(self):
        grader = ArtifactCheckGrader()
        task = make_task("artifact_check", GraderType.ARTIFACT, {
            "expected_type": "code_file",
        })
        result = await grader.grade(make_trial(), [], task)
        assert result.score == 0.0 and result.passed is False
        # 未产出产物是关于 agent 的结论, 不是评测侧故障
        assert result.verdict is TrialVerdict.VALID

    async def test_no_criteria_is_invalid(self):
        grader = ArtifactCheckGrader()
        result = await grader.grade(make_trial(), [], make_task("artifact_check", GraderType.ARTIFACT, {}))
        assert result.verdict is TrialVerdict.INVALID
        assert result.invalid_reason is InvalidReason.NO_CRITERIA_CONFIGURED

    async def test_type_and_content_match(self):
        grader = ArtifactCheckGrader()
        outcome = {"artifacts": [{"type": "code_file", "content": "def hello(): pass"}]}
        task = make_task("artifact_check", GraderType.ARTIFACT, {
            "expected_type": "code_file",
            "content_regex": r"def \w+\(",
        })
        result = await grader.grade(make_trial(outcome=outcome), [], task)
        assert result.score == 1.0 and result.passed is True

    async def test_type_mismatch_scores_zero(self):
        grader = ArtifactCheckGrader()
        outcome = {"artifacts": [{"type": "document", "content": "x"}]}
        task = make_task("artifact_check", GraderType.ARTIFACT, {"expected_type": "code_file"})
        result = await grader.grade(make_trial(outcome=outcome), [], task)
        assert result.score == 0.0
        assert "Expected type" in result.explanation

    async def test_content_mismatch_scores_partial(self):
        grader = ArtifactCheckGrader()
        outcome = {"artifacts": [{"type": "code_file", "content": "no match here"}]}
        task = make_task("artifact_check", GraderType.ARTIFACT, {"content_regex": r"def \w+\("})
        result = await grader.grade(make_trial(outcome=outcome), [], task)
        assert result.score == 0.3

    async def test_artifacts_from_spans_only_via_mapping(
        self, make_artifact_span, make_context, vocabulary
    ):
        """产物属性在规范里没有定义: 只有宿主映射条目才读得到 (加表项即接入)。"""
        grader = ArtifactCheckGrader()
        task = make_task("artifact_check", GraderType.ARTIFACT, {
            "expected_type": "code_file",
        })
        spans = [make_artifact_span()]
        result = await grader.grade(make_trial(), spans, task, make_context(task, spans))

        if vocabulary == "host":
            assert result.score == 1.0 and result.passed is True
            assert result.details["artifacts"][0]["type"] == "code_file"
            assert result.details["artifacts"][0]["id"] == "art_1"
        else:
            assert result.score == 0.0
            assert result.explanation == "No artifacts produced"
            # 内置条目里没有产物属性名 (规范未定义) → trace 一条产物也读不出
            assert result.details["evidence"]["artifacts_observed"] == 0
