"""pytest integration — metric fixtures + suite gate (framework layer).

模块级只依赖 pytest + agent_eval 自身 (AChat 装配 eval_integration 在
runner 装配路径内延迟导入 — 纯框架使用场景不触碰 app 代码, AST import
检查仅约束 app.*)。

注册方式 (dev 期 import 注册, entry-point 打包延后至独立 repo — design D3):
根 conftest.py 中 ``pytest_plugins = ["agent_eval.metrics.pytest_plugin"]``

Fixtures:
    eval_metrics      — P0 指标注册表 (name → SyncMetric)
    answer_relevancy / faithfulness / context_recall / context_precision — 单指标
    eval_runner       — EvalRunner (默认经 eval_integration 装配, 未配置时
                        skip 并提示; 可用 set_eval_runner_factory 覆盖装配)

每个指标 fixture 值为 SyncMetric 同步包装: 同步测试直接调用
``measure(...)`` (内部 asyncio.run, 零事件循环管理); 异步测试取
``.async_metric`` 直接 await measure (不与 pytest-asyncio 抢 loop)。
指标默认未注入 llm_fn — 缺配置时报错信息可读 (LLMNotConfiguredError),
测试可经 ``fixture.async_metric.llm_fn = stub`` 注入。

Suite 门禁:
    pytest --eval-suite=suite.yaml --eval-threshold=0.7 [--eval-invalid-limit=0.2]
    pytest --eval-suite=suite.yaml --eval-baseline=<run_id]

常规测试循环结束后执行 suite 评测, terminal summary 打印修正后的 pass@k
(实测区间为组合无偏估计, 外推项带 * 标注)、分母计数与 pass@1 的 95% 区间。
三条互相独立的失败条件 (任一成立都置 session.testsfailed, 退出码非 0,
terminal summary 点名触发的是哪个门):
1. 绝对阈值门: 实测 `pass@1` 低于阈值或有效样本为 0 (证据不足, 含全 pending);
2. 评测可信度: `invalid` trial 占比超过上限 (评测本身不可信 — 输出「评测侧
   问题」而非 agent 表现结论);
3. 基线相对门 (--eval-baseline): 与指定基线 run 比较后显著变差 / 不可比 /
   不可判 (core.comparison.compare_baseline 同一语义 — 与 CLI run --baseline、
   REST compare 三处同源; 宁可红不可哑, 不可比不许静默放行)。
评测 runner 装配与 eval_runner fixture 同源 (set_eval_runner_factory 可覆盖);
基线 run 从同一 runner 的 storage 读取。
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

import pytest

from agent_eval.core.comparison import (
    BASELINE_GATE_FAILURES,
    BaselineComparison,
    format_baseline_report,
)
from agent_eval.core.types import DEFAULT_INVALID_RATIO_LIMIT, MeasurementContext, RunResult
from agent_eval.metrics import (
    AnswerRelevancyMetric,
    ContextPrecisionMetric,
    ContextRecallMetric,
    FaithfulnessMetric,
    build_default_metrics_registry,
)
from agent_eval.metrics.base import Metric, MetricResult

# ─── 同步包装 ────────────────────────────────────────────────────────────────


class SyncMetric:
    """Metric 同步包装 — measure 内部 asyncio.run, 供纯同步 pytest 测试调用。

    用法边界: 仅限无 running event loop 的同步测试 (notebook 等已有 loop
    的环境不适用); 异步测试取 .async_metric 直接 await measure。
    0.3.0 宽签名: 入参与异步 Metric.measure 相同, 都是 MeasurementContext
    (便捷构造用 ``MeasurementContext.of(...)``)。
    """

    def __init__(self, metric: Metric):
        self._metric = metric

    @property
    def async_metric(self) -> Metric:
        """原始异步 Metric (异步测试直接 await 其 measure)"""
        return self._metric

    @property
    def name(self) -> str:
        return self._metric.name

    @property
    def threshold(self) -> float:
        return self._metric.threshold

    def measure(self, ctx: MeasurementContext) -> MetricResult:
        """同步 measure (内部 asyncio.run 桥接异步 Metric.measure)。"""
        return asyncio.run(self._metric.measure(ctx))


def _sync(metric: Metric) -> SyncMetric:
    return SyncMetric(metric)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def eval_metrics() -> dict[str, SyncMetric]:
    """P0 指标注册表 (name → SyncMetric; 原始异步对象经 .async_metric)。"""
    return {name: _sync(m) for name, m in build_default_metrics_registry().items()}


@pytest.fixture
def answer_relevancy() -> SyncMetric:
    """AnswerRelevancyMetric 同步包装 (llm_fn 未注入 — 按需注入 stub/凭证)。"""
    return _sync(AnswerRelevancyMetric())


@pytest.fixture
def faithfulness() -> SyncMetric:
    """FaithfulnessMetric 同步包装。"""
    return _sync(FaithfulnessMetric())


@pytest.fixture
def context_recall() -> SyncMetric:
    """ContextRecallMetric 同步包装。"""
    return _sync(ContextRecallMetric())


@pytest.fixture
def context_precision() -> SyncMetric:
    """ContextPrecisionMetric 同步包装。"""
    return _sync(ContextPrecisionMetric())


# ─── Runner 装配 (eval_runner fixture 与 suite 门禁共用) ─────────────────────


class EvalRunnerUnavailableError(Exception):
    """Aeval runner 无法装配 (eval_integration 不可用或配置缺失)。"""


# 同步返回 runner, 或返回 awaitable (async 工厂)
EvalRunnerFactory = Callable[[], Any]

_eval_runner_factory: EvalRunnerFactory | None = None


def set_eval_runner_factory(factory: EvalRunnerFactory | None) -> None:
    """注入 runner 装配工厂 (测试/宿主覆盖装配点, 如 MockRunner 演示)。"""
    global _eval_runner_factory
    _eval_runner_factory = factory


async def build_eval_runner():
    """装配 EvalRunner: 注入工厂优先; 默认经 eval_integration (AChat 接入层)。

    Raises:
        EvalRunnerUnavailableError: 装配点不可用 (错误信息可读;
            eval_runner fixture 据此 skip, suite 门禁据此判门禁失败)
    """
    if _eval_runner_factory is not None:
        result = _eval_runner_factory()
        if inspect.isawaitable(result):
            result = await result
        return result

    # 延迟导入: 框架独立使用场景不依赖 AChat 接入层
    try:
        from eval_integration.config import create_aeval_runner
    except Exception as e:
        raise EvalRunnerUnavailableError(
            "eval_integration unavailable — cannot assemble an Aeval runner "
            "(standalone usage: register one via set_eval_runner_factory): "
            f"{e}"
        ) from e
    try:
        return await create_aeval_runner()
    except Exception as e:
        raise EvalRunnerUnavailableError(
            "Aeval runner assembly failed (check EVAL_AGENT_ID / EVAL_API_BASE / "
            f"token settings): {e}"
        ) from e


@pytest.fixture
def eval_runner():
    """EvalRunner 实例 (默认经 eval_integration 装配; 未配置时 skip 并提示)。"""
    try:
        return asyncio.run(build_eval_runner())
    except EvalRunnerUnavailableError as e:
        pytest.skip(f"eval_runner unavailable — skipping: {e}")


# ─── Suite 门禁 (--eval-suite / --eval-threshold) ────────────────────────────


class SuiteGatePlugin:
    """--eval-suite 门禁: 测试循环结束后跑 suite, 打印修正后的 pass@k 并判定。

    三条独立的失败条件 (任一都构成不可放行, terminal summary 点名哪个门):
    1. 绝对阈值门 (agent 表现): 修正后的实测 `pass@1` (分母已排除
       invalid/pending) 低于阈值, 或因有效样本为 0 而证据不足 (含全 pending);
    2. 评测可信度: `invalid` trial 占比超过 `--eval-invalid-limit`;
    3. 基线相对门 (`--eval-baseline`): 与基线 run 比较判显著变差 / 不可比 /
       不可判 (core.comparison.compare_baseline, 与 CLI run --baseline 同语义)。
    阈值判定在 pytest_runtestloop wrapper 的 finally 中递增 session.testsfailed
    (早于 _main 的退出码计算; terminal summary 阶段已无法影响退出码)。
    """

    def __init__(
        self,
        suite_path: str,
        threshold: float,
        invalid_ratio_limit: float = DEFAULT_INVALID_RATIO_LIMIT,
        baseline_run_id: str | None = None,
    ):
        self.suite_path = suite_path
        self.threshold = threshold
        self.invalid_ratio_limit = invalid_ratio_limit
        self.baseline_run_id = baseline_run_id
        self.suite_name: str | None = None
        self.result: RunResult | None = None
        self.error: str | None = None
        self.failed = False
        self.harness_failed = False
        self.insufficient = False
        # 基线相对门状态 (未传 --eval-baseline 时全部保持 None/False/空)
        self.baseline_comparison: BaselineComparison | None = None
        self.baseline_failed = False
        self.baseline_error: str | None = None

    @pytest.hookimpl(wrapper=True)
    def pytest_runtestloop(self, session):
        try:
            return (yield)
        finally:
            self._apply_gate(session)

    def _apply_gate(self, session) -> None:
        if session.config.option.collectonly:
            return
        try:
            self.result = asyncio.run(self._run_suite())
        except Exception as e:
            self.error = str(e)

        pass1 = self._pass_at_1()
        ratio = self._invalid_ratio()
        # 装配失败静默放行比失败更危险: 出错/证据不足/invalid 超阈都判门禁失败
        self.harness_failed = self.error is not None or (
            ratio is not None and ratio > self.invalid_ratio_limit
        )
        self.insufficient = self.error is None and pass1 is None
        if (
            self.harness_failed
            or self.insufficient
            or pass1 < self.threshold
            or self.baseline_failed
        ):
            self.failed = True
            session.testsfailed += 1

    async def _run_suite(self):
        from agent_eval.core.suite import load_suite

        suite = load_suite(self.suite_path)
        self.suite_name = suite.name
        runner = await build_eval_runner()
        result = await runner.run_suite(suite)
        if self.baseline_run_id is not None:
            await self._compare_baseline(runner, result)
        return result

    async def _compare_baseline(self, runner, result: RunResult) -> None:
        """基线相对门: 从同一 runner 的 storage 读基线 run, 复用同一比较语义。

        基线缺失/存储不可读都是门禁失败 (宁可红不可哑), 与「装配失败静默
        放行是 CI 事故」同一条纪律。
        """
        from agent_eval.core.comparison import compare_baseline

        storage = getattr(runner, "storage", None)
        getter = getattr(storage, "get_run", None)
        if storage is None or getter is None:
            self.baseline_error = (
                f"eval runner storage does not support reading baseline run "
                f"'{self.baseline_run_id}'"
            )
            self.baseline_failed = True
            return
        baseline = await getter(self.baseline_run_id)
        if baseline is None:
            self.baseline_error = (
                f"baseline run '{self.baseline_run_id}' not found in the eval "
                f"runner's storage — the gate refuses to pass on a missing baseline"
            )
            self.baseline_failed = True
            return
        self.baseline_comparison = compare_baseline(result, baseline)
        self.baseline_failed = self.baseline_comparison.verdict in BASELINE_GATE_FAILURES

    def _summary(self):
        if self.result is None or self.result.summary is None:
            return None
        return self.result.summary

    def _pass_at_1(self) -> float | None:
        """修正后的实测 pass@1; None = 有效样本为 0 (证据不足, 含全 pending)。"""
        summary = self._summary()
        if summary is None:
            return None
        value = summary.pass_at_k.get(1, summary.pass_at_k.get("1"))
        return None if value is None else float(value)

    def _invalid_ratio(self) -> float | None:
        """invalid trial 占比 (相对全部 trial); None = 无汇总。"""
        summary = self._summary()
        if summary is None or not summary.total_trials:
            return None
        return (summary.invalid_trials or 0) / summary.total_trials

    def pytest_terminal_summary(self, terminalreporter, exitstatus) -> None:
        terminalreporter.write_sep("=", "AEVAL EVALUATION GATE")
        suite_label = (
            f"{self.suite_name} ({self.suite_path})" if self.suite_name else self.suite_path
        )
        terminalreporter.write_line(
            f"Suite: {suite_label} (threshold: {self.threshold:.4f}, "
            f"invalid-ratio limit: {self.invalid_ratio_limit:.2f})"
        )

        if self.error is not None:
            terminalreporter.write_line(f"GATE ERROR (evaluation-side): {self.error}")
            return

        summary = self._summary()
        if self.result is None or summary is None:
            terminalreporter.write_line(
                "GATE FAILED (evaluation-side): evaluation produced no result"
            )
            return

        terminalreporter.write_line(
            f"Run {self.result.run_id} status={self.result.status} "
            f"tasks={summary.total_tasks} trials={summary.total_trials} "
            f"(valid={summary.valid_trials} invalid={summary.invalid_trials} "
            f"pending={summary.pending_trials})"
        )
        terminalreporter.write_line(
            "Pass@k: "
            + ", ".join(
                f"@{k}={_fmt_rate(v)}{'' if not self._extrapolated(k) else '*'}"
                for k, v in sorted(_int_key_items(summary.pass_at_k))
            )
            + ("   (* = extrapolated beyond the measured sample)"
               if any(self._extrapolated(k) for k, _ in _int_key_items(summary.pass_at_k))
               else "")
        )
        ci = self._pass1_interval()
        if ci is not None:
            terminalreporter.write_line(
                f"pass@1 95% CI: [{ci[0]:.4f}, {ci[1]:.4f}] (denominator: "
                f"{summary.valid_trials} valid trials)"
            )

        if self.harness_failed:
            ratio = self._invalid_ratio()
            terminalreporter.write_line(
                f"GATE FAILED (evaluation-side, not agent performance): "
                f"invalid trial ratio {ratio:.4f} > limit {self.invalid_ratio_limit:.2f} — "
                "check the graders/judge configuration before reading any score"
            )
        elif self.insufficient:
            terminalreporter.write_line(
                "GATE FAILED (insufficient evidence): pass@1 has no valid trial "
                "in its denominator (all trials invalid or awaiting human scoring)"
            )
        else:
            pass1 = self._pass_at_1()
            if self.failed:
                terminalreporter.write_line(
                    f"GATE FAILED: pass@1 {pass1:.4f} < threshold {self.threshold:.4f}"
                )
            else:
                terminalreporter.write_line(
                    f"GATE PASSED: pass@1 {pass1:.4f} >= threshold {self.threshold:.4f}"
                )

        if self.baseline_run_id is not None:
            # 基线相对门独立成块: 与绝对阈值门任一失败即失败, 输出点名是哪个门
            terminalreporter.write_line("")
            terminalreporter.write_line(
                f"Baseline gate (--eval-baseline {self.baseline_run_id}):"
            )
            if self.baseline_error is not None:
                terminalreporter.write_line(f"BASELINE GATE ERROR: {self.baseline_error}")
            elif self.baseline_comparison is not None:
                for line in format_baseline_report(self.baseline_comparison):
                    terminalreporter.write_line(f"  {line}")
                verdict = self.baseline_comparison.verdict
                if self.baseline_failed:
                    terminalreporter.write_line(
                        f"BASELINE GATE FAILED ({verdict.value}) — relative regression "
                        "gate did not pass (worse / not comparable / undecidable)"
                    )
                else:
                    terminalreporter.write_line(
                        f"BASELINE GATE PASSED ({verdict.value}) — no significant "
                        "regression against the baseline"
                    )

    def _extrapolated(self, k: int) -> bool:
        summary = self._summary()
        if summary is None:
            return False
        est = summary.estimates.get(k) or summary.estimates.get(str(k))
        return bool(est and est.extrapolated)

    def _pass1_interval(self) -> tuple[float, float] | None:
        summary = self._summary()
        if summary is None:
            return None
        est = summary.estimates.get(1) or summary.estimates.get("1")
        if est is None or est.p_lower_bound is None or est.p_upper_bound is None:
            return None
        return (est.p_lower_bound, est.p_upper_bound)


def _int_key_items(d: dict) -> list[tuple[int, float | None]]:
    items = []
    for k, v in (d or {}).items():
        try:
            items.append((int(k), None if v is None else float(v)))
        except (TypeError, ValueError):
            continue
    return items


def _fmt_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


_gate_plugin: SuiteGatePlugin | None = None


def pytest_addoption(parser) -> None:
    group = parser.getgroup("aeval", "Aeval evaluation framework")
    group.addoption(
        "--eval-suite",
        action="store",
        default=None,
        help="Aeval 评测 Suite YAML 路径 (常规测试跑完后执行 suite 门禁)",
    )
    group.addoption(
        "--eval-threshold",
        action="store",
        type=float,
        default=0.7,
        help="门禁通过阈值 (修正后的实测 pass@1, 默认 0.7)",
    )
    group.addoption(
        "--eval-invalid-limit",
        action="store",
        type=float,
        default=DEFAULT_INVALID_RATIO_LIMIT,
        help=(
            "可接受的 invalid trial 占比上限 (超过则判评测本身不可信, 退出码非 0; "
            f"默认 {DEFAULT_INVALID_RATIO_LIMIT})"
        ),
    )
    group.addoption(
        "--eval-baseline",
        action="store",
        default=None,
        help=(
            "基线 run ID (评测 runner storage 中已落盘): suite 评测完成后与其做基线"
            "比较, 显著变差/不可比/不可判都置 session.testsfailed (宁可红不可哑); "
            "terminal summary 打印基线比较结论。与 --eval-threshold 可并用, "
            "任一门失败即失败且输出点名触发的是哪个门"
        ),
    )


def pytest_configure(config) -> None:
    global _gate_plugin
    suite_path = config.getoption("eval_suite")
    if suite_path:
        _gate_plugin = SuiteGatePlugin(
            suite_path,
            float(config.getoption("eval_threshold")),
            float(config.getoption("eval_invalid_limit")),
            baseline_run_id=config.getoption("eval_baseline"),
        )
        config.pluginmanager.register(_gate_plugin)
