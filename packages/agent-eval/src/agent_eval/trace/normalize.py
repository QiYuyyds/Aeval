"""
span → 标准观测的归一化引擎 (capability: trace-provider)。

一次翻译, 处处消费: 本模块是唯一允许读原始属性名的地方, 其余各层只读
:class:`~agent_eval.trace.observations.NormalizedTrace`。角色判定按标准观测字段
而非 span 名称子串; 读不到的字段一律带原因地缺失, 不以 0 顶替。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from agent_eval.trace.mapping import (
    FIELD_AGENT_NAME,
    FIELD_AGENT_VERSION,
    FIELD_ARTIFACT_CONTENT,
    FIELD_ARTIFACT_ID,
    FIELD_ARTIFACT_TYPE,
    FIELD_CACHE_READ_TOKENS,
    FIELD_ERROR_TYPE,
    FIELD_INPUT_TOKENS,
    FIELD_MODEL,
    FIELD_OPERATION_NAME,
    FIELD_OUTPUT_TOKENS,
    FIELD_REASONING_TOKENS,
    FIELD_SESSION_ID,
    FIELD_SPAN_ROLE,
    FIELD_TOOL_ARGUMENTS,
    FIELD_TOOL_NAME,
    FIELD_TOOL_RESULT,
    FIELD_TOOL_SUCCESS,
    FIELD_TOTAL_TOKENS,
    ROLE_ARTIFACT,
    ROLE_LLM,
    ROLE_OTHER,
    ROLE_TOOL,
    AttributeMapping,
    default_mapping,
)
from agent_eval.trace.observations import (
    AbsentReason,
    ArtifactObservation,
    LlmCallObservation,
    Missing,
    NormalizedTrace,
    ToolCallObservation,
    is_missing,
)

logger = logging.getLogger(__name__)



def default_redactor() -> Any:
    """默认证据脱敏处理。

    延迟导入: 归一化层是下层, 不在模块导入期反向依赖 core (core 会 import 本模块)。
    """
    from agent_eval.core.redaction import HashingEvidenceRedactor

    return HashingEvidenceRedactor()

_TOKEN_FIELDS = (
    FIELD_INPUT_TOKENS,
    FIELD_OUTPUT_TOKENS,
    FIELD_REASONING_TOKENS,
    FIELD_CACHE_READ_TOKENS,
    FIELD_TOTAL_TOKENS,
)

_ERROR_STATUS_CODES = frozenset({"error", "status_error", "2"})
_OK_STATUS_CODES = frozenset({"ok", "status_ok", "1"})


def normalize_spans(
    spans: list[dict[str, Any]] | None,
    *,
    mapping: AttributeMapping | None = None,
    capture_tool_arguments: bool = False,
    redactor: Any | None = None,
    source_status: str | None = None,
    source_detail: str = "",
) -> NormalizedTrace:
    """把一个 trace 的 spans 归一化为标准观测。

    Args:
        spans: provider 返回的 span 列表 (None 视为什么都没取到)
        mapping: 属性翻译表; None = 内置 OTel GenAI 条目
        capture_tool_arguments: 套件级入参/结果采集开关 (默认关)
        redactor: 采集开启时强制应用的脱敏处理; None = 默认摘要实现
        source_status: "ok" / "empty" / "unavailable"; None = 由 spans 推断
        source_detail: provider 报错原文等说明性信息
    """
    table = mapping or default_mapping()
    trace = NormalizedTrace(
        spec_version=table.spec_version,
        mapping_version=table.version,
    )
    items = list(spans or [])
    if source_status is None:
        source_status = "ok" if items else "empty"
    trace.source_status = source_status  # type: ignore[assignment]
    trace.source_detail = source_detail
    trace.source_spans = items if trace.source_status == "ok" else []

    if trace.source_status != "ok":
        # 取证通道没产出: 计数报缺失而不是 0, 否则下游会把「没读到」当成「没做」
        reason = (
            AbsentReason.PROVIDER_UNAVAILABLE
            if trace.source_status == "unavailable"
            else AbsentReason.NO_SUCH_CALL
        )
        trace.tool_call_count = Missing(reason, "trace 未提供任何 span")
        trace.llm_call_count = Missing(reason, "trace 未提供任何 span")
        trace.session_id = Missing(reason)
        trace.agent_name = Missing(reason)
        trace.agent_version = Missing(reason)
        return trace

    reader = _FieldReader(table, trace)
    known = table.attribute_names()

    for span in items:
        attributes = span.get("attributes") if isinstance(span, dict) else None
        if not isinstance(attributes, dict):
            attributes = {}
        unrecognized = sorted(name for name in attributes if name not in known)
        for name in unrecognized:
            if name not in trace.unrecognized_attributes:
                trace.unrecognized_attributes.append(name)

        role = _span_role(attributes, table)
        latency = _latency_ms(span)

        if role == ROLE_TOOL:
            trace.tool_calls.append(
                _tool_call(
                    span,
                    attributes,
                    table,
                    reader,
                    unrecognized,
                    latency,
                    capture_tool_arguments=capture_tool_arguments,
                    redactor=redactor or default_redactor(),
                )
            )
        elif role == ROLE_LLM:
            trace.llm_calls.append(_llm_call(attributes, reader, unrecognized, latency))
        elif role == ROLE_ARTIFACT:
            trace.artifacts.append(_artifact(attributes, reader, unrecognized))

        _collect_context_fields(attributes, table, reader, trace)

    trace.unrecognized_attributes.sort()
    trace.tool_call_count = len(trace.tool_calls)
    trace.llm_call_count = len(trace.llm_calls)
    if trace.unrecognized_attributes:
        logger.warning(
            "归一化映射未识别 %d 个属性名 (规范版本 %s / 映射版本 %s): %s",
            len(trace.unrecognized_attributes),
            table.spec_version,
            table.version,
            trace.unrecognized_attributes,
        )
    return trace


class _FieldReader:
    """按映射读字段, 并把「为什么没读到」记进 trace.missing_fields。"""

    def __init__(self, table: AttributeMapping, trace: NormalizedTrace):
        self._table = table
        self._trace = trace

    def read(self, attributes: dict[str, Any], field: str, hint: Any = None) -> Any:
        converter = _CONVERTERS.get(field, _as_str)
        raw = self._table.read(attributes, field)
        if raw is not None:
            converted = converter(raw)
            if not is_missing(converted):
                return converted
            return self._absent(field, converted, attributes)
        return self._absent(field, hint if is_missing(hint) else None, attributes)

    def register_absent(self, field: str, reason: AbsentReason) -> None:
        """记录一次「该字段没读到」及其原因 (缺什么要说得出来)。"""
        self._trace.missing_fields.setdefault(field, reason.value)

    def _absent(self, field: str, hint: Missing | None, attributes: dict[str, Any]) -> Missing:
        if hint is not None:
            reason, detail = hint.reason, hint.detail
        else:
            # 属性名不认识 与 provider 未覆盖 是两回事: 前者说明宿主埋了但用了我们不认的名字
            known = self._table.attribute_names()
            if any(name not in known for name in attributes):
                reason = AbsentReason.UNRECOGNIZED_ATTRIBUTE
                detail = ""
            else:
                reason = AbsentReason.PROVIDER_NOT_COVERED
                detail = ""
        self._trace.missing_fields.setdefault(field, reason.value)
        return Missing(reason, detail or field)


def _span_role(attributes: dict[str, Any], table: AttributeMapping) -> str:
    """角色判定: 显式声明 > 操作名 > 按标准观测字段推断 (不看 span 名称)。"""
    explicit = table.read(attributes, FIELD_SPAN_ROLE)
    if explicit is not None:
        role = table.role_of(explicit)
        if role != ROLE_OTHER:
            return role
    operation = table.read(attributes, FIELD_OPERATION_NAME)
    if operation is not None:
        return table.role_of(operation)
    for field, role in (
        (FIELD_TOOL_NAME, ROLE_TOOL),
        (FIELD_TOOL_ARGUMENTS, ROLE_TOOL),
        (FIELD_TOOL_RESULT, ROLE_TOOL),
        *((f, ROLE_LLM) for f in _TOKEN_FIELDS),
        (FIELD_ARTIFACT_TYPE, ROLE_ARTIFACT),
        (FIELD_ARTIFACT_ID, ROLE_ARTIFACT),
    ):
        if table.read(attributes, field) is not None:
            return role
    return ROLE_OTHER


def _tool_call(
    span: dict[str, Any],
    attributes: dict[str, Any],
    table: AttributeMapping,
    reader: _FieldReader,
    unrecognized: list[str],
    latency: Any,
    *,
    capture_tool_arguments: bool,
    redactor: Any,
) -> ToolCallObservation:
    hint = _absent_for(unrecognized)
    return ToolCallObservation(
        tool_name=reader.read(attributes, FIELD_TOOL_NAME, hint),
        success=_tool_success(span, attributes, table, reader, hint),
        arguments=_captured(
            attributes, table, reader, FIELD_TOOL_ARGUMENTS,
            capture_tool_arguments=capture_tool_arguments, redactor=redactor,
        ),
        result=_captured(
            attributes, table, reader, FIELD_TOOL_RESULT,
            capture_tool_arguments=capture_tool_arguments, redactor=redactor,
        ),
        latency_ms=latency,
    )


def _captured(
    attributes: dict[str, Any],
    table: AttributeMapping,
    reader: _FieldReader,
    field: str,
    *,
    capture_tool_arguments: bool,
    redactor: Any,
) -> Any:
    """入参/结果槽位: 未 opt-in 一律不读 (与规范自身的 Opt-In 立场一致)。"""
    if not capture_tool_arguments:
        reader.register_absent(field, AbsentReason.CAPTURE_DISABLED)
        return Missing(AbsentReason.CAPTURE_DISABLED, "套件未开启工具入参采集")
    raw = table.read(attributes, field)
    if raw is None:
        return reader.read(attributes, field)
    return redactor.redact(raw, field=field)


def _tool_success(
    span: dict[str, Any],
    attributes: dict[str, Any],
    table: AttributeMapping,
    reader: _FieldReader,
    hint: Missing | None,
) -> Any:
    """成败: 宿主显式布尔 > error.type > span status, 三者皆无则报缺失。"""
    declared = table.read(attributes, FIELD_TOOL_SUCCESS)
    if declared is not None:
        converted = _as_bool(declared)
        if not is_missing(converted):
            return converted
    if table.read(attributes, FIELD_ERROR_TYPE) is not None:
        return False
    status = span.get("status")
    code = status.get("status_code") if isinstance(status, dict) else None
    if isinstance(code, (str, int)) and not isinstance(code, bool):
        normalized = str(code).strip().lower()
        if normalized in _ERROR_STATUS_CODES:
            return False
        if normalized in _OK_STATUS_CODES:
            return True
    return reader.read(attributes, FIELD_TOOL_SUCCESS, hint)


def _llm_call(
    attributes: dict[str, Any],
    reader: _FieldReader,
    unrecognized: list[str],
    latency: Any,
) -> LlmCallObservation:
    hint = _absent_for(unrecognized)
    return LlmCallObservation(
        input_tokens=reader.read(attributes, FIELD_INPUT_TOKENS, hint),
        output_tokens=reader.read(attributes, FIELD_OUTPUT_TOKENS, hint),
        reasoning_tokens=reader.read(attributes, FIELD_REASONING_TOKENS, hint),
        cache_read_tokens=reader.read(attributes, FIELD_CACHE_READ_TOKENS, hint),
        total_tokens=reader.read(attributes, FIELD_TOTAL_TOKENS, hint),
        model=reader.read(attributes, FIELD_MODEL, hint),
        latency_ms=latency,
    )


def _artifact(
    attributes: dict[str, Any],
    reader: _FieldReader,
    unrecognized: list[str],
) -> ArtifactObservation:
    hint = _absent_for(unrecognized)
    return ArtifactObservation(
        artifact_type=reader.read(attributes, FIELD_ARTIFACT_TYPE, hint),
        artifact_id=reader.read(attributes, FIELD_ARTIFACT_ID, hint),
        content=reader.read(attributes, FIELD_ARTIFACT_CONTENT, hint),
    )


def _collect_context_fields(
    attributes: dict[str, Any],
    table: AttributeMapping,
    reader: _FieldReader,
    trace: NormalizedTrace,
) -> None:
    for field, target in (
        (FIELD_SESSION_ID, "session_id"),
        (FIELD_AGENT_NAME, "agent_name"),
        (FIELD_AGENT_VERSION, "agent_version"),
    ):
        if not is_missing(getattr(trace, target)):
            continue
        value = reader.read(attributes, field)
        if not is_missing(value):
            setattr(trace, target, value)


def _absent_for(unrecognized: list[str]) -> Missing | None:
    if unrecognized:
        return Missing(AbsentReason.UNRECOGNIZED_ATTRIBUTE, ",".join(unrecognized))
    return None


def _latency_ms(span: dict[str, Any]) -> Any:
    start = _parse_ts(span.get("start_time"))
    end = _parse_ts(span.get("end_time"))
    if start is None or end is None or end < start:
        return Missing(AbsentReason.PROVIDER_NOT_COVERED, "span 时间戳不可用")
    return (end - start) * 1000.0


def _parse_ts(value: Any) -> float | None:
    """ISO-8601 字符串或 epoch 秒 → epoch 秒。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _as_int(value: Any) -> Any:
    if isinstance(value, bool):
        return Missing(AbsentReason.UNRECOGNIZED_ATTRIBUTE, "布尔不是 token 数")
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return Missing(AbsentReason.UNRECOGNIZED_ATTRIBUTE, f"无法解析为整数: {value!r}")


