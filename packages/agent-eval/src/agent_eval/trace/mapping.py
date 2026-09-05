"""
属性翻译表: 把任意埋点词汇的 span 归一化为标准观测 (capability: trace-provider)。

框架源码里 MUST NOT 出现任何宿主的私有属性名 —— 它们一律是运行时注入的映射条目
(``AttributeMapping.with_extra``), 加一个宿主 = 加一条表项。内置条目对齐 OTel GenAI
语义约定, 该规范已整体迁出主仓库且除 ``error.type`` 外全部处于 Development 稳定性,
因此版本被钉死在常量里并随 run 落盘, 使历史数据可判断是否同口径。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

# 对齐 open-telemetry/semantic-conventions-genai。该仓库尚无 tagged release,
# 故钉到具体 commit; 规范属性名再变时表现为「未识别属性清单」告警而非静默读零。
OTEL_GENAI_SPEC_VERSION = "genai-94f432d"

# 本表自身的版本: 增删条目即递增, 与规范版本一起写进 run 以供口径复核。
ATTRIBUTE_MAPPING_VERSION = "1"

# 归一化字段名 (词汇无关的框架内命名)
FIELD_TOOL_NAME = "tool.name"
FIELD_TOOL_SUCCESS = "tool.success"
FIELD_TOOL_ARGUMENTS = "tool.arguments"
FIELD_TOOL_RESULT = "tool.result"
FIELD_ERROR_TYPE = "error.type"
FIELD_INPUT_TOKENS = "usage.input_tokens"
FIELD_OUTPUT_TOKENS = "usage.output_tokens"
FIELD_REASONING_TOKENS = "usage.reasoning_tokens"
FIELD_CACHE_READ_TOKENS = "usage.cache_read_tokens"
FIELD_TOTAL_TOKENS = "usage.total_tokens"
FIELD_MODEL = "model"
FIELD_SESSION_ID = "session.id"
FIELD_AGENT_NAME = "agent.name"
FIELD_AGENT_VERSION = "agent.version"
FIELD_OPERATION_NAME = "operation.name"
FIELD_SPAN_ROLE = "span.role"
FIELD_ARTIFACT_TYPE = "artifact.type"
FIELD_ARTIFACT_ID = "artifact.id"
FIELD_ARTIFACT_CONTENT = "artifact.content"

# span 角色
ROLE_TOOL = "tool"
ROLE_LLM = "llm"
ROLE_ARTIFACT = "artifact"
ROLE_OTHER = "other"

# 规范未定义其属性名的字段: 默认无条目, 只能由宿主映射接入 (读不到即报缺失)
_UNMAPPED_BY_DEFAULT: tuple[str, ...] = (
    FIELD_TOOL_SUCCESS,
    FIELD_TOTAL_TOKENS,
    FIELD_SPAN_ROLE,
    FIELD_ARTIFACT_TYPE,
    FIELD_ARTIFACT_ID,
    FIELD_ARTIFACT_CONTENT,
)

DEFAULT_FIELD_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    FIELD_TOOL_NAME: ("gen_ai.tool.name",),
    FIELD_TOOL_ARGUMENTS: ("gen_ai.tool.call.arguments",),
    FIELD_TOOL_RESULT: ("gen_ai.tool.call.result",),
    FIELD_ERROR_TYPE: ("error.type",),
    FIELD_INPUT_TOKENS: ("gen_ai.usage.input_tokens",),
    FIELD_OUTPUT_TOKENS: ("gen_ai.usage.output_tokens",),
    FIELD_REASONING_TOKENS: ("gen_ai.usage.reasoning.output_tokens",),
    FIELD_CACHE_READ_TOKENS: ("gen_ai.usage.cache_read.input_tokens",),
    FIELD_MODEL: ("gen_ai.response.model", "gen_ai.request.model"),
    FIELD_SESSION_ID: ("gen_ai.conversation.id",),
    FIELD_AGENT_NAME: ("gen_ai.agent.name",),
    FIELD_AGENT_VERSION: ("gen_ai.agent.version",),
    FIELD_OPERATION_NAME: ("gen_ai.operation.name",),
    **{name: () for name in _UNMAPPED_BY_DEFAULT},
}

# gen_ai.operation.name 取值 → 角色 (规范当前列出的取值, 未列出的按 other)
DEFAULT_OPERATION_ROLES: dict[str, str] = {
    "execute_tool": ROLE_TOOL,
    "chat": ROLE_LLM,
    "text_completion": ROLE_LLM,
    "generate_content": ROLE_LLM,
    "invoke_agent": ROLE_OTHER,
    "create_agent": ROLE_OTHER,
    "invoke_workflow": ROLE_OTHER,
    "plan": ROLE_OTHER,
    "retrieval": ROLE_OTHER,
    "embeddings": ROLE_OTHER,
}

# 直接声明角色的字段值 (宿主可用一条 span.role 映射把角色写在自己习惯的属性上)
_ROLE_VALUE_ALIASES: dict[str, str] = {
    ROLE_TOOL: ROLE_TOOL,
    "tool_call": ROLE_TOOL,
    "tool.call": ROLE_TOOL,
    ROLE_LLM: ROLE_LLM,
    "llm": ROLE_LLM,
    ROLE_ARTIFACT: ROLE_ARTIFACT,
}


@dataclass(frozen=True)
class AttributeMapping:
    """标准字段 → 候选属性名 (按优先级取首个命中的属性)。"""

    fields: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_FIELD_ATTRIBUTES)
    )
    operation_roles: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_OPERATION_ROLES)
    )
    spec_version: str = OTEL_GENAI_SPEC_VERSION
    version: str = ATTRIBUTE_MAPPING_VERSION

    # ── 查询 ──────────────────────────────────────────────────────────────

    def attribute_names(self) -> frozenset[str]:
        """本表认识的全部属性名 (未出现的属性名进未识别清单)。"""
        return frozenset(name for names in self.fields.values() for name in names)

    def known_fields(self) -> tuple[str, ...]:
        return tuple(self.fields)

    def candidates(self, canonical_field: str) -> tuple[str, ...]:
        return self.fields.get(canonical_field, ())

    def read(self, attributes: Mapping[str, Any], canonical_field: str) -> Any:
        """按候选属性名读原始值; 都没有时返回 None (由调用方归类缺失原因)。"""
        for name in self.candidates(canonical_field):
            if name in attributes and attributes[name] is not None:
                return attributes[name]
        return None

    def role_of(self, value: Any) -> str:
        """操作名/角色声明值 → 角色。"""
        if not isinstance(value, str):
            return ROLE_OTHER
        text = value.strip().lower()
        if text in self.operation_roles:
            return self.operation_roles[text]
        return _ROLE_VALUE_ALIASES.get(text, ROLE_OTHER)

    # ── 扩展 ──────────────────────────────────────────────────────────────

    def with_extra(self, entries: Mapping[str, str | Sequence[str]]) -> AttributeMapping:
        """增加/覆盖映射条目 (宿主私有词汇的接入点, 不改框架源码与评分器)。

        同一字段的候选名按「新给出的在前」合并: 宿主埋点优先于内置默认。
        """
        merged = dict(self.fields)
        for canonical_field, names in entries.items():
            extra = (names,) if isinstance(names, str) else tuple(names)
            existing = tuple(n for n in merged.get(canonical_field, ()) if n not in extra)
            merged[canonical_field] = tuple(extra) + existing
        return replace(self, fields=merged)

    def with_version(self, version: str) -> AttributeMapping:
        return replace(self, version=version)


def default_mapping(
    extra_entries: Mapping[str, str | Sequence[str]] | None = None,
    version: str | None = None,
) -> AttributeMapping:
    """内置条目 (OTel GenAI) + 可选的宿主条目。"""
    mapping = AttributeMapping()
    if extra_entries:
        mapping = mapping.with_extra(extra_entries)
    if version is not None:
        mapping = mapping.with_version(version)
    return mapping
