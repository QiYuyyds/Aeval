"""
Core data types for the Aeval evaluation framework.

This module defines all the data models used throughout the framework:
- Task definition layer: EvalTask, EvalSuite, GraderConfig
- Run result layer: TrialResult, GraderResult, TaskSummary, RunSummary, RunResult
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

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
    capture_tool_arguments: bool | None = Field(
        None,
        description="是否采集工具入参/结果; None = 继承 suite 级声明 (默认关闭)",
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

    def get_grader_config(self, name: str) -> dict[str, Any]:
        """获取指定名称的评分器配置"""
        for g in self.graders:
            if g.name == name:
                return g.config
        return {}


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
    capture_tool_arguments: bool = Field(
        False,
        description="suite 级工具入参/结果采集开关 (默认关闭; task 级可覆盖)",
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

    def capture_for(self, task: EvalTask) -> bool:
        """该 task 生效的入参采集声明 (task 覆盖 suite, 双向收紧/放宽都允许)。"""
        if task.capture_tool_arguments is None:
            return self.capture_tool_arguments
        return task.capture_tool_arguments

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
    capture_by_task: dict[str, bool] = Field(
        default_factory=dict, description="task id → 该 task 生效的采集声明"
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

    def avg_score(self) -> float:
        """计算所有 grader 的平均分"""
        if not self.grader_results:
            return 0.0
        return sum(r.score for r in self.grader_results) / len(self.grader_results)


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
