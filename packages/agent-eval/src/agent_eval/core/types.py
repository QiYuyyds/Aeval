"""
Core data types for the Aeval evaluation framework.

This module defines all the data models used throughout the framework:
- Task definition layer: EvalTask, EvalSuite, GraderConfig, TaskView
- Evidence layer: ObservedBy, Observation, TrialEvidence, GradeAttempt
- Run result layer: TrialResult, GraderResult, TaskSummary, RunSummary, RunResult
"""

from __future__ import annotations

import copy
import time
import uuid
from collections.abc import Iterable
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agent_eval.trace.observations import AbsentReason

# ─── Task Definition Layer ───────────────────────────────────────────────────


class GraderType(str, Enum):
    """评分器类型枚举"""

    CODE = "code"  # 确定性评分 (字符串匹配/正则/静态分析)
    MODEL = "model"  # LLM Judge
    STATE = "state"  # 环境状态检查
    TOOL_CALLS = "tool_calls"  # 工具调用验证
    TRANSCRIPT = "transcript"  # 转录记录分析
    ARTIFACT = "artifact"  # 产物检查
    METRIC = "metric"  # LLM 输出质量指标 (AnswerRelevancy/Faithfulness/...)
    CUSTOM = "custom"  # 自定义


class ScoreStrategy(str, Enum):
    """评分聚合策略"""

    ALL_PASS = "all_pass"  # 所有 grader 必须通过
    WEIGHTED = "weighted"  # 加权平均
    HYBRID = "hybrid"  # required 必须通过 + 非 required 加权


class TrialVerdict(str, Enum):
    """一次 trial / 一个 grader 结论的分类。

    invalid 表示评测侧未能产生有效判定 —— 既不计入通过率的分子, 也不计入分母。
    pending 表示等待人工评分回传, 不得被改写为 invalid。
    """

    VALID = "valid"
    INVALID = "invalid"
    PENDING = "pending"


class InvalidReason(str, Enum):
    """评测侧失败的封闭枚举 (verdict=invalid 时必填)。"""

    GRADER_ERROR = "grader_error"  # 评分器抛出异常
    GRADER_TIMEOUT = "grader_timeout"  # 评分器超时
    UNKNOWN_GRADER = "unknown_grader"  # 配置的 grader 名未注册
    JUDGE_UNAVAILABLE = "judge_unavailable"  # LLM 判定调用失败
    VERDICT_UNPARSEABLE = "verdict_unparseable"  # LLM 判定输出无法解析
    NO_CRITERIA_CONFIGURED = "no_criteria_configured"  # 配置了 grader 但无判据
    TRIAL_TIMEOUT = "trial_timeout"  # trial 超过单次执行时限
    UNCLASSIFIED_AGENT_ERROR = "unclassified_agent_error"  # 接入方未声明类别, 需人工判定
    EXTERNAL_DEPENDENCY_UNAVAILABLE = "external_dependency_unavailable"  # 声明的外部依赖故障
    EVIDENCE_UNAVAILABLE = "evidence_unavailable"  # 取证通道不可用, 过程证据缺失
    TRIAL_CANCELLED = "trial_cancelled"  # 取消生效后未运行, 评测没跑完
    EVIDENCE_LEVEL_MISMATCH = "evidence_level_mismatch"  # 结论依据了未声明来源级别的证据
    SUBJECT_ONLY_EVIDENCE = "subject_only_evidence"  # 通过只由被评方自报证据支撑


class TerminationReason(str, Enum):
    """一次 trial 因何结束 —— 由框架判定, 不信被评测方的自报状态。

    预算触顶属「任务约束未达成」(计未通过, 单列计数); ``timeout`` 属「评测没跑完」
    (走 invalid 通道, 不占分母); 两者与「评分判定的不通过」三者必须可辨。
    """

    AGENT_COMPLETED = "agent_completed"  # agent 自行完成且未触任何上限
    STEP_BUDGET_EXCEEDED = "step_budget_exceeded"
    TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded"
    COST_BUDGET_EXCEEDED = "cost_budget_exceeded"
    TIMEOUT = "timeout"  # 框架强制时限
    AGENT_ERROR = "agent_error"  # 被评测系统报错 (类别由接入方声明)
    CANCELLED = "cancelled"  # 取消生效前尚未启动/未跑完的 trial


# 统计口径版本: 估计量/分母/有效性规则变化时递增。历史 run 不回算,
# 跨版本比较由 CLI / API / Dashboard 标注为不可比。
STATISTICS_VERSION = "2"

# 本口径内确定的数值默认值 (经 /v1/meta 公布, 避免同套件在不同配置下产出不可比数字)
DEFAULT_CONFIDENCE_LEVEL = 0.95
DEFAULT_BOOTSTRAP_ROUNDS = 1000
MIN_VALID_TRIALS_FOR_SATURATION = 5
DEFAULT_INVALID_RATIO_LIMIT = 0.2


# ─── Evidence provenance (spec: extension-contracts) ─────────────────────────

# 三级来源的可信度排序: 数值越大越可信。分级不是装饰 —— 评分器只该消费自己
# 声明过的那几级, 而「最弱的一级」决定了这条结论在别人眼里值多少。
EVIDENCE_STRENGTH: dict[str, int] = {"harness": 2, "runner": 1, "subject": 0}


class ObservedBy(str, Enum):
    """一条读数是**谁**观测到的。

    ``harness`` 与 ``subject`` 之间必须留一级: 接入适配层是用户自己写的可信代码,
    把它的输出与被评 agent 的自吹同等对待会误伤, 也会让人干脆绕过分级。
    """

    HARNESS = "harness"  # 评测侧在环境停止前独立取证 (探针/dump/文件清单)
    RUNNER = "runner"  # 接入适配层交付 (transcript、trace_id、自报终态)
    SUBJECT = "subject"  # 被评 agent 自己写出的内容 —— 不得单独支撑「通过」

    @property
    def strength(self) -> int:
        return EVIDENCE_STRENGTH[self.value]

    @classmethod
    def default_declaration(cls) -> tuple[ObservedBy, ...]:
        """评分器未声明时的默认可消费级别 (**不含**被评方自报)。"""
        return (cls.HARNESS, cls.RUNNER)


def weakest_level(levels: Iterable[ObservedBy | str]) -> ObservedBy | None:
    """支撑一条结论的最弱证据级别 —— 结论要披露自己依据的最差那一级。"""
    resolved = [ObservedBy(x) for x in levels]
    if not resolved:
        return None
    return min(resolved, key=lambda level: level.strength)


