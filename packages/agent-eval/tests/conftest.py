"""Aeval test-suite configuration.

Register the built-in metric pytest plugin (fixtures answer_relevancy /
faithfulness / context_recall / context_precision / eval_metrics /
eval_runner and the --eval-suite / --eval-threshold gate options). pytest 9
requires `pytest_plugins` in a top-level conftest, so registration lives here.

Dual-vocabulary fixtures: trace-consuming tests run once against spans using
the OTel GenAI names the framework ships, and once against a host's private
names supplied purely as mapping configuration. Both must reach the same
conclusion — that is what stops the suite from endorsing one private vocabulary
(design D9).
"""

from __future__ import annotations

from typing import Any

import pytest

pytest_plugins = ["agent_eval.metrics.pytest_plugin"]

# 宿主私有属性名只允许出现在测试配置里: 框架源码内含它即为违规
# (见 tests/test_vocabulary_isolation.py)。这里刻意沿用真实宿主的命名, 使
# 「接入一个宿主 = 增加映射条目」这条路径在测试中就是可执行的示例。
HOST_VOCABULARY: dict[str, str] = {
    "tool.name": "agenthub.tool_name",
    "tool.success": "agenthub.success",
    "usage.total_tokens": "agenthub.total_tokens",
    "session.id": "agenthub.session_id",
    "agent.name": "agenthub.agent_name",
    "agent.version": "agenthub.agent_version",
    "artifact.type": "agenthub.artifact_type",
    "artifact.id": "agenthub.artifact_id",
    "artifact.content": "agenthub.content",
}

_START = "2026-08-29T10:00:00Z"


def _put(attributes: dict[str, Any], mapping: Any, field: str, value: Any) -> None:
    """按当前词汇写入一个字段 (表里没有该字段的属性名就不写)。"""
    candidates = mapping.candidates(field)
    if candidates:
        attributes[candidates[0]] = value


def _span(
    mapping: Any,
    *,
    name: str,
    attributes: dict[str, Any],
    end_offset_s: int = 1,
    status_code: str | None = None,
) -> dict[str, Any]:
    span: dict[str, Any] = {
        "name": name,
        "attributes": attributes,
        "start_time": _START,
        "end_time": f"2026-08-29T10:00:{end_offset_s:02d}Z",
    }
    # 没有状态是一个独立的观测 (既非 OK 也非 ERROR), 不默认填 OK
    if status_code is not None:
        span["status"] = {"status_code": status_code}
    return span


@pytest.fixture(params=["standard", "host"])
def vocabulary(request) -> str:
    """当前用例使用的埋点词汇。"""
    return request.param


@pytest.fixture
def attribute_mapping(vocabulary):
    """词汇 → 翻译表 (宿主词汇只是多给的一条条映射, 不改框架源码)。"""
    from agent_eval.trace.mapping import default_mapping

    if vocabulary == "standard":
        return default_mapping()
    return default_mapping(HOST_VOCABULARY, version="host-1")


@pytest.fixture
def make_tool_span(attribute_mapping):
    """构造一次工具调用 span: 同一逻辑事实, 各自词汇各自表达。"""

    def factory(
        tool: str | None = "fs_write",
        *,
        success: bool | None = True,
        arguments: Any = None,
        result: Any = None,
    ) -> dict[str, Any]:
        attributes: dict[str, Any] = {}
        if tool is not None:
            _put(attributes, attribute_mapping, "tool.name", tool)
        if success is False:
            # 标准词汇用 error.type 与 span status 表达失败; 宿主用自有布尔属性
            _put(attributes, attribute_mapping, "error.type", "tool_error")
        if success is not None:
            _put(attributes, attribute_mapping, "tool.success", success)
        if arguments is not None:
            _put(attributes, attribute_mapping, "tool.arguments", arguments)
        if result is not None:
            _put(attributes, attribute_mapping, "tool.result", result)
        return _span(
            attribute_mapping,
            name="tool.call",
            attributes=attributes,
            status_code="ERROR" if success is False else "OK",
        )

    return factory


@pytest.fixture
def make_turn_span(attribute_mapping):
    """构造一次模型调用 span (token 分解按当前词汇表达)。"""

    def factory(
        *,
        input_tokens: int | None = 100,
        output_tokens: int | None = 50,
        reasoning_tokens: int | None = None,
        cache_read_tokens: int | None = None,
        total_tokens: int | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        attributes: dict[str, Any] = {}
        for field, value in (
            ("usage.input_tokens", input_tokens),
            ("usage.output_tokens", output_tokens),
            ("usage.reasoning_tokens", reasoning_tokens),
            ("usage.cache_read_tokens", cache_read_tokens),
            ("usage.total_tokens", total_tokens),
            ("model", model),
        ):
            if value is not None:
                _put(attributes, attribute_mapping, field, value)
        return _span(
            attribute_mapping,
            name="agent.turn",
            attributes=attributes,
            status_code="OK",
        )

    return factory


@pytest.fixture
def make_artifact_span(attribute_mapping):
    """构造一个产物 span (规范未定义产物属性, 只有宿主映射才读得到)。"""

    def factory(
        *,
        artifact_type: str = "code_file",
        artifact_id: str = "art_1",
        content: str = "# code",
    ) -> dict[str, Any]:
        attributes: dict[str, Any] = {}
        _put(attributes, attribute_mapping, "artifact.type", artifact_type)
        _put(attributes, attribute_mapping, "artifact.id", artifact_id)
        _put(attributes, attribute_mapping, "artifact.content", content)
        return _span(
            attribute_mapping,
            name="artifact.create",
            attributes=attributes,
            status_code="OK",
        )

    return factory


@pytest.fixture
def normalize(attribute_mapping):
    """按当前词汇的翻译表归一化 spans (默认不采集工具入参)。"""
    from agent_eval.trace.normalize import normalize_spans

    def factory(
        spans: list[dict[str, Any]],
        *,
        capture_tool_arguments: bool = False,
        redactor: Any = None,
        **kwargs: Any,
    ):
        kwargs.setdefault("mapping", attribute_mapping)
        return normalize_spans(
            spans,
            capture_tool_arguments=capture_tool_arguments,
            redactor=redactor,
            **kwargs,
        )

    return factory


@pytest.fixture
def make_context(attribute_mapping, normalize):
    """把 spans 归一化后装进 EvalContext (内置评分器读观测, 不读原始 spans)。"""
    from agent_eval.core.contract import EvalContext
    from agent_eval.core.types import TrialResult

    def factory(
        task: Any,
        spans: list[dict[str, Any]],
        trial: TrialResult | None = None,
        *,
        capture_tool_arguments: bool = False,
        redactor: Any = None,
    ) -> EvalContext:
        observations = normalize(
            spans,
            capture_tool_arguments=capture_tool_arguments,
            redactor=redactor,
        )
        return EvalContext(
            run_id="test-run",
            task=task,
            trial=trial or TrialResult(trial_index=0),
            spans=spans,
            observations=observations,
        )

    return factory
