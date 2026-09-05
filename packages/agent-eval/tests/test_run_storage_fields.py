"""Run/suite persistence of the new statistics fields (tasks 2.5).

Both backends must round-trip the verdict and estimate fields, and rows written
before this change must read back as "unknown" (None) rather than failing.
"""

import json

import pytest

from agent_eval.core.types import (
    STATISTICS_VERSION,
    InvalidReason,
    PassKEstimate,
    RunResult,
    RunSummary,
    ScoreDistribution,
    TaskSummary,
    TrialResult,
    TrialVerdict,
)
from agent_eval.storage.memory import MemoryStorage
from agent_eval.storage.sqlite import SqliteStorage


def _run() -> RunResult:
    return RunResult(
        run_id="run_stats_fields",
        suite_name="stats",
        status="completed",
        statistics_version=STATISTICS_VERSION,
        trials={
            "t1": [
                TrialResult(
                    trial_index=0,
                    success=True,
                    verdict=TrialVerdict.VALID,
                ),
                TrialResult(
                    trial_index=1,
                    success=False,
                    verdict=TrialVerdict.INVALID,
                    invalid_reason=InvalidReason.GRADER_ERROR,
                    grader_results=[
                        {
                            "grader_name": "boom",
                            "grader_type": "code",
                            "score": 0.0,
                            "passed": False,
                            "verdict": "invalid",
                            "invalid_reason": "grader_error",
                            "explanation": "Grader error: kaboom",
                        }
                    ],
                ),
                TrialResult(
                    trial_index=2,
                    success=False,
                    verdict=TrialVerdict.PENDING,
                ),
            ]
        },
        summary=RunSummary(
            total_tasks=1,
            total_trials=3,
            pass_at_k={1: 1.0, 2: None},
            estimates={
                1: PassKEstimate(
                    k=1,
                    n=1,
                    successes=1,
                    value=1.0,
                    method="extrapolated",
                    extrapolated=True,
                    p_point=1.0,
                    p_lower_bound=0.2065,
                    p_upper_bound=1.0,
                )
            },
            valid_trials=1,
            invalid_trials=1,
            pending_trials=1,
            avg_score=0.8,
            score_distribution=ScoreDistribution(
                n=1, mean=0.8, std_dev=0.0, worst_of_n=0.8, method="bootstrap"
            ),
            task_summaries=[
                TaskSummary(
                    task_id="t1",
                    total_trials=3,
                    valid_trials=1,
                    invalid_trials=1,
                    pending_trials=[2],
                    invalid_trial_indices=[1],
                    invalid_reasons={"1": "grader_error"},
                    sample_sufficient=False,
                )
            ],
        ),
    )


def _legacy_row() -> dict:
    """口径修正之前落盘的 run 记录: 无任何新增字段。"""
    return {
        "run_id": "run_legacy",
        "suite_name": "legacy",
        "status": "completed",
        "started_at": 1.0,
        "completed_at": 2.0,
        "trials": {
            "t1": [
                {
                    "trial_index": 0,
                    "trace_id": "tr",
                    "success": True,
                    "grader_results": [
                        {
                            "grader_name": "human",
                            "grader_type": "custom",
                            "score": 0.0,
                            "passed": False,
                            "explanation": "等待人工评分",
                            "details": {"status": "pending"},
                        }
                    ],
                    "metrics": {},
                    "transcript": [],
                    "outcome": {},
                    "duration_ms": 1.0,
                    "error": None,
                }
            ]
        },
        "summary": {
            "total_tasks": 1,
            "total_trials": 1,
            "pass_at_k": {"1": 1.0},
            "pass_power_k": {"1": 1.0},
            "avg_score": 0.0,
            "avg_metrics": {},
            "task_summaries": [
                {
                    "task_id": "t1",
                    "task_description": "",
                    "total_trials": 1,
                    "pass_at_k": {"1": 1.0},
                    "pass_power_k": {"1": 1.0},
                    "avg_score": 0.0,
                    "avg_metrics": {},
                    "failures": [],
                    "pending_trials": [0],
                    "consistent": True,
                    "score_std_dev": 0.0,
                }
            ],
            "failures": [],
            "saturation": {},
        },
        "error": None,
    }


