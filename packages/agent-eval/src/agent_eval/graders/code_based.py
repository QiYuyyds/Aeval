"""
Code-based grader — deterministic scoring via string/regex matching.

Supports:
- contains: substring match
- not_contains: substring absence
- regex: regular expression match
- exact: exact string equality

Config schema:
    {
        "checks": [
            {"type": "contains", "value": "def hello", "target": "transcript"},
            {"type": "regex", "value": "class \\w+:", "target": "outcome"},
        ],
        "threshold": 1.0  # fraction of checks that must pass
    }

Target can be: "transcript" | "outcome" | "spans"
"""

from __future__ import annotations

import json
import re
from typing import Any

from agent_eval.core.contract import EvalContext
from agent_eval.core.types import (
    EvalTask,
    GraderResult,
    GraderType,
    ObservedBy,
    TrialResult,
    weakest_level,
)
from agent_eval.graders._evidence import consulted_levels, implementation_version_of
from agent_eval.graders._verdicts import no_criteria_result

# 检查目标 → 它实际读的是哪条证据通道
_TARGET_CHANNELS = {
    "transcript": ("transcript",),
    "outcome": ("subject_state", "harness_state"),
    "spans": ("steps",),
}


class CodeBasedGrader:
    """通用确定性评分器"""

    name = "code_based"
    evidence_levels = (ObservedBy.HARNESS, ObservedBy.RUNNER, ObservedBy.SUBJECT)
    implementation_version = "2"

    async def grade(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None = None,
    ) -> GraderResult:
        config = task.get_grader_config(self.name)
        checks = config.get("checks", [])
        threshold = config.get("threshold", 1.0)

        if not checks:
            return no_criteria_result(self.name, GraderType.CODE, "checks")

        evidence = context.evidence if context is not None else None
        passed_count = 0
        details: list[dict[str, Any]] = []
        used: list[ObservedBy] = []

        for check in checks:
            check_type = check.get("type", "contains")
            target = check.get("target", "transcript")
            value = check.get("value", "")
            levels = consulted_levels(evidence, *_TARGET_CHANNELS.get(target, ("transcript",)))

            # 获取目标文本
            if target == "transcript":
                text = json.dumps(
                    evidence.messages(levels) if evidence is not None else trial.transcript,
                    ensure_ascii=False,
                )
            elif target == "outcome":
                outcome = (
                    evidence.state_payload(levels)
                    if evidence is not None
                    else trial.outcome
                )
                text = json.dumps(outcome, ensure_ascii=False)
            elif target == "spans":
                text = json.dumps(spans, ensure_ascii=False)
            else:
                text = ""

            # 执行检查
            if check_type == "contains":
                ok = value in text
            elif check_type == "not_contains":
                ok = value not in text
            elif check_type == "regex":
                ok = bool(re.search(value, text))
            elif check_type == "exact":
                ok = value == text
            else:
                ok = False

            if ok:
                passed_count += 1
                used.extend(levels)
            details.append({
                "check": check,
                "passed": ok,
                "target": target,
                "observed_by": [level.value for level in levels],
            })

        total = len(checks)
        score = passed_count / total if total > 0 else 1.0

        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.CODE,
            score=score,
            passed=score >= threshold,
            explanation=f"{passed_count}/{total} checks passed",
            details={
                "checks": details,
                "grader_version": implementation_version_of(self),
            },
            # 报最弱的一级: 一条检查只要是被自报内容满足的, 整条结论就值那个分量
            evidence_levels=([weakest_level(used)] if used else []),
        )
