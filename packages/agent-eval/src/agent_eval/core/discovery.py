"""扩展点发现 (⑤: extension-contracts / cli)。

经 entry-point 组发现第三方的 grader / environment / simulator, 注册进
运行时**唯一的一份注册表** —— CLI 与 API 能力清单读同一份发现结果,
不存在「命令行一套、接口一套」的并行装配路径 (design D5)。

组名约定 (与既有 ``agent_eval.runners`` 同构):

- ``agent_eval.graders``      → 自定义评分器
- ``agent_eval.environments`` → 环境管理器
- ``agent_eval.simulators``   → 用户模拟器

加载策略 (design Risks):

- **惰性导入**: 只在套件确实引用某名字时才导入其宿主包 —— 导入即执行
  第三方模块代码, 不在启动期全量导入;
- 导入异常降级为「该扩展点不可用」告警并继续装配其余, 套件引用到它时
  按未注册处理 (``unknown_grader``), 来源包名保留供人排查;
- 同名冲突显式报错并列出两个来源包, **不静默覆盖**。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points
from typing import Any

logger = logging.getLogger(__name__)

GRADERS_ENTRY_POINT_GROUP = "agent_eval.graders"
ENVIRONMENTS_ENTRY_POINT_GROUP = "agent_eval.environments"
SIMULATORS_ENTRY_POINT_GROUP = "agent_eval.simulators"

# kind → entry-point 组名 (CLI / API / 测试共用这一份映射)
EXTENSION_KINDS: dict[str, str] = {
    "graders": GRADERS_ENTRY_POINT_GROUP,
    "environments": ENVIRONMENTS_ENTRY_POINT_GROUP,
    "simulators": SIMULATORS_ENTRY_POINT_GROUP,
}


class ExtensionConflictError(RuntimeError):
    """两个已安装的包注册了同名扩展点 —— 显式报错, 不静默覆盖。"""


@dataclass
class ExtensionEntry:
    """注册表里的一条发现记录 (此时**尚未**导入宿主包)。"""

    kind: str
    name: str
    group: str
    source: str  # 来源发行包名 (导入失败时的排查线索)
    entry_point: EntryPoint


@dataclass
class ExtensionRegistry:
    """发现的扩展点注册表 (唯一的一份; 惰性加载)。"""

    entries: dict[str, dict[str, ExtensionEntry]] = field(default_factory=dict)
    # 尝试加载失败的名字 → 原因 (供 unknown_* 错误信息引用, 可诊断不阻断)
    load_failures: dict[str, str] = field(default_factory=dict)
    # 曾尝试加载过的名字 (含成功): 供失败信息指出「本次是否曾尝试加载外部包」
    attempted: set[str] = field(default_factory=set)

    def names(self, kind: str) -> list[str]:
        """该 kind 下已发现的名字 (排序稳定, 供错误信息与清单共用)。"""
        return sorted(self.entries.get(kind, {}))

    def catalog(self) -> dict[str, list[dict[str, str]]]:
        """落盘/呈现形式: kind → [{name, source}]; CLI 清单与 /meta 能力清单同源。"""
        return {
            kind: [
                {"name": entry.name, "source": entry.source}
                for entry in sorted(entries.values(), key=lambda e: e.name)
            ]
            for kind, entries in sorted(self.entries.items())
        }

    def resolve(self, kind: str, name: str) -> tuple[Any | None, str | None]:
        """按名解析扩展点实例; 惰性导入, 失败降级为 (None, 原因)。

        Returns:
            (实例, None) 或 (None, 失败原因)。名字未注册返回 (None, None) ——
            「没注册」与「注册了但导入失败」是两种不同的失败, 错误信息要分得开。
        """
        entry = self.entries.get(kind, {}).get(name)
        if entry is None:
            return None, None
        self.attempted.add(name)
        try:
            obj = entry.entry_point.load()
        except Exception as e:  # noqa: BLE001 — 第三方导入异常不可枚举
            reason = f"{entry.source}: {type(e).__name__}: {e}"
            self.load_failures[name] = reason
            logger.warning(
                "扩展点 %s '%s' (来自 %s) 导入失败, 该扩展点不可用: %s",
                kind, name, entry.source, e,
            )
            return None, reason
        try:
            # 类 → 实例化; 工厂 → 调用; 实例 → 直接用 (三种声明方式都收)
            if isinstance(obj, type) or callable(obj) and not hasattr(obj, "grade") and not hasattr(obj, "setup") \
                    and not hasattr(obj, "next_message"):
                obj = obj()
        except Exception as e:  # noqa: BLE001
            reason = f"{entry.source}: {type(e).__name__}: {e}"
            self.load_failures[name] = reason
            logger.warning(
                "扩展点 %s '%s' (来自 %s) 构造失败, 该扩展点不可用: %s",
                kind, name, entry.source, e,
            )
            return None, reason
        return obj, None


def discover_extensions() -> ExtensionRegistry:
    """扫描三个 entry-point 组, 产出唯一的一份注册表。

    同名冲突 (同一组内两个来源包注册同一名字) 直接抛
    ``ExtensionConflictError`` —— 装配期失败好过运行期静默用错实现。
    元数据损坏 / 单条读取失败降级为告警并跳过该条 (不阻断启动)。
    """
    registry = ExtensionRegistry()
    for kind, group in EXTENSION_KINDS.items():
        try:
            discovered = entry_points(group=group)
        except Exception as e:  # noqa: BLE001 — 元数据系统整体异常只告警
            logger.warning("扩展点组 %s 扫描失败, 该组扩展点不可用: %s", group, e)
            continue
        for ep in discovered:
            try:
                source = ep.dist.name if ep.dist is not None else "unknown"
            except Exception:  # noqa: BLE001 — 损坏的 dist 元数据不阻断其余条目
                source = "unknown"
            bucket = registry.entries.setdefault(kind, {})
            existing = bucket.get(ep.name)
            if existing is not None:
                raise ExtensionConflictError(
                    f"扩展点名冲突: '{ep.name}' 同时由 {existing.source} 与 {source} "
                    f"注册 (entry-point 组 {group}) — 请重命名其中之一, "
                    "框架不静默覆盖"
                )
            bucket[ep.name] = ExtensionEntry(
                kind=kind, name=ep.name, group=group, source=source, entry_point=ep,
            )
    return registry