class TestMemoryRoundTrip:
    async def test_new_fields_survive(self):
        storage = MemoryStorage()
        run = _run()
        await storage.save_run(run)
        loaded = await storage.get_run(run.run_id)

        assert loaded.statistics_version == STATISTICS_VERSION
        assert loaded.summary.valid_trials == 1
        assert loaded.summary.invalid_trials == 1
        assert loaded.summary.pending_trials == 1
        assert loaded.summary.pass_at_k[2] is None
        assert loaded.summary.estimates[1].extrapolated is True
        assert loaded.summary.estimates[1].p_lower_bound == pytest.approx(0.2065)
        assert loaded.summary.score_distribution.worst_of_n == 0.8

        trials = loaded.trials["t1"]
        assert [t.verdict for t in trials] == ["valid", "invalid", "pending"]
        assert trials[1].invalid_reason is InvalidReason.GRADER_ERROR
        assert trials[1].grader_results[0].verdict is TrialVerdict.INVALID
        assert trials[1].grader_results[0].invalid_reason is InvalidReason.GRADER_ERROR

    async def test_grader_result_defaults_to_valid(self):
        """未声明 verdict 的第三方 grader 结论仍按 valid 计入分母。"""
        trial = TrialResult(
            trial_index=0,
            success=True,
            grader_results=[
                {"grader_name": "ext", "grader_type": "custom", "score": 1.0, "passed": True}
            ],
        )
        assert trial.grader_results[0].verdict is TrialVerdict.VALID
        assert trial.grader_results[0].invalid_reason is None


class TestSqliteRoundTrip:
    async def test_new_fields_survive(self, tmp_path):
        storage = SqliteStorage(str(tmp_path / "stats.db"))
        await storage.initialize()
        run = _run()
        await storage.save_run(run)
        loaded = await storage.get_run(run.run_id)

        assert loaded.summary.valid_trials == 1
        assert loaded.summary.invalid_trials == 1
        assert loaded.summary.pass_at_k[2] is None
        assert loaded.summary.estimates[1].extrapolated is True
        assert loaded.summary.estimates[1].value == 1.0
        trials = loaded.trials["t1"]
        assert trials[1].verdict is TrialVerdict.INVALID
        assert trials[1].grader_results[0].invalid_reason is InvalidReason.GRADER_ERROR

    async def test_legacy_row_reads_as_unknown(self, tmp_path):
        """缺字段的历史行: 读为「未知」而非失败。"""
        import aiosqlite

        db_path = str(tmp_path / "legacy.db")
        storage = SqliteStorage(db_path)
        await storage.initialize()
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO runs (run_id, suite_name, status, started_at, completed_at, data)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "run_legacy",
                    "legacy",
                    "completed",
                    1.0,
                    2.0,
                    json.dumps(_legacy_row()),
                ),
            )
            await db.commit()

        loaded = await storage.get_run("run_legacy")
        assert loaded is not None
        assert loaded.statistics_version is None
        assert loaded.summary.valid_trials is None
        assert loaded.summary.invalid_trials is None
        assert loaded.summary.pending_trials is None
        assert loaded.summary.estimates == {}
        assert loaded.summary.score_distribution is None
        ts = loaded.summary.task_summaries[0]
        assert ts.valid_trials is None
        assert ts.invalid_trials is None
        assert ts.sample_sufficient is None
        # 旧行的 grader 结论缺 verdict → 默认 valid, 但 pending 标记仍被识别
        gr = loaded.trials["t1"][0].grader_results[0]
        assert gr.verdict is TrialVerdict.VALID
        assert gr.details["status"] == "pending"


class TestLegacyRunStillReadable:
    def test_model_accepts_row_without_new_keys(self):
        run = RunResult(**_legacy_row())
        assert run.statistics_version is None
        assert run.summary.avg_score == 0.0
        assert run.summary.task_summaries[0].pending_trials == [0]