class EvidenceKind(str, Enum):
    """一次 trial 的证据按通道分成的类别。"""

    TRANSCRIPT = "transcript"  # 对话/事件记录
    STEP = "step"  # 过程步骤 (工具调用一类)
    STATE = "state"  # 环境状态读数
    ARTIFACT = "artifact"  # 产物
    BUDGET = "budget"  # 资源用量读数


class JudgmentMoment(str, Enum):
    """环境状态判据所依据的**时刻**。

    取证可以在运行中发生, 「终态」就不再只有一个时刻: 只取最后一个快照会让
    「建完文件又删掉」判通过, 而发现这种情况正是这类检查存在的理由。
    """

    AT_END = "at_end"  # 结束时成立 (默认)
    NOT_AT_END = "not_at_end"  # 结束时不成立 (断言某物已被清掉)
    ANY_TIME = "any_time"  # 历史上任一时刻成立 (只有多次探针读数时才判得了)


class Observation(BaseModel):
    """一条带来源与时刻的读数 —— 协议里唯一的证据原子。

    ``absent_reason`` 非空表示这条读数**没取到** (缺失), 与 ``value`` 是空集合
    (确实没有) 严格区分: 沿用 ② 的 ``AbsentReason`` 词汇, 不另造第二套缺失表示。
    """

    kind: EvidenceKind
    observed_by: ObservedBy = ObservedBy.RUNNER
    observed_at: float = Field(
        default_factory=lambda: time.time() * 1000, description="采集时刻 (epoch 毫秒)"
    )
    channel: str = Field("", description="取证的通道/探针名, 便于回溯是哪条路径读到的")
    value: Any = Field(None, description="读数内容; 缺失时忽略")
    absent_reason: str | None = Field(
        None, description="没取到的原因 (AbsentReason 值); None = 这条读数取到了"
    )
    detail: str = Field("", description="补充说明 (探针原文、错误信息等)")

    @property
    def is_absent(self) -> bool:
        """这条读数是否为「没取到」。"""
        return self.absent_reason is not None

    @classmethod
    def absent(
        cls,
        kind: EvidenceKind,
        reason: str,
        *,
        observed_by: ObservedBy = ObservedBy.HARNESS,
        channel: str = "",
        detail: str = "",
    ) -> Observation:
        """构造一条「没取到」的读数 —— 空读数会把它冒充成「确实没有」。"""
        return cls(
            kind=kind,
            observed_by=observed_by,
            channel=channel,
            absent_reason=str(reason),
            detail=detail,
        )

    def render(self) -> Any:
        """落盘/呈现形式: 缺失显式成型, 不压成字符串以免丢原因。"""
        if self.is_absent:
            return {
                "missing": True,
                "reason": self.absent_reason,
                "detail": self.detail,
                "observed_by": self.observed_by.value,
            }
        return self.value


class CaptureDecision(BaseModel):
    """一次 trial 生效的敏感证据采集声明 (工具入参与模型正文共用一套语义)。

    刻意不做成两个互不相干的开关: 两套规则迟早出现「一个开了一个没开」的组合,
    而那种组合没人记得清。
    """

    tool_arguments: bool = Field(False, description="工具调用入参/结果是否采集")
    model_content: bool = Field(False, description="模型输入输出正文是否采集")

    @classmethod
    def coerce(cls, value: Any) -> CaptureDecision:
        """接受 bool (旧表达: 那只管工具入参) / dict / 实例。"""
        if isinstance(value, CaptureDecision):
            return value
        if isinstance(value, bool):
            return cls(tool_arguments=value)
        if isinstance(value, dict):
            return cls(**value)
        return cls()

    def any_content(self) -> bool:
        return self.tool_arguments or self.model_content


class CapturePolicy(BaseModel):
    """套件/任务级的采集**声明**: 逐字段 None = 继承上一层。

    工具入参与模型正文是同一个声明里的两个字段, 不是两套开关语义 —— 校验、
    落盘、复核都走 ``resolve()`` 出来的那一个 ``CaptureDecision``。
    """

    tool_arguments: bool | None = Field(
        None, description="工具入参/结果采集; None = 继承 suite 级声明"
    )
    model_content: bool | None = Field(
        None, description="模型输入输出正文采集; None = 继承 suite 级声明"
    )

    def is_empty(self) -> bool:
        return self.tool_arguments is None and self.model_content is None

    def resolve(self, parent: CapturePolicy | None = None) -> CaptureDecision:
        """逐字段: 自己声明了就用自己的, 否则用上一层的, 都没有则关闭。"""
        upper = parent or _CAPTURE_CLOSED
        return CaptureDecision(
            tool_arguments=self._pick(self.tool_arguments, upper.tool_arguments, False),
            model_content=self._pick(self.model_content, upper.model_content, False),
        )

    @staticmethod
    def _pick(own: bool | None, upper: bool | None, fallback: bool) -> bool:
        if own is not None:
            return own
        return upper if upper is not None else fallback


_CAPTURE_CLOSED = CapturePolicy()


class GraderConfig(BaseModel):
    """单个评分器的配置"""

    type: GraderType
    name: str = Field(
        ...,
        min_length=1,
        pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$",
        description="评分器名称 (用于注册/查找)",
    )
    weight: float = Field(1.0, ge=0.0, description="权重 (用于加权评分)")
    required: bool = Field(False, description="是否必须通过")
    sample_count: int = Field(
        1, ge=1, le=10, description="采样次数 (LLM Judge 多采样计算 confidence)"
    )
    dependencies: list[str] = Field(
        default_factory=list,
        description="依赖的其他 grader 名称 (拓扑排序, 依赖未通过则跳过)",
    )
    config: dict[str, Any] = Field(default_factory=dict, description="类型特定的配置")

    # ── 证据取信 (spec: graders 按声明的证据级别取信) ──
    evidence: list[ObservedBy] = Field(
        default_factory=lambda: list(ObservedBy.default_declaration()),
        description="该判据允许消费的来源级别 (默认 harness+runner, 不含被评方自报)",
    )
    allow_subject: bool = Field(
        False,
        description="逃生开关: 显式打开后, 被评方自报证据可单独支撑通过 "
        "(该选择随 run 落盘并在结论上可见)",
    )
    judgment_moment: JudgmentMoment = Field(
        JudgmentMoment.AT_END,
        description="环境状态类判据所依据的时刻 (默认「结束时」); 时刻随结论可见",
    )

    @field_validator("evidence")
    @classmethod
    def _validate_evidence(cls, v: list[ObservedBy]) -> list[ObservedBy]:
        if not v:
            raise ValueError(
                "evidence 不能为空: 一个级别都不许读的判据无从判定 "
                "(要收紧到只认评测侧取证就写 [harness])"
            )
        duplicates = sorted({x.value for x in v if [y.value for y in v].count(x.value) > 1})
        if duplicates:
            raise ValueError(f"evidence 含重复级别: {duplicates}")
        return v


