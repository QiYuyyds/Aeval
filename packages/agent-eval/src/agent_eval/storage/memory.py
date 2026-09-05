"""
In-memory storage implementation.

Useful for testing and short-lived runs.
Data is lost when the process exits.

Usage:
    storage = MemoryStorage()
    await storage.save_run(run_result)
"""

from __future__ import annotations

import copy
from typing import Any

from agent_eval.core.types import (
    EvalSuite,
    GradeAttempt,
    RunResult,
    TrialEvidence,
)
from agent_eval.dataset.storage import MemoryDatasetStorage


class MemoryStorage:
    """内存存储实现 — 用于测试"""

    def __init__(self):
        self._runs: dict[str, RunResult] = {}
        self._suites: dict[str, EvalSuite] = {}
        self._human_score_requests: list[dict[str, Any]] = []
        # 证据归档与判定历史: 与 run 分表存放, 删除 run 时一并清除
        self._evidence: dict[tuple[str, str, int], TrialEvidence] = {}
        self._attempts: list[GradeAttempt] = []
        # 数据集存储组合暴露 (与 runs/suites 分表, 见 dataset/storage.py)
        self.datasets = MemoryDatasetStorage()

    # ── Run 操作 ──

    async def save_run(self, run: RunResult) -> None:
        """保存运行结果"""
        self._runs[run.run_id] = copy.deepcopy(run)

    async def get_run(self, run_id: str) -> RunResult | None:
        """获取运行结果"""
        run = self._runs.get(run_id)
        return copy.deepcopy(run) if run else None

    async def list_runs(
        self, suite_name: str | None = None, limit: int = 50
    ) -> list[RunResult]:
        """列出运行历史"""
        runs = list(self._runs.values())
        if suite_name:
            runs = [r for r in runs if r.suite_name == suite_name]
        runs.sort(key=lambda r: r.started_at, reverse=True)
        return runs[:limit]

    async def delete_run(self, run_id: str) -> bool:
        """删除运行结果 (一并清除其证据、判定条目与其它派生内容)"""
        if run_id not in self._runs:
            return False
        del self._runs[run_id]
        self._human_score_requests = [
            req for req in self._human_score_requests if req.get("run_id") != run_id
        ]
        self._evidence = {
            key: value for key, value in self._evidence.items() if key[0] != run_id
        }
        self._attempts = [a for a in self._attempts if a.run_id != run_id]
        return True

    # ── Suite 操作 ──

    async def save_suite(self, suite: EvalSuite) -> None:
        """保存评测套件"""
        self._suites[suite.name] = copy.deepcopy(suite)

    async def get_suite(self, name: str) -> EvalSuite | None:
        """获取评测套件"""
        suite = self._suites.get(name)
        return copy.deepcopy(suite) if suite else None

    async def list_suites(self) -> list[EvalSuite]:
        """列出所有评测套件"""
        return list(self._suites.values())

    async def delete_suite(self, name: str) -> bool:
        """删除评测套件"""
        if name in self._suites:
            del self._suites[name]
            return True
        return False

    # ── 人工评分请求 ──

    async def save_human_score_request(self, request: dict[str, Any]) -> None:
        """保存人工评分请求 (HumanGrader pending 语义)"""
        self._human_score_requests.append(dict(request))

    async def list_human_score_requests(
        self, run_id: str | None = None
    ) -> list[dict[str, Any]]:
        """列出人工评分请求"""
        if run_id is None:
            return [dict(r) for r in self._human_score_requests]
        return [
            dict(r) for r in self._human_score_requests if r.get("run_id") == run_id
        ]

    # ── 证据归档 ──

    async def save_trial_evidence(
        self, run_id: str, task_id: str, trial_index: int, evidence: TrialEvidence
    ) -> None:
        """按 trial 独立归档证据 (存深拷贝: 后续评分不得改写已落盘的读数)"""
        self._evidence[(run_id, task_id, trial_index)] = copy.deepcopy(evidence)

    async def get_trial_evidence(
        self, run_id: str, task_id: str, trial_index: int
    ) -> TrialEvidence | None:
        """完整取回一次 trial 的证据。"""
        evidence = self._evidence.get((run_id, task_id, trial_index))
        return copy.deepcopy(evidence) if evidence else None

    # ── 判定历史 ──

    async def save_grade_attempt(self, attempt: GradeAttempt) -> None:
        """追加判定条目; 同一条 trial 的旧条目只落下 current 指针, 不删除。"""
        for existing in self._attempts:
            if (
                existing.run_id == attempt.run_id
                and existing.task_id == attempt.task_id
                and existing.trial_index == attempt.trial_index
                and existing.is_current
            ):
                existing.is_current = False
        self._attempts.append(attempt.model_copy(deep=True))

    async def list_grade_attempts(
        self, run_id: str, task_id: str | None = None, trial_index: int | None = None
    ) -> list[GradeAttempt]:
        """按时间升序列出判定条目 (原结论与重评结论并列可查)。"""
        found = [a for a in self._attempts if a.run_id == run_id]
        if task_id is not None:
            found = [a for a in found if a.task_id == task_id]
        if trial_index is not None:
            found = [a for a in found if a.trial_index == trial_index]
        found.sort(key=lambda a: (a.created_at, a.attempt_id))
        return [a.model_copy(deep=True) for a in found]
