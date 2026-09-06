"""
Metric grader — the built-in dispatcher from grader configs to Metric instances.

Task grader configs with `type: metric` route through this grader (D1):
- `name: metric` + `config.metric_name` (single dispatcher per task), or
- `name: <metric_name>` + `type: metric` (several metrics per task — the
  runner falls back to this dispatcher for unregistered metric-type configs)

The metric registry and LLM function are injected by EvalRunner
(metrics_registry=..., llm_fn=...). Evaluation-side failures — an unregistered
metric name, a missing `metric_name`, a missing LLM configuration, or a metric
that could not be computed — yield an ``invalid`` verdict with an explicit
reason (never a 0 or half score), so they stay out of the pass-rate
denominator and do not crash the run.
"""

from __future__ import annotations

from typing import Any

from agent_eval.core.contract import EvalContext
from agent_eval.core.types import (
    EvalTask,
    GraderConfig,
    GraderResult,
    GraderType,
    InvalidReason,
    ObservedBy,
    TrialResult,
    TrialVerdict,
)
from agent_eval.graders._evidence import consulted_levels, implementation_version_of
from agent_eval.graders._verdicts import no_criteria_result
from agent_eval.metrics.base import (
    METRIC_CALC_ERRORS as _CALC_ERRORS,
)
from agent_eval.metrics.base import (
    Metric,
    build_measurement_context,
    metric_declaration,
    metric_failure_reason,
    metric_role,
    uncalculable_metric_result,
)
from agent_eval.metrics.llm_judge import LLMFn


class MetricGrader:
    """按 config.metric_name 从注入注册表分发到对应 Metric 计算"""

    name = "metric"
    # 指标读的是正文 (输入/实际输出), 正文可能是 agent 自述 → 声明到 subject 一级
    evidence_levels = (ObservedBy.HARNESS, ObservedBy.RUNNER, ObservedBy.SUBJECT)
    implementation_version = "2"

    def __init__(
        self,
        metrics_registry: dict[str, Metric] | None = None,
        llm_fn: LLMFn | None = None,
    ):
        # 由 EvalRunner 组合根覆盖注入 (与 storage 注入同一模式)
        from agent_eval.metrics.base import assert_measurement_signature

        for metric in (metrics_registry or {}).values():
            # 装配期拒绝旧五字符串签名 (spec: 旧签名不被接受, 不静默降级)
            assert_measurement_signature(metric)
        self.metrics_registry: dict[str, Metric] = dict(metrics_registry or {})
        self.llm_fn = llm_fn

    async def grade(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None = None,
    ) -> GraderResult:
        config = self._active_config(task, context)
        metric_name = self._metric_name(config)

        if not metric_name:
            return no_criteria_result(
                config.name, GraderType.METRIC, "metric_name", details={"config": config.config}
            )

        metric = self.metrics_registry.get(metric_name)
        if metric is None:
            # 错误文案列出已注册指标: 升格 (判据引用指标为判分量) 拼错名在这里可诊断
            registered = ", ".join(sorted(self.metrics_registry)) or "(空)"
            return self._result(
                config,
                0.0,
                False,
                f"未知指标: {metric_name} (未在 metrics_registry 注册; "
                f"已注册: {registered})",
                verdict=TrialVerdict.INVALID,
                invalid_reason=InvalidReason.UNKNOWN_GRADER,
            )

        # 判据取信级别已由 runner 的 _context_for 在证据视图上收紧; 指标通道声明
        # 在其上二次裁剪 (未声明通道不交付) —— 两边都同意才算数
        evidence = context.evidence if context is not None else None
        ctx = build_measurement_context(
            metric,
            task_id=task.id,
            trial=trial,
            config=config.config,
            evidence=evidence,
        )
        try:
            result = await metric.measure(ctx)
        except _CALC_ERRORS as e:
            return self._result(
                config,
                0.0,
                False,
                f"配置/计算错误: {e}",
                verdict=TrialVerdict.INVALID,
                invalid_reason=metric_failure_reason(e),
            )
        except Exception as e:  # noqa: BLE001 — 指标实现方错误同样不 crash run
            return self._result(
                config,
                0.0,
                False,
                f"指标计算异常: {e}",
                verdict=TrialVerdict.INVALID,
                invalid_reason=metric_failure_reason(e),
            )

        if result.details.get("error"):
            return uncalculable_metric_result(
                config.name,
                str(result.details["error"]),
                result.reason,
                {**result.details, "metric": result.name},
            )

        threshold = float(config.config.get("threshold", metric.threshold))
        role = metric_role(metric)
        declaration = metric_declaration(metric)
        return self._result(
            config,
            result.score,
            result.score >= threshold,
            result.reason,
            details={
                **result.details,
                "metric": result.name,
                "metric_threshold": result.threshold,
                "grader_threshold": threshold,
                "grader_version": implementation_version_of(self),
                # 升格判据的结论携带指标的目录角色与取信声明 (spec: llm-metrics)
                "metric_role": role.value,
                "metric_evidence_declaration": declaration.described(),
            },
            evidence_levels=consulted_levels(
                context.evidence if context is not None else None, "transcript"
            ),
        )

    # ── Config resolution ────────────────────────────────────────────────

    @staticmethod
    def _active_config(task: EvalTask, context: EvalContext | None) -> GraderConfig:
        """当前生效的 metric 配置: runner 传入优先, 否则取名为 metric 的配置"""
        if context is not None and context.grader_config is not None:
            return context.grader_config
        for g in task.graders:
            if g.type == GraderType.METRIC:
                return g
        raise ValueError(f"task '{task.id}' has no metric grader config")

    @staticmethod
    def _metric_name(config: GraderConfig) -> str:
        """metric_name 显式配置优先; 命名分发型 (name=指标名) 取配置名"""
        explicit = config.config.get("metric_name")
        if explicit:
            return str(explicit)
        if config.name != MetricGrader.name:
            return config.name
        return ""

    @staticmethod
    def _result(
        config: GraderConfig,
        score: float,
        passed: bool,
        explanation: str,
        details: dict[str, Any] | None = None,
        verdict: TrialVerdict = TrialVerdict.VALID,
        invalid_reason: InvalidReason | None = None,
        evidence_levels: list[ObservedBy] | None = None,
    ) -> GraderResult:
        return GraderResult(
            grader_name=config.name,
            grader_type=GraderType.METRIC,
            score=max(0.0, min(1.0, score)),
            passed=passed,
            explanation=explanation,
            details=details or {},
            verdict=verdict,
            invalid_reason=invalid_reason,
            evidence_levels=evidence_levels or [],
        )
