"""
标准观测记录 — 框架与宿主之间的词汇无关边界 (capability: trace-provider)。

span 只被翻译一次: 评分器与统计层一律读这里的记录, 不读原始属性名。
每条记录的字段类型都是 ``T | Missing`` —— ``Missing`` 携带缺失原因, 与
「值为 0」在语义上严格分离: 计数为 0 表示 trace 里确实没有这类调用,
``Missing`` 表示什么都没观测到。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class AbsentReason(str, Enum):
    """字段缺失的原因 (spec: 字段缺失与字段值为零语义分离)。"""

    PROVIDER_UNAVAILABLE = "provider_unavailable"  # 后端不可达 / 未安装 / 返回空
    PROVIDER_NOT_COVERED = "provider_not_covered"  # provider 未覆盖该属性
    UNRECOGNIZED_ATTRIBUTE = "unrecognized_attribute"  # 属性名不被映射认识
    NO_SUCH_CALL = "no_such_call"  # trace 中确实没有这类调用
    CAPTURE_DISABLED = "capture_disabled"  # 套件未开启入参/结果采集


@dataclass(frozen=True)
class Missing:
    """「什么都没读到」的显式表示 —— 不得以 0 / 空字符串冒充。"""

    reason: AbsentReason
    detail: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - 防误用
        raise TypeError(
            "Missing 表示证据缺失, 不得当作布尔值使用; 请用 is_missing() 判定"
        )


def is_missing(value: Any) -> bool:
    """字段是否为「缺失」而非「读到的零值/空值」。"""
    return isinstance(value, Missing)


def missing_reason(value: Any) -> AbsentReason | None:
    """取字段的缺失原因; 字段有值时返回 None。"""
    return value.reason if isinstance(value, Missing) else None


# ─── 观测记录 ─────────────────────────────────────────────────────────────────


@dataclass
class ToolCallObservation:
    """一次工具调用 (工具名、成败、入参/结果槽位、时延)。"""

    tool_name: Any = Missing(AbsentReason.PROVIDER_NOT_COVERED)
    success: Any = Missing(AbsentReason.PROVIDER_NOT_COVERED)
    arguments: Any = Missing(AbsentReason.CAPTURE_DISABLED)
    result: Any = Missing(AbsentReason.CAPTURE_DISABLED)
    latency_ms: Any = Missing(AbsentReason.PROVIDER_NOT_COVERED)


@dataclass
class LlmCallObservation:
    """一次模型调用 (输入/输出/推理/缓存 token、模型、时延)。"""

    input_tokens: Any = Missing(AbsentReason.PROVIDER_NOT_COVERED)
    output_tokens: Any = Missing(AbsentReason.PROVIDER_NOT_COVERED)
    reasoning_tokens: Any = Missing(AbsentReason.PROVIDER_NOT_COVERED)
    cache_read_tokens: Any = Missing(AbsentReason.PROVIDER_NOT_COVERED)
    total_tokens: Any = Missing(AbsentReason.PROVIDER_NOT_COVERED)
    model: Any = Missing(AbsentReason.PROVIDER_NOT_COVERED)
    latency_ms: Any = Missing(AbsentReason.PROVIDER_NOT_COVERED)


@dataclass
class ArtifactObservation:
    """一个产物 (类型/标识/内容)。规范未定义产物属性, 故只能由映射条目接入。"""

    artifact_type: Any = Missing(AbsentReason.PROVIDER_NOT_COVERED)
    artifact_id: Any = Missing(AbsentReason.PROVIDER_NOT_COVERED)
    content: Any = Missing(AbsentReason.PROVIDER_NOT_COVERED)


SourceStatus = Literal["ok", "unavailable", "empty"]


@dataclass
class NormalizedTrace:
    """一个 trace 归一化后的全部标准观测。"""

    tool_calls: list[ToolCallObservation] = field(default_factory=list)
    llm_calls: list[LlmCallObservation] = field(default_factory=list)
    artifacts: list[ArtifactObservation] = field(default_factory=list)
    session_id: Any = Missing(AbsentReason.PROVIDER_NOT_COVERED)
    agent_name: Any = Missing(AbsentReason.PROVIDER_NOT_COVERED)
    agent_version: Any = Missing(AbsentReason.PROVIDER_NOT_COVERED)

    # 计数即观测: provider 什么都没给时是 Missing, 而不是 0 (spec 1.6)
    tool_call_count: Any = 0
    llm_call_count: Any = 0

    unrecognized_attributes: list[str] = field(default_factory=list)
    # 按采集声明被整条摘掉的属性名: 「没采到」与「采了但没授权落盘」要能分开
    stripped_attributes: list[str] = field(default_factory=list)
    missing_fields: dict[str, str] = field(default_factory=dict)
    # 原始 span 随观测保留: 既供第三方 grader 沿用旧签名, 也使结论可回溯到原文
    source_spans: list[dict[str, Any]] = field(default_factory=list)
    spec_version: str = ""
    mapping_version: str = ""
    source_status: SourceStatus = "ok"
    source_detail: str = ""

    @property
    def observed_anything(self) -> bool:
        """是否真的读到了东西 (False = 取证通道为空或不可用)。"""
        return self.source_status == "ok" and bool(
            self.tool_calls or self.llm_calls or self.artifacts
        )

    def observed_tools(self) -> list[str]:
        """有名称的工具调用序列 (名称缺失的调用不臆造名字)。"""
        return [t.tool_name for t in self.tool_calls if not is_missing(t.tool_name)]

    def sum_field(self, attr: str) -> Any:
        """跨 LLM 调用求和; 一次都没有读到值时返回 Missing, 不返回 0。"""
        values = [
            getattr(call, attr)
            for call in self.llm_calls
            if not is_missing(getattr(call, attr))
        ]
        if not values:
            return Missing(AbsentReason.PROVIDER_NOT_COVERED, attr)
        return sum(values)
