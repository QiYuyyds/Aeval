"""
Integration contracts for the Aeval evaluation framework.

This module defines the Protocol interfaces that projects must implement
to integrate with Aeval. Only AgentRunner is required; all others have
default implementations.

Contracts:
    - AgentRunner (REQUIRED): 运行被评系统并**交付带来源的证据** (TrialEvidence)
    - TrialSession (handed to the runner): 随做随推 / 请求当场取证 / 时限与取消
    - TraceProvider (optional): Fetch trace spans from a trace backend
    - Grader (optional): Score a single trial's evidence
    - Storage (optional): Persist run/suite results
    - EnvironmentManager (optional): Setup/teardown 环境 + **评测侧取证探针**

一次调用仍对应一个 trial, 因此最简接入只是「跑完返回证据」: ``emit`` 与运行中
探针都可以完全不碰, 递进而非翻倍。
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agent_eval.core.types import (
    EVENT_CHANNEL,
    FIRST_PROMPT_CHANNEL,
    HUMAN_MESSAGE_CHANNEL,
    SIMULATED_USER_CHANNEL,
    EvalSuite,
    EvalTask,
    EvidenceKind,
    GradeAttempt,
    GraderConfig,
    GraderResult,
    Observation,
    ObservedBy,
    RunResult,
    TaskView,
    TrialEvidence,
    TrialResult,
)
from agent_eval.trace.observations import AbsentReason, NormalizedTrace

# 探针回调: (channel) → 该通道的一次独立取证读数
ProbeFn = Callable[[str], Awaitable[Iterable[Observation]]]

# ─── Errors ───────────────────────────────────────────────────────────────────


class TransientError(Exception):
    """
    瞬态错误 — AgentRunner 实现方显式抛出。

    框架仅对 TransientError 做指数退避重试 (默认最多 2 次)。
    httpx 超时/网络抖动等属于实现层知识, 由实现方判断并包装为本异常;
    其他异常 (含 asyncio.TimeoutError) 不重试, 直接记失败。
    """


class AgentRunError(Exception):
    """
    被评测系统报错, 且接入方**显式声明了该错误的归类**。

    框架不猜: 只抛通用异常的 trial 会被记为 ``unclassified_agent_error`` 并要求
    人工判定, 既不折算为通过也不折算为不通过。接入方按下面的两个子类声明。
    """

    classification = "unclassified"


class AgentDefect(AgentRunError):
    """属 agent 自身缺陷 (崩溃/死循环/无法收敛) → trial 计为未通过。"""

    classification = "agent_defect"


class ExternalDependencyError(AgentRunError):
    """属外部依赖不可达 (上游服务/凭据/网络) → trial 记为结论不可信, 不占分母。"""

    classification = "external_dependency"


# ─── User simulator (⑤: extension-contracts) ─────────────────────────────────


@dataclass
class SimulatorContext:
    """模拟器的输入视图 —— 只含任务描述、目标与本 trial 已有的对话历史。

    判据、期望输出与答案键不在这个类型里 (沿用 ③ 的信息屏障语义:
    「读不到」而不是「别去读」), 答案因此无法经由对话被洗进被评系统。
    """

    task_id: str = ""
    description: str = ""
    first_prompt: str = ""
    goal: str | None = None
    # 已有对话历史: [{"role", "content", "at", "channel"}], 按发生顺序
    history: list[dict[str, Any]] = field(default_factory=list)
    max_turns: int | None = None


@dataclass
class SimulatorReply:
    """模拟器的一句话术产出。

    ``end=True`` 表示模拟器认为目标已达成、建议收尾 —— 收尾判定权在框架:
    框架核对边界 (轮数上限/取消/预算) 后才会结束会话, 并把收尾原因落盘。
    ``unavailable_reason`` 非空 = 本次产不出话术 (缺配置等), 框架据此给
    带原因的不可用结论, 不让 trial 崩溃。
    """

    text: str = ""
    end: bool = False
    prompt: str | None = None  # 生成本句所用提示词 (经脱敏后随证据留痕)
    unavailable_reason: str | None = None


@runtime_checkable
class UserSimulator(Protocol):
    """用户侧模拟器协议 (⑤: extension-contracts)。

    预写话术与目标驱动两种模式共用这一个协议:
    - 预写话术: 按序弹出, 不调用任何模型 (确定性, CI 用);
    - 目标驱动: 依据 ``context.goal`` 与已有历史生成下一句, 通常经 LLMFn。

    实现方 MUST NOT 在协议内拿到期望输出或答案键 —— ``SimulatorContext``
    从类型上就不含它们。
    """

    name: str
    implementation_version: str

    async def next_message(self, context: SimulatorContext) -> SimulatorReply | None:
        """产出下一句用户话术。

        Returns:
            SimulatorReply: 下一句话术; ``end=True`` 表示建议收尾。
            None: 会话结束 (预写话术用尽 / 模拟器主动收尾后的后续调用)。
        """
        ...


# ─── Evaluation Context ───────────────────────────────────────────────────────


@dataclass
class EvalContext:
    """
    单次 trial 的评分上下文 —— 贯穿评分调用, 在 grader 之间共享。

    Attributes:
        run_id: 所属 run 的 ID
        task: 任务定义 (含判据与答案键; 只有评分侧拿得到)
        trial: 本次 trial 的结果 (评分过程中可能被填充)
        spans: trace span 列表 (原始词汇, 仅供自述证据与自定义 grader 使用)
        observations: spans 经翻译表归一化后的标准观测; 内置评分器只读这个
        evidence: 本次 trial 的带来源证据 (重评分时唯一的数据来源)
        shared_state: 同一 trial 内各 grader 间共享的可变状态
        grader_config: 当前评分调用的 grader 配置 (runner 每次调用前以
            replace() 注入; 供 name 与配置名不一致的分发型 grader 定位
            自己的配置, 如 MetricGrader)
    """

    run_id: str
    task: EvalTask
    trial: TrialResult
    spans: list[dict[str, Any]] = field(default_factory=list)
    observations: NormalizedTrace | None = None
    evidence: TrialEvidence | None = None
    shared_state: dict[str, Any] = field(default_factory=dict)
    grader_config: GraderConfig | None = None
    # ⑤: 本次运行的环境身份 ('none' = 未装配环境; None = 未记录) ——
    # 声明了环境检查判据却无环境时, 判据用它给出「缺环境装配」的原因
    environment_identity: str | None = None


# ─── Trial session ────────────────────────────────────────────────────────────


class TrialSession:
    """框架递给接入方的会话句柄: 推送读数、请求当场取证、观察时限与取消。

    三个能力全是可选的 —— 一个只用 ``run()`` 返回值的接入方与今天一样简单。
    框架会把 ``emit`` 进来的读数与探针读数并进返回的证据里, 因此适配层不需要
    自己维护完整账本; 反过来, 它也不能靠返回值覆盖探针得到的读数。

    ⑤ 新增 (仍全部可选, 单轮适配器一个都不碰即与此前逐位一致):
    - ``next_user_message()``: 多轮会话中取下一条用户消息 (框架经模拟器供给);
    - ``inject_event()`` / ``human_message()``: 运行中事件与人工介入,
      带时刻以 harness 来源进 transcript 证据。
    """

    def __init__(
        self,
        *,
        probe: ProbeFn | None = None,
        deadline_ms: float | None = None,
        cancelled: Callable[[], bool] | None = None,
        simulator: UserSimulator | None = None,
        conversation: Any | None = None,
        first_user_message: str | None = None,
    ):
        """
        Args:
            probe: 评测侧取证回调; None = 该环境未接入探针 (读数按缺失归类)
            deadline_ms: 本次 trial 的绝对截止时刻 (epoch 毫秒); None = 未设时限
            cancelled: 取消状态查询 (框架的取消旗标)
            simulator: 用户模拟器 (框架构造, 适配器经 ``next_user_message`` 消费);
                None = 单轮任务 (多轮钩子全部不可用, 行为与此前逐位一致)
            conversation: 会话声明 (ConversationSpec; 事件排程/目标/轮数上限)
            first_user_message: 首轮用户输入 (仅供用户侧输入序列落盘; 声明了
                会话才会记录)
        """
        self._probe = probe
        self.deadline_ms = deadline_ms
        self._cancelled = cancelled or (lambda: False)
        self.emitted: list[Observation] = []
        self.probe_readings: list[Observation] = []
        self._last_moment = 0.0
        # ── ⑤: 会话状态 ──
        self._simulator = simulator
        self._conversation = conversation
        self.turns_consumed = 0
        self.conversation_ended = False
        self.end_reason: str | None = None
        self.unavailable_reason: str | None = None
        # 注入产物 (transcript 通道, harness 来源): runner 并进证据
        self.injected: list[Observation] = []
        # 用户侧输入序列 (可重放脚本): runner 并进 evidence.user_inputs
        self.user_inputs: list[Observation] = []
        self._history: list[dict[str, Any]] = []
        self._pending_events: list[Any] = list(
            sorted(
                getattr(conversation, "events", None) or [],
                key=lambda e: e.after_turn,
            )
        ) if conversation is not None else []
        self.events_injected = 0
        self.human_messages = 0
        if simulator is not None and first_user_message:
            moment = self._next_moment()
            self.user_inputs.append(
                Observation(
                    kind=EvidenceKind.TRANSCRIPT,
                    observed_by=ObservedBy.HARNESS,
                    observed_at=moment,
                    channel=FIRST_PROMPT_CHANNEL,
                    value={"role": "user", "content": first_user_message, "turn": 0},
                )
            )
            self._history.append(
                {"role": "user", "content": first_user_message, "at": moment,
                 "channel": FIRST_PROMPT_CHANNEL}
            )

    def _next_moment(self) -> float:
        """一次取证 = 一个时刻。

        同一次调用跨多个通道 (文件清单 + DB dump), 它们必须共享同一时刻, 否则「结束态」
        就无法界定; 又因为本机时钟粒度可能粗到毫秒, 这里强制递增, 保证两次探针调用不会
        被压成同一个时刻 —— 「任一时刻」类判据正依赖这个区分。
        """
        moment = time.time() * 1000
        if moment <= self._last_moment:
            moment = self._last_moment + 0.001
        self._last_moment = moment
        return moment

    # ── 接入方推送 ──

    def emit(
        self,
        observation_or_kind: Observation | EvidenceKind | str,
        value: Any = None,
        *,
        observed_by: ObservedBy = ObservedBy.RUNNER,
        channel: str = "",
        detail: str = "",
    ) -> Observation:
        """随做随推一条观测 —— 不必等整个 trial 跑完。"""
        if isinstance(observation_or_kind, Observation):
            observation = observation_or_kind
        else:
            observation = Observation(
                kind=EvidenceKind(str(getattr(observation_or_kind, "value", observation_or_kind))),
                observed_by=observed_by,
                value=value,
                channel=channel,
                detail=detail,
            )
        self.emitted.append(observation)
        return observation

    # ── 当场取证 ──

    async def harness_probe(self, channel: str = "") -> list[Observation]:
        """请求评测侧**当场**独立取证; 读数带 ``observed_by=harness`` 与采集时刻。

        接入方不支持探针时返回一条明确的「没取到」读数, 而不是空列表 —— 空读数
        会被下游读成「环境里确实没有」, 那是一个结论而不是一句抱歉。
        """
        moment = self._next_moment()
        if self._probe is None:
            readings = [
                Observation.absent(
                    EvidenceKind.STATE,
                    AbsentReason.PROVIDER_UNAVAILABLE.value,
                    channel=channel or "probe",
                    detail="该环境未接入取证探针",
                )
            ]
        else:
            readings = list(await self._probe(channel))
            if not readings:
                # 探针实现方忘了报缺失时由这里兜住: 空集 ≠ 没取到
                readings = [
                    Observation.absent(
                        EvidenceKind.STATE,
                        AbsentReason.PROVIDER_UNAVAILABLE.value,
                        channel=channel or "probe",
                        detail="探针未产出任何读数",
                    )
                ]
        # 一次取证 = 一个时刻: 该次调用跨的所有通道共用它, 结束态才界定得清楚
        stamped = [obs.model_copy(update={"observed_at": moment}) for obs in readings]
        self.probe_readings.extend(stamped)
        return stamped

    # ── 时限与取消 ──

    @property
    def cancelled(self) -> bool:
        """框架是否已请求取消本次 trial (接入方应尽快收尾)。"""
        return bool(self._cancelled())

    @property
    def remaining_ms(self) -> float | None:
        """距时限还有多少毫秒; None = 未设时限。"""
        if self.deadline_ms is None:
            return None
        return self.deadline_ms - time.time() * 1000

    @property
    def over_deadline(self) -> bool:
        remaining = self.remaining_ms
        return remaining is not None and remaining <= 0

    # ── ⑤: 多轮会话钩子 (挂在框架产出的会话对象上; 适配器可选消费) ──

    async def next_user_message(self) -> str | None:
        """多轮会话: 取下一条用户消息; None = 会话结束。

        轮次由框架经模拟器供给 (预写话术零模型调用; 目标驱动经模拟器协议),
        适配器向会话要下一轮而不是被反复调用 —— ``AgentRunner`` 契约不变。
        每条产出以 harness 来源带时刻进 transcript 证据, 与普通消息可区分。
        """
        if self._simulator is None:
            return None
        self._inject_pending_events()
        if self.conversation_ended:
            return None
        context = SimulatorContext(
            task_id=str(getattr(self._conversation, "id", "") or ""),
            description=str(getattr(self._conversation, "description", "") or ""),
            first_prompt=self._first_prompt(),
            goal=getattr(self._conversation, "goal", None),
            history=list(self._history),
            max_turns=getattr(self._conversation, "max_turns", None),
        )
        reply = await self._simulator.next_message(context)
        if reply is None:
            self.conversation_ended = True
            self.end_reason = self.end_reason or "simulator_exhausted"
            return None
        if reply.unavailable_reason:
            # 带原因的不可用: 不崩溃, 由 runner 据此给 invalid 结论
            self.conversation_ended = True
            self.unavailable_reason = reply.unavailable_reason
            self.end_reason = f"simulator_unavailable: {reply.unavailable_reason}"
            return None
        if reply.end and not reply.text:
            # 纯收尾信号 (无附带话术): 目标达成, 会话就此结束, 不多消费一轮
            self.conversation_ended = True
            self.end_reason = "goal_achieved"
            return None
        if (
            context.max_turns is not None
            and self.turns_consumed >= context.max_turns
        ):
            self.conversation_ended = True
            self.end_reason = "max_turns_reached"
            return None
        self.turns_consumed += 1
        moment = self._next_moment()
        value: dict[str, Any] = {
            "role": "user",
            "content": reply.text,
            "turn": self.turns_consumed,
            "simulated": True,
        }
        if reply.prompt is not None:
            value["simulator_prompt"] = reply.prompt
        observation = Observation(
            kind=EvidenceKind.TRANSCRIPT,
            observed_by=ObservedBy.HARNESS,
            observed_at=moment,
            channel=SIMULATED_USER_CHANNEL,
            value=value,
        )
        self.injected.append(observation)
        self.user_inputs.append(observation)
        self._history.append(
            {"role": "user", "content": reply.text, "at": moment,
             "channel": SIMULATED_USER_CHANNEL}
        )
        if reply.end:
            # 收尾判定权在框架: 模拟器的 end 只是建议, 这里核对后落盘原因
            self.conversation_ended = True
            self.end_reason = "goal_achieved"
        return reply.text

    def inject_event(
        self,
        message: str,
        *,
        data: dict[str, Any] | None = None,
        channel: str = EVENT_CHANNEL,
    ) -> Observation:
        """运行中注入一个环境事件 (如文件被外部改动)。

        带采集时刻、以 harness 来源进 transcript 证据, 与普通消息可区分;
        「事件之后」类判定时刻正是从这里取界定点。
        """
        moment = self._next_moment()
        value: dict[str, Any] = {"event": message, "injected": True}
        if data:
            value["data"] = data
        observation = Observation(
            kind=EvidenceKind.TRANSCRIPT,
            observed_by=ObservedBy.HARNESS,
            observed_at=moment,
            channel=channel or EVENT_CHANNEL,
            value=value,
        )
        self.injected.append(observation)
        self.user_inputs.append(observation)
        self.events_injected += 1
        self._history.append(
            {"role": "event", "content": message, "at": moment, "channel": channel}
        )
        return observation

    def human_message(self, text: str) -> Observation:
        """运行中注入一条人工介入消息 (对齐 Inspect 的 Intervention 语义)。

        与 ``inject_event`` 同一落盘语义: harness 来源 + 时刻, 结论可标注
        该 trial 含人工介入。
        """
        moment = self._next_moment()
        observation = Observation(
            kind=EvidenceKind.TRANSCRIPT,
            observed_by=ObservedBy.HARNESS,
            observed_at=moment,
            channel=HUMAN_MESSAGE_CHANNEL,
            value={"role": "user", "content": text, "human": True},
        )
        self.injected.append(observation)
        self.user_inputs.append(observation)
        self.human_messages += 1
        self._history.append(
            {"role": "user", "content": text, "at": moment,
             "channel": HUMAN_MESSAGE_CHANNEL}
        )
        return observation

    def diagnostics(self) -> dict[str, Any] | None:
        """轮级过程量 (只进报告诊断块, 不进任何分母); 单轮任务返回 None。"""
        if self._simulator is None:
            return None
        return {
            "turns_declared": len(getattr(self._conversation, "turns", None) or []) or None,
            "goal": getattr(self._conversation, "goal", None),
            "max_turns": getattr(self._conversation, "max_turns", None),
            "turns_consumed": self.turns_consumed,
            "events_injected": self.events_injected,
            "human_messages": self.human_messages,
            "end_reason": self.end_reason,
            "unavailable_reason": self.unavailable_reason,
        }

    def _first_prompt(self) -> str:
        for item in self._history:
            if item.get("channel") == FIRST_PROMPT_CHANNEL:
                return str(item.get("content", ""))
        return ""

    def _inject_pending_events(self) -> None:
        """把排程点已到的事件注入 (after_turn <= 已消费轮数), 保持声明顺序。"""
        while self._pending_events and self._pending_events[0].after_turn <= self.turns_consumed:
            event = self._pending_events.pop(0)
            self.inject_event(event.message, data=event.data or None)


# ─── Required Contract ────────────────────────────────────────────────────────


@runtime_checkable
class AgentRunner(Protocol):
    """
    项目必须实现: 运行被评系统并交付**带来源的证据**。

    这是唯一的必选接入点。框架通过这个接口与 Agent 系统交互, 不需要知道 Agent
    的内部实现细节; 而它拿到的不再是三元组裸数据, 是每条读数都标了「谁、在什么
    时候、通过哪条通道观测到的」的证据对象。

    Example (最简接入 —— 只用返回值):
        class MyAgentRunner:
            async def run(self, view: TaskView, session: TrialSession) -> TrialEvidence:
                trace_id, transcript, outcome = await my_agent.run(view.prompt, view.env)
                return TrialEvidence.runner_reported(
                    trace_id=trace_id, transcript=transcript, state=outcome,
                )

    Example (递进接入 —— 随做随推 + 运行中取证):
        async def run(self, view, session):
            evidence = TrialEvidence()
            async for message in my_agent.stream(view.prompt, view.env):
                session.emit(EvidenceKind.TRANSCRIPT, message)
                if session.cancelled:
                    break
            await session.harness_probe("workspace_listing")   # 评测侧读, 不是自报
            return evidence

    Raises:
        TransientError: 瞬态故障 (框架按指数退避重试)
        AgentDefect / ExternalDependencyError: 已声明归类的失败
        asyncio.TimeoutError: 超过 per_trial_timeout
    """

    async def run(
        self,
        view: TaskView,
        session: TrialSession,
    ) -> TrialEvidence:
        """
        执行一个评测任务并交付证据。

        Args:
            view: 任务视图 —— 只含执行所需的输入与环境参数, 判据与答案键不在
                这个类型里, 因此不是「别去读」而是「读不到」
            session: 会话句柄 (emit / harness_probe / deadline / cancelled),
                可以完全不碰

        Returns:
            TrialEvidence: 本次 trial 的全部观测及其来源
        """
        ...


# ─── Optional Contracts ───────────────────────────────────────────────────────


@runtime_checkable
class TraceProvider(Protocol):
    """
    Trace 数据获取。

    默认提供 Phoenix 实现, 可自定义以支持其他后端 (Jaeger, Tempo, ...)。
    """

    async def get_spans(
        self,
        trace_id: str,
    ) -> list[dict[str, Any]]:
        """
        获取一个 trace 的所有 span。

        Args:
            trace_id: OTel trace ID

        Returns:
            span 列表, 每个 span 是 dict, 包含:
                - name: str          # span 名称
                - attributes: dict   # span 属性
                - start_time: str    # 开始时间
                - end_time: str      # 结束时间
                - status: dict       # 状态
        """
        ...

    async def get_trace_ids(
        self,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[str]:
        """
        查询 trace ID 列表 (用于历史记录浏览)。

        Args:
            filters: 过滤条件 (时间范围/状态/标签等)
            limit: 返回数量限制

        Returns:
            trace ID 列表
        """
        ...


@runtime_checkable
class Grader(Protocol):
    """
    评分器接口。

    内置 9 个通用实现, 项目可自定义以适配业务逻辑。

    评分器**必须**能被问出它允许消费哪一级来源: 未声明的级别读不到, 而不是照单
    全收。不声明的实现按默认两级 (harness + runner) 处理 —— 也就是说被评方自报
    内容默认不在其取信范围内。
    """

    name: str  # 评分器唯一名称
    # 可消费的来源级别 (缺省由框架按 ObservedBy.default_declaration() 补全)
    evidence_levels: tuple[ObservedBy, ...]
    # 判分实现版本: 随每次判定落盘, 使「翻判是因为哪一版变了」可回答
    implementation_version: str

    async def grade(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None = None,
    ) -> GraderResult:
        """
        对一次 trial 进行评分。

        Args:
            trial: trial 结果 (含证据与 transcript/outcome 视图)
            spans: trace span 列表 (用于分析过程)
            task: 任务定义 (含 grader config)
            context: 评分上下文 (run_id/task/trial/spans/observations/evidence/
                shared_state); 内置评分器只读它给的标准观测与声明过的证据级别

        Returns:
            GraderResult: 评分结果 (必须自陈它实际依据了哪几级来源)
        """
        ...


@runtime_checkable
class Storage(Protocol):
    """
    结果持久化。

    默认 SQLite, 可选 PostgreSQL / Memory。
    """

    # ── Run 操作 ──

    async def save_run(self, run: RunResult) -> None:
        """保存运行结果"""
        ...

    async def get_run(self, run_id: str) -> RunResult | None:
        """获取运行结果"""
        ...

    async def list_runs(
        self, suite_name: str | None = None, limit: int = 50
    ) -> list[RunResult]:
        """列出运行历史"""
        ...

    async def delete_run(self, run_id: str) -> bool:
        """删除运行结果"""
        ...

    # ── Suite 操作 ──

    async def save_suite(self, suite: EvalSuite) -> None:
        """保存评测套件"""
        ...

    async def get_suite(self, name: str) -> EvalSuite | None:
        """获取评测套件"""
        ...

    async def list_suites(self) -> list[EvalSuite]:
        """列出所有评测套件"""
        ...

    async def delete_suite(self, name: str) -> bool:
        """删除评测套件"""
        ...

    # ── 人工评分请求 (可选, HumanGrader pending 语义使用) ──

    async def save_human_score_request(self, request: dict[str, Any]) -> None:
        """保存人工评分请求"""
        ...

    async def list_human_score_requests(
        self, run_id: str | None = None
    ) -> list[dict[str, Any]]:
        """列出人工评分请求"""
        ...

    # ── 证据归档 (可选; 未实现则该 run 可读、可评, 但不可重评分) ──

    async def save_trial_evidence(
        self, run_id: str, task_id: str, trial_index: int, evidence: TrialEvidence
    ) -> None:
        """按 trial 独立归档证据 —— 重评分唯一的数据来源。"""
        ...

    async def get_trial_evidence(
        self, run_id: str, task_id: str, trial_index: int
    ) -> TrialEvidence | None:
        """完整取回一次 trial 的证据 (含每条读数的来源级别与缺失原因)。"""
        ...

    # ── 判定历史 (可选; 每次判定的口径都要留得下来) ──

    async def save_grade_attempt(self, attempt: GradeAttempt) -> None:
        """追加一条判定结论并把该 trial 的 current 指针移过来; **永不覆盖**既有条目。"""
        ...

    async def list_grade_attempts(
        self, run_id: str, task_id: str | None = None, trial_index: int | None = None
    ) -> list[GradeAttempt]:
        """按时间升序列出判定条目 (原结论与其后重评结论并列可查)。"""
        ...


@runtime_checkable
class EnvironmentManager(Protocol):
    """
    环境管理。可选; 不传即没有环境管理器 (框架不假造一个「什么都没做却声称干净」
    的默认实现)。

    用于:
    - workspace 隔离 (每次 trial 从干净环境开始)
    - 数据准备 (注入测试数据)
    - 资源清理 (删除临时文件/数据库)
    - 泄漏检测 (trial 前后环境一致性校验)
    - **评测侧取证** (在被评方写不动的通道上读数)
    """

    async def setup(self, task: EvalTask) -> None:
        """trial 开始前: 准备环境"""
        ...

    async def teardown(self, task: EvalTask) -> None:
        """trial 结束后: 清理环境"""
        ...

    async def probe(self, channel: str = "") -> list[Observation]:
        """
        评测侧独立取证 (框架调用, 接入方实现具体动作)。

        由框架在 teardown **之前**触发, 也可被适配层在运行中按需触发; 每条读数
        自带 ``observed_by="harness"`` 与采集时刻, 被评方无法通过返回内容影响它。

        Args:
            channel: 取证通道名 (如 workspace_listing / db_dump); "" = 全部通道

        Returns:
            读数列表。不支持探针时 MUST 返回一条带原因的「没取到」读数,
            MUST NOT 返回空列表 —— 空读数会被当成「环境里确实没有」。
        """
        ...

    async def snapshot(self) -> dict[str, Any]:
        """
        拍摄环境基线快照 (JSON 可序列化)。

        Returns:
            环境状态快照, 传给 verify_clean / restore
        """
        ...

    async def verify_clean(
        self,
        baseline: dict[str, Any],
        harness_readings: list[Observation] | None = None,
    ) -> dict[str, Any]:
        """
        校验环境是否与基线一致。

        Args:
            baseline: snapshot() 返回的基线快照
            harness_readings: 本次 trial 结束前的评测侧取证读数 —— 泄漏判定该
                依据独立观测, 而不是被评方自报的状态 (实现方可忽略而保持旧行为)

        Returns:
            {"clean": bool, "differences": [...]}
        """
        ...

    async def restore(self, baseline: dict[str, Any]) -> None:
        """将环境恢复到基线状态"""
        ...
