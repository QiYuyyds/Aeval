"""扩展点发现与装配的测试 (change ⑤: 组 2)。

用伪造的 entry point 元数据驱动发现逻辑 (不真实安装包):
- 惰性导入: 只有套件引用某名字才 load 其宿主;
- 导入失败降级为告警 + unknown_grader (带「曾尝试加载外部包」线索);
- 同名冲突显式报错, 不静默覆盖;
- CLI extensions 清单与 REST /meta 能力清单读同一份发现结果。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_eval.core import discovery as discovery_module
from agent_eval.core.discovery import (
    ExtensionConflictError,
    ExtensionRegistry,
    discover_extensions,
)
from agent_eval.core.runner import EvalRunner
from agent_eval.core.types import (
    EvalSuite,
    EvalTask,
    GraderConfig,
    GraderType,
    InvalidReason,
    TrialVerdict,
)
from agent_eval.examples.mock_runner import MockAgentRunner, MockTraceProvider
from agent_eval.storage.memory import MemoryStorage

# ─── 伪造 entry point 基建 ───────────────────────────────────────────────────


class RecordingGrader:
    """被「外部包」注册的判据: 打点自己的构造与评分。"""

    name = "host_custom"
    evidence_levels = None
    implementation_version = "1"
    instantiated = 0

    def __init__(self):
        type(self).instantiated += 1

    async def grade(self, trial, spans, task, context=None):  # noqa: ANN001
        from agent_eval.core.types import GraderResult

        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.CUSTOM,
            score=1.0,
            passed=True,
            explanation="host custom grader passed",
        )


class ExplodingEntryPoint:
    """load() 即抛异常的 entry point 目标 (模拟损坏元数据/导入失败)。"""

    def __call__(self) -> Any:
        raise ImportError("No module named 'host_broken_pkg'")


@dataclass
class FakeDist:
    name: str = "host-pkg"


class FakeEntryPoint:
    """最小 EntryPoint 替身: name/dist/load。"""

    def __init__(self, name: str, group: str, factory: Any, dist: str = "host-pkg"):
        self._name = name
        self.group = group
        self._factory = factory
        self.dist = FakeDist(dist)

    @property
    def name(self) -> str:
        return self._name

    def load(self) -> Any:
        return self._factory


def patch_entry_points(monkeypatch, groups: dict[str, list[FakeEntryPoint]]) -> None:
    """把 discovery 模块的 entry_points 换成按组返回伪造条目。"""

    def fake_entry_points(group: str):
        return groups.get(group, [])

    monkeypatch.setattr(discovery_module, "entry_points", fake_entry_points)


def host_grader_config() -> GraderConfig:
    return GraderConfig(type=GraderType.CUSTOM, name="host_custom", config={})


def minimal_task(graders: list[GraderConfig]) -> EvalTask:
    return EvalTask(id="t1", prompt="p", graders=graders, max_trials=1)


# ─── 发现与惰性导入 ──────────────────────────────────────────────────────────


def test_discovered_grader_used_by_cli_assembly(monkeypatch):
    """发现的自定义判据经 EvalRunner 装配真正参与判定 (墙拆掉了)。"""
    RecordingGrader.instantiated = 0
    patch_entry_points(
        monkeypatch,
        {"agent_eval.graders": [FakeEntryPoint("host_custom", "g", RecordingGrader)]},
    )
    registry = discover_extensions()
    runner = EvalRunner(
        agent_runner=MockAgentRunner(success_rate=1.0, latency_range=(0.0, 0.01)),
        trace_provider=MockTraceProvider(),
        storage=MemoryStorage(),
        extensions=registry,
    )
    run = asyncio.run(runner.run_suite(EvalSuite(name="s", tasks=[minimal_task([host_grader_config()])])))
    trial = run.trials["t1"][0]
    result = next(g for g in trial.grader_results if g.grader_name == "host_custom")
    assert result.passed is True
    assert result.explanation == "host custom grader passed"
    assert RecordingGrader.instantiated == 1


def test_lazy_import_only_on_reference(monkeypatch):
    """套件没引用的名字不导入其宿主包 (惰性)。"""
    loaded: list[str] = []

    class CountingFactory:
        def __init__(self, name: str):
            self._name = name

        def __call__(self):
            loaded.append(self._name)
            return RecordingGrader()

    patch_entry_points(
        monkeypatch,
        {
            "agent_eval.graders": [
                FakeEntryPoint("host_custom", "g", CountingFactory("host_custom")),
                FakeEntryPoint("other_thing", "g", CountingFactory("other_thing")),
            ]
        },
    )
    registry = discover_extensions()
    assert registry.names("graders") == ["host_custom", "other_thing"]
    # 名字查询不触发导入
    assert loaded == []
    runner = EvalRunner(
        agent_runner=MockAgentRunner(success_rate=1.0, latency_range=(0.0, 0.01)),
        trace_provider=MockTraceProvider(),
        storage=MemoryStorage(),
        extensions=registry,
    )
    asyncio.run(runner.run_suite(EvalSuite(name="s", tasks=[minimal_task([host_grader_config()])])))
    assert loaded == ["host_custom"]  # 只有被引用的名字被导入


def test_broken_metadata_warns_but_does_not_block(monkeypatch, caplog):
    """导入失败: 告警 + 继续装配其余; 引用到它按 unknown_grader 处理。"""
    patch_entry_points(
        monkeypatch,
        {
            "agent_eval.graders": [
                FakeEntryPoint("broken_one", "g", ExplodingEntryPoint(), dist="broken-pkg"),
                FakeEntryPoint("host_custom", "g", RecordingGrader),
            ]
        },
    )
    registry = discover_extensions()
    runner = EvalRunner(
        agent_runner=MockAgentRunner(success_rate=1.0, latency_range=(0.0, 0.01)),
        trace_provider=MockTraceProvider(),
        storage=MemoryStorage(),
        extensions=registry,
    )
    task = minimal_task([host_grader_config()])
    task.graders.append(GraderConfig(type=GraderType.CUSTOM, name="broken_one", config={}))
    run = asyncio.run(runner.run_suite(EvalSuite(name="s", tasks=[task])))
    trial = run.trials["t1"][0]
    # 好的判据照常生效
    host = next(g for g in trial.grader_results if g.grader_name == "host_custom")
    assert host.passed is True
    # 坏的按未注册处理, 失败信息列出可用名字 + 曾尝试加载外部包
    broken = next(g for g in trial.grader_results if g.grader_name == "broken_one")
    assert broken.verdict is TrialVerdict.INVALID
    assert broken.invalid_reason is InvalidReason.UNKNOWN_GRADER
    assert "host_custom" in broken.explanation  # 可用名字清单
    assert "broken-pkg" in broken.explanation  # 来源包名保留供排查
    assert "曾尝试" in broken.explanation


def test_same_name_conflict_raises_listing_both_sources(monkeypatch):
    """同名冲突: 显式报错并列出两个来源包, 不静默覆盖。"""
    patch_entry_points(
        monkeypatch,
        {
            "agent_eval.graders": [
                FakeEntryPoint("host_custom", "g", RecordingGrader, dist="pkg-a"),
                FakeEntryPoint("host_custom", "g", RecordingGrader, dist="pkg-b"),
            ]
        },
    )
    with pytest.raises(ExtensionConflictError) as exc:
        discover_extensions()
    assert "pkg-a" in str(exc.value) and "pkg-b" in str(exc.value)


# ─── CLI 与 API 同源 ─────────────────────────────────────────────────────────


def test_cli_extensions_listing_shows_builtin_and_discovered(monkeypatch):
    """eval-suite extensions: 内置 + 被发现的 (标注来源包)。"""
    from agent_eval._cli_app import app

    patch_entry_points(
        monkeypatch,
        {
            "agent_eval.graders": [FakeEntryPoint("host_custom", "g", RecordingGrader)],
            "agent_eval.environments": [],
            "agent_eval.simulators": [],
        },
    )
    result = CliRunner().invoke(app, ["extensions"])
    assert result.exit_code == 0
    assert "host_custom" in result.output
    assert "host-pkg" in result.output
    assert "state_check" in result.output  # 内置判据也在清单上


def test_cli_and_rest_meta_list_same_names(monkeypatch):
    """CLI 清单与 /meta 能力清单给出的自定义判据名字集合相同。"""
    from agent_eval._cli_app import app
    from agent_eval.api.app import meta_payload

    patch_entry_points(
        monkeypatch,
        {"agent_eval.graders": [FakeEntryPoint("host_custom", "g", RecordingGrader)]},
    )
    meta = meta_payload()
    meta_custom = meta["capabilities"]["extensions"].get("graders", [])

    result = CliRunner().invoke(app, ["extensions"])
    cli_custom = {
        line.split()[1]
        for line in result.output.splitlines()
        if line.strip().startswith("- ") and "host-pkg" in line
    }
    assert {e["name"] for e in meta_custom} == cli_custom == {"host_custom"}


def test_empty_discovery_yields_empty_catalog(monkeypatch):
    """无任何注册时: 空目录, 不报错 (离线 CI 的常态)。"""
    patch_entry_points(monkeypatch, {})
    registry: ExtensionRegistry = discover_extensions()
    assert registry.catalog() == {}
    assert registry.names("graders") == []
