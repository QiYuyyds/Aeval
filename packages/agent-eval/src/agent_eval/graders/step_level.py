"""
Step-level grader — compares the agent's tool-call sequence against an
expected trace (design decision D6, first version: exact index-by-index
comparison only).

The step sequence comes from the normalized tool-call observations, so a host
that instruments under different attribute names needs only a mapping entry —
never a change here. A call whose tool name could not be read keeps its slot in
the sequence as ``null`` (index alignment is preserved, the step is judged
incorrect, and the gap is visible in the result).

Config schema:
    {
        "expected_trace": ["fs_read", "fs_write", "bash"],  # required
        "threshold": 0.7,  # optional, pass threshold
    }

When ``expected_trace`` is not configured there is nothing to compare against,
so the grader reports an invalid ``no_criteria_configured`` verdict rather than
an auto-pass: an unconfigured criterion is not evidence about the agent.
"""

from __future__ import annotations

from typing import Any

from agent_eval.core.contract import EvalContext
from agent_eval.core.types import EvalTask, GraderResult, GraderType, TrialResult
from agent_eval.graders._evidence import evidence_report, observations_for
from agent_eval.graders._verdicts import no_criteria_result
from agent_eval.trace.observations import NormalizedTrace, is_missing


class StepLevelGrader:
    """步骤级评估 — expected_trace 按索引对照, 定位首个错误步骤"""

    name = "step_level"

    async def grade(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None = None,
    ) -> GraderResult:
        config = task.get_grader_config(self.name)
        expected = config.get("expected_trace")
        threshold = config.get("threshold", 0.7)

        observations = observations_for(spans, context)
        actual = _step_sequence(observations)

        if not expected:
            return no_criteria_result(
                self.name, GraderType.CUSTOM, "expected_trace",
                details={
                    "actual_steps": actual,
                    "evidence": evidence_report(observations),
                },
            )

        total = len(expected)
        step_details: list[dict[str, Any]] = []
        first_error: int | None = None

        for i in range(total):
            expected_step = expected[i]
            actual_step = actual[i] if i < len(actual) else None
            ok = actual_step == expected_step
            if not ok and first_error is None:
                first_error = i
            step_details.append({
                "index": i,
                "expected": expected_step,
                "actual": actual_step,
                "correct": ok,
            })

        correct_count = sum(1 for s in step_details if s["correct"])
        score = correct_count / total if total > 0 else 1.0

        explanation = f"{correct_count}/{total} steps correct"
        if first_error is not None:
            explanation += (
                f"; first error at step {first_error}: "
                f"expected '{expected[first_error]}', "
                f"got '{actual[first_error] if first_error < len(actual) else None}'"
            )
        if None in actual:
            explanation += (
                f"; {actual.count(None)} 步的工具名当时没读到 (null, 非 agent 少调用)"
            )

        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.CUSTOM,
            score=score,
            passed=score >= threshold,
            explanation=explanation,
            details={
                "steps": step_details,
                "first_error_step": first_error,
                "actual_steps": actual,
                "extra_steps": (
                    actual[total:] if len(actual) > total else []
                ),
                "evidence": evidence_report(observations),
            },
        )


def _step_sequence(observations: NormalizedTrace) -> list[str | None]:
    """归一化工具调用的名称序列 (名称缺失的位置保留为 None)。"""
    return [
        None if is_missing(call.tool_name) else str(call.tool_name)
        for call in observations.tool_calls
    ]
