"""
Tool-calls grader — tool call validation.

Validates that the agent:
- Called required tools
- Did not use forbidden tools
- (Optional) Called tools in a specific order

Evidence comes from the normalized trace observations, never from span names
or host-private attribute names.

Config schema:
    {
        "required_tools": ["fs_read", "fs_write"],
        "forbidden_tools": ["dangerous_tool"],
        "threshold": 1.0
    }

Scoring: with a required set configured the score is the F1 of tool selection
(precision + recall of the actually-called set against the expected set); the
legacy recall value stays available under ``details["recall"]``. A trace whose
tool-call evidence could not be read yields an invalid ``evidence_unavailable``
verdict rather than an agent failure.
"""

from __future__ import annotations

from typing import Any

from agent_eval.core.contract import EvalContext
from agent_eval.core.types import EvalTask, GraderResult, GraderType, TrialResult
from agent_eval.graders._evidence import (
    evidence_report,
    evidence_unavailable_result,
    observations_for,
    render,
)
from agent_eval.graders._verdicts import no_criteria_result
from agent_eval.trace.observations import is_missing


class ToolCallsGrader:
    """工具调用验证评分器"""

    name = "tool_calls"

    async def grade(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None = None,
    ) -> GraderResult:
        config = task.get_grader_config(self.name)
        required_tools = config.get("required_tools", [])
        forbidden_tools = config.get("forbidden_tools", [])
        threshold = config.get("threshold", 1.0)

        if not required_tools and not forbidden_tools:
            return no_criteria_result(
                self.name, GraderType.TOOL_CALLS, "required_tools/forbidden_tools"
            )

        observations = observations_for(spans, context)
        # 取证通道什么都没读到 ≠ agent 一次工具都没调
        if is_missing(observations.tool_call_count):
            return evidence_unavailable_result(
                self.name,
                GraderType.TOOL_CALLS,
                [("tool.name", observations.missing_fields.get("tool.name", "provider_unavailable"))],
                details={"evidence": evidence_report(observations)},
            )

        rendered = [
            {
                "name": render(call.tool_name),
                "success": render(call.success),
                "arguments": render(call.arguments),
                "result": render(call.result),
                "latency_ms": render(call.latency_ms),
            }
            for call in observations.tool_calls
        ]
        used_tools = observations.observed_tools()
        unnamed_calls = observations.tool_call_count - len(used_tools)
        used_set = set(used_tools)
        required_set = set(required_tools)

        missing = [t for t in required_tools if t not in used_set]
        violated = [t for t in forbidden_tools if t in used_set]

        recall, precision, f1 = _selection_metrics(required_set, used_set)
        score = f1 if required_set else 1.0
        if violated:
            score = 0.0  # 违反禁止工具直接 0 分
        passed = score >= threshold

        parts = []
        if used_tools:
            parts.append(f"Used: {used_tools}")
        if missing:
            parts.append(f"Missing required: {missing}")
        if violated:
            parts.append(f"Violated forbidden: {violated}")
        explanation = "; ".join(parts) if parts else "No tool calls checked"

        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.TOOL_CALLS,
            score=score,
            passed=passed,
            explanation=(
                f"{explanation}; P={precision if precision is not None else 'n/a'} "
                f"R={recall if recall is not None else 'n/a'} "
                f"F1={f1 if f1 is not None else 'n/a'}"
            ),
            details={
                "tool_calls": rendered,
                "used_tools": used_tools,
                "missing": missing,
                "violated": violated,
                # recall 是既有的消费字段, 与本次扩展并列保留
                "recall": recall,
                "precision": precision,
                "f1": f1,
                "unnamed_tool_calls": unnamed_calls,
                "evidence": evidence_report(observations),
            },
        )


def _selection_metrics(
    expected: set[str],
    actual: set[str],
) -> tuple[float | None, float | None, float | None]:
    """工具选择的召回率 / 精确率 / F1 (期望集合对照实际调用集合)。

    无可比集合时返回 None —— 「算不出来」与「算出来是 0」不是同一件事。
    """
    if not expected:
        return None, None, None
    true_positive = len(expected & actual)
    recall = true_positive / len(expected)
    if not actual:
        return recall, None, 0.0
    precision = true_positive / len(actual)
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return recall, precision, f1