class EvalTask(BaseModel):
    """单个评测任务"""

    id: str = Field(..., min_length=1, description="唯一标识")
    description: str = Field("", description="人类可读描述")
    prompt: str = Field(..., description="给 Agent 的输入")
    graders: list[GraderConfig] = Field(..., min_length=1, description="评分器列表")
    env: dict[str, Any] = Field(default_factory=dict, description="环境参数 (透传给 AgentRunner)")
    max_trials: int = Field(3, ge=1, description="默认 trial 数")
    score_strategy: ScoreStrategy = Field(ScoreStrategy.HYBRID, description="评分聚合策略")
    score_threshold: float = Field(0.7, ge=0.0, le=1.0, description="通过阈值 (用于 WEIGHTED/HYBRID)")
    tracked_metrics: list[str] = Field(
        default_factory=lambda: [
            "n_turns",
            "n_toolcalls",
            "n_total_tokens",
            "latency_ms",
            "n_input_tokens",
            "n_output_tokens",
            "n_reasoning_tokens",
            "n_cache_read_tokens",
        ],
        description="从 trace 提取的过程指标",
    )

    # ── 任务元数据 (spec: suite-format) ──
    tags: list[str] = Field(default_factory=list, description="任务标签 (检索/分组用)")
    category: str | None = Field(None, description="任务类别")
    difficulty: Literal["easy", "medium", "hard"] | None = Field(
        None, description="难度标注 (仅呈现, 不参与计分)"
    )

    # ── 预算与效率 (只用于终止与归类, 不改变计分方式) ──
    optimal_steps: int | None = Field(
        None, ge=1, description="最优步数; 声明后步数效率作为诊断量输出"
    )
    step_budget: int | None = Field(
        None, ge=1, description="步数上限; 触顶即该 trial 记 step_budget_exceeded"
    )
    token_budget: int | None = Field(
        None, ge=1, description="token 上限 (近似判定, 依赖 provider 及时上报用量)"
    )
    cost_budget: float | None = Field(
        None, gt=0.0, description="成本上限 (仅当单价表可折算 cost_usd 时生效)"
    )

    # ── 证据边界 ──
    capture: CapturePolicy | None = Field(
        None,
        description="敏感证据采集声明 (工具入参 / 模型正文); 逐字段 None = 继承 suite",
    )
    capture_tool_arguments: bool | None = Field(
        None,
        description="是否采集工具入参/结果; None = 继承 suite 级声明 (默认关闭)。"
        "与 capture.tool_arguments 同一处落点, 不是第二个开关",
    )

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, v: list[str]) -> list[str]:
        cleaned = [tag.strip() for tag in v]
        if any(not tag for tag in cleaned):
            raise ValueError("tags 含空标签")
        if len(set(cleaned)) != len(cleaned):
            duplicates = sorted({t for t in cleaned if cleaned.count(t) > 1})
            raise ValueError(f"tags 含重复标签: {duplicates}")
        return cleaned

    @field_validator("category")
    @classmethod
    def _validate_category(cls, v: str | None) -> str | None:
        if v is None:
            return v
        stripped = v.strip()
        if not stripped:
            raise ValueError("category 不能是空白字符串 (不需要就别写该字段)")
        return stripped

    @model_validator(mode="after")
    def _fold_capture_alias(self) -> EvalTask:
        """标量别名与 capture 是同一处声明, 不允许各说各话。"""
        if "capture_tool_arguments" not in self.model_fields_set:
            return self
        if self.capture is not None and self.capture.tool_arguments is not None:
            if self.capture.tool_arguments != self.capture_tool_arguments:
                raise ValueError(
                    "capture.tool_arguments 与 capture_tool_arguments 取值冲突: "
                    "二者是同一个开关, 只写一个"
                )
            return self
        current = self.capture or CapturePolicy()
        self.capture = current.model_copy(
            update={"tool_arguments": self.capture_tool_arguments}
        )
        return self

    def get_grader_config(self, name: str) -> dict[str, Any]:
        """获取指定名称的评分器配置"""
        for g in self.graders:
            if g.name == name:
                return g.config
        return {}


class TaskView(BaseModel):
    """递给被评方的任务视图 —— 只含执行所需的输入与环境参数。

    判据、期望值、参考产物一律不在类型里: 答案键泄漏曾经是「靠文档提醒别读」,
    现在是「拿不到」。评分阶段读的仍是完整的 ``EvalTask``。
    """

    id: str = Field(..., description="任务标识 (被评方需要它来关联自己的日志)")
    description: str = Field("", description="人类可读描述")
    prompt: str = Field(..., description="给 Agent 的输入")
    env: dict[str, Any] = Field(default_factory=dict, description="环境参数")

    @classmethod
    def of(cls, task: EvalTask) -> TaskView:
        """从完整任务定义裁出视图 (答案键在这一步被留在框架侧)。"""
        return cls(id=task.id, description=task.description, prompt=task.prompt, env=task.env)


