"""
Trace provider implementations and the span → 标准观测归一化边界.

Aeval reads OTel traces through two layers:

- provider (``PhoenixProvider`` or a user-supplied ``TraceProvider``): 取原始 span
- normalization (``normalize_spans`` / ``collect_observations``): 按版本钉定的
  ``AttributeMapping`` 翻译成框架内标准观测, 供评分器与统计层消费

框架源码不含任何宿主私有属性名: 私有一词只能作为 ``AttributeMapping.with_extra``
的运行时条目接入。

Usage:
    from agent_eval.trace import PhoenixProvider, collect_observations

    obs = await collect_observations(PhoenixProvider(), "trace_abc123")
"""

from agent_eval.trace.mapping import (
    ATTRIBUTE_MAPPING_VERSION,
    OTEL_GENAI_SPEC_VERSION,
    AttributeMapping,
    default_mapping,
)
from agent_eval.trace.normalize import collect_observations, normalize_spans
from agent_eval.trace.observations import (
    AbsentReason,
    ArtifactObservation,
    LlmCallObservation,
    Missing,
    NormalizedTrace,
    ToolCallObservation,
    is_missing,
)
from agent_eval.trace.phoenix import PhoenixProvider

__all__ = [
    "PhoenixProvider",
    "AttributeMapping",
    "default_mapping",
    "OTEL_GENAI_SPEC_VERSION",
    "ATTRIBUTE_MAPPING_VERSION",
    "normalize_spans",
    "collect_observations",
    "NormalizedTrace",
    "ToolCallObservation",
    "LlmCallObservation",
    "ArtifactObservation",
    "Missing",
    "AbsentReason",
    "is_missing",
]
