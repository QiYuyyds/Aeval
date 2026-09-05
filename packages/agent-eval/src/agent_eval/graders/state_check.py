"""
State-check grader — 环境状态验证 (按来源级别与判定时刻取信)。

Validates the environment state after a trial:
- file_exists: file is present
- file_contains: file content contains a substring
- file_regex: content matches a pattern
- db_record: database record matches criteria
- no_conflict_markers: merge markers absent

Config schema:
    {
        "expectations": [
            {"type": "file_exists", "path": "output.py"},
            {"type": "file_contains", "path": "output.py", "value": "def main"},
            {"type": "db_record", "table": "users", "match": {"id": 1}},
        ],
        "threshold": 1.0
    }

这个判据以前读的是 ``trial.outcome`` —— 也就是被评方自己序列化上交的那个字典,
agent 想让 ``files`` 里出现什么, 判据就判什么通过。现在它按级别取信:

- 优先用评测侧在 teardown 之前独立取证的读数 (``harness``), 其次才是适配层交付的
  终态 (``runner``);
- 被评方自报 (``subject``) 默认不在可消费范围内 —— 它要说动套件显式写
  ``allow_subject``, 而那会让这条结论被标成弱证据;
- 判的是哪一个**时刻**必须说清楚 (``judgment_moment``, 默认「结束时」): 中途建完
  又删掉的文件在默认口径下就是不成立。
"""

from __future__ import annotations

import re
from typing import Any

from agent_eval.core.contract import EvalContext
from agent_eval.core.types import (
    EvalTask,
    GraderConfig,
    GraderResult,
    GraderType,
    JudgmentMoment,
    ObservedBy,
    StateWindow,
    TrialEvidence,
    TrialResult,
)
from agent_eval.graders._evidence import (
    consultable_levels,
    evidence_unavailable_result,
    implementation_version_of,
)
from agent_eval.graders._verdicts import no_criteria_result


class StateCheckGrader:
    """环境状态检查评分器"""

    name = "state_check"
    # 会读取证通道, 也会在套件显式放行时读自报通道
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
        expectations = config.get("expectations", [])
        threshold = config.get("threshold", 1.0)
        grader_config = _active_grader_config(task, context)
        moment = (
            grader_config.judgment_moment
            if grader_config is not None
            else JudgmentMoment.AT_END
        )

        if not expectations:
            return no_criteria_result(self.name, GraderType.STATE, "expectations")

        evidence = context.evidence if context is not None else None
        windows = _windows(evidence, moment, grader_config)

        if windows is None:
            # 没有带来源的证据 (第三方直接调用本评分器): 退回自报终态视图, 按
            # 适配层交付级处理 —— 至少不再假装它是评测侧观测
            payload = trial.outcome or {}
            windows = [
                StateWindow(
                    observed_by=ObservedBy.RUNNER,
                    moment=moment,
                    payload=payload,
                    invert=moment is JudgmentMoment.NOT_AT_END,
                )
            ]
        elif not windows:
            return evidence_unavailable_result(
                self.name,
                GraderType.STATE,
                _missing_reasons(evidence, moment, grader_config),
                details={
                    "judgment_moment": moment.value,
                    "declared_evidence": _declared_names(grader_config),
                    "grader_version": implementation_version_of(self),
                },
            )

        passed_count = 0
        details: list[dict[str, Any]] = []
        used: list[ObservedBy] = []

        for exp in expectations:
            verdict = _judge_one(exp, windows)
            if verdict["passed"]:
                passed_count += 1
            used.extend(verdict["supported_by"])
            details.append({
                "expectation": exp,
                "passed": verdict["passed"],
                "supported_by": [level.value for level in verdict["supported_by"]],
                "consulted": [level.value for level in verdict["consulted"]],
                "moment": moment.value,
            })

        total = len(expectations)
        score = passed_count / total if total else 1.0
        strongest = _strongest(used) if passed_count else _weakest_consulted(windows)

        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.STATE,
            score=score,
            passed=score >= threshold,
            explanation=(
                f"{passed_count}/{total} state checks passed"
                + f" (依据 {strongest.value} 级取证, 时刻 {moment.value})"
            ),
            details={
                "expectations": details,
                "judgment_moment": moment.value,
                "declared_evidence": _declared_names(grader_config),
                "probe_channels": _channel_names(windows),
                "grader_version": implementation_version_of(self),
            },
            # 只报最可信的那一级: 「成立」必须由它支撑; 未成立时报 consulted
            evidence_levels=[strongest] if strongest else [],
            judgment_moment=moment,
        )


