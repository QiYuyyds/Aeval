"""Metric protocol — MetricResult / Metric ABC / BaseLLMMetric / to_grader bridge.

A Metric measures LLM output quality (score 0-1 + reason). `to_grader()`
adapts a Metric into the Grader protocol so it can be injected directly into
EvalRunner; the registry-dispatch path (type: metric grader config) lives in
graders/metric_check.py.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from agent_eval.core.types import (
    GraderResult,
    GraderType,
    InvalidReason,
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

    def __post_init__(self):
        self.success = self.score >= self.threshold


class MetricError(Exception):
    """指标计算失败 (LLM 输出结构不符合预期等)。"""


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


class Metric(ABC):
    """指标基类 — 所有 LLM 输出质量指标的抽象"""

    name: str = "base_metric"
    threshold: float = 0.5

    @abstractmethod
    async def measure(
        self,
        input: str,
        actual_output: str,
        expected_output: str | None = None,
        context: list[str] | None = None,
        retrieval_context: list[str] | None = None,
    ) -> MetricResult:
        """
        核心测量方法。

        Args:
            input: 用户输入/问题
            actual_output: Agent 实际输出
            expected_output: 期望输出 (可选)
            context: 回答所依据的上下文 (RAG)
            retrieval_context: 检索到的原始文档 (RAG)

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

    def __init__(self, llm_fn: LLMFn | None = None, threshold: float = 0.5):
        self.llm_fn = llm_fn
        self.threshold = threshold

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

    从 trial 提取 input/actual_output (transcript 首末条), 调用
    metric.measure 映射为 GraderResult。grader name = metric.name,
    task 配置须使用同名 grader (config 可覆盖 threshold)。
    """

    def __init__(self, metric: Metric):
        self.metric = metric
        self.name = metric.name

    async def grade(self, trial, spans, task, context=None):
        config = task.get_grader_config(self.name)
        threshold = config.get("threshold", self.metric.threshold)

        first = trial.transcript[0] if trial.transcript else {}
        last = trial.transcript[-1] if trial.transcript else {}
        prompt = first.get("content", "") if isinstance(first, dict) else ""
        output = last.get("content", "") if isinstance(last, dict) else ""

        try:
            result = await self.metric.measure(
                input=prompt,
                actual_output=output,
                expected_output=config.get("expected_output"),
                context=config.get("context"),
                retrieval_context=config.get("retrieval_context"),
            )
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
            },
        )
