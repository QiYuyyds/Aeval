"""证据采集边界、脱敏落盘与历史兼容 (specs/graders + specs/storage)。

覆盖三件容易被悄悄破坏的事:
1. 入参默认不采集; 开启后必须先经脱敏才可能落盘 (未脱敏值不进存储/呈现层)。
2. 本变更之前落盘的 run 仍可读取, 缺的证据字段读为 unknown, 不报 0。
3. 删除一个 run 要连它的证据与派生缓存一起清掉。
"""

import json

import pytest

from agent_eval.core.redaction import IdentityEvidenceRedactor
from agent_eval.core.runner import EvalRunner
from agent_eval.core.types import (
    EvalSuite,
    EvalTask,
    EvidenceBoundary,
    GraderConfig,
    GraderType,
    RunResult,
)
from agent_eval.examples.mock_runner import MockAgentRunner, MockTraceProvider
from agent_eval.storage.memory import MemoryStorage
from agent_eval.storage.sqlite import SqliteStorage

FAST = (0.0, 0.01)
SECRET = "sk-live-abcdef1234567890"
TRANSCRIPT_CHECK = {"type": "contains", "value": "Mock response", "target": "transcript"}

# 逻辑 trace 里埋一条含凭据样式的工具入参 (标准属性名, Opt-In 内容)
def spans_with_secret() -> list[dict]:
    return [
        {
            "name": "agent.turn",
            "attributes": {
                "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.output_tokens": 50,
            },
            "start_time": "2026-09-04T10:00:00Z",
            "end_time": "2026-09-04T10:00:01Z",
            "status": {"status_code": "OK"},
        },
        {
            "name": "tool.call",
            "attributes": {
                "gen_ai.tool.name": "http_fetch",
                "gen_ai.tool.call.arguments": {"url": "https://api.test", "token": SECRET},
            },
            "start_time": "2026-09-04T10:00:01Z",
            "end_time": "2026-09-04T10:00:02Z",
            "status": {"status_code": "OK"},
        },
    ]


def make_suite(capture: bool, task_capture: bool | None = None) -> EvalSuite:
    task = EvalTask(
        id="t1",
        prompt="p",
        max_trials=1,
        graders=[
            GraderConfig(
                type=GraderType.TOOL_CALLS,
                name="tool_calls",
                config={"required_tools": ["http_fetch"]},
            )
        ],
        capture_tool_arguments=task_capture,
    )
    return EvalSuite(
        name="evidence-suite", version="1.0.0", tasks=[task],
        capture_tool_arguments=capture,
    )


def make_runner(suite: EvalSuite, **overrides) -> EvalRunner:
    overrides.setdefault("storage", MemoryStorage())
    return EvalRunner(
        agent_runner=MockAgentRunner(latency_range=FAST, script={"t1": ["success"]}),
        trace_provider=MockTraceProvider(spans_by_task={"t1": spans_with_secret()}),
        **overrides,
    )


async def run_and_dump(runner: EvalRunner, suite: EvalSuite) -> tuple[RunResult, str]:
    run = await runner.run_suite(suite)
    stored = await runner.storage.get_run(run.run_id)
    return run, json.dumps(stored.model_dump(mode="json"), default=str)


def token_digest(run: RunResult) -> str:
    """本次 run 里 token 入参落成的脱敏摘要 (跨 run 可比即证明摘要稳定)。"""
    return run.trials["t1"][0].grader_results[0].details["tool_calls"][0]["arguments"]["token"]


