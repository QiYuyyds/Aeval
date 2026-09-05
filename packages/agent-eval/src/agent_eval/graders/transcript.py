"""
Transcript grader — transcript analysis.

Analyzes the efficiency of an agent's execution:
- Number of turns vs limit
- Token usage vs limit
- Tool call redundancy
- Step efficiency vs the task's declared optimal number of steps (diagnostic)

Every input is read from the normalized observations. A quantity that could not
be observed is left out of the average (and reported as a gap) rather than
scored as if it were zero.

Config schema:
    {
        "max_turns": 20,
        "max_tokens": 10000,
        "threshold": 0.5,
        "step_efficiency_threshold": null   # 显式声明才让步数效率参与门禁
    }
"""

from __future__ import annotations

from typing import Any

from agent_eval.core.contract import EvalContext
from agent_eval.core.types import (
    EvalTask,
    GraderResult,
    GraderType,
    ObservedBy,
    TrialResult,
)
from agent_eval.graders._evidence import (
    evidence_report,
    evidence_unavailable_result,
    implementation_version_of,
    observations_for,
)
from agent_eval.trace.observations import NormalizedTrace, is_missing


class TranscriptGrader:
    """转录记录分析评分器"""

    name = "transcript"
    evidence_levels = (ObservedBy.HARNESS, ObservedBy.RUNNER)
    implementation_version = "2"

    async def grade(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None = None,
    ) -> GraderResult:
        config = task.get_grader_config(self.name)
        max_turns = config.get("max_turns", 20)
        max_tokens = config.get("max_tokens", 10000)
        threshold = config.get("threshold", 0.5)
        efficiency_gate = config.get("step_efficiency_threshold")

        observations = observations_for(spans, context)
        step_efficiency = _step_efficiency(observations, task)

        components: dict[str, float] = {}
        unavailable: list[tuple[str, str]] = []

        turns = observations.llm_call_count
        if is_missing(turns):
            unavailable.append(("n_turns", turns.reason.value))
        elif max_turns > 0:
            components["turns_score"] = max(0.0, 1.0 - turns / max_turns)

        tokens = _observed_tokens(observations)
        if is_missing(tokens):
            unavailable.append(("n_total_tokens", tokens.reason.value))
        elif max_tokens > 0:
            components["tokens_score"] = max(0.0, 1.0 - tokens / max_tokens)

        redundancy = _redundancy(observations)
        if redundancy is None:
            unavailable.append(("tool.calls", "provider_unavailable"))
        else:
            components["redundancy_score"] = 1.0 - redundancy

        if not components:
            return evidence_unavailable_result(
                self.name,
                GraderType.TRANSCRIPT,
                unavailable,
                details={"evidence": evidence_report(observations)},
            )

        gated_on_efficiency = bool(efficiency_gate) and step_efficiency is not None
        if gated_on_efficiency:
            components["step_efficiency_gate"] = float(
                step_efficiency >= efficiency_gate
            )
        score = sum(components.values()) / len(components)

        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.TRANSCRIPT,
            score=score,
            passed=score >= threshold,
            explanation=(
                f"turns={_render(turns)}/{max_turns}, "
                f"tokens={_render(tokens)}/{max_tokens}, "
                f"redundancy={_render_redundancy(redundancy)}"
                + (f", step_efficiency={step_efficiency:.2f}" if step_efficiency else "")
                + (f" (门禁阈值 {efficiency_gate})" if gated_on_efficiency else "")
                + (f"; 未测得: {[f for f, _ in unavailable]}" if unavailable else "")
            ),
            details={
                **components,
                "step_efficiency": step_efficiency,
                "optimal_steps": task.optimal_steps,
                "measured_components": sorted(components),
                "unavailable": [field for field, _ in unavailable],
                "evidence": evidence_report(observations),
                "grader_version": implementation_version_of(self),
            },
            evidence_levels=[ObservedBy.RUNNER],
        )


def _step_efficiency(observations: NormalizedTrace, task: EvalTask) -> float | None:
    """最优步数 / 实际步数 —— 诊断量, 未声明门禁前不影响通过判定。"""
    if not task.optimal_steps:
        return None
    steps = observations.tool_call_count
    if is_missing(steps) or steps <= 0:
        return None
    return task.optimal_steps / float(steps)


def _observed_tokens(observations: NormalizedTrace) -> Any:
    """输入+输出 token 总量 (两者都没读到时才认 provider 直报的总数)。"""
    total = observations.sum_field("total_tokens")
    input_tokens = observations.sum_field("input_tokens")
    output_tokens = observations.sum_field("output_tokens")
    if is_missing(input_tokens) and is_missing(output_tokens):
        return total
    return (0 if is_missing(input_tokens) else input_tokens) + (
        0 if is_missing(output_tokens) else output_tokens
    )


def _redundancy(observations: NormalizedTrace) -> float | None:
    """工具调用冗余度 = 1 - 唯一调用数 / 总调用数; 什么都没读到返回 None。"""
    total = observations.tool_call_count
    if is_missing(total):
        return None
    if not total:
        return 0.0
    return 1.0 - len(set(observations.observed_tools())) / total


def _render(value: Any) -> str:
    return "未测得" if is_missing(value) else str(int(value))


def _render_redundancy(value: float | None) -> str:
    return "未测得" if value is None else f"{value:.1%}"
