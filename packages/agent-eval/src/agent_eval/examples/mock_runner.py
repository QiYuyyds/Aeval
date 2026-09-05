"""
Mock AgentRunner / TraceProvider for testing and demonstration.

Simulates an agent by returning predefined results, with per-task scripted
behaviors for exercising the framework's failure paths.

It doubles as the **reference implementation of the integration contract** and
deliberately shows both paths a host can take:

- 默认: ``run()`` 跑完一次性返回 ``TrialEvidence`` (``runner_reported``) ——
  最简接入只需要这一条, 复杂度不比分居两端时更高。
- 脚本行为 ``"push"``: 随做随推 ``session.emit(...)`` + 运行中请求
  ``await session.harness_probe(...)`` —— 需要中途状态时才走这条。

The mock trace provider writes span attributes **using whatever attribute
names the supplied ``AttributeMapping`` declares**, so one logical trace can be
emitted in any vocabulary. That is what breaks the self-fulfilling loop where a
mock and the code under test agree on one private word: a test can hand both
sides a host vocabulary and the framework must still read it.

Usage:
    from agent_eval.examples.mock_runner import MockAgentRunner, MockTraceProvider

    # Random behavior (demo)
    runner = EvalRunner(agent_runner=MockAgentRunner())

    # Scripted behavior (tests): each task consumes its behavior list in
    # order across calls; the last entry repeats once exhausted.
    agent = MockAgentRunner(
        latency_range=(0.0, 0.01),
        script={
            "task_ok": ["success"],
            "task_flaky": ["transient", "transient", "success"],
            "task_dead": ["failure"],
            "task_slow": ["timeout"],
            "task_defect": ["defect"],
            "task_infra": ["external"],
            "task_crash": ["error"],
            "task_stream": ["push"],
        },
    )
"""

from __future__ import annotations

import asyncio
import random
import uuid
from typing import Any

from agent_eval.core.contract import (
    AgentDefect,
    ExternalDependencyError,
    TransientError,
    TrialSession,
)
from agent_eval.core.types import (
    EvidenceKind,
    ObservedBy,
    TaskView,
    TrialEvidence,
)
from agent_eval.trace.mapping import (
    FIELD_AGENT_NAME,
    FIELD_AGENT_VERSION,
    FIELD_CACHE_READ_TOKENS,
    FIELD_INPUT_TOKENS,
    FIELD_MODEL,
    FIELD_OUTPUT_TOKENS,
    FIELD_REASONING_TOKENS,
    FIELD_SESSION_ID,
    FIELD_TOOL_ARGUMENTS,
    FIELD_TOOL_NAME,
    FIELD_TOOL_RESULT,
    FIELD_TOOL_SUCCESS,
    AttributeMapping,
    default_mapping,
)

# 一次逻辑 trace 的全部内容 (与词汇无关): 归一化后两套表达必须一致
MOCK_TURN = {
    "input_tokens": 100,
    "output_tokens": 50,
    "reasoning_tokens": 20,
    "cache_tokens": 30,
}

# 脚本行为里表示「走 emit + 运行中探针」那一条路径的取值
_SESSION_BEHAVIORS = frozenset({"push"})


