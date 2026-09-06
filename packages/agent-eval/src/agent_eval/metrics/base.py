"""Metric protocol — MeasurementContext / Metric ABC / BaseLLMMetric / to_grader bridge.

A Metric measures LLM output quality (score 0-1 + reason). 0.3.0 起 measure()
收一个 :class:`MeasurementContext` (任务输入 + 按声明过滤后的观测 + 取信声明),
旧的五字符串签名已移除 —— 按扩展协议「协议演进不保留双路径」执行, 装配期校验
拒绝旧签名 (``assert_measurement_signature``)。`to_grader()` 把 Metric 适配进
Grader 协议注入 EvalRunner; 注册表分发路径 (type: metric grader config) 在
graders/metric.py。
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from agent_eval.core.types import (
    GraderResult,
    GraderType,
    InvalidReason,
    MeasurementContext,
    MetricEvidenceDeclaration,
    MetricRole,
    Observation,
    TrialEvidence,
    TrialVerdict,
)
from agent_eval.metrics.llm_judge import (
    LLMFn,
    LLMJudgeError,
    LLMNotConfiguredError,
    judge_json,
)


def metric_failure_reason(error: BaseException) -> InvalidReason:
    """把指标计算异常映射到评测侧失败原因 (封闭枚举)。

    与内置 grader 同一枚举与文案约定 (任务 3.3)。LLMJudgeError 不区分
    「调用失败」与「响应非 JSON」, 统一按判定通道不可用计。
    """
    if isinstance(error, (LLMNotConfiguredError, LLMJudgeError)):
        return InvalidReason.JUDGE_UNAVAILABLE
    if isinstance(error, MetricError):
        return InvalidReason.VERDICT_UNPARSEABLE
    return InvalidReason.GRADER_ERROR


@dataclass
class MetricResult:
    """单个指标的计算结果"""

    name: str  # 指标名称
    score: float  # 分数 (0-1)
    reason: str = ""  # 评分理由 (LLM Judge 生成)
    details: dict[str, Any] = field(default_factory=dict)  # 中间数据 (statements/verdicts...)
    threshold: float = 0.5  # 通过阈值
    success: bool = False  # 是否通过
    role: str = MetricRole.JUDGING.value  # 指标角色 (diagnostic | judging)

    def __post_init__(self):
        self.success = self.score >= self.threshold


class MetricError(Exception):
    """指标计算失败 (LLM 输出结构不符合预期等)。"""


class MetricProtocolError(Exception):
    """指标不符合 0.3.0 测量协议 (旧五字符串签名 / 非法取信声明)。"""


# 评测侧失败 (配置/解析/调用) 的异常族: 记 invalid, 不折 0 分, 不 crash run (D2)
METRIC_CALC_ERRORS = (LLMNotConfiguredError, LLMJudgeError, MetricError)


def uncalculable_metric_result(
    grader_name: str,
    error: str,
    reason: str = "",
    details: dict[str, Any] | None = None,
) -> GraderResult:
    """指标因缺料未计算 (faithfulness 无 context / recall 无 expected_output)。

    Metric 层对独立调用方保留带内 ``details["error"]`` 契约; 进入评分流水线时
    它是判据缺席而非 agent 表现结论, 因此记 invalid 且不占通过率分母 (D2)。
    """
    return GraderResult(
        grader_name=grader_name,
        grader_type=GraderType.METRIC,
        score=0.0,
        passed=False,
        explanation=f"指标未计算 ({error}){': ' + reason if reason else ''}",
        details=details or {},
        verdict=TrialVerdict.INVALID,
        invalid_reason=InvalidReason.NO_CRITERIA_CONFIGURED,
    )


def assert_measurement_signature(metric: Any) -> None:
    """装配期校验: 指标 measure 必须是单参宽签名 (spec: 旧签名不被接受)。

    旧五字符串签名的指标在注册/注入时即报错并说明新签名形状 —— 不静默降级
    为只传字符串, 也不留继承桥。第三方直接调用方拿到的是显式 TypeError 语义。
    """
    measure = getattr(metric, "measure", None)
    if measure is None:
        raise MetricProtocolError(
            f"Metric '{getattr(metric, 'name', type(metric).__name__)}' 没有 measure 方法"
        )
    try:
        params = [
            p
            for p in inspect.signature(measure).parameters.values()
            if p.kind
            in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        ]
    except (TypeError, ValueError):
        return  # 内建 C 函数等无法内省的对象不阻塞装配
    if len(params) == 1:
        return
    got = ", ".join(p.name for p in params) or "(无参数)"
    raise MetricProtocolError(
        f"Metric '{getattr(metric, 'name', type(metric).__name__)}'.measure 的签名是 "
        f"measure({got}) —— 这是已移除的旧五字符串形状。0.3.0 起统一为 "
        f"measure(ctx: MeasurementContext) -> MetricResult: 任务输入读 ctx.prompt / "
        f"ctx.actual_output / ctx.expected_output, RAG 物料读 ctx.context / "
        f"ctx.retrieval_context, 轨迹读 ctx.observations (按指标声明的证据通道交付, "
        f"见 Metric.evidence_levels)。迁移对照见 docs/integration-guide.md。"
    )


def metric_declaration(metric: Any) -> MetricEvidenceDeclaration:
    """解析指标的取信声明 (类属性 evidence_levels; None/缺省 = 仅最终输出)。"""
    declared = getattr(metric, "evidence_levels", None)
    if declared is not None:
        declared = tuple(declared)
        if not declared:
            # 类属性不经 pydantic 校验, 空声明在这里显式报错并列可选值
            raise MetricProtocolError(
                f"Metric '{getattr(metric, 'name', type(metric).__name__)}' 声明了空的 "
                f"evidence_levels: 一个通道都不读就删掉该声明 (不写 = 仅最终输出); "
                f"可选通道: transcript / steps / harness_state / subject_state"
            )
    return MetricEvidenceDeclaration(evidence_levels=declared)


def metric_role(metric: Any) -> MetricRole:
    """解析指标的目录角色 (类属性 role; 缺省 judging 保持既有指标行为不变)。"""
    role = getattr(metric, "role", None)
    if role is None:
        return MetricRole.JUDGING
    return role if isinstance(role, MetricRole) else MetricRole(role)


def endpoint_outputs(trial: Any) -> tuple[str, str]:
    """trial 转录的首末条正文 (prompt, actual_output) —— 0.2.0 五参的取材路径。"""
    first = trial.transcript[0] if trial.transcript else {}
    last = trial.transcript[-1] if trial.transcript else {}
    prompt = first.get("content", "") if isinstance(first, dict) else ""
    output = last.get("content", "") if isinstance(last, dict) else ""
    return prompt, output


def measurement_observations(
    evidence: TrialEvidence | None,
    declaration: MetricEvidenceDeclaration,
) -> list[Observation]:
    """按指标的通道声明从证据裁出观测序列 (spec: 未声明的通道不交付)。

    交付的是「证据里带来源分级与时刻的读数」; 缺失读数一并保留 —— 它说的是
    「这一级没取到」, 与被排除是两回事。
    """
    if evidence is None:
        return []
    channels = declaration.channels
    if not channels:
        return []
    observations: list[Observation] = []
    for channel in ("transcript", "steps", "harness_state", "subject_state"):
        if channel in channels:
            observations.extend(getattr(evidence, channel))
    return observations


def build_measurement_context(
    metric: Any,
    *,
    task_id: str = "",
    trial: Any = None,
    config: dict[str, Any] | None = None,
    evidence: TrialEvidence | None = None,
) -> MeasurementContext:
    """从 trial / 判据配置 / 证据视图构造该指标的 MeasurementContext。

    ``evidence`` 传入**已经按判据取信级别 permitted 过**的证据视图 (runner 的
    ``_context_for`` 产物); 指标通道声明在其上二次裁剪 —— 两个声明都要同意。
    prompt/actual_output 沿用 0.2.0 的首末条路径, 保证默认声明下逐字段等价。
    """
    declaration = metric_declaration(metric)
    prompt, output = endpoint_outputs(trial) if trial is not None else ("", "")
    criterion = config or {}
    return MeasurementContext(
        task_id=task_id,
        prompt=prompt,
        actual_output=output,
        expected_output=criterion.get("expected_output"),
        context=criterion.get("context"),
        retrieval_context=criterion.get("retrieval_context"),
        observations=measurement_observations(evidence, declaration),
        declaration=declaration,
    )


class Metric(ABC):
    """指标基类 — 所有 LLM 输出质量指标的抽象

    类属性:
        evidence_levels: 取信声明 (通道级, 可选值 transcript / steps /
            harness_state / subject_state); None = 仅最终输出 (与 0.2.0 等价)。
        role: 目录角色 (diagnostic | judging); 轨迹类指标应注册为 diagnostic,
            升格由套件判据显式引用完成。
    """

    name: str = "base_metric"
    threshold: float = 0.5
    evidence_levels: tuple[str, ...] | None = None
    role: MetricRole = MetricRole.JUDGING

    @abstractmethod
    async def measure(self, ctx: MeasurementContext) -> MetricResult:
        """
        核心测量方法 (0.3.0 宽签名)。

        Args:
            ctx: 测量上下文 —— 任务输入 (prompt/actual_output/expected_output)、
                RAG 物料 (context/retrieval_context)、按声明过滤后的观测序列
                (observations) 与本指标生效的取信声明 (declaration)

        Raises:
            LLMNotConfiguredError: 未注入 LLM 函数 (明确配置错误, 非 0 分)
            MetricError / LLMJudgeError: 计算失败
        """
        ...

    def to_grader(self) -> MetricGraderAdapter:
        """将 Metric 转换为 Grader 适配器, 融入评分流水线 (grader 类型 metric)"""
        return MetricGraderAdapter(self)


class BaseLLMMetric(Metric):
    """基于 LLM Judge 的指标基类"""

    def __init__(
        self,
        llm_fn: LLMFn | None = None,
        threshold: float = 0.5,
        redactor: Any | None = None,
    ):
        self.llm_fn = llm_fn
        self.threshold = threshold
        # 脱敏钩子: 轨迹注入提示词前对正文应用 (与采集侧同一接口); None = 未注入
        self.redactor = redactor

    async def _llm_judge(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        """调用 LLM 并解析结构化 JSON (容错 + 重试在 llm_judge.judge_json)"""
        return await judge_json(self.llm_fn, system_prompt, user_prompt)

    @staticmethod
    def _score_of(data: dict[str, Any]) -> float:
        """从 judge 输出中取 score 并夹取到 [0, 1]; 非数值判分 = 解析失败。"""
        try:
            score = float(data["score"])
        except (KeyError, TypeError, ValueError) as e:
            raise MetricError(f"judge 'score' is not a number: {data!r}") from e
        return max(0.0, min(1.0, score))


class MetricGraderAdapter:
    """
    Metric → Grader 协议适配器 (metric.to_grader() 产物)。

    从 trial 取证视图与判据配置构造 MeasurementContext, 调用 metric.measure
    映射为 GraderResult。grader name = metric.name, task 配置须使用同名 grader
    (config 可覆盖 threshold)。
    """

    def __init__(self, metric: Metric):
        assert_measurement_signature(metric)
        self.metric = metric
        self.name = metric.name
        self.declaration = metric_declaration(metric)
        self.role = metric_role(metric)

    async def grade(self, trial, spans, task, context=None):
        config = task.get_grader_config(self.name)
        threshold = config.get("threshold", self.metric.threshold)

        # 判据取信级别已由 runner 的 _context_for 在 evidence 视图上收紧;
        # 指标通道声明在其上二次裁剪 (未声明通道不交付)
        evidence = getattr(context, "evidence", None) if context is not None else None
        ctx = build_measurement_context(
            self.metric,
            task_id=task.id,
            trial=trial,
            config=config,
            evidence=evidence,
        )

        try:
            result = await self.metric.measure(ctx)
        except Exception as e:  # noqa: BLE001 — 评测侧故障记 invalid, 不折 agent 0 分
            reason = metric_failure_reason(e)
            kind = "配置/计算错误" if isinstance(e, METRIC_CALC_ERRORS) else "指标计算异常"
            return GraderResult(
                grader_name=self.name,
                grader_type=GraderType.METRIC,
                score=0.0,
                passed=False,
                explanation=f"{kind}: {e}",
                verdict=TrialVerdict.INVALID,
                invalid_reason=reason,
            )

        metric_error = result.details.get("error")
        if metric_error:
            return uncalculable_metric_result(
                self.name,
                str(metric_error),
                result.reason,
                {**result.details, "metric": result.name},
            )

        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.METRIC,
            score=result.score,
            passed=result.score >= threshold,
            explanation=result.reason,
            details={
                **result.details,
                "metric": result.name,
                "metric_threshold": result.threshold,
                "metric_role": self.role.value,
                "metric_evidence_declaration": self.declaration.described(),
            },
        )