def _as_float(value: Any) -> Any:
    try:
        return float(value)
    except (TypeError, ValueError):
        return Missing(AbsentReason.UNRECOGNIZED_ATTRIBUTE, f"无法解析为浮点数: {value!r}")


def _as_bool(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "ok", "success"}:
            return True
        if text in {"false", "0", "no", "error", "failure"}:
            return False
    if isinstance(value, int):
        return value != 0
    return Missing(AbsentReason.UNRECOGNIZED_ATTRIBUTE, f"无法解析为布尔值: {value!r}")


def _as_str(value: Any) -> Any:
    if isinstance(value, str):
        return value if value.strip() else Missing(
            AbsentReason.PROVIDER_NOT_COVERED, "空字符串"
        )
    if isinstance(value, (int, float, bool)):
        return str(value)
    return Missing(AbsentReason.UNRECOGNIZED_ATTRIBUTE, f"无法解析为字符串: {value!r}")


_CONVERTERS: dict[str, Any] = {
    FIELD_INPUT_TOKENS: _as_int,
    FIELD_OUTPUT_TOKENS: _as_int,
    FIELD_REASONING_TOKENS: _as_int,
    FIELD_CACHE_READ_TOKENS: _as_int,
    FIELD_TOTAL_TOKENS: _as_int,
    FIELD_TOOL_SUCCESS: _as_bool,
}


async def collect_observations(
    provider: Any,
    trace_id: str,
    *,
    mapping: AttributeMapping | None = None,
    capture_tool_arguments: bool = False,
    redactor: Any | None = None,
) -> NormalizedTrace:
    """从 TraceProvider 取 span 并归一化。

    后端未安装 / 不可达 / 抛异常都只归类为证据缺失 (spec: 取证通道不可用时按
    缺失处理而非崩溃); 任务取消不属于证据缺失, 原样上抛。
    """
    import asyncio

    try:
        spans = await provider.get_spans(trace_id)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — provider 的实现方异常类型不可枚举
        logger.warning("Trace provider failed for %s: %s", trace_id, e)
        return normalize_spans(
            [],
            mapping=mapping,
            capture_tool_arguments=capture_tool_arguments,
            redactor=redactor,
            source_status="unavailable",
            source_detail=f"{type(e).__name__}: {e}",
        )
    return normalize_spans(
        spans,
        mapping=mapping,
        capture_tool_arguments=capture_tool_arguments,
        redactor=redactor,
    )
