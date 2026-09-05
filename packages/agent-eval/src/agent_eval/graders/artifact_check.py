"""
Artifact-check grader — artifact verification.

Validates artifacts produced by the agent:
- Artifact exists
- Artifact type matches expected
- Artifact content matches regex pattern

Artifacts come from the trial's reported outcome or from the normalized
artifact observations — never from host-private span attributes.

Config schema:
    {
        "expected_type": "code_file",
        "content_regex": "def \\w+\\(",
        "threshold": 1.0
    }
"""

from __future__ import annotations

import re
from typing import Any

from agent_eval.core.contract import EvalContext
from agent_eval.core.types import EvalTask, GraderResult, GraderType, TrialResult
from agent_eval.graders._evidence import (
    evidence_report,
    observations_for,
    render,
)
from agent_eval.graders._verdicts import no_criteria_result
from agent_eval.trace.observations import NormalizedTrace


class ArtifactCheckGrader:
    """产物检查评分器"""

    name = "artifact_check"

    async def grade(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None = None,
    ) -> GraderResult:
        config = task.get_grader_config(self.name)
        expected_type = config.get("expected_type")
        content_regex = config.get("content_regex")
        threshold = config.get("threshold", 1.0)

        if not expected_type and not content_regex:
            return no_criteria_result(
                self.name, GraderType.ARTIFACT, "expected_type/content_regex"
            )

        observations = observations_for(spans, context)
        # 优先用被评测方自报的 outcome, 其次才是 trace 里的产物观测
        artifacts = trial.outcome.get("artifacts") or _from_observations(observations)
        evidence = evidence_report(observations)

        if not artifacts:
            return GraderResult(
                grader_name=self.name,
                grader_type=GraderType.ARTIFACT,
                score=0.0,
                passed=False,
                explanation="No artifacts produced",
                details={"evidence": evidence},
            )

        # 检查类型
        if expected_type:
            types = [a.get("type", "") for a in artifacts]
            if expected_type not in types:
                return GraderResult(
                    grader_name=self.name,
                    grader_type=GraderType.ARTIFACT,
                    score=0.0,
                    passed=False,
                    explanation=(
                        f"Expected type '{expected_type}', "
                        f"got {types}"
                    ),
                    details={"artifacts": artifacts, "evidence": evidence},
                )

        # 检查内容
        if content_regex:
            contents = [a.get("content", "") for a in artifacts]
            content_match = any(re.search(content_regex, c) for c in contents)
            if not content_match:
                return GraderResult(
                    grader_name=self.name,
                    grader_type=GraderType.ARTIFACT,
                    score=0.3,
                    passed=threshold <= 0.3,
                    explanation=f"Content does not match pattern: {content_regex}",
                    details={"artifacts": artifacts, "evidence": evidence},
                )

        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.ARTIFACT,
            score=1.0,
            passed=True,
            explanation=f"Artifact check passed: {len(artifacts)} artifact(s)",
            details={"artifacts": artifacts, "evidence": evidence},
        )


def _from_observations(observations: NormalizedTrace) -> list[dict[str, Any]]:
    """归一化产物观测 → 检查用的产物记录 (读不到的字段留空而非臆造)。"""
    return [
        {
            "type": render(artifact.artifact_type) or "",
            "id": render(artifact.artifact_id) or "",
            "content": render(artifact.content) or "",
        }
        for artifact in observations.artifacts
    ]