class TestCaptureBoundary:
    async def test_off_by_default_no_arguments_persisted(self):
        """Scenario: 默认关闭 → 落盘里没有入参内容, run 记录标注未采集。"""
        suite = make_suite(capture=False)
        runner = make_runner(suite)
        run, blob = await run_and_dump(runner, suite)

        assert run.evidence.capture_tool_arguments is False
        assert run.evidence.capture_by_task == {"t1": False}
        assert SECRET not in blob
        trial = run.trials["t1"][0]
        # 结论自陈: 入参槽位是「未采集」而非空串或假定的零
        arguments = trial.grader_results[0].details["tool_calls"][0]["arguments"]
        assert arguments == {
            "missing": True,
            "reason": "capture_disabled",
            "detail": "套件未开启工具入参采集",
        }
        assert (
            trial.grader_results[0].details["evidence"]["missing_fields"]["tool.arguments"]
            == "capture_disabled"
        )

    async def test_enabled_capture_persists_only_redacted_form(self):
        """Scenario: 入参含凭据样式内容 → 落盘与呈现都是脱敏形式。"""
        suite = make_suite(capture=True)
        runner = make_runner(suite)
        run, blob = await run_and_dump(runner, suite)

        assert SECRET not in blob, "未脱敏值不得进入持久化"
        assert "redacted:sha256:" in blob
        assert run.evidence.capture_tool_arguments is True
        assert run.evidence.redactor_identifier == "aeval.sha256-summary"

        # 摘要而非删字段: 键与结构留着, 字符串值换成可比的摘要
        arguments = run.trials["t1"][0].grader_results[0].details["tool_calls"][0]["arguments"]
        assert set(arguments) == {"url", "token"}
        assert all(value.startswith("redacted:sha256:") for value in arguments.values())

    async def test_redacted_comparison_is_stable_and_reproducible(self):
        """同一入参两次评测 → 摘要一致, 参数比对仍可在脱敏形式下进行。"""
        suite = make_suite(capture=True)
        first, _ = await run_and_dump(make_runner(suite), suite)
        second, _ = await run_and_dump(make_runner(suite), suite)

        assert token_digest(first) == token_digest(second)

    async def test_replacing_the_hook_is_recorded_for_audit(self):
        """Scenario: 替换脱敏实现 → 不再叠加默认处理, run 记录其标识。"""
        suite = make_suite(capture=True)
        runner = make_runner(suite, redactor=IdentityEvidenceRedactor())
        run, blob = await run_and_dump(runner, suite)

        # 恒等钩子是显式逃生舱: 明文会被落盘, 但审计看得见是谁关的
        assert SECRET in blob
        assert run.evidence.redactor_identifier == "aeval.identity-none"

    async def test_task_level_tightening_is_visible_on_the_run(self):
        """Scenario: 套件开启而单任务关闭 → run 记录反映该差异。"""
        suite = make_suite(capture=True, task_capture=False)
        run, blob = await run_and_dump(make_runner(suite), suite)

        assert run.evidence.capture_by_task == {"t1": False}
        assert SECRET not in blob

    async def test_boundary_records_the_translation_caliber(self):
        """Scenario: 复核一个历史结论 → run 直接回答当时的规范与映射版本。"""
        run, _ = await run_and_dump(make_runner(make_suite(capture=False)), make_suite(False))
        assert run.evidence.spec_version
        assert run.evidence.mapping_version == "1"


class TestBoundaryComparability:
    def test_same_boundary_is_comparable(self):
        a = EvidenceBoundary(spec_version="s", mapping_version="1", capture_tool_arguments=False,
                             redactor_identifier="r", redactor_version="1")
        ok, reason = a.compare_with(a.model_copy(deep=True))
        assert ok is True and reason is None

    @pytest.mark.parametrize("field,value,keyword", [
        ("mapping_version", "9", "翻译表版本"),
        ("spec_version", "other", "规范版本"),
        ("capture_tool_arguments", True, "采集开关"),
        ("redactor_identifier", "custom", "脱敏处理"),
    ])
    def test_differing_boundary_is_flagged(self, field, value, keyword):
        base = EvidenceBoundary(spec_version="s", mapping_version="1",
                                capture_tool_arguments=False,
                                redactor_identifier="r", redactor_version="1")
        other = base.model_copy(update={field: value})
        ok, reason = base.compare_with(other)
        assert ok is False
        assert keyword in reason

    def test_missing_boundary_cannot_be_judged_comparable(self):
        base = EvidenceBoundary(spec_version="s", mapping_version="1")
        ok, reason = base.compare_with(None)
        assert ok is False
        assert "历史 run" in reason