def _active_grader_config(
    task: EvalTask, context: EvalContext | None
) -> GraderConfig | None:
    for g in task.graders:
        if g.name == StateCheckGrader.name:
            return g
    return None


def _windows(
    evidence: TrialEvidence | None,
    moment: JudgmentMoment,
    grader_config: GraderConfig | None,
) -> list[StateWindow] | None:
    """本判据可消费的那几级在指定时刻上的读数窗口。

    None = 这次判定没有带来源的证据可问; 空列表 = 有证据但没有一级读数可用。
    """
    if evidence is None:
        return None
    return [
        window
        for window in (
            evidence.state_window(level, moment) for level in consultable_levels(grader_config)
        )
        if window.usable
    ]


def _missing_reasons(
    evidence: TrialEvidence | None,
    moment: JudgmentMoment,
    grader_config: GraderConfig | None,
) -> list[tuple[str, str]]:
    """说清缺的是哪一级、为什么 —— 「没有证据」和「没授权读证据」不是一回事。"""
    reasons: list[tuple[str, str]] = []
    for level in consultable_levels(grader_config):
        series = evidence.state_series(level) if evidence else []
        detail = next((obs.absent_reason for obs in series if obs.is_absent), None)
        reasons.append(
            (
                f"state[{level.value}]",
                detail or ("no_such_call" if not series else "provider_unavailable"),
            )
        )
    if moment is JudgmentMoment.ANY_TIME and evidence is not None and not evidence.observed_window:
        reasons.append(("state.window", "provider_unavailable"))
    return reasons


def _declared_names(grader_config: GraderConfig | None) -> list[str]:
    if grader_config is None:
        return [level.value for level in ObservedBy.default_declaration()]
    return [level.value for level in grader_config.evidence]


def _judge_one(exp: dict[str, Any], windows: list[StateWindow]) -> dict[str, Any]:
    """逐级问一遍: 被允许的那几级里只要有它说成立, 就算成立 (自报级需套件放行)。"""
    satisfied = [w for w in windows if _matches(exp, w.payload) != w.invert]
    consulted = [w.observed_by for w in windows]
    if satisfied:
        best = min(satisfied, key=lambda w: -w.observed_by.strength)
        return {"passed": True, "supported_by": [best.observed_by], "consulted": consulted}
    return {"passed": False, "supported_by": [], "consulted": consulted}


def _matches(exp: dict[str, Any], payload: dict[str, Any]) -> bool:
    """单条期望在这一份状态读数上成立吗。"""
    exp_type = exp.get("type", "file_exists")
    files = payload.get("files") or {}
    content = files.get(exp.get("path", ""), "") if isinstance(files, dict) else ""

    if exp_type == "file_exists":
        return isinstance(files, dict) and exp.get("path") in files
    if exp_type == "file_contains":
        return exp.get("value", "") in content
    if exp_type == "file_regex":
        return bool(re.search(exp.get("value", ""), content))
    if exp_type == "no_conflict_markers":
        return "<<<<<<<" not in content and ">>>>>>>" not in content
    if exp_type == "db_record":
        records = payload.get("db_records") or []
        criteria = exp.get("match", {})
        return any(
            all(r.get(k) == v for k, v in criteria.items())
            for r in records
            if isinstance(r, dict)
        )
    return False


def _strongest(levels: list[ObservedBy]) -> ObservedBy | None:
    return max(levels, key=lambda level: level.strength) if levels else None


def _weakest_consulted(windows: list[StateWindow]) -> ObservedBy | None:
    return min(
        (w.observed_by for w in windows), key=lambda level: level.strength, default=None
    )


def _channel_names(windows: list[StateWindow]) -> list[str]:
    return sorted(
        {
            reading.channel or f"{window.observed_by.value}_state"
            for window in windows
            for reading in window.readings
        }
    )
