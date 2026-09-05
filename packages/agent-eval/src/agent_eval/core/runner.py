"""
EvalRunner — the core orchestration engine.

Coordinates the full evaluation lifecycle:
1. Load suite
2. For each task, run N trials (TransientError → exponential-backoff retry)
3. For each trial: snapshot → setup → run agent → get traces → grade → teardown → leak check
4. Aggregate results into a RunSummary (pass@k / pass^k / consistency / saturation)

Usage:
    runner = EvalRunner(agent_runner=my_runner)
    result = await runner.run_suite(suite)
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import Any

from agent_eval.core.contract import (
    AgentDefect,
    AgentRunner,
    EnvironmentManager,
    EvalContext,
    ExternalDependencyError,
    Grader,
    Storage,
    TraceProvider,
    TransientError,
)
from agent_eval.core.metrics import (
    ProcessMetrics,
    aggregate_metrics,
    classify_trial,
    estimates_to_rates,
    extract_process_metrics,
    grader_verdict,
    has_enough_data,
    merge_termination_distributions,
    pass_at_k,
    pass_power_k,
    split_trials_by_verdict,
    summarize_resources,
    summarize_scores,
    termination_distribution,
    trial_invalid_reason,
    valid_trials,
    wilson_interval,
)
from agent_eval.core.pricing import PRICE_TABLE_NOT_CONFIGURED, PriceTable
from agent_eval.core.redaction import HashingEvidenceRedactor, describe_redactor
from agent_eval.core.types import (
    DEFAULT_BOOTSTRAP_ROUNDS,
    DEFAULT_CONFIDENCE_LEVEL,
    MIN_VALID_TRIALS_FOR_SATURATION,
    STATISTICS_VERSION,
    EvalSuite,
    EvalTask,
    EvidenceBoundary,
    EvidenceGap,
    GraderConfig,
    GraderResult,
    GraderType,
    InvalidReason,
    PassKEstimate,
    RunResult,
    RunSummary,
    ScoreStrategy,
    TaskSummary,
    TerminationReason,
    TrialResult,
    TrialVerdict,
)
from agent_eval.graders import DEFAULT_GRADERS
from agent_eval.metrics.base import Metric
from agent_eval.metrics.llm_judge import LLMFn
from agent_eval.storage import MemoryStorage
from agent_eval.trace import PhoenixProvider, collect_observations
from agent_eval.trace.mapping import AttributeMapping, default_mapping
from agent_eval.trace.observations import AbsentReason, NormalizedTrace, is_missing

logger = logging.getLogger(__name__)

# 预算触顶类终止原因 (与 timeout / 报错 / 取消相区分: 属任务约束未达成, 计未通过)
_BUDGET_REASONS = frozenset(
    {
        TerminationReason.STEP_BUDGET_EXCEEDED,
        TerminationReason.TOKEN_BUDGET_EXCEEDED,
        TerminationReason.COST_BUDGET_EXCEEDED,
    }
)


# ─── Default Environment (NoOp) ──────────────────────────────────────────────


class NoOpEnvironment:
    """默认无操作环境管理器"""

    async def setup(self, task: EvalTask) -> None:
        pass

    async def teardown(self, task: EvalTask) -> None:
        pass

    async def snapshot(self) -> dict[str, Any]:
        return {}

    async def verify_clean(self, baseline: dict[str, Any]) -> dict[str, Any]:
        return {"clean": True, "differences": []}

    async def restore(self, baseline: dict[str, Any]) -> None:
        return None


# ─── Cost price table (external config only) ─────────────────────────────────


def _coerce_price_table(
    price_table: PriceTable | Mapping[str, Any] | None,
) -> PriceTable | None:
    """单价表接受模型或外部配置字典; 空配置返回 None (= 成本不可计算)。"""
    if price_table is None or isinstance(price_table, PriceTable):
        return price_table
    return PriceTable.from_mapping(price_table)


# ─── Progress Callback Type ──────────────────────────────────────────────────

ProgressCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


# ─── EvalRunner ───────────────────────────────────────────────────────────────


class EvalRunner:
    """
    核心编排器。

    接收项目注入的组件, 执行评测。
    所有组件都有默认值, 只需配置你关心的部分。

    使用方式:
        runner = EvalRunner(
            agent_runner=MyAgentRunner(),          # 必选
            trace_provider=PhoenixProvider(...),   # 可选, 默认 Phoenix
            storage=SqliteStorage(...),            # 可选, 默认 Memory
            environment=MyEnvironment(),           # 可选, 默认 NoOp
            graders=[MyCustomGrader()],            # 可选, 与内置合并 (同名覆盖)
            concurrency=1,                         # trial 并发数
        )
        result = await runner.run_suite(suite)
    """

    def __init__(
        self,
        agent_runner: AgentRunner,
        trace_provider: TraceProvider | None = None,
        storage: Storage | None = None,
        environment: EnvironmentManager | None = None,
        graders: list[Grader] | None = None,
        concurrency: int = 1,
        max_concurrent_graders: int = 4,
        per_trial_timeout: float = 300.0,
        max_trial_retries: int = 2,
        retry_base_delay: float = 1.0,
        grader_timeout: float = 60.0,
        verify_environment: bool = True,
        enable_grader_cache: bool = True,
        metrics_registry: dict[str, Metric] | None = None,
        llm_fn: LLMFn | None = None,
        confidence_level: float = DEFAULT_CONFIDENCE_LEVEL,
        bootstrap_rounds: int = DEFAULT_BOOTSTRAP_ROUNDS,
        min_valid_trials_for_saturation: int = MIN_VALID_TRIALS_FOR_SATURATION,
        trace_mapping: AttributeMapping | None = None,
        price_table: PriceTable | Mapping[str, Any] | None = None,
        redactor: Any | None = None,
    ):
        """
        Args:
            agent_runner: Agent 运行器 (必选)
            trace_provider: Trace 数据提供者 (默认 Phoenix)
            storage: 结果存储 (默认 Memory; 组合暴露 storage.datasets)
            environment: 环境管理器 (默认 NoOp)
            graders: 额外评分器 (与内置合并, 同名覆盖)
            concurrency: trial 并发数
            max_concurrent_graders: 评分器最大并发数
            per_trial_timeout: 单个 trial 超时 (秒)
            max_trial_retries: TransientError 最大重试次数
            retry_base_delay: 重试指数退避基础延迟 (秒)
            grader_timeout: 单个 grader 评分超时 (秒)
            verify_environment: 是否做环境泄漏检测
            enable_grader_cache: 是否启用 grader 结果缓存 (prompt-hash)
            metrics_registry: LLM 输出质量指标注册表 (name → Metric),
                供 metric grader 分发; None = metric 类 grader 得配置错误结果
            llm_fn: LLM 函数 (system, user) → text, 注入未配置的指标与
                metric grader; None 不改变既有行为
            confidence_level: 置信区间水平 (D7, 默认 0.95)
            bootstrap_rounds: 连续分数 bootstrap 重采样次数 (D7, 默认 1000)
            min_valid_trials_for_saturation: 参与饱和判定的最小有效 trial 数
                (D7, 默认 5)
            trace_mapping: span → 标准观测的属性翻译表; None = 内置 OTel GenAI
                条目 (宿主私有词汇用 ``default_mapping(extra_entries)`` 接入,
                不改框架源码与评分器)
            price_table: 成本单价表 (外部配置: PriceTable 或 dict); None = 成本
                一律报不可计算, 框架不内置默认价目
            redactor: 工具入参/结果采集开启时强制应用的脱敏处理;
                None = 默认摘要实现
        """
        self.agent_runner = agent_runner
        self.trace_provider = trace_provider or PhoenixProvider()
        self.storage = storage or MemoryStorage()
        self.environment = environment or NoOpEnvironment()
        self.concurrency = max(1, concurrency)
        self.max_concurrent_graders = max(1, max_concurrent_graders)
        self.per_trial_timeout = per_trial_timeout
        self.max_trial_retries = max(0, max_trial_retries)
        self.retry_base_delay = max(0.0, retry_base_delay)
        self.grader_timeout = grader_timeout
        self.verify_environment = verify_environment
        self.enable_grader_cache = enable_grader_cache
        self.metrics_registry: dict[str, Metric] = dict(metrics_registry or {})
        self.llm_fn = llm_fn
        self.confidence_level = confidence_level
        self.bootstrap_rounds = max(1, bootstrap_rounds)
        self.min_valid_trials_for_saturation = max(1, min_valid_trials_for_saturation)
        self.trace_mapping = trace_mapping or default_mapping()
        self.price_table = _coerce_price_table(price_table)
        self.redactor = redactor or HashingEvidenceRedactor()

        # 注册 grader: 内置 + 自定义 (自定义覆盖同名)
        self._graders: dict[str, Grader] = {}
        for g in DEFAULT_GRADERS:
            self._graders[g.name] = g
        if graders:
            for g in graders:
                self._graders[g.name] = g

        # 注入 storage 到需要它的 grader (如 HumanGrader 的评分请求落库)。
        # 覆盖式注入: runner 是组合根, grader 实例可能被多个 runner 复用。
        for g in self._graders.values():
            if hasattr(g, "storage"):
                g.storage = self.storage
            # metric 分发 grader: 注入指标注册表与 LLM 函数 (D1/D2)
            if hasattr(g, "metrics_registry"):
                g.metrics_registry = self.metrics_registry
            if hasattr(g, "llm_fn") and self.llm_fn is not None and g.llm_fn is None:
                g.llm_fn = self.llm_fn

        # LLM 函数注入未自行配置的指标 (指标实现持有 llm_fn 属性)
        if self.llm_fn is not None:
            for metric in self.metrics_registry.values():
                if getattr(metric, "llm_fn", None) is None:
                    metric.llm_fn = self.llm_fn

        # Grader 结果缓存 (按 run 分桶: 删除 run 时其派生结果一并失效)
        self._grader_cache: dict[str, dict[str, GraderResult]] = {}
        # 取消请求标志 (run_id → 是否已请求取消)
        self._cancel_flags: dict[str, bool] = {}

    # ── Public API ────────────────────────────────────────────────────────

    async def run_suite(
        self,
        suite: EvalSuite,
        callback: ProgressCallback | None = None,
        run_id: str | None = None,
    ) -> RunResult:
        """
        执行整个 suite。

        Args:
            suite: 评测套件
            callback: 进度回调 (用于 SSE 推送)
            run_id: 指定 run ID (API 层预生成, 便于启动即返回)

        Returns:
            RunResult: 完整运行结果
        """
        run = RunResult(
            run_id=run_id or f"run_{uuid.uuid4().hex[:12]}",
            suite_name=suite.name,
            status="running",
            started_at=time.time() * 1000,
            statistics_version=STATISTICS_VERSION,
        )
        self._cancel_flags[run.run_id] = False
        unrecognized: set[str] = set()

        try:
            # 保存 suite 定义与初始 run 记录 (保证启动后立即可查询)
            await self.storage.save_suite(suite)
            await self.storage.save_run(run)

            for task in suite.tasks:
                if self._cancel_flags.get(run.run_id, False):
                    # 取消后不再启动的 task: trial 仍要留痕, 否则「大面积取消」
                    # 只会表现为样本变少而不是评测没跑完
                    run.trials[task.id] = [
                        self._cancelled_trial(task, index)
                        for index in range(task.max_trials)
                    ]
                    continue

                await self._emit(callback, "task_start", {
                    "task_id": task.id,
                    "task_description": task.description,
                })

                trials = await self._run_task_with_retries(
                    task,
                    callback,
                    run_id=run.run_id,
                    capture_tool_arguments=suite.capture_for(task),
                )
                run.trials[task.id] = trials
                for trial in trials:
                    unrecognized.update(trial.unrecognized_attributes)

                counted = valid_trials(trials)
                pass_rate = (
                    sum(1 for t in counted if t.success) / len(counted)
                    if counted else None
                )
                budget_exceeded = sum(
                    1
                    for t in trials
                    if t.termination_reason in _BUDGET_REASONS
                )
                await self._emit(callback, "task_complete", {
                    "task_id": task.id,
                    "trials": len(trials),
                    "valid_trials": len(counted),
                    "invalid_trials": sum(
                        1 for t in trials if classify_trial(t) is TrialVerdict.INVALID
                    ),
                    "pending_trials": sum(
                        1 for t in trials if classify_trial(t) is TrialVerdict.PENDING
                    ),
                    "budget_exceeded_trials": budget_exceeded,
                    "pass_rate": pass_rate,
                })

            # 证据边界随 run 落盘 (复核时不必再猜当时的采集与翻译口径)
            run.evidence = self._evidence_boundary(suite, unrecognized)
            # 计算汇总
            run.summary = self._compute_summary(run, suite)
            run.status = "cancelled" if self._cancel_flags.get(run.run_id, False) else "completed"

        except asyncio.CancelledError:
            run.status = "cancelled"
            raise

        except Exception as e:
            run.status = "failed"
            run.error = str(e)

        finally:
            self._cancel_flags.pop(run.run_id, None)
            if run.evidence is None:
                run.evidence = self._evidence_boundary(suite, unrecognized)
            # 所有退出路径 (含启动阶段被取消) 都落盘最终状态
            run.completed_at = time.time() * 1000
            await self.storage.save_run(run)

        return run

    async def cancel_run(self, run_id: str) -> bool:
        """
        取消一个 run。

        正在执行的 trial 会继续完成 (框架不强杀被评测方的工作), 但其后尚未启动的
        trial 一律记 ``cancelled`` 而不是被静默丢弃。返回 False 表示该 run 不由本
        runner 持有。
        """
        if run_id not in self._cancel_flags:
            return False
        self._cancel_flags[run_id] = True
        return True

    def _cancelled_trial(self, task: EvalTask, index: int) -> TrialResult:
        """取消生效前没跑起来的 trial: 评测没跑完, 不是 agent 失败。"""
        return TrialResult(
            trial_index=index,
            success=False,
            verdict=TrialVerdict.INVALID,
            invalid_reason=InvalidReason.TRIAL_CANCELLED,
            termination_reason=TerminationReason.CANCELLED,
            error="取消请求生效前该 trial 未启动",
        )

    def evict_run_cache(self, run_id: str) -> bool:
        """丢弃某 run 的派生评分缓存 (删除 run 时一并清除证据的衍生物)。"""
        return self._grader_cache.pop(run_id, None) is not None

    def _evidence_boundary(
        self,
        suite: EvalSuite,
        unrecognized: set[str],
    ) -> EvidenceBoundary:
        """本次 run 的证据采集边界: 采集开关 + 翻译口径 + 脱敏处理身份。"""
        identifier, version = describe_redactor(self.redactor)
        per_task = {task.id: suite.capture_for(task) for task in suite.tasks}
        return EvidenceBoundary(
            capture_tool_arguments=any(per_task.values()),
            capture_by_task=per_task,
            spec_version=self.trace_mapping.spec_version,
            mapping_version=self.trace_mapping.version,
            redactor_identifier=identifier,
            redactor_version=version,
            unrecognized_attributes=sorted(unrecognized),
        )

    # ── Task Execution ───────────────────────────────────────────────────

    async def _run_task_with_retries(
        self,
        task: EvalTask,
        callback: ProgressCallback | None,
        run_id: str = "",
        capture_tool_arguments: bool = False,
    ) -> list[TrialResult]:
        """执行单个任务的多个 trial (TransientError 指数退避重试)"""
        semaphore = asyncio.Semaphore(self.concurrency)

        async def _trial(index: int) -> TrialResult:
            async with semaphore:
                # 取消已生效: 该 trial 没跑, 记 cancelled 而不是悄悄少一个样本
                if self._cancel_flags.get(run_id, False):
                    return self._cancelled_trial(task, index)
                # trial_start 发一次 (重试不重复发; 事件按 (task_id, trial_index) 幂等)
                await self._emit(callback, "trial_start", {
                    "task_id": task.id,
                    "trial_index": index,
                })
                for attempt in range(self.max_trial_retries + 1):
                    try:
                        result = await self._run_trial(
                            task,
                            index,
                            run_id=run_id,
                            capture_tool_arguments=capture_tool_arguments,
                        )
                        await self._emit(callback, "trial_complete", {
                            "task_id": task.id,
                            "trial_index": index,
                            "success": result.success,
                            "termination_reason": (
                                result.termination_reason.value
                                if result.termination_reason
                                else None
                            ),
                        })
                        return result
                    except TransientError as e:
                        if attempt < self.max_trial_retries:
                            delay = self.retry_base_delay * (2**attempt)
                            logger.warning(
                                "TransientError in task %s trial %d (attempt %d), "
                                "retrying in %.2fs: %s",
                                task.id, index, attempt + 1, delay, e,
                            )
                            await asyncio.sleep(delay)
                            continue
                        # 重试用尽: 接入方把故障声明为瞬态 (= 基建问题而非
                        # agent 能力), 故该 trial 不占通过率分母
                        return TrialResult(
                            trial_index=index,
                            trace_id="",
                            success=False,
                            grader_results=[],
                            metrics={},
                            transcript=[],
                            outcome={},
                            duration_ms=0.0,
                            verdict=TrialVerdict.INVALID,
                            invalid_reason=InvalidReason.EXTERNAL_DEPENDENCY_UNAVAILABLE,
                            termination_reason=TerminationReason.AGENT_ERROR,
                            error=(
                                f"TransientError after {self.max_trial_retries} "
                                f"retries: {e}"
                            ),
                        )

        trials = await asyncio.gather(
            *[_trial(i) for i in range(task.max_trials)],
            return_exceptions=False,
        )
        return list(trials)

    async def _run_trial(
        self,
        task: EvalTask,
        index: int,
        run_id: str = "",
        capture_tool_arguments: bool = False,
    ) -> TrialResult:
        """执行单次 trial (含环境基线快照与泄漏检测)。

        每条退出路径都给出框架判定的终止原因: 被评测方自报的「已完成」不足以
        决定它 —— 触顶即触顶 (spec: orchestration)。
        """
        # 1. 拍摄环境基线快照 (setup 前, 即"干净"状态)
        try:
            baseline = await self.environment.snapshot()
        except Exception as e:
            logger.warning("Environment snapshot failed for task %s: %s", task.id, e)
            baseline = {}

        # 2. 准备环境
        await self.environment.setup(task)
        start_time = time.time() * 1000

        # 已采集的部分结果 (超时/异常时不得丢弃)
        trace_id = ""
        transcript: list[dict[str, Any]] = []
        outcome: dict[str, Any] = {}
        extraction = ProcessMetrics()

        try:
            # 3. 运行 Agent (带超时)
            trace_id, transcript, outcome = await asyncio.wait_for(
                self.agent_runner.run(task),
                timeout=self.per_trial_timeout,
            )

            # 4. 获取 trace 并翻译为标准观测 (词汇差异只在这条边界上解决一次)
            observations = await collect_observations(
                self.trace_provider,
                trace_id,
                mapping=self.trace_mapping,
                capture_tool_arguments=capture_tool_arguments,
                redactor=self.redactor,
            )

            # 5. 从归一化观测提取过程指标 (读不到的项报缺口, 不冒充 0)
            elapsed = time.time() * 1000 - start_time
            extraction = extract_process_metrics(
                observations,
                task.tracked_metrics,
                price_table=self.price_table,
                optimal_steps=task.optimal_steps,
            )
            metrics = {**extraction.metrics, "latency_ms": elapsed}
            gaps = list(extraction.gaps)

            # 6. 预算判定 (触顶即停止该 trial, 不再花评分成本)
            breach = self._first_budget_breach(task, observations, metrics, gaps)
            trial = TrialResult(
                trial_index=index,
                trace_id=trace_id,
                success=breach is None,
                grader_results=[],
                metrics=metrics,
                transcript=transcript,
                outcome=outcome,
                duration_ms=elapsed,
                evidence_gaps=gaps,
                unrecognized_attributes=list(observations.unrecognized_attributes),
                termination_reason=breach or TerminationReason.AGENT_COMPLETED,
            )
            if breach is not None:
                trial.error = (
                    f"{breach.value}: 任务声明的预算已达上限, "
                    "该 trial 就此停止 (不采信被评测方的自报完成)"
                )
                return trial

            # 7. 运行评分器 (EvalContext 贯穿, shared_state 在 grader 间共享)
            context = EvalContext(
                run_id=run_id,
                task=task,
                trial=trial,
                spans=observations.source_spans,
                observations=observations,
                shared_state={},
            )
            trial = await self._grade_trial(trial, observations.source_spans, task, context)

            return trial

        except TimeoutError:
            # 超时是评测预算耗尽, 不是「agent 未通过评分」→ invalid (不占分母)
            elapsed = time.time() * 1000 - start_time
            return TrialResult(
                trial_index=index,
                trace_id=trace_id,
                success=False,
                grader_results=[],
                metrics={**extraction.metrics, "latency_ms": elapsed},
                transcript=transcript,
                outcome=outcome,
                duration_ms=elapsed,
                verdict=TrialVerdict.INVALID,
                invalid_reason=InvalidReason.TRIAL_TIMEOUT,
                termination_reason=TerminationReason.TIMEOUT,
                evidence_gaps=list(extraction.gaps),
                error=f"Trial timed out after {self.per_trial_timeout}s",
            )

        except asyncio.CancelledError:
            raise

        except TransientError:
            # 交由 _run_task_with_retries 处理重试
            raise

        except AgentDefect as e:
            # 接入方已声明: 属 agent 自身缺陷 → 任务约束未达成, 计未通过 (占分母)
            elapsed = time.time() * 1000 - start_time
            return self._error_trial(
                index,
                elapsed=elapsed,
                trace_id=trace_id,
                transcript=transcript,
                outcome=outcome,
                extraction=extraction,
                verdict=TrialVerdict.VALID,
                invalid_reason=None,
                error=f"agent_defect: {e}",
            )

        except ExternalDependencyError as e:
            # 接入方已声明: 属外部依赖不可达 → 结论不可信, 不占分母
            elapsed = time.time() * 1000 - start_time
            return self._error_trial(
                index,
                elapsed=elapsed,
                trace_id=trace_id,
                transcript=transcript,
                outcome=outcome,
                extraction=extraction,
                verdict=TrialVerdict.INVALID,
                invalid_reason=InvalidReason.EXTERNAL_DEPENDENCY_UNAVAILABLE,
                error=f"external_dependency: {e}",
            )

        except Exception as e:
            # 未声明类别的报错: 框架不猜是 agent 缺陷还是基建故障 → 需人工判定
            elapsed = time.time() * 1000 - start_time
            return self._error_trial(
                index,
                elapsed=elapsed,
                trace_id=trace_id,
                transcript=transcript,
                outcome=outcome,
                extraction=extraction,
                verdict=TrialVerdict.INVALID,
                invalid_reason=InvalidReason.UNCLASSIFIED_AGENT_ERROR,
                error=f"unclassified_agent_error (需人工判定): {e}",
            )

        finally:
            # 8. 清理环境
            await self.environment.teardown(task)

            # 9. 泄漏检测: 与基线比对, 泄漏则告警并恢复 (不判 trial 失败 —
            #    泄漏是环境问题不是 Agent 问题)
            if self.verify_environment:
                try:
                    verify = await self.environment.verify_clean(baseline)
                    if not verify.get("clean", False):
                        logger.warning(
                            "Environment leak detected in task %s trial %d: %s",
                            task.id,
                            index,
                            verify.get("differences"),
                        )
                        await self.environment.restore(baseline)
                except Exception as e:
                    logger.warning(
                        "Environment leak check failed for task %s trial %d: %s",
                        task.id, index, e,
                    )

    def _error_trial(
        self,
        index: int,
        *,
        elapsed: float,
        trace_id: str,
        transcript: list[dict[str, Any]],
        outcome: dict[str, Any],
        extraction: ProcessMetrics,
        verdict: TrialVerdict,
        invalid_reason: InvalidReason | None,
        error: str,
    ) -> TrialResult:
        """报错路径的 trial 结果: 终止原因一律 agent_error, 归类由声明决定。"""
        return TrialResult(
            trial_index=index,
            trace_id=trace_id,
            success=False,
            metrics={**extraction.metrics, "latency_ms": elapsed},
            transcript=transcript,
            outcome=outcome,
            duration_ms=elapsed,
            verdict=verdict,
            invalid_reason=invalid_reason,
            termination_reason=TerminationReason.AGENT_ERROR,
            evidence_gaps=list(extraction.gaps),
            error=error,
        )

    @staticmethod
    def _first_budget_breach(
        task: EvalTask,
        observations: NormalizedTrace,
        metrics: dict[str, float],
        gaps: list[EvidenceGap],
    ) -> TerminationReason | None:
        """步数 / token / 成本任一达上限即停止该 trial (按此优先级报首个触顶项)。

        token 与成本预算是近似判定: 依赖 provider 及时上报用量, 读不到时不猜。
        """
        steps = observations.tool_call_count
        if task.step_budget is not None:
            if not is_missing(steps) and steps >= task.step_budget:
                return TerminationReason.STEP_BUDGET_EXCEEDED
            if is_missing(steps):
                gaps.append(
                    EvidenceGap(
                        field="budget.step_budget",
                        reason=steps.reason.value,
                        detail="步数预算无法判定 (trace 证据缺失)",
                    )
                )

        tokens = metrics.get("n_total_tokens")
        if task.token_budget is not None:
            if tokens is not None and tokens >= task.token_budget:
                return TerminationReason.TOKEN_BUDGET_EXCEEDED
            if tokens is None:
                gaps.append(
                    EvidenceGap(
                        field="budget.token_budget",
                        reason=AbsentReason.PROVIDER_NOT_COVERED.value,
                        detail="token 预算无法判定 (未读到用量)",
                    )
                )

        cost = metrics.get("cost_usd")
        if task.cost_budget is not None:
            if cost is not None and cost >= task.cost_budget:
                return TerminationReason.COST_BUDGET_EXCEEDED
            if cost is None:
                gaps.append(
                    EvidenceGap(
                        field="budget.cost_budget",
                        reason=PRICE_TABLE_NOT_CONFIGURED,
                        detail="成本预算无法判定 (单价表未配置或 token 分解缺失)",
                    )
                )
        return None

    # ── Grading ──────────────────────────────────────────────────────────

    async def _grade_trial(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None = None,
    ) -> TrialResult:
        """
        对一次 trial 运行所有评分器 (依赖拓扑 Pipeline)。

        1. 按 GraderConfig.dependencies 拓扑排序
        2. 依赖 grader 未通过 (或未配置/未运行) → 跳过记 0 分
        3. 单个 grader 超时 → 记 0 分失败
        4. MODEL 类 grader sample_count > 1 → 多采样聚合 (平均分/不确定性/置信度)
        5. sample_count == 1 且缓存开启 → prompt-hash 结果缓存
        """
        grader_results: dict[str, GraderResult] = {}
        run_cache = self._grader_cache.setdefault(
            context.run_id if context is not None else "", {}
        )

        for config in self._topological_sort(task.graders):
            grader = self._resolve_grader(config)

            if grader is None:
                # 未注册 grader = 评测配置故障, 不是 agent 表现
                grader_results[config.name] = GraderResult(
                    grader_name=config.name,
                    grader_type=config.type,
                    score=0.0,
                    passed=False,
                    explanation=f"Unknown grader: {config.name}",
                    verdict=TrialVerdict.INVALID,
                    invalid_reason=InvalidReason.UNKNOWN_GRADER,
                )
                continue

            # 依赖检查: 所有依赖的 grader 必须已运行且通过。
            # 依赖未满足是关于 agent 的结论 (前置条件没达成), 保持 valid + 0 分 (D3)
            unsatisfied_dep = self._first_unsatisfied_dependency(config, grader_results)
            if unsatisfied_dep is not None:
                grader_results[config.name] = GraderResult(
                    grader_name=config.name,
                    grader_type=config.type,
                    score=0.0,
                    passed=False,
                    explanation=(
                        f"依赖未满足: grader '{unsatisfied_dep}' 未通过或未配置"
                    ),
                    verdict=TrialVerdict.VALID,
                )
                continue

            # 缓存命中 (多采样 deliberate 重试绕过缓存; 缓存按 run 分桶)
            use_cache = self.enable_grader_cache and config.sample_count <= 1
            if use_cache:
                cache_key = self._grader_cache_key(config, trial)
                cached = run_cache.get(cache_key)
                if cached is not None:
                    hit = cached.model_copy(deep=True)
                    hit.details = {**hit.details, "cached": True}
                    grader_results[config.name] = hit
                    continue

            try:
                # 分发型 grader (MetricGrader) 经 context.grader_config 感知
                # 当前生效配置; 其余 grader 不读取该字段, 行为不变
                call_context = (
                    replace(context, grader_config=config)
                    if context is not None
                    else None
                )
                result = await asyncio.wait_for(
                    grader.grade(trial, spans, task, call_context),
                    timeout=self.grader_timeout,
                )
            except TimeoutError:
                result = GraderResult(
                    grader_name=config.name,
                    grader_type=config.type,
                    score=0.0,
                    passed=False,
                    explanation=f"Grader timeout after {self.grader_timeout}s",
                    verdict=TrialVerdict.INVALID,
                    invalid_reason=InvalidReason.GRADER_TIMEOUT,
                )
            except Exception as e:
                result = GraderResult(
                    grader_name=config.name,
                    grader_type=config.type,
                    score=0.0,
                    passed=False,
                    explanation=f"Grader error: {e}",
                    verdict=TrialVerdict.INVALID,
                    invalid_reason=InvalidReason.GRADER_ERROR,
                )

            # LLM Judge 多采样: 计算平均分/不确定性/置信度
            if config.type == GraderType.MODEL and config.sample_count > 1:
                result = await self._multi_sample(grader, result, config, trial, spans, task, context)

            if use_cache:
                run_cache[cache_key] = result.model_copy(deep=True)

            grader_results[config.name] = result

        trial.grader_results = [
            grader_results[config.name] for config in task.graders
        ]

        # 根据评分策略计算最终成功状态
        trial.success = self._compute_trial_success(task, trial.grader_results)
        # trial 结论分类 (invalid/pending 不占通过率分母); 不改写 success 语义
        trial.verdict = classify_trial(trial)
        trial.invalid_reason = trial_invalid_reason(trial)

        return trial

    def _resolve_grader(self, config: GraderConfig) -> Grader | None:
        """按配置名解析 grader 实例。

        metric 类配置 (type: metric) 的 name 即指标名, 不在 grader 注册表
        内时回退到 "metric" 分发器 (D1), 由其按 metric_name/config.name
        路由到注入的 Metric 实例; 其余类型保持未知 grader 语义。
        """
        grader = self._graders.get(config.name)
        if grader is not None:
            return grader
        if config.type == GraderType.METRIC:
            return self._graders.get("metric")
        return None

    async def _multi_sample(
        self,
        grader: Grader,
        first_result: GraderResult,
        config: GraderConfig,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None,
    ) -> GraderResult:
        """多采样评分: 平均分 + 不确定性 (极差/2) + 置信度 (1 - 不确定性)

        任一采样为评测侧失败时, 聚合结论同样不可用 (pending 优先于 invalid),
        不得把 judge 故障折进平均分。
        """
        samples = [first_result]
        for _ in range(config.sample_count - 1):
            try:
                samples.append(await grader.grade(trial, spans, task, context))
            except Exception as e:
                samples.append(GraderResult(
                    grader_name=config.name,
                    grader_type=config.type,
                    score=0.0,
                    passed=False,
                    explanation=f"Grader error: {e}",
                    verdict=TrialVerdict.INVALID,
                    invalid_reason=InvalidReason.GRADER_ERROR,
                ))

        scores = [s.score for s in samples]
        avg_score = sum(scores) / len(scores)
        uncertainty = (max(scores) - min(scores)) / 2
        confidence = max(0.0, 1.0 - uncertainty)
        # 多数样本通过才视为通过 (偶数采样平票判不通过)
        passed = sum(1 for s in samples if s.passed) * 2 > len(samples)

        verdicts = [grader_verdict(s) for s in samples]
        verdict = TrialVerdict.VALID
        invalid_reason: InvalidReason | None = None
        if TrialVerdict.PENDING in verdicts:
            verdict = TrialVerdict.PENDING
        elif TrialVerdict.INVALID in verdicts:
            verdict = TrialVerdict.INVALID
            invalid_reason = next(
                (
                    s.invalid_reason
                    for s in samples
                    if grader_verdict(s) is TrialVerdict.INVALID
                    and s.invalid_reason is not None
                ),
                InvalidReason.GRADER_ERROR,
            )

        return GraderResult(
            grader_name=first_result.grader_name,
            grader_type=first_result.grader_type,
            score=avg_score,
            passed=passed,
            explanation=f"Multi-sample avg over {len(samples)} samples",
            details={
                **first_result.details,
                "sample_scores": scores,
                "sample_explanations": [s.explanation for s in samples],
            },
            confidence=confidence,
            uncertainty=uncertainty,
            sample_count=len(samples),
            verdict=verdict,
            invalid_reason=invalid_reason,
        )

    def _first_unsatisfied_dependency(
        self,
        config: GraderConfig,
        grader_results: dict[str, GraderResult],
    ) -> str | None:
        """返回第一个未满足的依赖名; 全部满足返回 None。

        依赖未在本 task 中配置 (因而没有结果) 视为未满足。
        """
        for dep in config.dependencies:
            result = grader_results.get(dep)
            if result is None or not result.passed:
                return dep
        return None

    def _topological_sort(
        self,
        configs: list[GraderConfig],
    ) -> list[GraderConfig]:
        """按 dependencies 拓扑排序 (保持声明顺序稳定, 环依赖按声明序兜底)"""
        config_map = {c.name: c for c in configs}
        visited: set[str] = set()
        visiting: set[str] = set()
        ordered: list[GraderConfig] = []

        def visit(name: str) -> None:
            if name in visited or name not in config_map:
                return
            if name in visiting:
                # 环依赖: 跳过 (由依赖检查兜底记 0 分)
                return
            visiting.add(name)
            for dep in config_map[name].dependencies:
                visit(dep)
            visiting.discard(name)
            visited.add(name)
            ordered.append(config_map[name])

        for config in configs:
            visit(config.name)
        return ordered

    def _grader_cache_key(self, config: GraderConfig, trial: TrialResult) -> str:
        """prompt-hash 缓存 key: sha256(grader name + config + transcript/outcome)"""
        payload = json.dumps(
            {
                "grader": config.name,
                "config": config.config,
                "transcript": trial.transcript,
                "outcome": trial.outcome,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _compute_trial_success(
        self,
        task: EvalTask,
        grader_results: list[GraderResult],
    ) -> bool:
        """根据评分策略判断 trial 是否成功"""
        strategy = task.score_strategy
        threshold = task.score_threshold

        if strategy == "all_pass":
            return all(r.passed for r in grader_results) if grader_results else True

        elif strategy == "weighted":
            if not grader_results:
                return True
            # 使用 grader config 中的权重
            total_weight = sum(
                gc.weight for gc in task.graders
            )
            if total_weight == 0:
                return True
            weighted_score = sum(
                r.score * gc.weight
                # strict=False: replay 路径 (runs.py) 的 grader_results 可能来自
                # 旧版本 suite, 长度不保证一致 — 保持截断语义
                for r, gc in zip(grader_results, task.graders, strict=False)
            ) / total_weight
            return weighted_score >= threshold

        elif strategy == "hybrid":
            # required 必须通过
            required_pass = all(
                r.passed
                for r, gc in zip(grader_results, task.graders, strict=False)
                if gc.required
            )
            if not required_pass:
                return False

            # 非 required 加权
            non_required = [
                (r, gc) for r, gc in zip(grader_results, task.graders, strict=False)
                if not gc.required
            ]
            if not non_required:
                return True

            total_weight = sum(gc.weight for _, gc in non_required)
            if total_weight == 0:
                return True
            weighted_score = sum(
                r.score * gc.weight for r, gc in non_required
            ) / total_weight
            return weighted_score >= threshold

        return True

    def _trial_weighted_score(
        self,
        task: EvalTask | None,
        trial: TrialResult,
    ) -> float | None:
        """与成功判定同源的加权分 (D5)。

        一致性、平均分与分布摘要全部读这一条序列, 不再一个用简单平均、
        一个用加权。无 grader 结果时返回 None (该 trial 不进分布摘要)。
        """
        if not trial.grader_results:
            return None
        if task is None:
            return trial.avg_score()

        results = trial.grader_results
        if task.score_strategy == ScoreStrategy.HYBRID:
            pairs = [
                (r, gc)
                for r, gc in zip(results, task.graders, strict=False)
                if not gc.required
            ]
        else:
            pairs = list(zip(results, task.graders, strict=False))

        total_weight = sum(gc.weight for _, gc in pairs)
        if total_weight == 0:
            return trial.avg_score()
        return sum(r.score * gc.weight for r, gc in pairs) / total_weight

    @staticmethod
    def _stable_seed(*parts: str) -> int:
        """由 run/task 派生 bootstrap 随机种子 (同一 run 重读结果可复现)。"""
        digest = hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()
        return int(digest[:12], 16)

    # ── Summary Computation ──────────────────────────────────────────────

    def _compute_summary(
        self,
        run: RunResult,
        suite: EvalSuite,
    ) -> RunSummary:
        """计算 suite 级别的汇总。

        口径: 只有 valid trial 进入分子与分母; invalid (评测侧失败) 与
        pending (待人工评分) 单列计数; 分母为 0 的聚合量为 insufficient_data
        (None) 而非 0.0; 每个通过率附 Wilson 区间, 每个连续分数附 bootstrap
        区间与 worst_of_n。
        """
        task_summaries: list[TaskSummary] = []
        all_trials: list[TrialResult] = []

        tasks_by_id = {t.id: t for t in suite.tasks}

        # 预计算 k_values (避免空 trials 时 KeyError)
        max_trials = max(
            (len(trials) for trials in run.trials.values()), default=0
        )
        k_values = list(range(1, max_trials + 1)) if max_trials > 0 else [1]

        pooled_valid = 0
        pooled_invalid = 0
        pooled_pending = 0
        pooled_successes = 0
        pooled_scores: list[float] = []

        for task_id, trials in run.trials.items():
            all_trials.extend(trials)
            task = tasks_by_id.get(task_id)

            buckets = split_trials_by_verdict(trials)
            valid_indices = [i for i, _ in buckets[TrialVerdict.VALID]]
            invalid_indices = [i for i, _ in buckets[TrialVerdict.INVALID]]
            pending_indices = [i for i, _ in buckets[TrialVerdict.PENDING]]
            counted = [t for _, t in buckets[TrialVerdict.VALID]]

            # 估计量自行按 verdict 过滤分母, 传入完整 trials
            estimates = {
                k: pass_at_k(trials, k, confidence=self.confidence_level) for k in k_values
            }
            power_estimates = {
                k: pass_power_k(trials, k, confidence=self.confidence_level)
                for k in k_values
            }

            # 加权分序列 (与成功判定同源), 仅取 valid trial
            scores = [
                s for s in (
                    self._trial_weighted_score(task, t) for _, t in buckets[TrialVerdict.VALID]
                )
                if s is not None
            ]
            dist = summarize_scores(
                scores,
                rounds=self.bootstrap_rounds,
                confidence=self.confidence_level,
                seed=self._stable_seed(run.run_id, task_id),
            )

            consistency = self._check_trial_consistency(scores)

            task_summaries.append(TaskSummary(
                task_id=task_id,
                task_description=tasks_by_id[task_id].description if task else "",
                total_trials=len(trials),
                pass_at_k=estimates_to_rates(estimates),
                pass_power_k=estimates_to_rates(power_estimates),
                estimates=estimates,
                power_estimates=power_estimates,
                valid_trials=len(valid_indices),
                invalid_trials=len(invalid_indices),
                avg_score=dist.mean,
                score_distribution=dist,
                avg_metrics=aggregate_metrics(trials),
                failures=[i for i in valid_indices if not trials[i].success],
                invalid_trial_indices=invalid_indices,
                invalid_reasons={
                    str(i): (trial_invalid_reason(trials[i]) or InvalidReason.GRADER_ERROR).value
                    for i in invalid_indices
                },
                pending_trials=pending_indices,
                consistent=consistency["consistent"],
                score_std_dev=consistency["std_dev"],
                sample_sufficient=has_enough_data(
                    len(valid_indices), self.min_valid_trials_for_saturation
                ),
                termination_reasons=termination_distribution(trials),
                resources=summarize_resources(trials),
            ))

            pooled_valid += len(valid_indices)
            pooled_invalid += len(invalid_indices)
            pooled_pending += len(pending_indices)
            pooled_successes += sum(1 for t in counted if t.success)
            pooled_scores.extend(scores)

        pooled_dist = summarize_scores(
            pooled_scores,
            rounds=self.bootstrap_rounds,
            confidence=self.confidence_level,
            seed=self._stable_seed(run.run_id, "__run__"),
        )

        run_estimates = self._pooled_estimates(
            task_summaries, k_values, pooled_valid, pooled_successes, extrapolating=True
        )
        run_power_estimates = self._pooled_estimates(
            task_summaries, k_values, pooled_valid, pooled_successes, extrapolating=False
        )

        return RunSummary(
            total_tasks=len(run.trials),
            total_trials=len(all_trials),
            pass_at_k=estimates_to_rates(run_estimates),
            pass_power_k=estimates_to_rates(run_power_estimates),
            estimates=run_estimates,
            power_estimates=run_power_estimates,
            valid_trials=pooled_valid,
            invalid_trials=pooled_invalid,
            pending_trials=pooled_pending,
            avg_score=pooled_dist.mean,
            score_distribution=pooled_dist,
            avg_metrics=aggregate_metrics(all_trials),
            task_summaries=task_summaries,
            failures=[ts.task_id for ts in task_summaries if ts.failures],
            termination_reasons=merge_termination_distributions(
                [ts.termination_reasons for ts in task_summaries]
            ),
            resources=summarize_resources(all_trials),
            saturation=self._detect_saturation(
                task_summaries, min_valid_trials=self.min_valid_trials_for_saturation
            ),
        )

    def _pooled_estimates(
        self,
        task_summaries: list[TaskSummary],
        k_values: list[int],
        pooled_valid: int,
        pooled_successes: int,
        extrapolating: bool,
    ) -> dict[int, PassKEstimate]:
        """全局每个 k 的估计: 值为各 task 的均值, 区间为合并 trial 的 Wilson。

        分母为 0 的 task 不参与均值 (不以 0.0 顶替); 没有任何 task 有值时整项
        为 insufficient_data; 只要有 task 的值来自外推, 全局值即标为外推。
        """
        source = "power_estimates" if not extrapolating else "estimates"
        per_task = [getattr(ts, source) for ts in task_summaries]

        bounds = wilson_interval(pooled_successes, pooled_valid, self.confidence_level)
        low, high = bounds if bounds else (None, None)
        p_point = pooled_successes / pooled_valid if pooled_valid else None

        out: dict[int, PassKEstimate] = {}
        for k in k_values:
            entries = [est for est in (m.get(k) for m in per_task) if est is not None]
            vals = [est.value for est in entries if est.value is not None]
            if not vals:
                out[k] = PassKEstimate(
                    k=k,
                    n=pooled_valid,
                    successes=pooled_successes,
                    method="insufficient_data",
                    ci_level=self.confidence_level,
                )
                continue
            any_extrapolated = any(est.extrapolated for est in entries)
            out[k] = PassKEstimate(
                k=k,
                n=pooled_valid,
                successes=pooled_successes,
                value=sum(vals) / len(vals),
                method="extrapolated" if any_extrapolated else "measured",
                extrapolated=any_extrapolated,
                p_point=p_point,
                p_lower_bound=low,
                p_upper_bound=high,
                ci_level=self.confidence_level,
            )
        return out

    @staticmethod
    def _check_trial_consistency(scores: list[float]) -> dict[str, Any]:
        """检查 trial 间加权分一致性 (std < 0.2 视为一致)。

        入参是与成功判定同源的加权分序列 (D5), 不再是各 trial 的简单平均。
        有效样本不足 2 时 std 无从谈起: consistent/std_dev 均为 None
        (insufficient_data), 单点不得记为「完美一致」。
        """
        if len(scores) < 2:
            return {"consistent": None, "std_dev": None, "scores": scores}

        avg = sum(scores) / len(scores)
        variance = sum((s - avg) ** 2 for s in scores) / len(scores)
        std_dev = variance**0.5

        return {
            "consistent": std_dev < 0.2,
            "std_dev": std_dev,
            "scores": scores,
        }

    @staticmethod
    def _detect_saturation(
        task_summaries: list[TaskSummary],
        threshold: float = 0.95,
        min_valid_trials: int = MIN_VALID_TRIALS_FOR_SATURATION,
    ) -> dict[str, Any]:
        """饱和度检测: 有效样本足够的 task 中过半实测 pass@1 ≥ threshold → 建议加难

        分母排除 invalid/pending (pass@1 已按 valid 计算); 有效 trial 数不足
        最低要求的任务不参与判定, 单独列为样本不足。
        """
        if not task_summaries:
            return {
                "is_saturated": False,
                "saturation_ratio": None,
                "saturated_tasks": [],
                "eligible_tasks": [],
                "insufficient_sample_tasks": [],
                "threshold": threshold,
                "min_valid_trials": min_valid_trials,
                "recommendation": None,
            }

        eligible: list[TaskSummary] = []
        insufficient: list[str] = []
        for ts in task_summaries:
            pass1 = ts.estimates.get(1)
            if (
                ts.valid_trials is not None
                and ts.valid_trials >= min_valid_trials
                and pass1 is not None
                and not pass1.extrapolated
                and pass1.value is not None
            ):
                eligible.append(ts)
            else:
                insufficient.append(ts.task_id)

        saturated_tasks = [
            ts.task_id for ts in eligible
            if ts.estimates[1].value >= threshold
        ]
        saturation_ratio = len(saturated_tasks) / len(eligible) if eligible else None
        is_saturated = bool(eligible) and saturation_ratio > 0.5

        return {
            "is_saturated": is_saturated,
            "saturation_ratio": saturation_ratio,
            "saturated_tasks": saturated_tasks,
            "eligible_tasks": [ts.task_id for ts in eligible],
            "insufficient_sample_tasks": insufficient,
            "threshold": threshold,
            "min_valid_trials": min_valid_trials,
            "recommendation": (
                "评测已饱和, 建议增加更有挑战性的任务" if is_saturated else None
            ),
        }

    # ── Helpers ──────────────────────────────────────────────────────────

    async def _emit(
        self,
        callback: ProgressCallback | None,
        event: str,
        data: dict[str, Any],
    ) -> None:
        """发送进度事件"""
        if callback is not None:
            # 回调不应中断主流程
            with contextlib.suppress(Exception):
                await callback(event, data)
