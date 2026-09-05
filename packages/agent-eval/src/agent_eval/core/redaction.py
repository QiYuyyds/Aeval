"""
默认证据脱敏 (capability: graders — 采集开启时脱敏为默认启用的必经环节)。

工具入参与结果可能含凭据与用户数据, 因此: 默认不采集; 一旦采集, MUST 经脱敏
处理后才进入证据存储 / API / Dashboard。默认实现以哈希摘要替换原文而不是删字段,
使「期望参数 vs 实际参数」的比对仍可在同一函数下稳定复现。
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol, runtime_checkable

DEFAULT_REDACTOR_IDENTIFIER = "aeval.sha256-summary"
DEFAULT_REDACTOR_VERSION = "1"

# 摘要前缀与截断长度: 可读、可比较、不反推原文
_DIGEST_PREFIX = "redacted:sha256:"
_DIGEST_CHARS = 16
_MAX_RECURSION_DEPTH = 6


@runtime_checkable
class EvidenceRedactor(Protocol):
    """可替换的脱敏接口 (整体替换, 框架不叠加默认处理)。"""

    identifier: str
    version: str

    def redact(self, value: Any, *, field: str = "") -> Any:
        """返回可安全落盘/呈现的形式; 同一输入 MUST 得到同一输出。"""
        ...


class HashingEvidenceRedactor:
    """默认脱敏: 字符串经规范化后替换为定长摘要, 结构与非字符串标量保留。"""

    identifier = DEFAULT_REDACTOR_IDENTIFIER
    version = DEFAULT_REDACTOR_VERSION

    def redact(self, value: Any, *, field: str = "") -> Any:
        return self._redact(value, 0)

    def _redact(self, value: Any, depth: int) -> Any:
        if isinstance(value, str):
            # 幂等: 归档里的正文已是摘要形式, 重评分再读一次不得得到另一个值
            if value.startswith(_DIGEST_PREFIX):
                return value
            return self.digest(value)
        if depth >= _MAX_RECURSION_DEPTH:
            return self.digest(repr(value))
        if isinstance(value, dict):
            return {
                key: self._redact(item, depth + 1) for key, item in sorted(value.items(), key=_key_order)
            }
        if isinstance(value, (list, tuple)):
            return [self._redact(item, depth + 1) for item in value]
        if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
            return value
        return self.digest(str(value))

    @staticmethod
    def digest(text: str) -> str:
        """规范化 (去首尾空白) 后取摘要: 尾随空白差异不该让同一参数比对失败。"""
        normalized = text.strip()
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return f"{_DIGEST_PREFIX}{digest[:_DIGEST_CHARS]}"


class IdentityEvidenceRedactor:
    """恒等钩子 (逐字比对场景的显式逃生舱)。

    用它意味着原始入参将明文落盘 —— 该选择由 run 上记录的 identifier 暴露给审计。
    """

    identifier = "aeval.identity-none"
    version = DEFAULT_REDACTOR_VERSION

    def redact(self, value: Any, *, field: str = "") -> Any:
        return value


def _key_order(item: tuple[Any, Any]) -> tuple[str, str]:
    """dict 键可能混合类型 (JSON 里通常是 str), 排序需对任意键稳定。"""
    return (type(item[0]).__name__, str(item[0]))


def describe_redactor(redactor: Any) -> tuple[str, str]:
    """取脱敏处理的 (标识, 版本); 匿名实现回落到类名。"""
    identifier = getattr(redactor, "identifier", None) or type(redactor).__name__
    version = str(getattr(redactor, "version", "") or "unversioned")
    return str(identifier), version