class EvalSuite(BaseModel):
    """评测套件 (一组任务)"""

    name: str = Field(..., description="套件名称")
    description: str = Field("", description="套件描述")
    version: str = Field(
        "1.0.0",
        pattern=r"^\d+\.\d+\.\d+$",
        description="语义化版本 (semver)",
    )
    tasks: list[EvalTask] = Field(..., min_length=1, description="任务列表")
    metadata: dict[str, Any] = Field(default_factory=dict, description="自定义元数据")
    capture: CapturePolicy = Field(
        default_factory=CapturePolicy,
        description="suite 级敏感证据采集声明 (默认全关; task 级可逐字段覆盖)",
    )
    capture_tool_arguments: bool = Field(
        False,
        description="suite 级工具入参/结果采集开关 (默认关闭; task 级可覆盖)。"
        "与 capture.tool_arguments 同一处落点, 不是第二个开关",
    )

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Suite name cannot be empty")
        if len(v) > 128:
            raise ValueError("Suite name too long (max 128 chars)")
        return v

    @model_validator(mode="after")
    def _validate_task_ids_unique(self) -> EvalSuite:
        ids = [t.id for t in self.tasks]
        if len(ids) != len(set(ids)):
            duplicates = sorted({x for x in ids if ids.count(x) > 1})
            raise ValueError(f"Duplicate task IDs: {duplicates}")
        return self

    @model_validator(mode="after")
    def _fold_capture_alias(self) -> EvalSuite:
        """标量别名只往 capture 这一处折; 折完把标量镜像成实际生效值。

        用 ``model_fields_set`` 判断用户是否真写了标量: 它的默认值就是 False,
        不这么区分既检测不到「块里开、标量关」的冲突, 也会把没写过的字段当成声明过。
        """
        written = "capture_tool_arguments" in self.model_fields_set
        if written and self.capture.tool_arguments is not None:
            if self.capture.tool_arguments != self.capture_tool_arguments:
                raise ValueError(
                    "capture.tool_arguments 与 capture_tool_arguments 取值冲突: "
                    "二者是同一个开关, 只写一个"
                )
        elif written and self.capture.tool_arguments is None:
            self.capture = self.capture.model_copy(
                update={"tool_arguments": self.capture_tool_arguments}
            )
        self.capture_tool_arguments = bool(self.capture.tool_arguments)
        return self

    def resolved_capture(self, task: EvalTask | None = None) -> CaptureDecision:
        """该 task 生效的采集声明 (task 逐字段覆盖 suite, 双向收紧/放宽都允许)。"""
        if task is None or task.capture is None:
            return CapturePolicy().resolve(self.capture)
        return task.capture.resolve(self.capture)

    def capture_for(self, task: EvalTask) -> bool:
        """该 task 的工具入参采集声明 (``resolved_capture`` 的便捷读取)。"""
        return self.resolved_capture(task).tool_arguments

    @classmethod
    def from_yaml(cls, path: str) -> EvalSuite:
        """从 YAML 文件加载评测套件 (含严格校验, 错误带文件路径上下文)"""
        from agent_eval.core.suite import load_suite

        return load_suite(path)


# ─── Evidence reporting ───────────────────────────────────────────────────────


class EvidenceGap(BaseModel):
    """一个没读到的字段及其原因 (缺失 ≠ 零)。

    ``reason`` 取 ``agent_eval.trace.observations.AbsentReason`` 的值, 或成本侧的
    ``agent_eval.core.pricing`` 原因常量。
    """

    field: str
    reason: str
    detail: str = ""


class EvidenceBoundary(BaseModel):
    """本次 run 的证据采集边界: 复核旧结论时不必再猜当时的配置与口径。"""

    capture_tool_arguments: bool = Field(
        False,
        description="工具入参/结果是否采集 (只要有 task 采集即为 True; "
        "逐 task 差异见 capture_by_task)",
    )
    capture_model_content: bool = Field(
        False,
        description="模型输入输出正文是否采集 (与入参同一套声明解析而来)",
    )
    capture_by_task: dict[str, bool] = Field(
        default_factory=dict, description="task id → 该 task 生效的工具入参采集声明"
    )
    capture_content_by_task: dict[str, bool] = Field(
        default_factory=dict, description="task id → 该 task 生效的正文采集声明"
    )
    subject_allowed: dict[str, bool] = Field(
        default_factory=dict,
        description="task/grader → 是否显式放行「被评方自报证据可单独支撑通过」; "
        "弱证据判定必须在这里看得见, 而不是藏在配置里",
    )
    spec_version: str | None = Field(None, description="归一化所依据的规范版本")
    mapping_version: str | None = Field(None, description="翻译表版本")
    redactor_identifier: str | None = Field(None, description="脱敏处理标识")
    redactor_version: str | None = Field(None, description="脱敏处理版本")
    unrecognized_attributes: list[str] = Field(
        default_factory=list, description="映射不认识、因而不参与归一化的属性名"
    )

    def compare_with(self, other: EvidenceBoundary | None) -> tuple[bool, str | None]:
        """两个 run 的证据边界是否可直接比较。"""
        if other is None:
            return False, "对方 run 未记录证据采集边界 (历史 run), 无法判定可比性"
        if self.spec_version != other.spec_version:
            return False, (
                f"归一化所依据的规范版本不同: {self.spec_version} vs {other.spec_version}"
            )
        if self.mapping_version != other.mapping_version:
            return False, (
                f"属性翻译表版本不同: {self.mapping_version} vs {other.mapping_version}"
            )
        if self.capture_tool_arguments != other.capture_tool_arguments:
            return False, (
                "工具入参采集开关状态不同: "
                f"{self.capture_tool_arguments} vs {other.capture_tool_arguments}"
            )
        if self.capture_by_task != other.capture_by_task:
            return False, "逐 task 的工具入参采集声明不同"
        if self.capture_model_content != other.capture_model_content:
            return False, (
                "模型正文采集开关状态不同: "
                f"{self.capture_model_content} vs {other.capture_model_content}"
            )
        if self.capture_content_by_task != other.capture_content_by_task:
            return False, "逐 task 的模型正文采集声明不同"
        if self.subject_allowed != other.subject_allowed:
            return False, "逐判据的被评方自报证据放行声明不同"
        if (
            self.redactor_identifier,
            self.redactor_version,
        ) != (
            other.redactor_identifier,
            other.redactor_version,
        ):
            return False, (
                "脱敏处理不同: "
                f"{self.redactor_identifier}@{self.redactor_version} vs "
                f"{other.redactor_identifier}@{other.redactor_version}"
            )
        return True, None


# ─── Trial evidence (spec: extension-contracts / orchestration) ──────────────


class StateWindow(BaseModel):
    """某一来源级别在某一判定时刻上「能说什么」。

    ``reason`` 非空表示这一级在这个时刻上判不了 —— 拿结束态冒充全时段观测,
    等于把结果检查悄悄换成过程检查。
    """

    observed_by: ObservedBy
    moment: JudgmentMoment
    payload: dict[str, Any] = Field(default_factory=dict, description="该窗口内合并读数")
    readings: list[Observation] = Field(default_factory=list, description="参与判定的读数")
    invert: bool = Field(False, description="True = 判「结束时不成立」(期望取反)")
    reason: str | None = Field(None, description="判不了的原因 (AbsentReason 值等)")
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.reason is None


