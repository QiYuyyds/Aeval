"""Shared verdict helpers for the built-in graders.

A grader that was configured onto a task but carries no criteria at all did not
measure anything: it is neither an auto-pass (1.0) nor an agent failure (0.0),
it is an invalid evaluation-side conclusion that must not occupy the denominator.
"""

from __future__ import annotations

from agent_eval.core.types import (
    GraderResult,
    GraderType,
    InvalidReason,
    TrialVerdict,
)


def no_criteria_result(
    grader_name: str,
    grader_type: GraderType,
    criteria_field: str,
    details: dict | None = None,
) -> GraderResult:
    """未配置判据的 grader 结论 (invalid / no_criteria_configured)。"""
    return GraderResult(
        grader_name=grader_name,
        grader_type=grader_type,
        score=0.0,
        passed=False,
        explanation=(
            f"未配置判据 (config.{criteria_field} 为空), 该 grader 形同未挂载; "
            "不计入通过率分母"
        ),
        details=details or {},
        verdict=TrialVerdict.INVALID,
        invalid_reason=InvalidReason.NO_CRITERIA_CONFIGURED,
    )