class TestHistoricalRows:
    @staticmethod
    def _legacy_payload() -> dict:
        """一份本变更之前落盘的 run 记录 (不含任何新增字段)。"""
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
                        "trace_id": "trace_1",
                        "success": True,
                        "grader_results": [],
                        "metrics": {"n_total_tokens": 150.0, "latency_ms": 12.0},
                        "transcript": [],
                        "outcome": {},
                        "duration_ms": 12.0,
                        "verdict": "valid",
                    }
                ]
            },
            "summary": {
                "total_tasks": 1,
                "total_trials": 1,
                "pass_at_k": {},
                "pass_power_k": {},
                "task_summaries": [
                    {"task_id": "t1", "total_trials": 1}
                ],
            },
        }

    async def test_legacy_run_still_readable_with_unknown_evidence_fields(
        self, tmp_path
    ):
        """Scenario: 读取历史 run → 成功, 终止原因与成本报告为未知, 既有数值不变。"""
        import aiosqlite

        db = str(tmp_path / "legacy.db")
        storage = SqliteStorage(db_path=db)
        await storage.initialize()
        async with aiosqlite.connect(db) as db_conn:
            payload = self._legacy_payload()
            await db_conn.execute(
                "INSERT INTO runs (run_id, suite_name, status, started_at, completed_at, data)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "run_legacy", "legacy", "completed", 1.0, 2.0,
                    json.dumps(payload, default=str),
                ),
            )
            await db_conn.commit()

        run = await storage.get_run("run_legacy")
        assert run is not None
        assert run.evidence is None
        trial = run.trials["t1"][0]
        assert trial.termination_reason is None
        assert trial.evidence_gaps == []
        # 既有字段原样
        assert trial.metrics == {"n_total_tokens": 150.0, "latency_ms": 12.0}
        assert run.summary.task_summaries[0].resources is None

    def test_legacy_trials_tally_as_unknown_not_agent_completed(self):
        from agent_eval.core.metrics import termination_distribution

        run = RunResult(**self._legacy_payload())
        assert termination_distribution(run.trials["t1"]) == {"unknown": 1}

    def test_cost_trend_excludes_runs_without_a_cost_axis(self):
        """Scenario: 历史 run 不参与成本趋势, 被排除且标注原因。"""
        from agent_eval.core.metrics import cost_trend
        from agent_eval.core.types import ResourceSummary, RunSummary

        legacy = RunResult(**self._legacy_payload())
        priced = RunResult(
            run_id="run_priced",
            suite_name="legacy",
            summary=RunSummary(
                total_tasks=1,
                total_trials=1,
                resources=ResourceSummary(total_cost_usd=0.42, avg_cost_usd=0.42),
            ),
        )
        unpriced = RunResult(
            run_id="run_unpriced",
            suite_name="legacy",
            summary=RunSummary(
                total_tasks=1,
                total_trials=1,
                resources=ResourceSummary(
                    total_cost_usd=None, cost_unknown_reason="price_table_not_configured"
                ),
            ),
        )

        trend = cost_trend([legacy, priced, unpriced])
        assert [point["run_id"] for point in trend["series"]] == ["run_priced"]
        excluded = {item["run_id"]: item["reason"] for item in trend["excluded"]}
        assert excluded == {
            "run_legacy": "history_run_without_resource_axis",
            "run_unpriced": "price_table_not_configured",
        }


class TestDeletionPurgesEvidence:
    async def test_sqlite_delete_removes_run_and_its_requests(self, tmp_path):
        storage = SqliteStorage(db_path=str(tmp_path / "purge.db"))
        await storage.initialize()
        suite = make_suite(capture=True)
        runner = make_runner(suite, storage=storage)
        run = await runner.run_suite(suite)
        await storage.save_human_score_request(
            {"run_id": run.run_id, "task_id": "t1", "trial_index": 0, "grader_name": "human"}
        )
        assert await storage.list_human_score_requests(run_id=run.run_id)

        assert await storage.delete_run(run.run_id) is True
        assert await storage.get_run(run.run_id) is None
        assert await storage.list_human_score_requests(run_id=run.run_id) == []

    async def test_memory_delete_removes_requests_and_caches(self):
        storage = MemoryStorage()
        suite = make_suite(capture=True)
        runner = make_runner(suite, storage=storage)
        run = await runner.run_suite(suite)
        await storage.save_human_score_request({"run_id": run.run_id, "grader_name": "human"})
        assert runner._grader_cache.get(run.run_id) is not None  # 派生缓存已建立

        assert await storage.delete_run(run.run_id) is True
        assert await storage.list_human_score_requests(run_id=run.run_id) == []
        assert runner.evict_run_cache(run.run_id) is True
        assert runner.evict_run_cache(run.run_id) is False

    async def test_other_runs_keep_their_cache_after_eviction(self):
        suite = make_suite(capture=False)
        runner = make_runner(suite)
        first = await runner.run_suite(suite)
        second = await runner.run_suite(suite)

        runner.evict_run_cache(first.run_id)
        assert second.run_id in runner._grader_cache