class MockAgentRunner:
    """
    模拟 AgentRunner —— 新接入契约的参考实现。

    用于测试和演示框架功能，无需真实 Agent 系统。
    支持脚本化场景: success / failure / transient / timeout / defect /
    external / error / push (会话句柄路径) / slow_steps / heavy_tokens。
    """

    def __init__(
        self,
        success_rate: float = 0.7,
        latency_range: tuple[float, float] = (0.1, 0.5),
        script: dict[str, list[str]] | None = None,
        timeout_duration: float = 10.0,
        state_channel: str = "workspace_listing",
    ):
        """
        Args:
            success_rate: 随机模式下的模拟成功率 (0.0-1.0)
            latency_range: 模拟延迟范围 (秒)
            script: task_id → 行为序列, 逐次调用消耗, 耗尽后重复最后一项:
                "success" | "failure" | "transient" | "timeout" | "defect"
                (agent 自身缺陷) | "external" (外部依赖不可达) | "error" (未声明类别的崩溃)
                | "push" (用会话句柄: emit + 运行中探针)
            timeout_duration: "timeout" 行为的挂起时长 (秒),
                              配合 EvalRunner(per_trial_timeout=...) 触发超时
            state_channel: "push" 路径请求的取证通道名
        """
        self.success_rate = success_rate
        self.latency_range = latency_range
        self.script = script or {}
        self.timeout_duration = timeout_duration
        self.state_channel = state_channel
        self.call_counts: dict[str, int] = {}
        # 会话句柄路径的留痕, 供测试断言「探针确实被调过」
        self.sessions_seen: list[TrialSession] = []
        self.probe_calls: list[str] = []

    def _next_behavior(self, task_id: str, call_index: int) -> str | None:
        """取该 task 指定调用的脚本行为 (无脚本返回 None = 随机模式)"""
        behaviors = self.script.get(task_id)
        if not behaviors:
            return None
        return behaviors[min(call_index, len(behaviors) - 1)]

    async def run(
        self,
        view: TaskView,
        session: TrialSession,
    ) -> TrialEvidence:
        """
        模拟 Agent 执行并交付证据。

        Returns:
            TrialEvidence: 每条读数都带来源 (默认全部为适配层交付的 ``runner`` 级)
        """
        index = self.call_counts.get(view.id, 0)
        self.call_counts[view.id] = index + 1
        behavior = self._next_behavior(view.id, index)

        # 超时场景: 长时间挂起, 由框架的 per_trial_timeout 打断
        if behavior == "timeout":
            await asyncio.sleep(self.timeout_duration)

        # 模拟延迟
        latency = random.uniform(*self.latency_range)
        await asyncio.sleep(latency)

        # 瞬态错误场景: 框架按指数退避重试
        if behavior == "transient":
            raise TransientError(
                f"Mock transient failure for {view.id} (call {index + 1})"
            )
        # 已声明归类的报错场景 (spec: agent 报错的归类不由框架猜测)
        if behavior == "defect":
            raise AgentDefect(f"Mock agent defect for {view.id}")
        if behavior == "external":
            raise ExternalDependencyError(f"Mock upstream outage for {view.id}")
        # 未声明类别的报错: 框架不得猜它属于 agent 还是基建
        if behavior == "error":
            raise RuntimeError(f"Mock unclassified crash for {view.id}")

        # 生成 trace_id (编码 task id, 便于 MockTraceProvider 关联 spans)
        trace_id = f"trace_{view.id}_{uuid.uuid4().hex[:8]}"

        # 构建 transcript
        transcript = [
            {
                "role": "user",
                "content": view.prompt,
            },
            {
                "role": "assistant",
                "content": f"Mock response for task: {view.id}",
            },
        ]

        # 构建状态读数 (模拟成功/失败)
        if behavior == "success":
            success = True
        elif behavior == "failure":
            success = False
        else:
            success = random.random() < self.success_rate

        state: dict[str, Any] = {
            "success": success,
            "files": {
                "output.py": f"# Mock output for {view.id}\ndef hello(): pass\n",
            },
            "artifacts": [
                {
                    "type": "code_file",
                    "id": f"art_{uuid.uuid4().hex[:8]}",
                    "content": f"# Generated code for {view.id}",
                }
            ] if success else [],
        }

        if behavior in _SESSION_BEHAVIORS:
            return await self._run_with_session(
                session, trace_id=trace_id, transcript=transcript, state=state
            )

        # 路径 A: 只用返回值交付证据 —— 会话句柄一次都没碰
        return TrialEvidence.runner_reported(
            trace_id=trace_id,
            transcript=transcript,
            state=state,
            artifacts=list(state.get("artifacts") or []),
        )

    async def _run_with_session(
        self,
        session: TrialSession,
        *,
        trace_id: str,
        transcript: list[dict[str, Any]],
        state: dict[str, Any],
    ) -> TrialEvidence:
        """路径 B: 随做随推 + 运行中请求评测侧取证。"""
        self.sessions_seen.append(session)
        evidence = TrialEvidence(trace_id=trace_id)
        for message in transcript:
            evidence.transcript.append(
                session.emit(EvidenceKind.TRANSCRIPT, message, observed_by=ObservedBy.RUNNER)
            )
        # agent 自述的「我做了什么」: 这是最低一级, 不能单独支撑通过
        evidence.subject_state.append(
            session.emit(
                EvidenceKind.STATE,
                {"claimed_files": sorted(state.get("files", {}))},
                observed_by=ObservedBy.SUBJECT,
                channel="agent_self_report",
            )
        )
        # 运行中取证: 框架在自己写不动的通道上读一次 (中途状态)
        mid = await session.harness_probe(self.state_channel)
        self.probe_calls.append(self.state_channel)
        evidence.harness_state.extend(mid)
        # 交付终态 (适配层自报) + 产物
        evidence.subject_state.append(
            session.emit(EvidenceKind.STATE, state, observed_by=ObservedBy.RUNNER)
        )
        for artifact in state.get("artifacts") or []:
            evidence.artifacts.append(
                session.emit(EvidenceKind.ARTIFACT, artifact, observed_by=ObservedBy.RUNNER)
            )
        return evidence