class TrialEvidence(BaseModel):
    """一次 trial 的全部观测及其来源 —— 接入契约的交付物, 也是重评分的唯一依据。

    刻意分成两个互不替换的环境状态通道: ``harness_state`` 是评测侧独立取证的
    带时刻序列, ``subject_state`` 是被评侧上报的状态。两者可以矛盾, 而矛盾本身
    就是要呈现的信息。
    """

    trace_id: str = Field("", description="OTel trace ID")
    transcript: list[Observation] = Field(default_factory=list, description="对话/事件记录")
    steps: list[Observation] = Field(default_factory=list, description="过程步骤读数")
    subject_state: list[Observation] = Field(
        default_factory=list, description="被评侧上报的环境状态读数 (runner 或 subject)"
    )
    harness_state: list[Observation] = Field(
        default_factory=list, description="评测侧取证读数序列 (带时刻)"
    )
    artifacts: list[Observation] = Field(default_factory=list, description="产物读数")
    budget: list[Observation] = Field(default_factory=list, description="资源用量读数")
    gaps: list[EvidenceGap] = Field(
        default_factory=list, description="本次采集没取到的字段及原因 (复用 ② 的表示)"
    )
    capture: CaptureDecision = Field(
        default_factory=CaptureDecision, description="本次证据生效的采集声明"
    )
    source_spans: list[dict[str, Any]] = Field(
        default_factory=list,
        description="按采集声明裁剪后的原始 span: 重评分据此重放, 不回查被评系统",
    )
    unrecognized_attributes: list[str] = Field(
        default_factory=list, description="翻译表不认识的属性名 (供映射更新)"
    )
    stripped_attributes: list[str] = Field(
        default_factory=list,
        description="按采集声明从 span 上摘掉的属性名: 事后要确认「当时采了什么」不必猜",
    )
    trace_status: Literal["ok", "empty", "unavailable"] = Field(
        "ok", description="取证通道的产出状态: 没取到 ≠ 什么都没发生"
    )
    trace_detail: str = Field("", description="取证通道报错原文等说明")

    # ── 最简接入: 跑完一次返回证据 ──────────────────────────────────────────

    @classmethod
    def runner_reported(
        cls,
        *,
        trace_id: str = "",
        transcript: list[dict[str, Any]] | None = None,
        state: dict[str, Any] | None = None,
        steps: list[dict[str, Any]] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        observed_by: ObservedBy = ObservedBy.RUNNER,
    ) -> TrialEvidence:
        """把适配层的一次性返回包成带来源的证据 (最简接入路径)。

        ``state`` 走的是被评侧通道 —— 适配层自己读环境读到的东西也在这里, 因此
        它的级别由适配层给出: 默认 ``runner`` (可信适配层), agent 自述填 ``subject``。
        """
        evidence = cls(trace_id=trace_id)
        for message in transcript or []:
            evidence.transcript.append(
                Observation(kind=EvidenceKind.TRANSCRIPT, observed_by=observed_by, value=message)
            )
        for step in steps or []:
            evidence.steps.append(
                Observation(kind=EvidenceKind.STEP, observed_by=observed_by, value=step)
            )
        if state is not None:
            evidence.subject_state.append(
                Observation(kind=EvidenceKind.STATE, observed_by=observed_by, value=state)
            )
        for artifact in artifacts or []:
            evidence.artifacts.append(
                Observation(kind=EvidenceKind.ARTIFACT, observed_by=observed_by, value=artifact)
            )
        return evidence

    # ── 装配 ────────────────────────────────────────────────────────────────

    def add(self, observation: Observation) -> Observation:
        """按「类别 + 来源」把一条读数归入正确通道。

        环境状态是唯一需要按级别分流的类别: 评测侧取证与被评侧上报是两个互不
        替换的通道, 混在一起就等于把「我没看到」写成「那里没有」。
        """
        if observation.kind is EvidenceKind.TRANSCRIPT:
            self.transcript.append(observation)
        elif observation.kind is EvidenceKind.STEP:
            self.steps.append(observation)
        elif observation.kind is EvidenceKind.ARTIFACT:
            self.artifacts.append(observation)
        elif observation.kind is EvidenceKind.BUDGET:
            self.budget.append(observation)
        elif observation.observed_by is ObservedBy.HARNESS:
            self.harness_state.append(observation)
        else:
            self.subject_state.append(observation)
        return observation

    def all_observations(self) -> list[Observation]:
        """全部通道里的读数 (装配时按对象身份去重用)。"""
        return [
            *self.transcript,
            *self.steps,
            *self.subject_state,
            *self.harness_state,
            *self.artifacts,
            *self.budget,
        ]

    def permitted(self, levels: Iterable[ObservedBy | str]) -> TrialEvidence:
        """只保留被允许来源级别的读数视图 (评分器读这个, 而不是整份证据)。

        缺失读数一并保留: 它说的是「这一级没取到」, 与被排除是两回事。原始 span
        属于适配层交付的观测, 因此不允许 ``runner`` 级时一并清空 —— 否则收紧声明
        只是装饰。
        """
        allowed = _level_set(levels)
        keep = lambda seq: [obs for obs in seq if obs.observed_by in allowed]  # noqa: E731
        may_read_trace = ObservedBy.RUNNER in allowed or ObservedBy.HARNESS in allowed
        view = TrialEvidence(
            trace_id=self.trace_id,
            transcript=keep(self.transcript),
            steps=keep(self.steps),
            subject_state=keep(self.subject_state),
            harness_state=keep(self.harness_state),
            artifacts=keep(self.artifacts),
            budget=keep(self.budget),
            gaps=list(self.gaps),
            capture=self.capture,
            unrecognized_attributes=list(self.unrecognized_attributes),
            trace_status=self.trace_status if may_read_trace else "unavailable",
            trace_detail=(
                self.trace_detail
                if may_read_trace
                else "本次判据未声明可消费适配层交付的观测"
            ),
        )
        view.source_spans = list(self.source_spans) if may_read_trace else []
        return view

    def gap(self, field: str, reason: str, detail: str = "") -> None:
        """记一次「这个字段没取到」—— 复用 ② 的缺失表示, 不另造第二套。"""
        self.gaps.append(EvidenceGap(field=field, reason=str(reason), detail=detail))

    # ── 通道视图 ────────────────────────────────────────────────────────────

    def messages(self, levels: Iterable[ObservedBy | str] | None = None) -> list[dict[str, Any]]:
        """指定来源级别下的 transcript 消息序列 (缺来源的读数不会混进来)。"""
        allowed = _level_set(levels)
        return [
            obs.value
            for obs in self.transcript
            if not obs.is_absent and obs.observed_by in allowed and isinstance(obs.value, dict)
        ]

    def state_payload(self, levels: Iterable[ObservedBy | str] | None = None) -> dict[str, Any]:
        """指定级别下被评侧上报的环境状态终值 (供 outcome 视图与旧签名 grader 使用)。"""
        allowed = _level_set(levels)
        merged: dict[str, Any] = {}
        for obs in self.subject_state:
            if obs.is_absent or obs.observed_by not in allowed:
                continue
            if isinstance(obs.value, dict):
                merged.update(copy.deepcopy(obs.value))
        return merged

    def state_series(self, observed_by: ObservedBy) -> list[Observation]:
        """该来源级别下的状态读数, 按采集时刻升序 (缺失读数保留: 缺失也要看得见)。"""
        source = (
            self.harness_state
            if observed_by is ObservedBy.HARNESS
            else self.subject_state
        )
        return sorted(
            (obs for obs in source if obs.observed_by is observed_by),
            key=lambda obs: obs.observed_at,
        )

    @property
    def observed_window(self) -> bool:
        """是否采到了构成「窗口」的多次取证 —— 「任一时刻」类判据的前提。

        计数单位是**取证时刻**而不是读数条数: 一次取证常跨多个通道 (文件清单 +
        DB dump) 且共享同一时刻, 那只是一个时刻的多份读数, 不构成时间窗口。
        """
        moments = {obs.observed_at for obs in self.harness_state if not obs.is_absent}
        return len(moments) >= 2

    def state_window(
        self,
        observed_by: ObservedBy,
        moment: JudgmentMoment = JudgmentMoment.AT_END,
    ) -> StateWindow:
        """该级别在指定判定时刻上能说的话 (结束态 / 结束态取反 / 全窗口)。"""
        series = self.state_series(observed_by)
        usable = [obs for obs in series if not obs.is_absent]
        window = StateWindow(
            observed_by=observed_by,
            moment=moment,
            readings=series,
            invert=moment is JudgmentMoment.NOT_AT_END,
        )
        if not usable:
            first_absent = series[0] if series else None
            window.reason = (
                first_absent.absent_reason
                if first_absent is not None
                else AbsentReason.PROVIDER_UNAVAILABLE.value
            )
            window.detail = (
                (first_absent.detail if first_absent is not None else "")
                or f"{observed_by.value} 级未产出可用的状态读数"
            )
            return window

        if moment is JudgmentMoment.ANY_TIME:
            if observed_by is not ObservedBy.HARNESS or not self.observed_window:
                # 拿结束态冒充全时段观测, 等于把结果检查悄悄换成过程检查
                window.reason = AbsentReason.PROVIDER_UNAVAILABLE.value
                window.detail = (
                    "该通道只上报一次, 回答不了「任一时刻」: "
                    "需要评测侧在运行中取证才有窗口"
                )
                return window
            window.readings = usable
            for obs in usable:
                if isinstance(obs.value, dict):
                    _merge_state(window.payload, obs.value)
            return window

        # 结束态 = **最后一个取证时刻**上的全部读数合并。一次取证往往跨多个通道
        # (文件清单 + DB dump) 且共享同一时刻; 只取序列里最后一条会让一个通道把
        # 另一个通道的终态顶掉 (真实链路验收踩过: db_dump 抹掉了文件清单)。
        last_moment = max(obs.observed_at for obs in usable)
        at_end = [obs for obs in usable if obs.observed_at == last_moment]
        window.readings = at_end
        for obs in at_end:
            if isinstance(obs.value, dict):
                _merge_state(window.payload, obs.value)
        if not window.payload:
            window.reason = AbsentReason.UNRECOGNIZED_ATTRIBUTE.value
            window.detail = "结束态读数不是可判定的字典结构"
        return window


