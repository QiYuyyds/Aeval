"""环境初始态声明与环境身份边界的测试 (change ⑤: 组 5)。

- EvidenceBoundary 记录环境标识与版本; 无环境显式记 'none', 历史行读回为空;
- 环境身份不同的两个 run 拒绝方向性结论 (not_comparable_reason);
- task 的 fixture 声明经既有 setup(task) 句柄递给环境 (签名不变);
- 声明了环境检查判据却未装配环境 → 证据不足并指明缺环境装配。
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_eval.core.runner import EvalRunner
from agent_eval.core.types import (
    NO_ENVIRONMENT,
    EvalSuite,
    EvalTask,
    EvidenceBoundary,
    EvidenceKind,
    GraderConfig,
    GraderType,
    JudgmentMoment,
    Observation,
    ObservedBy,
    TrialVerdict,
)
from agent_eval.examples.mock_runner import MockAgentRunner, MockTraceProvider
from agent_eval.storage.memory import MemoryStorage

FAST = {"latency_range": (0.0, 0.01)}


class RecordingEnvironment:
    """记录 setup 收到的 task 的最小环境实现。"""

    def __init__(self, *, environment_id: str = "pytest-sandbox", version: str = "v2"):
        self.environment_id = environment_id
        self.environment_version = version
        self.setup_tasks: list[Any] = []
        self.baseline = {"files": {}}

    async def setup(self, task) -> None:
        self.setup_tasks.append(task)

    async def teardown(self, task) -> None:
        return None

    async def probe(self, channel: str = ""):
        return [
            Observation(
                kind=EvidenceKind.STATE,
                observed_by=ObservedBy.HARNESS,
                channel=channel or "workspace_listing",
                value={"files": {"output.py": "def hello(): pass\n"}},
            )
        ]

    async def snapshot(self):
        return dict(self.baseline)

    async def verify_clean(self, baseline, harness_readings=None):
        return {"clean": True, "differences": []}

    async def restore(self, baseline) -> None:
        return None


def state_task(task_id: str = "t1", *, harness_only: bool = False) -> EvalTask:
    grader_kwargs: dict = {
        "type": GraderType.STATE,
        "name": "state_check",
        "config": {"expectations": [{"type": "file_exists", "path": "output.py"}]},
        "judgment_moment": JudgmentMoment.AT_END,
    }
    if harness_only:
        grader_kwargs["evidence"] = ["harness"]
    return EvalTask(
        id=task_id,
        prompt="p",
        graders=[GraderConfig(**grader_kwargs)],
        max_trials=1,
    )


def make_runner(agent=None, environment=None) -> EvalRunner:
    return EvalRunner(
        agent_runner=agent or MockAgentRunner(success_rate=1.0, **FAST),
        trace_provider=MockTraceProvider(),
        storage=MemoryStorage(),
        environment=environment,
    )


# ─── 环境身份记录 (5.2) ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_environment_identity_recorded_with_manager():
    env = RecordingEnvironment(environment_id="pytest-sandbox", version="v2")
    runner = make_runner(environment=env)
    run = await runner.run_suite(EvalSuite(name="s", tasks=[state_task()]))
    boundary = run.evidence
    assert boundary.environment_identity == "pytest-sandbox"
    assert boundary.environment_version == "v2"


@pytest.mark.asyncio
async def test_no_environment_recorded_as_explicit_none():
    """无环境参与: 显式记 'none' (哨兵), 与历史 run 的「未记录」(None) 区分。"""
    runner = make_runner(environment=None)
    run = await runner.run_suite(EvalSuite(name="s", tasks=[state_task()]))
    assert run.evidence.environment_identity == NO_ENVIRONMENT


def test_historical_boundary_without_identity_reads_back_clean():
    """历史行的 environment_identity=None 读回为空且不报错。"""
    boundary = EvidenceBoundary(
        capture_tool_arguments=False,
        capture_model_content=False,
        capture_by_task={},
        capture_content_by_task={},
        subject_allowed={},
        spec_version="1",
        mapping_version="1",
    )
    assert boundary.environment_identity is None
    assert boundary.environment_version is None


# ─── 不可比判定 (5.3 / 5.6) ──────────────────────────────────────────────────


def _boundary(identity: str | None, version: str | None = None) -> EvidenceBoundary:
    return EvidenceBoundary(
        capture_tool_arguments=False,
        capture_model_content=False,
        capture_by_task={},
        capture_content_by_task={},
        subject_allowed={},
        spec_version="1",
        mapping_version="1",
        environment_identity=identity,
        environment_version=version,
    )


def test_different_environment_identity_not_comparable():
    comparable, reason = _boundary("env-a", "v1").compare_with(_boundary("env-b", "v1"))
    assert comparable is False
    assert "env-a" in reason and "env-b" in reason


def test_same_fixture_declaration_comparable():
    """同一 fixture 声明跑两次 → 相同环境身份记录, 可比。"""
    assert _boundary("env-a", "v1").compare_with(_boundary("env-a", "v1")) == (True, None)


def test_different_initial_state_version_not_comparable():
    """换初始态声明版本 → 与历史 run 判为不可比。"""
    comparable, reason = _boundary("env-a", "v1").compare_with(_boundary("env-a", "v2"))
    assert comparable is False
    assert "v1" in reason and "v2" in reason


def test_recorded_vs_unrecorded_identity_not_comparable():
    """一边有记录一边未记录 (历史 run) → 拒绝直接比较, 不猜。"""
    comparable, reason = _boundary("env-a").compare_with(_boundary(None))
    assert comparable is False
    assert "未记录" in reason
    # 两个都是 'none' (都明确无环境) → 可比
    assert _boundary(NO_ENVIRONMENT).compare_with(_boundary(NO_ENVIRONMENT)) == (True, None)


@pytest.mark.asyncio
async def test_run_level_compare_flags_environment_change():
    """端到端: 换初始态版本后两个 run 的比较报 not_comparable_reason。"""
    from agent_eval.api.routes.runs import _build_comparison

    env_v1 = RecordingEnvironment(version="v1")
    run1 = await make_runner(environment=env_v1).run_suite(
        EvalSuite(name="s", tasks=[state_task()])
    )
    env_v2 = RecordingEnvironment(version="v2")
    run2 = await make_runner(environment=env_v2).run_suite(
        EvalSuite(name="s", tasks=[state_task()])
    )
    comparison = _build_comparison(run1, run2)
    assert comparison["comparable"] is False
    reason = comparison["not_comparable_reason"]
    assert "v1" in reason and "v2" in reason


# ─── fixture 声明递给环境 (5.1) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_fixture_declaration_reaches_setup():
    """task.environment.fixture 经既有 setup(task) 句柄递给环境实现。"""
    env = RecordingEnvironment()
    task = state_task()
    from agent_eval.core.types import EnvironmentDeclaration

    task.environment = EnvironmentDeclaration(
        id="pytest-sandbox", version="v9", fixture={"seed_files": {"a.txt": "hi"}}
    )
    runner = make_runner(environment=env)
    await runner.run_suite(EvalSuite(name="s", tasks=[task]))
    assert env.setup_tasks, "setup(task) 被调用"
    received = env.setup_tasks[0].environment
    assert received is not None
    assert received.fixture == {"seed_files": {"a.txt": "hi"}}
    assert received.id == "pytest-sandbox"


# ─── 缺环境装配 (5.4) ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_state_grader_without_any_environment_reports_missing_assembly():
    """声明了环境检查判据但未装配环境 → 证据不足, 指明缺环境装配。"""
    runner = make_runner(environment=None)
    task = state_task(harness_only=True)
    # 只信评测侧取证 (被评方自报不算数): 无环境时 harness 探针无从谈起
    run = await runner.run_suite(EvalSuite(name="s", tasks=[task]))
    trial = run.trials["t1"][0]
    result = next(g for g in trial.grader_results if g.grader_name == "state_check")
    assert result.verdict is TrialVerdict.INVALID
    assert "no_environment_assembled" in result.explanation
    # 不折成被评方失败: 结论级别是评测侧证据问题, 不是 agent 失败
    assert result.invalid_reason.value == "evidence_unavailable"


@pytest.mark.asyncio
async def test_per_trial_environment_lifecycle_and_leak_warning_not_failure():
    """per-trial 建/拆; verify_clean 检出泄漏仍是告警 + restore, 不判失败。"""

    class LeakyEnvironment(RecordingEnvironment):
        def __init__(self):
            super().__init__()
            self.restored = 0

        async def verify_clean(self, baseline, harness_readings=None):
            return {"clean": False, "differences": ["stray file: /tmp/x"]}

        async def restore(self, baseline) -> None:
            self.restored += 1

    env = LeakyEnvironment()
    agent = MockAgentRunner(success_rate=1.0, **FAST)
    runner = make_runner(agent=agent, environment=env)
    run = await runner.run_suite(EvalSuite(name="s", tasks=[state_task()]))
    trial = run.trials["t1"][0]
    # 泄漏不判失败: trial 是否成功只由评分决定; 泄漏是告警 + restore
    assert trial.verdict is TrialVerdict.VALID
    assert env.restored >= 1  # 自动 restore 发生了
    assert len(env.setup_tasks) == 1  # per-trial: setup 恰好一次