class MockTraceProvider:
    """模拟 TraceProvider。

    按给定翻译表写 span 属性名: 同一逻辑 trace 可用标准词汇或任意宿主词汇表达,
    归一化结果必须一致。可选按 task id 关联 span 数据: trace_id 形如
    "trace_{task_id}_{suffix}" 时返回 spans_by_task[task_id] (若已配置),
    否则返回默认 spans。
    """

    def __init__(
        self,
        spans_by_task: dict[str, list[dict[str, Any]]] | None = None,
        default_spans: list[dict[str, Any]] | None = None,
        mapping: AttributeMapping | None = None,
        raises: bool = False,
    ):
        """
        Args:
            spans_by_task: task_id → 预先构造的 spans (原样返回, 不经词汇生成)
            default_spans: 覆盖默认 spans (原样返回)
            mapping: 写 span 时使用的属性词汇表; None = 内置 OTel GenAI 条目
            raises: 模拟取证后端不可用 (抛异常)
        """
        self.mapping = mapping or default_mapping()
        self.raises = raises
        self.spans_by_task = spans_by_task or {}
        self.default_spans = default_spans or self._build_default_spans()

    def attribute_name(self, field: str) -> str | None:
        """该字段在当前词汇下的属性名 (表里没有就没有, 不臆造)。"""
        candidates = self.mapping.candidates(field)
        return candidates[0] if candidates else None

    def _attrs(self, entries: dict[str, Any]) -> dict[str, Any]:
        """把 (标准字段 → 值) 翻成当前词汇下的属性字典 (无属性名的字段直接跳过)。"""
        attributes: dict[str, Any] = {}
        for field, value in entries.items():
            name = self.attribute_name(field)
            if name is not None:
                attributes[name] = value
        return attributes

    def _build_default_spans(self) -> list[dict[str, Any]]:
        """一个 turn + 一次工具调用的逻辑 trace, 按当前词汇表达。

        入参与结果照样写出: 规范把它们列为 Opt-In, 采不采集由框架侧开关决定,
        被评测方埋了就写。
        """
        turn_attrs = self._attrs(
            {
                FIELD_INPUT_TOKENS: MOCK_TURN["input_tokens"],
                FIELD_OUTPUT_TOKENS: MOCK_TURN["output_tokens"],
                FIELD_REASONING_TOKENS: MOCK_TURN["reasoning_tokens"],
                FIELD_CACHE_READ_TOKENS: MOCK_TURN["cache_tokens"],
                FIELD_MODEL: "mock-model-1",
                FIELD_SESSION_ID: "sess_mock_1",
                FIELD_AGENT_NAME: "mock-agent",
                FIELD_AGENT_VERSION: "1.0.0",
            }
        )
        tool_attrs = self._attrs(
            {
                FIELD_TOOL_NAME: "fs_write",
                FIELD_TOOL_SUCCESS: True,
                FIELD_TOOL_ARGUMENTS: {"path": "/tmp/out.py", "mode": "w"},
                FIELD_TOOL_RESULT: {"bytes_written": 33},
            }
        )
        return [
            {
                # span 名称只是给人看的: 框架按属性判定角色, 不看名称
                "name": "agent.turn",
                "attributes": turn_attrs,
                "start_time": "2026-08-29T10:00:00Z",
                "end_time": "2026-08-29T10:00:01Z",
                "status": {"status_code": "OK"},
            },
            {
                "name": "tool.call",
                "attributes": tool_attrs,
                "start_time": "2026-08-29T10:00:01Z",
                "end_time": "2026-08-29T10:00:02Z",
                "status": {"status_code": "OK"},
            },
        ]

    async def get_spans(self, trace_id: str) -> list[dict[str, Any]]:
        """返回模拟 span 数据 (优先按 task id 匹配)"""
        if self.raises:
            raise RuntimeError("Mock trace backend unreachable")
        if trace_id.startswith("trace_"):
            remainder = trace_id[len("trace_"):]
            for task_id, spans in self.spans_by_task.items():
                if remainder == task_id or remainder.startswith(f"{task_id}_"):
                    return spans
        return self.default_spans

    async def get_trace_ids(
        self,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[str]:
        return [f"trace_mock_{i}" for i in range(min(limit, 5))]
