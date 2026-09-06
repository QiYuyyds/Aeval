"""
属性翻译表: 把任意埋点词汇的 span 归一化为标准观测 (capability: trace-provider)。

框架源码里 MUST NOT 出现任何宿主的私有属性名 —— 它们一律是运行时注入的映射条目
(``AttributeMapping.with_extra``), 加一个宿主 = 加一条表项。内置条目是**可按名字选的
公共约定预设** (``default_mapping(vocabulary=...)``): 默认 OTel GenAI, 另有
OpenInference —— 后者是 Phoenix 一类后端实际导出的名字。每个预设把它取号所依据的
规范修订号钉在常量里并随 run 落盘, 使历史数据可判断是否同口径。
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
# 模型输入/输出正文: 采集开关必须知道该从 span 上摘掉哪些属性名, 而这些名字
# 只能来自翻译表 (与工具入参同一套处理), 不能散落在裁剪逻辑里
FIELD_MODEL_INPUT_CONTENT = "content.input"
FIELD_MODEL_OUTPUT_CONTENT = "content.output"

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
    FIELD_MODEL_INPUT_CONTENT: ("gen_ai.input.messages", "gen_ai.prompt"),
    FIELD_MODEL_OUTPUT_CONTENT: ("gen_ai.output.messages", "gen_ai.completion"),
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


# ── 词汇预设 (公共埋点约定) ────────────────────────────────────────────────

VOCABULARY_OTEL_GENAI = "otel-genai"
VOCABULARY_OPENINFERENCE = "openinference"

# 取号依据: openinference-semantic-conventions 0.1.30 的真实常量。
# 这只是「条目抄自哪一版规范」的标签 —— 本表是纯数据, 该包不是运行时依赖。
OPENINFERENCE_SPEC_VERSION = "openinference-0.1.30"

# OpenInference 相对默认表的增量条目 (未列出的字段沿用默认)。
#
# total_tokens 在这里可以安全映射: OpenInference 把它挂在 LLM span 自己身上,
# 该 span 本就因 prompt/completion 而判为 LLM, 不会像宿主那样把 run 级收尾
# span 伪装成一次模型调用。
#
# 刻意不映 tool.parameters: 无法确认它是调用实参还是参数 schema。猜错的代价
# 不是"读不到", 而是把一个错误的东西当成证据去判定, 比报缺失更坏。
OPENINFERENCE_FIELD_OVERRIDES: dict[str, tuple[str, ...]] = {
    FIELD_TOOL_NAME: ("tool.name",),
    FIELD_INPUT_TOKENS: ("llm.token_count.prompt",),
    FIELD_OUTPUT_TOKENS: ("llm.token_count.completion",),
    FIELD_REASONING_TOKENS: ("llm.token_count.completion_details.reasoning",),
    FIELD_CACHE_READ_TOKENS: ("llm.token_count.prompt_details.cache_read",),
    FIELD_TOTAL_TOKENS: ("llm.token_count.total",),
    FIELD_MODEL: ("llm.model_name",),
    FIELD_SESSION_ID: ("session.id",),
    FIELD_SPAN_ROLE: ("openinference.span.kind",),
    FIELD_MODEL_INPUT_CONTENT: ("input.value", "llm.input_messages"),
    FIELD_MODEL_OUTPUT_CONTENT: ("output.value", "llm.output_messages"),
}


# 每个预设钉住的规范修订号: 选哪个词汇 = 按哪一版公共约定读数据。
# 能力清单据此公布可选预设, 调用方不必读源码就能列出可填的值。
VOCABULARY_SPEC_VERSIONS: dict[str, str] = {
    VOCABULARY_OTEL_GENAI: OTEL_GENAI_SPEC_VERSION,
    VOCABULARY_OPENINFERENCE: OPENINFERENCE_SPEC_VERSION,
}


def known_vocabularies() -> tuple[str, ...]:
    """可选的公共约定预设 (供 CLI、能力清单与人发现); 默认值在前。"""
    return tuple(VOCABULARY_SPEC_VERSIONS)


def _preset(vocabulary: str) -> tuple[dict[str, tuple[str, ...]], str]:
    """解析词汇预设 → (候选条目表, 规范修订号)。"""
    if vocabulary == VOCABULARY_OTEL_GENAI:
        return dict(DEFAULT_FIELD_ATTRIBUTES), VOCABULARY_SPEC_VERSIONS[vocabulary]

    if vocabulary == VOCABULARY_OPENINFERENCE:
        merged = dict(DEFAULT_FIELD_ATTRIBUTES)
        for field_name, names in OPENINFERENCE_FIELD_OVERRIDES.items():
            existing = tuple(n for n in merged.get(field_name, ()) if n not in names)
            merged[field_name] = names + existing  # 新约定优先, 默认名兜底
        return merged, VOCABULARY_SPEC_VERSIONS[vocabulary]

    # 静默退回默认表会产出一整套「看似正常、实则全空」的观测, 比直接失败更难查。
    raise ValueError(
        f"未知的 trace 词汇预设 {vocabulary!r}; 可选值: {', '.join(known_vocabularies())}"
    )


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
    vocabulary: str = VOCABULARY_OTEL_GENAI,
) -> AttributeMapping:
    """内置条目 (按 ``vocabulary`` 选公共约定预设) + 可选的宿主条目。"""
    fields, spec_version = _preset(vocabulary)
    mapping = AttributeMapping(fields=fields, spec_version=spec_version)
    if extra_entries:
        mapping = mapping.with_extra(extra_entries)
    if version is not None:
        mapping = mapping.with_version(version)
    return mapping
