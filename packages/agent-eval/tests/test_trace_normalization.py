"""归一化边界的行为契约 (specs/trace-provider).

核心断言: 同一条逻辑 trace 用标准词汇和用宿主词汇表达, 归一化结果必须一致
——差异只允许出现在映射配置里, 不允许出现在框架源码或评分器里。
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_eval.core.redaction import HashingEvidenceRedactor, IdentityEvidenceRedactor
from agent_eval.trace.mapping import default_mapping
from agent_eval.trace.normalize import collect_observations, normalize_spans
from agent_eval.trace.observations import AbsentReason, is_missing

try:  # 与整套测试共用同一份宿主词汇定义
    from conftest import HOST_VOCABULARY
except ImportError:  # pragma: no cover
    HOST_VOCABULARY = {
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

STANDARD = default_mapping()
HOST = default_mapping(HOST_VOCABULARY, version="host-1")

# 一条逻辑 trace: 一次模型调用 (输入/输出/推理/缓存) + 两次工具调用 (一成一败)
LOGICAL = {
    "input_tokens": 120,
    "output_tokens": 34,
    "reasoning_tokens": 8,
    "cache_read_tokens": 16,
    "session_id": "sess_42",
    "agent_name": "chat-agent",
    "agent_version": "2.3.1",
    "tools": [("fs_read", True), ("fs_write", False)],
}


def _write(mapping, entries: dict[str, Any]) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    for field, value in entries.items():
        candidates = mapping.candidates(field)
        if candidates:
            attributes[candidates[0]] = value
    return attributes


def _trace_in(mapping) -> list[dict[str, Any]]:
    """用给定词汇把同一条逻辑 trace 写出来。"""
    turn_attributes = _write(
        mapping,
        {
            "usage.input_tokens": LOGICAL["input_tokens"],
            "usage.output_tokens": LOGICAL["output_tokens"],
            "usage.reasoning_tokens": LOGICAL["reasoning_tokens"],
            "usage.cache_read_tokens": LOGICAL["cache_read_tokens"],
            "session.id": LOGICAL["session_id"],
            "agent.name": LOGICAL["agent_name"],
            "agent.version": LOGICAL["agent_version"],
        },
    )
    outcome_spans = []
    for name, ok in LOGICAL["tools"]:
        # 失败在标准词汇里用 error.type 表达, 宿主词汇另有自己的布尔属性
        entries = {"tool.name": name, "tool.arguments": {"path": "/tmp/a.py"}}
        if ok:
            entries["tool.success"] = True
        else:
            entries["tool.success"] = False
            entries["error.type"] = "tool_error"
        outcome_spans.append(
            _span(
                "tool.call",
                _write(mapping, entries),
                status_code="OK" if ok else "ERROR",
            )
        )
    return [_span("agent.turn", turn_attributes, status_code="OK"), *outcome_spans]


def _span(name: str, attributes: dict[str, Any], status_code: str) -> dict[str, Any]:
    return {
        "name": name,
        "attributes": attributes,
        "start_time": "2026-09-04T10:00:00Z",
        "end_time": "2026-09-04T10:00:02Z",
        "status": {"status_code": status_code},
    }


def _facts(observations) -> dict[str, Any]:
    """两套词汇必须逐项一致的可比事实。"""
    return {
        "tool_count": observations.tool_call_count,
        "llm_count": observations.llm_call_count,
        "tools": observations.observed_tools(),
        "successes": [c.success for c in observations.tool_calls],
        "latencies": [c.latency_ms for c in observations.tool_calls],
        "input": observations.sum_field("input_tokens"),
        "output": observations.sum_field("output_tokens"),
        "reasoning": observations.sum_field("reasoning_tokens"),
        "cache": observations.sum_field("cache_read_tokens"),
        "session": observations.session_id,
        "agent": (observations.agent_name, observations.agent_version),
    }


class TestSameLogicalTraceBothVocabularies:
    def test_standard_vocabulary_reads_nonzero(self):
        """Scenario: 标准埋点的 trace 开箱可读 —— 无需任何宿主专用配置。"""
        observations = normalize_spans(_trace_in(STANDARD))
        facts = _facts(observations)
        assert facts["tool_count"] == 2
        assert facts["input"] == 120
        assert facts["reasoning"] == 8
        assert facts["session"] == "sess_42"
        assert observations.spec_version == STANDARD.spec_version
        assert observations.mapping_version == STANDARD.version

    def test_host_vocabulary_reads_the_same_facts(self):
        """Scenario: 宿主私有词汇以配置接入 —— 源码与评分器都不改。"""
        standard = _facts(normalize_spans(_trace_in(STANDARD)))
        host = _facts(normalize_spans(_trace_in(HOST), mapping=HOST))
        assert host == standard

    def test_host_spans_without_mapping_are_visible_gaps(self):
        """没配映射就读宿主 trace: 名称报「属性名不认识」并进清单, 不静默算成 0 次。"""
        observations = normalize_spans(_trace_in(HOST))
        assert observations.observed_tools() == []
        assert observations.missing_fields["tool.name"] == (
            AbsentReason.UNRECOGNIZED_ATTRIBUTE.value
        )
        assert "agenthub.tool_name" in observations.unrecognized_attributes

    def test_unrecognized_attribute_names_are_collected(self):
        """Scenario: 属性名不被认识 → 清单化并落进记录, 不静默丢弃。"""
        spans = _trace_in(HOST) + [
            _span("extra", {"acme.custom.thing": 1}, status_code="OK"),
        ]
        observations = normalize_spans(spans, mapping=STANDARD)
        assert "acme.custom.thing" in observations.unrecognized_attributes
        assert observations.unrecognized_attributes == sorted(
            observations.unrecognized_attributes
        )

    def test_missing_reason_distinguishes_uncovered_from_unrecognized(self):
        """Scenario: provider 未覆盖某属性 → 报缺失并给出原因, 而不是 0。"""
        uncovered = normalize_spans(
            [{"name": "t", "attributes": {"gen_ai.usage.input_tokens": 5}}]
        )
        assert is_missing(uncovered.sum_field("reasoning_tokens"))
        assert uncovered.missing_fields["usage.reasoning_tokens"] == (
            AbsentReason.PROVIDER_NOT_COVERED.value
        )

        guessed = normalize_spans(
            [{"name": "t", "attributes": {"gen_ai.usage.input_tokens": 5, "weird.attr": 1}}]
        )
        assert guessed.missing_fields["usage.reasoning_tokens"] == (
            AbsentReason.UNRECOGNIZED_ATTRIBUTE.value
        )


class TestCaptureBoundary:
    def test_arguments_absent_by_default(self):
        """Scenario: 未开启采集 → 入参槽位是 capture_disabled, 不是空值。"""
        observations = normalize_spans(_trace_in(STANDARD))
        for call in observations.tool_calls:
            assert is_missing(call.arguments)
            assert call.arguments.reason is AbsentReason.CAPTURE_DISABLED

    def test_arguments_readable_once_opted_in(self):
        """开启采集: 结构保留、字符串换成稳定摘要 (可比对但不落原文)。"""
        observations = normalize_spans(
            _trace_in(STANDARD), capture_tool_arguments=True
        )
        arguments = observations.tool_calls[0].arguments
        assert list(arguments) == ["path"]
        assert arguments["path"].startswith("redacted:sha256:")

    def test_enabled_capture_with_uninstrumented_upstream_says_so(self):
        """Scenario: 套件声明开启, 但 trace 没有入参属性 → 原因写 provider 未提供。"""
        spans = _trace_in(STANDARD)
        for span in spans:
            span["attributes"].pop("gen_ai.tool.call.arguments", None)
        observations = normalize_spans(spans, capture_tool_arguments=True)
        assert observations.tool_calls[0].arguments.reason is (
            AbsentReason.PROVIDER_NOT_COVERED
        )

    def test_redaction_is_applied_on_the_way_in(self):
        """开启采集时, 观测里的字符串已经是脱敏形式 (未脱敏值不进这一层)。"""
        spans = _trace_in(STANDARD)
        spans[1]["attributes"]["gen_ai.tool.call.arguments"] = {
            "path": "/tmp/a.py",
            "token": "sk-live-abcdef123456",
        }
        plain = normalize_spans(spans, capture_tool_arguments=True)
        secret = plain.tool_calls[0].arguments["token"]
        assert secret != "sk-live-abcdef123456"
        assert secret.startswith("redacted:sha256:")
        # 同一输入 → 同一摘要, 参数比对仍可稳定进行
        again = normalize_spans(spans, capture_tool_arguments=True)
        assert again.tool_calls[0].arguments["token"] == secret

    def test_replacing_the_hook_stops_the_default_from_stacking(self):
        """Scenario: 替换脱敏实现 → 框架不再叠加默认处理。"""
        spans = _trace_in(STANDARD)
        spans[1]["attributes"]["gen_ai.tool.call.arguments"] = {"token": "sk-live-abc"}
        plain = normalize_spans(
            spans, capture_tool_arguments=True, redactor=IdentityEvidenceRedactor()
        )
        assert plain.tool_calls[0].arguments == {"token": "sk-live-abc"}
        hashed = normalize_spans(
            spans, capture_tool_arguments=True, redactor=HashingEvidenceRedactor()
        )
        assert hashed.tool_calls[0].arguments["token"].startswith("redacted:sha256:")


class TestProviderAvailability:
    def test_scenario_uninstalled_optional_dependency(self):
        """Scenario: 未安装可选依赖 → run 正常完成, 过程指标一律报缺失。"""

        class BrokenProvider:
            async def get_spans(self, trace_id: str):
                raise RuntimeError("phoenix package not installed")

        observations = _collect(BrokenProvider())
        assert observations.source_status == "unavailable"
        assert is_missing(observations.tool_call_count)
        assert "phoenix" in observations.source_detail

    def test_empty_provider_result_is_not_zero_calls(self):
        class EmptyProvider:
            async def get_spans(self, trace_id: str):
                return []

        observations = _collect(EmptyProvider())
        assert observations.source_status == "empty"
        assert is_missing(observations.tool_call_count)

    def test_collect_uses_the_given_mapping(self):
        class FixedProvider:
            def __init__(self, spans):
                self._spans = spans

            async def get_spans(self, trace_id: str):
                return self._spans

        observations = _collect(FixedProvider(_trace_in(HOST)), mapping=HOST)
        assert observations.tool_call_count == 2


def _collect(provider, **kwargs):
    import asyncio

    return asyncio.run(collect_observations(provider, "trace_x", **kwargs))


@pytest.mark.parametrize("vocabulary", ["standard", "host"])
def test_counts_never_masquerade_as_zero(vocabulary):
    mapping = STANDARD if vocabulary == "standard" else HOST
    spans = _trace_in(mapping)
    observations = normalize_spans(spans, mapping=mapping)
    assert observations.tool_call_count == 2
    assert observations.llm_call_count == 1


# ── 后端摊平交付的还原 (specs/trace-provider: 后端摊平交付的属性必须还原为契约形状) ──
#
# 0.1.0 的真实缺陷: Phoenix 的 dataframe 不给 ``attributes`` 键, provider 读
# ``span.get("attributes", {})`` 恒得空 —— 全部过程指标静默为 0, 且读不出「为什么
# 没读到」。下列用例把这条交付路径钉住。


def _dataframe_rows() -> list[dict[str, Any]]:
    """Phoenix ``get_spans_dataframe().to_dict("records")`` 的行形状。"""
    return [
        {   # 宿主的点号键被后端按前缀收进一个嵌套 dict
            "name": "tool.call",
            "attributes.agenthub": {"tool_name": "fs_write", "success": True},
            "attributes.llm.tools": None,
            "context.trace_id": "t1",
            "context.span_id": "s1",
            "start_time": "2026-09-05T10:00:00Z",
            "end_time": "2026-09-05T10:00:01Z",
            "status_code": "OK",
        },
        {   # 公共约定词汇本来就是摊平列; 「没有值」有好几种写法
            "name": "ChatCompletion",
            "attributes.llm.token_count.prompt": 1000.0,
            "attributes.llm.model_name": "some-model",
            "attributes.agenthub": {"total_tokens": float("nan")},
            "attributes.agenthub.run_id": "",
            "context.trace_id": "t1",
            "context.span_id": "s2",
            "start_time": "2026-09-05T10:00:01Z",
            "end_time": "2026-09-05T10:00:03Z",
            "status_code": "ERROR",
        },
    ]


def test_flattened_and_nested_columns_rebuild_dotted_attributes():
    from agent_eval.trace.phoenix import PhoenixProvider

    spans = PhoenixProvider(endpoint="http://unused")._normalize_spans(_dataframe_rows())

    assert spans[0]["attributes"] == {
        "agenthub.tool_name": "fs_write",
        "agenthub.success": True,
    }
    assert spans[1]["attributes"]["llm.token_count.prompt"] == 1000.0
    assert spans[1]["attributes"]["llm.model_name"] == "some-model"
    # NaN 与空串都是「没有值」, 不是长度为零的观测
    assert "agenthub.total_tokens" not in spans[1]["attributes"]
    assert "agenthub.run_id" not in spans[1]["attributes"]
    # 错误状态还原成归一化层期望的形状, 否则故障 span 会被当成成功
    assert spans[0]["status"] == {"status_code": "OK"}
    assert spans[1]["status"] == {"status_code": "ERROR"}


def test_reconstructed_attributes_reach_the_normalization_layer():
    """还原不是终点: 归一化必须真的从中读出计数。

    HOST 表不含 OpenInference 名, 因此只有工具路可读; 再给一份宿主风格的
    ``extra_entries`` 后两路都通 —— 顺便把宿主实际需要哪几条钉在测试里。
    """
    from agent_eval.trace.phoenix import PhoenixProvider

    spans = PhoenixProvider(endpoint="http://unused")._normalize_spans(_dataframe_rows())

    tool_only = normalize_spans(spans, mapping=HOST)
    assert tool_only.tool_call_count == 1
    assert not is_missing(tool_only.tool_calls[0].tool_name)

    openinference = default_mapping(
        extra_entries={
            "tool.name": "agenthub.tool_name",
            "tool.success": "agenthub.success",
            "usage.input_tokens": "llm.token_count.prompt",
            "model": "llm.model_name",
        }
    )
    both = normalize_spans(spans, mapping=openinference)
    assert both.tool_call_count == 1
    assert both.llm_call_count == 1
    assert not is_missing(both.llm_calls[0].input_tokens)