def _merge_state(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    """合并多次取证以回答「任一时刻」: 这是存在量词, 中途出现过的东西不能丢。

    字典按键递归并集 (建完又删掉的文件仍留在窗口里), 列表按元素并集累加,
    标量以最后一次读数为准。
    """
    for key, value in incoming.items():
        existing = target.get(key)
        if isinstance(value, dict) and isinstance(existing, dict):
            _merge_state(existing, value)
        elif isinstance(value, list) and isinstance(existing, list):
            merged = list(existing)
            merged.extend(item for item in value if item not in merged)
            target[key] = merged
        else:
            target[key] = copy.deepcopy(value)


def _level_set(levels: Iterable[ObservedBy | str] | None) -> frozenset[ObservedBy]:
    if levels is None:
        return frozenset(ObservedBy.default_declaration())
    return frozenset(ObservedBy(level) for level in levels)


# ─── Run Result Layer ─────────────────────────────────────────────────────────


class GraderResult(BaseModel):
    """单个评分器的评分结果"""

    grader_name: str
    grader_type: GraderType
    score: float = Field(..., ge=0.0, le=1.0, description="评分 0.0-1.0")
    passed: bool = Field(..., description="是否通过")
    explanation: str = Field("", description="评分理由")
    details: dict[str, Any] = Field(default_factory=dict, description="类型特定的详情")
    confidence: float = Field(
        1.0, ge=0.0, le=1.0, description="置信度 (多采样时 = 1 - 不确定性)"
    )
    uncertainty: float = Field(
        0.0, ge=0.0, le=1.0, description="不确定性 (多采样极差的一半)"
    )
    sample_count: int = Field(1, ge=1, description="评分采样次数")
    duration_ms: float = Field(0.0, description="评分耗时 (毫秒)")
    verdict: TrialVerdict = Field(
        TrialVerdict.VALID,
        description="结论分类; 默认 valid 以保持既有第三方 grader 协议行为不变",
    )
    invalid_reason: InvalidReason | None = Field(
        None, description="评测侧失败原因 (verdict=invalid 时必填)"
    )

    # ── 证据取信自陈 (spec: graders) ──
    evidence_levels: list[ObservedBy] = Field(
        default_factory=list,
        description="本结论实际依据的读数级别; 空 = 没有一条读数是判据所认的",
    )
    judgment_moment: JudgmentMoment | None = Field(
        None, description="环境状态类判据所依据的时刻 (非状态类判据为 None)"
    )
    subject_only: bool = Field(
        False,
        description="本结论只由被评方自报证据支撑 (需套件显式放行才允许成立)",
    )


class PassKEstimate(BaseModel):
    """一个 k 值的通过率估计及其口径元数据。

    `p_lower_bound`/`p_upper_bound` 是单次成功概率 c/n 的 Wilson 区间: 实测且
    k=1 时它就是 `value` 自身的区间, 其余情况下它是该估计所依据概率的区间。
    """

    k: int
    n: int = Field(0, description="参与估计的有效 trial 数 (即分母)")
    successes: int = 0
    value: float | None = Field(None, description="None = insufficient_data")
    method: Literal["measured", "extrapolated", "insufficient_data"] = "insufficient_data"
    extrapolated: bool = Field(
        False, description="True = k 超出实测样本, 值为二项外推而非观测"
    )
    p_point: float | None = Field(None, description="单次成功概率 c/n")
    p_lower_bound: float | None = Field(None, description="p 的置信下界 (Wilson)")
    p_upper_bound: float | None = Field(None, description="p 的置信上界 (Wilson)")
    ci_level: float = DEFAULT_CONFIDENCE_LEVEL


class ScoreDistribution(BaseModel):
    """连续分数的分布摘要 (均值/标准差/地板值/bootstrap 区间)。"""

    n: int = 0
    mean: float | None = Field(None, description="None = insufficient_data")
    std_dev: float | None = None
    worst_of_n: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    ci_level: float = DEFAULT_CONFIDENCE_LEVEL
    method: Literal["bootstrap", "insufficient_data"] = "insufficient_data"


class TrialResult(BaseModel):
    """单次 trial 的完整结果"""

    trial_index: int = Field(..., ge=0, description="第几次 trial (0-based)")
    trace_id: str = Field("", description="OTel trace ID")
    success: bool = Field(True, description="最终是否成功")
    grader_results: list[GraderResult] = Field(default_factory=list, description="各 grader 的评分")
    metrics: dict[str, float] = Field(default_factory=dict, description="过程指标")
    transcript: list[dict[str, Any]] = Field(default_factory=list, description="完整对话记录")
    outcome: dict[str, Any] = Field(default_factory=dict, description="环境最终状态")
    duration_ms: float = Field(0.0, description="总耗时 (毫秒)")
    error: str | None = Field(None, description="错误信息 (如果失败)")
    verdict: TrialVerdict = Field(
        TrialVerdict.VALID,
        description="trial 结论分类 (invalid 不计入通过率分子与分母)",
    )
    invalid_reason: InvalidReason | None = Field(
        None, description="评测侧失败原因 (verdict=invalid 时必填)"
    )
    termination_reason: TerminationReason | None = Field(
        None,
        description="本次 trial 因何结束 (由框架判定); None = 历史 run 未记录, 即未知",
    )
    evidence_gaps: list[EvidenceGap] = Field(
        default_factory=list,
        description="本次判定没读到的字段及原因 (结论自陈所见证据; 缺失 ≠ 零)",
    )
    unrecognized_attributes: list[str] = Field(
        default_factory=list,
        description="该 trial 的 trace 上翻译表不认识的属性名 (供映射更新)",
    )

    # ── 证据与取信 (spec: extension-contracts / orchestration) ──
    evidence: TrialEvidence | None = Field(
        None,
        exclude=True,
        description="本次 trial 采集到的带来源证据; None = 本变更前落盘的历史 trial。"
        "刻意不参与序列化: 证据按 trial 另存一张表, run 记录只留结论",
    )
    evidence_archived: bool = Field(
        False,
        description="证据是否已完整落盘 (重评分的前提); False = 可读但不可重评",
    )
    weakest_evidence: ObservedBy | None = Field(
        None,
        description="支撑本 trial 结论的最弱证据级别 (spec: 结论必须披露最弱一级)",
    )

    @property
    def regradeable(self) -> bool:
        """能否在不回查被评系统的前提下重做判定。

        只看证据是否完整落盘 —— 证据正文按 trial 另存一张表, 从存储读回的 run
        本来就不带内联证据, 那不代表它不可重评。
        """
        return self.evidence_archived

    def avg_score(self) -> float:
        """计算所有 grader 的平均分"""
        if not self.grader_results:
            return 0.0
        return sum(r.score for r in self.grader_results) / len(self.grader_results)


class GradeAttempt(BaseModel):
    """一次判定的口径快照 —— 重评分只追加, **永不覆盖**既有结论。

    「judge 换代让多少 trial 翻判」是个只有历史才能回答的问题, 覆盖式更新会把它
    变成不可问。证据本体存在 trial_evidence 里, 这里只留结论与其口径。
    """

    attempt_id: str = Field(default_factory=lambda: f"att_{uuid.uuid4().hex[:12]}")
    run_id: str
    task_id: str
    trial_index: int
    is_current: bool = Field(True, description="是否为该 trial 当前生效的结论")
    created_at: float = Field(default_factory=lambda: time.time() * 1000)
    grader_versions: dict[str, str] = Field(
        default_factory=dict, description="grader 名 → 实现版本"
    )
    mapping_version: str | None = Field(None, description="当次生效的属性翻译表版本")
    spec_version: str | None = Field(None, description="当次生效的规范修订")
    statistics_version: str | None = Field(None, description="当次生效的统计口径版本")
    judge_models: dict[str, str] = Field(
        default_factory=dict, description="grader 名 → 判定所用模型标识"
    )
    triggered_by: Literal["run", "regrade", "human_score"] = Field(
        "run", description="这次判定由什么引起"
    )
    trial: TrialResult = Field(..., description="该次判定产出的 trial 结论 (不含证据正文)")


class TrialClassResources(BaseModel):
    """一类 trial (通过 / 未通过) 的资源消耗均值。"""

    trials: int = 0
    avg_total_tokens: float | None = Field(
        None, description="输入+输出 token 均值; None = 无一条读到"
    )
    avg_cost_usd: float | None = Field(
        None, description="成本均值; None = 成本不可计算, 不以 0 冒充"
    )
    costed_trials: int = Field(0, description="其中成本可计算的 trial 数")


class ResourceSummary(BaseModel):
    """成本轴摘要: 与通过率并列呈现, MUST NOT 折进任何单一复合分数。"""

    total_cost_usd: float | None = None
    avg_cost_usd: float | None = None
    p50_cost_usd: float | None = None
    p95_cost_usd: float | None = None
    avg_total_tokens: float | None = None
    passed: TrialClassResources = Field(default_factory=TrialClassResources)
    failed: TrialClassResources = Field(default_factory=TrialClassResources)
    cost_unknown_trials: int = Field(
        0, description="成本不可计算的 trial 数 (缺单价或缺 token 观测)"
    )
    cost_unknown_reason: str | None = Field(
        None, description="成本不可计算的首要原因 (无则为逐项缺失)"
    )


class TaskSummary(BaseModel):
    """单个任务的汇总 (跨 trials)"""

    task_id: str
    task_description: str = ""
    total_trials: int
    pass_at_k: dict[int, float | None] = Field(
        default_factory=dict, description="{k: rate}; None = insufficient_data"
    )
    pass_power_k: dict[int, float | None] = Field(
        default_factory=dict, description="{k: rate}; None = insufficient_data"
    )
    estimates: dict[int, PassKEstimate] = Field(
        default_factory=dict, description="每个 k 的估计方式与区间 (pass@k)"
    )
    power_estimates: dict[int, PassKEstimate] = Field(
        default_factory=dict, description="每个 k 的估计方式与区间 (pass^k)"
    )
    valid_trials: int | None = Field(
        None, description="有效 trial 数 (通过率与平均分的分母); None = 口径未知"
    )
    invalid_trials: int | None = Field(
        None, description="评测侧无效的 trial 数 (不占分母); None = 口径未知"
    )
    avg_score: float | None = Field(
        None, description="有效 trial 的加权平均分; None = insufficient_data"
    )
    score_distribution: ScoreDistribution | None = Field(
        None, description="分数的均值/SD/worst_of_n/bootstrap 区间"
    )
    avg_metrics: dict[str, float] = Field(default_factory=dict, description="过程指标分布摘要")
    failures: list[int] = Field(default_factory=list, description="失败的 trial 索引")
    invalid_trial_indices: list[int] = Field(
        default_factory=list, description="判定无效的 trial 索引 (不属失败)"
    )
    invalid_reasons: dict[str, str] = Field(
        default_factory=dict, description="trial 索引 (字符串) → 评测侧失败原因"
    )
    pending_trials: list[int] = Field(
        default_factory=list, description="等待人工评分的 trial 索引 (不计入通过率)"
    )
    consistent: bool | None = Field(
        None,
        description="trial 间加权分是否一致 (std < 0.2); None = 有效样本不足 2, 无法判定",
    )
    score_std_dev: float | None = Field(
        None, description="trial 间加权分标准差; None = insufficient_data (有效样本 < 2)"
    )
    sample_sufficient: bool | None = Field(
        None, description="有效样本是否足以参与饱和判定; None = 口径未知"
    )
    termination_reasons: dict[str, int] = Field(
        default_factory=dict,
        description="终止原因分布 (含 unknown 桶: 历史 trial 未记录); "
        "占比不折叠进通过率与平均分",
    )
    resources: ResourceSummary | None = Field(
        None, description="该 task 的 token/成本轴 (与通过率并列)"
    )
    evidence_levels: dict[str, int] = Field(
        default_factory=dict,
        description="valid trial 按其最弱证据级别的计数 "
        "(「由适配层交付」与「由评测侧取证」不能显示成同一个数字)",
    )
    subject_only_trials: list[int] = Field(
        default_factory=list,
        description="通过结论只由被评方自报证据支撑的 trial 索引 (弱证据判定)",
    )


class RunSummary(BaseModel):
    """一次 suite 运行的汇总"""

    total_tasks: int
    total_trials: int
    pass_at_k: dict[int, float | None] = Field(default_factory=dict, description="全局 pass@k")
    pass_power_k: dict[int, float | None] = Field(default_factory=dict, description="全局 pass^k")
    estimates: dict[int, PassKEstimate] = Field(
        default_factory=dict, description="全局每个 k 的估计方式与区间 (pass@k)"
    )
    power_estimates: dict[int, PassKEstimate] = Field(
        default_factory=dict, description="全局每个 k 的估计方式与区间 (pass^k)"
    )
    valid_trials: int | None = Field(None, description="全局有效 trial 数 (分母)")
    invalid_trials: int | None = Field(None, description="全局评测侧无效 trial 数")
    pending_trials: int | None = Field(None, description="全局等待人工评分 trial 数")
    avg_score: float | None = Field(
        None, description="有效 trial 的加权平均分; None = insufficient_data"
    )
    score_distribution: ScoreDistribution | None = Field(
        None, description="全局分数的均值/SD/worst_of_n/bootstrap 区间"
    )
    avg_metrics: dict[str, float] = Field(default_factory=dict, description="全局指标分布摘要")
    task_summaries: list[TaskSummary] = Field(default_factory=list, description="每个任务的汇总")
    failures: list[str] = Field(default_factory=list, description="未通过的任务 ID")
    saturation: dict[str, Any] = Field(
        default_factory=dict,
        description="饱和度检测结果 (is_saturated/saturation_ratio/eligible_tasks/"
        "insufficient_sample_tasks/recommendation); saturation_ratio=None 表示无合格任务",
    )
    termination_reasons: dict[str, int] = Field(
        default_factory=dict,
        description="全局终止原因分布; 大面积超时/取消必须在这里看得见, "
        "而不是表现为「分数下降」",
    )
    resources: ResourceSummary | None = Field(
        None, description="全局 token/成本轴 (与通过率并列呈现)"
    )
    evidence_levels: dict[str, int] = Field(
        default_factory=dict, description="全局 valid trial 按最弱证据级别的计数"
    )
    subject_only_trials: int = Field(
        0, description="通过结论只由被评方自报证据支撑的 trial 数 (弱证据判定)"
    )


class RunResult(BaseModel):
    """一次 suite 运行的完整结果"""

    run_id: str = Field(default_factory=lambda: f"run_{uuid.uuid4().hex[:12]}")
    suite_name: str = ""
    status: Literal["pending", "running", "completed", "failed", "cancelled"] = "pending"
    started_at: float = Field(default_factory=lambda: time.time() * 1000)
    completed_at: float | None = None
    trials: dict[str, list[TrialResult]] = Field(
        default_factory=dict, description="task_id → trials"
    )
    summary: RunSummary | None = None
    error: str | None = None
    statistics_version: str | None = Field(
        None,
        description="产生该汇总时所用的统计口径版本; None = 历史 run, 版本未知 "
        "(由 EvalRunner 显式盖章, 缺字段即口径未知)",
    )
    evidence: EvidenceBoundary | None = Field(
        None,
        description="本次 run 的证据采集边界 (规范/映射版本、入参采集与脱敏声明); "
        "None = 历史 run 未记录, 不得与记录了边界的 run 直接比较",
    )

    @property
    def duration_ms(self) -> float | None:
        """运行总耗时 (毫秒)"""
        if self.completed_at is not None:
            return self.completed_at - self.started_at
        return None

    @property
    def regradeable(self) -> bool:
        """能否对该 run 重做评分而不回查被评系统。

        本变更前落盘的 run 只存了三元组的产物, 来源分级与证据边界在现场就丢了:
        它们**仍可读**, 但硬要重算只会产出另一个错的东西。
        """
        trials = [t for group in self.trials.values() for t in group]
        return bool(trials) and all(t.regradeable for t in trials)
