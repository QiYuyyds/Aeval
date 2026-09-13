"""
eval-suite — the Aeval command line.

Commands:
    run        Execute a suite (default runner: built-in MockAgentRunner).
               Exit 0 all passed / 1 failed tasks or baseline regression /
               3 evaluation untrustworthy. --baseline adds a relative
               regression gate against a stored run
    validate   Validate a suite without running it
    list       List runs or suites from the storage DB
    show       Show one run's details (--task drills into a single task);
               reports valid/invalid/pending counts, pass@1 95% CI and
               extrapolation markers
    compare    A/B compare two runs; overlapping 95% intervals read as
               "not significant" with no directional verdict, and differing
               statistics versions or evidence boundaries read as not comparable
    power      Sample-size planning: trials needed for a target resolution
               (--delta with an assumed or measured baseline pass rate)
    serve      Serve the standalone API (/v1) via uvicorn

Source forms (run/validate, spec: suite-distribution): a single suite.yaml
file (status quo); a pack directory or .tar.gz/.zip archive (integrity-checked
against manifest.json); a git URL (shallow clone into a temp dir — needs the
git executable); or the literal "demo" (the built-in starter pack shipped
inside the wheel; a local file/dir named demo wins with a hint). Temp dirs are
cleaned up when the command ends.

Holdout (run/validate): tasks marked holdout: true are excluded by default
and the skip count is reported; --include-holdout runs them. validate reports
the holdout count without running anything.

Runner selection (run): --runner option > AEVAL_RUNNER env var > "mock"
(forced "mock" for the built-in demo pack unless --runner is explicit).
Custom runners register via the "agent_eval.runners" entry-point group
(name → zero-arg factory returning an AgentRunner).

Trace vocabulary (run): --vocabulary option > AEVAL_TRACE_VOCABULARY env var >
"otel-genai". The value selects a built-in public-convention preset
(agent_eval.trace.known_vocabularies()); host-private attribute names still go
in through default_mapping(extra_entries) in library code, never through the CLI.

Storage (list/show/compare and run persistence): SQLite, ./aeval.db by
default; override with --db or the AEVAL_DB environment variable.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path

import typer

from agent_eval.core.types import DEFAULT_INVALID_RATIO_LIMIT
from agent_eval.trace.mapping import (
    VOCABULARY_OTEL_GENAI,
    VOCABULARY_SPEC_VERSIONS,
    known_vocabularies,
)

DEFAULT_DB = "./aeval.db"
RUNNERS_ENTRY_POINT_GROUP = "agent_eval.runners"

# ⑤: 扩展点 kind → 描述 (CLI 清单与装配共用; 发现逻辑在 core/discovery.py)
_EXTENSION_KIND_LABELS = {
    "graders": "自定义评分器",
    "environments": "环境管理器",
    "simulators": "用户模拟器",
}

app = typer.Typer(
    help="Aeval — agent evaluation framework (https://github.com/QiYuyyds/Aeval)",
    no_args_is_help=True,
    add_completion=False,
)

LINE = "─" * 56


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _db_path(db: str | None) -> str:
    return db or os.environ.get("AEVAL_DB") or DEFAULT_DB


def _format_ts(ms: float | None) -> str:
    if not ms:
        return "-"
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


INSUFFICIENT_LABEL = "insufficient_data"

# 词汇预设的展示形态: 名字 + 各自钉住的规范修订号 (选词汇 = 选读证据的口径)
VOCABULARY_CHOICES = ", ".join(
    f"{name} (spec {VOCABULARY_SPEC_VERSIONS[name]})" for name in known_vocabularies()
)


def _k_display(d: dict) -> list[tuple[int, float | None]]:
    """pass@k / pass^k dict → 排序后的 (k, rate) 列表 (容错 str key 与空值)。"""
    items = []
    for k, v in (d or {}).items():
        try:
            items.append((int(k), None if v is None else float(v)))
        except (TypeError, ValueError):
            continue
    return sorted(items)


def _estimate(summary, field: str, k: int):
    """取某个 k 的估计元数据 (容错 str key / 历史 run 缺字段)。"""
    estimates = (getattr(summary, field, None) or {}) if summary else {}
    est = estimates.get(k)
    if est is None:
        est = estimates.get(str(k))
    return est


def _rate_pct(rate: float | None) -> str:
    if rate is None:
        return INSUFFICIENT_LABEL
    return f"{rate * 100:.1f}%"


def _rate_with_ci(summary, field: str, k: int, rate: float | None) -> str:
    """数值 + 外推标注 + 95% 区间 (无有效样本时只呈现证据不足)。"""
    if rate is None:
        return INSUFFICIENT_LABEL
    est = _estimate(summary, field, k)
    text = f"{rate * 100:.1f}%"
    if est is not None and getattr(est, "extrapolated", False):
        text += " (extrapolated)"
    low = getattr(est, "p_lower_bound", None) if est is not None else None
    high = getattr(est, "p_upper_bound", None) if est is not None else None
    if low is not None and high is not None:
        text += f"  [95% CI {low * 100:.1f}%..{high * 100:.1f}%]"
    return text


def _denominator_line(summary) -> str:
    """分母三态计数; 历史 run 缺字段时呈现 unknown。"""
    def _n(value) -> str:
        return "unknown" if value is None else str(value)

    return (
        f"  Denominator: valid={_n(summary.valid_trials)} "
        f"invalid={_n(summary.invalid_trials)} "
        f"pending={_n(summary.pending_trials)}"
    )


def _evidence_line(run) -> str:
    """证据强度 + 可否重评分: 通过率相同的两个 run, 分量可能完全不同。"""
    from agent_eval.core.runner import regrade_state

    summary = run.summary
    mix = getattr(summary, "evidence_levels", None) or {}
    weak = getattr(summary, "subject_only_trials", 0) or 0
    text = "  Evidence: " + (
        "  ".join(f"{level}={count}" for level, count in sorted(mix.items()))
        if mix
        else "unknown (历史 run 未记录来源分级)"
    )
    if weak:
        text += f"  weak-evidence passes: {weak}"
    possible, reason = regrade_state(run)
    text += "\n  Regrade: " + ("available" if possible else f"no — {reason}")
    return text


def _pass1(summary_or_task) -> float | None:
    """实测 pass@1; 键缺失或值为 None 都算证据不足 (task 或 run 汇总通用)。"""
    rates = getattr(summary_or_task, "pass_at_k", None) or {}
    value = rates.get(1, rates.get("1"))
    return None if value is None else float(value)


def _num(value: float | None) -> str:
    """诊断分布数值; None = 无有效分值 (不冒充 0)。"""
    return "n/a" if value is None else f"{value:.4f}"


def _resolve_agent_runner(name: str | None):
    """解析 AgentRunner: --runner > AEVAL_RUNNER > 内置 mock。

    自定义注册: entry-point group "agent_eval.runners" (name → 零参工厂)。
    """
    resolved = name or os.environ.get("AEVAL_RUNNER") or "mock"

    if resolved == "mock":
        from agent_eval.examples.mock_runner import MockAgentRunner

        return MockAgentRunner(success_rate=1.0, latency_range=(0.0, 0.01))

    from importlib.metadata import entry_points

    eps = entry_points(group=RUNNERS_ENTRY_POINT_GROUP)
    ep = next((e for e in eps if e.name == resolved), None)
    if ep is None:
        registered = ", ".join(sorted(["mock", *(e.name for e in eps)]))
        typer.echo(
            f"error: unknown runner '{resolved}' (registered: {registered}).\n"
            f"Point --runner at a name published under the "
            f"'{RUNNERS_ENTRY_POINT_GROUP}' entry-point group, or set "
            f"AEVAL_RUNNER."
        )
        raise typer.Exit(code=2)

    factory = ep.load()
    return factory()


def _build_storage(db: str | None):
    from agent_eval.storage.sqlite import SqliteStorage

    return SqliteStorage(_db_path(db))


def _resolve_suite_source_arg(source: str) -> tuple[str, str | None]:
    """来源参数预解析: `demo` 指向内置 starter pack。

    `demo` 不是文件路径; 与本地同名文件/目录冲突时本地路径优先并给出提示
    (spec: cli 的 run demo 零 setup 语义)。
    """
    if source != "demo":
        return source, None
    if Path("demo").exists():
        return source, (
            "hint: 当前目录存在名为 'demo' 的本地路径, 已优先使用它 "
            "(内置示例 pack 只在没有同名本地路径时启用)"
        )
    from agent_eval.core.packaging import builtin_pack_dir

    return str(builtin_pack_dir("starter")), None


def _discover_or_exit():
    """发现扩展点; 同名冲突直接退出 (不静默覆盖, spec: extension-contracts)。"""
    from agent_eval.core.discovery import ExtensionConflictError, discover_extensions

    try:
        return discover_extensions()
    except ExtensionConflictError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=2) from None


def _resolve_environment(suite, registry):
    """按套件声明的环境名装配环境管理器 (惰性导入, 引用不到即报错)。"""
    if not suite.environment:
        return None
    environment, failure = registry.resolve("environments", suite.environment)
    if environment is None:
        available = registry.names("environments") or ["(无)"]
        detail = f" (导入失败: {failure})" if failure else ""
        typer.echo(
            f"error: suite.environment '{suite.environment}' 未注册{detail}; "
            f"可用环境: {available}。环境经 'agent_eval.environments' "
            "entry-point 组注册",
            err=True,
        )
        raise typer.Exit(code=2)
    return environment


def _trace_provider_for(agent_runner):
    """CLI 默认离线: 内置 mock trace; 注册的 runner 可暴露自己的 provider。"""
    if agent_runner is not None and hasattr(agent_runner, "trace_provider"):
        return agent_runner.trace_provider
    from agent_eval.examples.mock_runner import MockTraceProvider

    return MockTraceProvider()


def _print_diagnostics_and_agreement(summary, verbose: bool) -> None:
    """诊断块与跨评分者一致性的分块呈现 (run 与 show 共用, 不与判分量混排)。

    诊断量不进分母, 默认折叠为一行, --verbose 展开。
    """
    diagnostics = getattr(summary, "diagnostic_metrics", None) or []
    if diagnostics:
        if verbose:
            typer.echo("")
            typer.echo("  Diagnostics (not in any denominator):")
            for d in diagnostics:
                typer.echo(
                    f"    - {d.name}: n={d.n} avg={_num(d.avg)} min={_num(d.min)} "
                    f"max={_num(d.max)} p50={_num(d.p50)}"
                    + (f" errors={d.errors}" if d.errors else "")
                )
        else:
            typer.echo(
                f"  Diagnostics: {len(diagnostics)} metric(s) "
                "(not in any denominator; use --verbose to expand)"
            )
    agreement = getattr(summary, "agreement", None) or {}
    if agreement:
        typer.echo("")
        typer.echo(
            "  Inter-rater agreement (single-rater multi-sample confidence is "
            "self-consistency, not inter-rater reliability):"
        )
        for name, report in agreement.items():
            if report.value is None:
                typer.echo(f"    - {name}: 不可计算 — {report.reason or 'n/a'}")
                continue
            pair = (
                f"  agree/disagree: {report.agree}/{report.disagree}"
                if report.agree is not None
                else ""
            )
            typer.echo(
                f"    - {name}: {report.measure} = {report.value:.4f}{pair}"
            )


def _print_run_summary(run, verbose: bool = False) -> None:
    """§11.2 形态的汇总输出 (无 emoji, 兼容非 UTF-8 终端)。

    诊断块默认折叠为一行 (诊断量不进分母, 也不抢判分量的视线); --verbose 展开。
    """
    summary = run.summary
    typer.echo(LINE)
    typer.echo("Results Summary")
    typer.echo(LINE)
    duration = run.duration_ms
    typer.echo(
        f"  Run: {run.run_id}  Status: {run.status}"
        + (f"  Duration: {duration / 1000:.1f}s" if duration else "")
    )
    typer.echo(f"  Statistics version: {run.statistics_version or 'unknown'}")
    if getattr(run, "canary_guid", None):
        typer.echo(f"  Canary GUID: {run.canary_guid}")
    typer.echo(_evidence_line(run))
    for k, rate in _k_display(summary.pass_at_k):
        typer.echo(f"  Pass@{k}:  {_rate_with_ci(summary, 'estimates', k, rate)}")
    for k, rate in _k_display(summary.pass_power_k):
        typer.echo(f"  Pass^{k}:  {_rate_with_ci(summary, 'power_estimates', k, rate)}")
    typer.echo(_denominator_line(summary))
    dist = summary.score_distribution
    if dist is not None and dist.mean is not None:
        typer.echo(
            f"  Avg Score: {dist.mean:.4f}"
            + (
                f"  [95% CI {dist.ci_low:.4f}..{dist.ci_high:.4f}]"
                if dist.ci_low is not None and dist.ci_high is not None
                else ""
            )
            + (f"  worst_of_n: {dist.worst_of_n:.4f}" if dist.worst_of_n is not None else "")
        )
    else:
        typer.echo(f"  Avg Score: {INSUFFICIENT_LABEL}")
    typer.echo(f"  Tasks: {summary.total_tasks}  Trials: {summary.total_trials}")

    # 诊断块 (折叠/展开) 与跨评分者一致性分块呈现, 不与判分量混排
    _print_diagnostics_and_agreement(summary, verbose)

    if summary.failures:
        typer.echo("")
        typer.echo("  Failures:")
        for ts in summary.task_summaries:
            if ts.task_id in summary.failures:
                valid = ts.total_trials if ts.valid_trials is None else ts.valid_trials
                passed = valid - len(ts.failures)
                typer.echo(f"    - {ts.task_id}: {passed}/{valid} valid trials passed")

    invalid_tasks = [ts for ts in summary.task_summaries if (ts.invalid_trials or 0) > 0]
    if invalid_tasks:
        typer.echo("")
        typer.echo("  Evaluation-side invalid trials (not counted in any pass rate):")
        for ts in invalid_tasks:
            reasons = ", ".join(
                f"{reason} x{count}"
                for reason, count in sorted(
                    _reason_counts(ts.invalid_reasons).items()
                )
            )
            typer.echo(
                f"    - {ts.task_id}: {ts.invalid_trials} invalid"
                + (f" ({reasons})" if reasons else "")
            )
    typer.echo(LINE)


def _reason_counts(reasons: dict) -> dict[str, int]:
    """{trial 索引: 原因} → {原因: 次数}。"""
    counts: dict[str, int] = {}
    for reason in (reasons or {}).values():
        counts[reason] = counts.get(reason, 0) + 1
    return counts


# ─── run ─────────────────────────────────────────────────────────────────────


@app.command()
def run(
    suite_path: str = typer.Argument(
        ...,
        help=(
            "套件来源: suite.yaml 文件 / pack 目录或 .tar.gz .zip 压缩包 / "
            "git URL / demo (内置示例 pack)"
        ),
    ),
    trials: int | None = typer.Option(None, "--trials", help="覆盖每个任务的 trial 数"),
    concurrency: int | None = typer.Option(
        None, "--concurrency", min=1, help="trial 并发数 (默认串行)"
    ),
    runner: str | None = typer.Option(
        None,
        "--runner",
        help=(
            "AgentRunner 名称 (内置 mock, 或 agent_eval.runners entry-point 注册名); "
            "缺省读 AEVAL_RUNNER 环境变量, 再缺省为 mock"
        ),
    ),
    vocabulary: str = typer.Option(
        VOCABULARY_OTEL_GENAI,
        "--vocabulary",
        envvar="AEVAL_TRACE_VOCABULARY",
        help=f"trace 属性词汇预设 (公共埋点约定): {VOCABULARY_CHOICES}",
    ),
    db: str | None = typer.Option(
        None, "--db", envvar="AEVAL_DB", help="SQLite 结果库路径 (默认 ./aeval.db)"
    ),
    invalid_limit: float = typer.Option(
        DEFAULT_INVALID_RATIO_LIMIT,
        "--invalid-limit",
        min=0.0,
        max=1.0,
        help=(
            "可接受的 invalid trial 占比上限; 超过则以退出码 3 结束 "
            "(评测可信度问题, 非 agent 表现)"
        ),
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="展开诊断指标块 (默认折叠: 诊断量不进分母, 一行提示)",
    ),
    baseline: str | None = typer.Option(
        None,
        "--baseline",
        help=(
            "基线 run ID (同一结果库中已落盘的 run): 本次 run 完成后与其做基线比较。"
            "显著变差 / 不可比 / 证据不足都以非零退出码结束 (宁可红不可哑); "
            "区间重叠或优于基线 → 退出码 0"
        ),
    ),
    include_holdout: bool = typer.Option(
        False,
        "--include-holdout",
        help=(
            "放行 holdout 任务 (默认排除并在开始前报告跳过数; "
            "私有保留集只在显式放行时运行)"
        ),
    ),
) -> None:
    """加载并执行 suite, 打印汇总。

    退出码: 0 全通过且基线门 (如启用) 未触发; 1 存在未通过任务或基线门判
    显著变差/不可比/不可判; 2 用法错误; 3 评测本身不可信 (invalid 超阈或
    关键统计量为 insufficient_data)。
    """
    from agent_eval.core.packaging import PackError, resolve_source
    from agent_eval.core.runner import EvalRunner, NoRunnableTasksError
    from agent_eval.core.suite import SuiteLoadError, load_suite
    from agent_eval.trace.mapping import default_mapping

    source_arg, demo_hint = _resolve_suite_source_arg(suite_path)
    if demo_hint:
        typer.echo(demo_hint)
    is_demo = suite_path == "demo"

    try:
        source = resolve_source(source_arg)
    except PackError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1) from None

    try:
        try:
            suite = load_suite(source.suite_path)
        except SuiteLoadError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(code=1) from None

        if trials is not None and trials < 1:
            typer.echo("error: --trials must be >= 1", err=True)
            raise typer.Exit(code=2)

        # 装配期就把词汇定死: 拼错的预设必须失败, 静默退回默认表会跑出一整轮
        # 「看似正常、实则全是证据缺失」的观测, 比直接报错难查得多。
        try:
            trace_mapping = default_mapping(vocabulary=vocabulary)
        except ValueError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(code=2) from None

        runnable_tasks = [
            t for t in suite.tasks if include_holdout or not t.holdout
        ]
        holdout_count = len(suite.tasks) - len(runnable_tasks)

        # demo 的承诺是「装完即跑出完整报告」: 未显式指 --runner 时强制 mock,
        # 不吃 AEVAL_RUNNER 环境变量 (显式旗标仍然生效)
        resolved_runner = (
            runner if runner is not None else ("mock" if is_demo else None)
        )

        if runnable_tasks:
            max_trials = trials or max(t.max_trials for t in runnable_tasks)
            typer.echo(
                f"Starting eval run: {suite.name} v{suite.version} "
                f"({len(runnable_tasks)} tasks, up to {max_trials} trials each)"
            )
            if holdout_count:
                typer.echo(
                    f"跳过 {holdout_count} 个 holdout 任务 "
                    "(默认排除; 用 --include-holdout 放行)"
                )
            if source.manifest is not None:
                typer.echo(
                    f"Source: pack '{source.pack_name}' "
                    f"(manifest 校验通过, {len(source.manifest['files'])} 个文件)"
                )
            typer.echo(
                f"Trace vocabulary: {vocabulary}  spec={trace_mapping.spec_version}  "
                f"mapping={trace_mapping.version}"
            )

        agent_runner = _resolve_agent_runner(resolved_runner)
        storage = _build_storage(db)
        registry = _discover_or_exit()
        environment = _resolve_environment(suite, registry)

        # 内置指标注册表同源装配 (与 metric grader / 诊断指标同一注入约定):
        # llm_fn 未配置时被引用的 LLM 指标返回明确配置错误, 不 crash run
        from agent_eval.metrics import build_default_metrics_registry

        eval_runner = EvalRunner(
            agent_runner=agent_runner,
            trace_provider=_trace_provider_for(agent_runner),
            storage=storage,
            trace_mapping=trace_mapping,
            metrics_registry=build_default_metrics_registry(),
            environment=environment,
            extensions=registry,
            **({"concurrency": concurrency} if concurrency else {}),
        )

        async def _execute():
            await storage.initialize()
            counter = {"n": 0}

            async def _progress(event: str, data: dict) -> None:
                if event == "task_complete":
                    counter["n"] += 1
                    total = data.get("trials", 0)
                    rate = data.get("pass_rate")
                    if rate is None:
                        detail = (
                            f"{data.get('valid_trials', 0)}/{total} valid trials "
                            f"(invalid={data.get('invalid_trials', 0)}, "
                            f"pending={data.get('pending_trials', 0)}) "
                            "-> insufficient evidence"
                        )
                    else:
                        valid = data.get("valid_trials", total)
                        detail = f"{round(rate * valid)}/{valid} valid trials passed"
                    typer.echo(f"  [{counter['n']}/{len(runnable_tasks)}] "
                               f"{data.get('task_id', '?')}: {detail}")

            return await eval_runner.run_suite(
                suite, callback=_progress, include_holdout=include_holdout
            )

        try:
            run_result = asyncio.run(_execute())
        except NoRunnableTasksError as e:
            typer.echo(f"error: {e}", err=True)
            raise typer.Exit(code=1) from None

        if run_result.status != "completed":
            typer.echo(f"error: run ended with status '{run_result.status}': "
                       f"{run_result.error}", err=True)
            raise typer.Exit(code=1)

        _print_run_summary(run_result, verbose=verbose)

        summary = run_result.summary
        if summary is None:
            raise typer.Exit(code=1)

        # 基线相对门 (opt-in): 与绝对阈值门同量 (套件实测 pass@1), 同一比较语义
        # (core.comparison.compare_baseline — 与 API compare / pytest 插件三处同源)。
        # 诊断块先行打印; 门判定在可信度与任务失败之后 (失败原因要能叠加呈现)。
        baseline_gate_failed = False
        if baseline is not None:
            from agent_eval.core.comparison import (
                BASELINE_GATE_FAILURES,
                compare_baseline,
                format_baseline_report,
            )

            baseline_run = asyncio.run(_get_run(storage, baseline))
            if baseline_run is None:
                typer.echo(
                    f"error: baseline run '{baseline}' not found in {_db_path(db)}", err=True
                )
                raise typer.Exit(code=2)

            comparison = compare_baseline(run_result, baseline_run)
            typer.echo("")
            for line in format_baseline_report(comparison):
                typer.echo(line)
            if comparison.verdict in BASELINE_GATE_FAILURES:
                baseline_gate_failed = True

        # 评测可信度条件先于 agent 表现结论 (不可信的分数不参与放行判定)
        reliability_problems: list[str] = []
        if summary.total_trials:
            ratio = (summary.invalid_trials or 0) / summary.total_trials
            if ratio > invalid_limit:
                reliability_problems.append(
                    f"invalid trial ratio {ratio:.2f} exceeds --invalid-limit {invalid_limit:.2f}"
                )
        for ts in summary.task_summaries:
            if _pass1(ts) is None:
                reliability_problems.append(
                    f"task '{ts.task_id}': pass@1 is {INSUFFICIENT_LABEL} "
                    f"(valid={ts.valid_trials}, invalid={ts.invalid_trials}, "
                    f"pending={len(ts.pending_trials)})"
                )
        if _pass1(summary) is None:
            reliability_problems.append(f"run-level pass@1 is {INSUFFICIENT_LABEL}")

        if reliability_problems:
            typer.echo("")
            typer.echo(
                "NOT PASSABLE - evaluation reliability problem (not an agent performance result):"
            )
            for problem in reliability_problems:
                typer.echo(f"  - {problem}")
            typer.echo(
                "  Check the grader/judge configuration; these trials produced no valid verdict."
            )
            raise typer.Exit(code=3)

        if summary.failures:
            raise typer.Exit(code=1)

        if baseline_gate_failed:
            # 显著变差 / 不可比 / 不可判: 基线门不放行 (宁可红不可哑)
            raise typer.Exit(code=1)
    finally:
        # git clone / 压缩包解包的临时目录用后即清 (本地文件/目录来源为空操作)
        source.cleanup()


# ─── validate ────────────────────────────────────────────────────────────────


@app.command()
def validate(
    suite_path: str = typer.Argument(
        ...,
        help=(
            "套件来源: suite.yaml 文件 / pack 目录或 .tar.gz .zip 压缩包 / "
            "git URL (pack 形态执行与 run 相同的 manifest 校验)"
        ),
    ),
) -> None:
    """只做加载校验: 输出结论, 校验失败退出码非 0。"""
    from agent_eval.core.packaging import PackError, resolve_source
    from agent_eval.core.suite import SuiteLoadError, load_suite

    try:
        source = resolve_source(suite_path)
    except PackError as e:
        typer.echo(f"INVALID: {e}", err=True)
        raise typer.Exit(code=1) from None

    try:
        try:
            suite = load_suite(source.suite_path)
        except SuiteLoadError as e:
            typer.echo(f"INVALID: {e}", err=True)
            raise typer.Exit(code=1) from None

        typer.echo(
            f"VALID: {suite.name} v{suite.version} — {len(suite.tasks)} task(s), "
            f"{sum(len(t.graders) for t in suite.tasks)} grader config(s)"
        )
        holdout_count = sum(1 for t in suite.tasks if t.holdout)
        if holdout_count:
            typer.echo(
                f"注意: 含 {holdout_count} 个 holdout 任务, 默认运行将排除 "
                "(--include-holdout 放行)"
            )
    finally:
        source.cleanup()


# ─── list ────────────────────────────────────────────────────────────────────


@app.command(name="list")
def list_cmd(
    kind: str = typer.Argument(..., help="runs | suites"),
    db: str | None = typer.Option(
        None, "--db", envvar="AEVAL_DB", help="SQLite 结果库路径 (默认 ./aeval.db)"
    ),
    limit: int = typer.Option(50, "--limit", min=1, help="runs 列表条数上限"),
) -> None:
    """列出运行历史 (runs) 或套件清单 (suites)。"""
    from agent_eval.storage.sqlite import SqliteStorage

    kind = kind.strip().lower()
    if kind not in ("runs", "suites"):
        typer.echo("error: kind must be 'runs' or 'suites'", err=True)
        raise typer.Exit(code=2)

    storage = SqliteStorage(_db_path(db))

    async def _query():
        await storage.initialize()
        if kind == "runs":
            return await storage.list_runs(limit=limit)
        return await storage.list_suites()

    rows = asyncio.run(_query())

    if not rows:
        typer.echo(f"No {kind} found in {_db_path(db)}")
        return

    if kind == "runs":
        typer.echo(f"{'RUN ID':<20} {'SUITE':<24} {'STATUS':<10} STARTED")
        for r in rows:
            typer.echo(
                f"{r.run_id:<20} {r.suite_name:<24} {r.status:<10} "
                f"{_format_ts(r.started_at)}"
            )
    else:
        typer.echo(f"{'NAME':<32} {'VERSION':<10} TASKS  DESCRIPTION")
        for s in rows:
            typer.echo(
                f"{s.name:<32} {s.version:<10} {len(s.tasks):<6} {s.description}"
            )


# ─── show ────────────────────────────────────────────────────────────────────


@app.command()
def show(
    run_id: str = typer.Argument(..., help="Run ID"),
    task: str | None = typer.Option(
        None, "--task", help="下钻单个任务: 输出逐 trial 明细"
    ),
    db: str | None = typer.Option(
        None, "--db", envvar="AEVAL_DB", help="SQLite 结果库路径 (默认 ./aeval.db)"
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="展开诊断块 (默认折叠为一行)",
    ),
) -> None:
    """输出 run 详情; --task 下钻单任务。"""
    storage = _build_storage(db)
    run = asyncio.run(_get_run(storage, run_id))

    if run is None:
        typer.echo(f"error: run '{run_id}' not found in {_db_path(db)}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Run: {run.run_id}  Suite: {run.suite_name}  Status: {run.status}")
    typer.echo(f"Started: {_format_ts(run.started_at)}  "
               f"Completed: {_format_ts(run.completed_at)}")

    summary = run.summary
    if summary is None:
        typer.echo("(no summary — run did not complete)")
        if run.error:
            typer.echo(f"Error: {run.error}")
        return

    typer.echo(f"Statistics version: {run.statistics_version or 'unknown'}")
    if getattr(run, "canary_guid", None):
        typer.echo(f"Canary GUID: {run.canary_guid}")
    typer.echo(_evidence_line(run))
    for k, rate in _k_display(summary.pass_at_k):
        typer.echo(f"  Pass@{k}:  {_rate_with_ci(summary, 'estimates', k, rate)}")
    for k, rate in _k_display(summary.pass_power_k):
        typer.echo(f"  Pass^{k}:  {_rate_with_ci(summary, 'power_estimates', k, rate)}")
    typer.echo(_denominator_line(summary))
    dist = summary.score_distribution
    if dist is not None and dist.mean is not None:
        typer.echo(
            f"  Avg Score: {dist.mean:.4f}"
            + (
                f"  [95% CI {dist.ci_low:.4f}..{dist.ci_high:.4f}]"
                if dist.ci_low is not None and dist.ci_high is not None
                else ""
            )
        )
    else:
        typer.echo(f"  Avg Score: {INSUFFICIENT_LABEL}")

    _print_diagnostics_and_agreement(summary, verbose)

    typer.echo("")
    typer.echo(
        f"{'TASK':<24} {'VALID':<8} {'INVALID':<8} {'PENDING':<8} "
        f"{'PASS@1':<28} {'AVG':<8} RESULT"
    )
    for ts in summary.task_summaries:
        pending = len(ts.pending_trials)
        valid = "-" if ts.valid_trials is None else str(ts.valid_trials)
        invalid = "-" if ts.invalid_trials is None else str(ts.invalid_trials)
        pass1 = _rate_with_ci(ts, "estimates", 1, _pass1(ts))
        avg = INSUFFICIENT_LABEL if ts.avg_score is None else f"{ts.avg_score:.4f}"
        if ts.invalid_trials and ts.valid_trials == 0:
            result = "NO-VERDICT"
        else:
            result = "PASS" if ts.task_id not in summary.failures else "FAIL"
        typer.echo(
            f"{ts.task_id:<24} {valid:<8} {invalid:<8} {pending:<8} "
            f"{pass1:<28} {avg:<8} {result}"
        )

    if task is not None:
        trials = run.trials.get(task)
        if trials is None:
            typer.echo(f"error: task '{task}' not in run {run.run_id}", err=True)
            raise typer.Exit(code=1)
        typer.echo("")
        typer.echo(f"Task '{task}' — {len(trials)} trial(s):")

        def _verdict_text(obj) -> str:
            verdict = obj.verdict.value if obj.verdict else "valid"
            if obj.invalid_reason is not None:
                verdict += f"/{obj.invalid_reason.value}"
            return verdict

        for t in trials:
            typer.echo(
                f"  trial {t.trial_index}: {'PASS' if t.success else 'FAIL'} "
                f"[verdict {_verdict_text(t)}] "
                + (
                    f"[evidence {t.weakest_evidence.value}]"
                    if t.weakest_evidence is not None
                    else "[evidence unknown]"
                )
                + f"(score {t.total_score():.4f}, {t.duration_ms:.0f}ms"
                + (f", error: {t.error}" if t.error else "") + ")"
            )
            for gr in t.grader_results:
                weak = " [WEAK: subject-only]" if gr.subject_only else ""
                typer.echo(
                    f"    - {gr.grader_name} [{gr.grader_type.value}]: "
                    f"{gr.score:.4f} {'passed' if gr.passed else 'FAILED'}"
                    f" verdict={_verdict_text(gr)}"
                    + (
                        f" observed_by={','.join(level.value for level in gr.evidence_levels)}"
                        if gr.evidence_levels
                        else ""
                    )
                    + (
                        f" moment={gr.judgment_moment.value}"
                        if gr.judgment_moment is not None
                        else ""
                    )
                    + weak
                    + (
                        f" [gate factor={gr.gate_factor:g} "
                        f"{'已塌缩' if gr.gate_applied else '未乘入'}]"
                        if gr.gate_factor is not None
                        else ""
                    )
                )
                if gr.explanation:
                    typer.echo(f"      {gr.explanation}")
                if gr.gate_reason:
                    typer.echo(f"      gate: {gr.gate_reason}")


async def _get_run(storage, run_id: str):
    await storage.initialize()
    return await storage.get_run(run_id)


# ─── compare ─────────────────────────────────────────────────────────────────


@app.command()
def compare(
    run_a: str = typer.Argument(..., help="基准 run ID (A)"),
    run_b: str = typer.Argument(..., help="对比 run ID (B)"),
    db: str | None = typer.Option(
        None, "--db", envvar="AEVAL_DB", help="SQLite 结果库路径 (默认 ./aeval.db)"
    ),
) -> None:
    """输出两 run 的指标 delta 与退化/提升任务清单。"""
    storage = _build_storage(db)

    async def _load():
        await storage.initialize()
        return await storage.get_run(run_a), await storage.get_run(run_b)

    a, b = asyncio.run(_load())
    if a is None:
        typer.echo(f"error: run '{run_a}' not found", err=True)
        raise typer.Exit(code=1)
    if b is None:
        typer.echo(f"error: run '{run_b}' not found", err=True)
        raise typer.Exit(code=1)
    if not a.summary or not b.summary:
        typer.echo("error: both runs must be completed to compare", err=True)
        raise typer.Exit(code=1)

    # 与 API 同语义 (agent_eval.api.routes.runs._build_comparison)
    from agent_eval.api.routes.runs import _build_comparison

    comparison = _build_comparison(a, b)

    typer.echo(f"Comparing: {run_a} (A)  vs  {run_b} (B)")
    versions = comparison["statistics_version"]
    typer.echo(
        f"Statistics version: A={versions['a'] or 'unknown'}  "
        f"B={versions['b'] or 'unknown'}"
    )
    boundaries = comparison["evidence_boundary"]
    typer.echo(
        f"Evidence boundary:  A={_cmp_boundary(boundaries['a'])}  "
        f"B={_cmp_boundary(boundaries['b'])}"
    )
    if not comparison["comparable"]:
        typer.echo(LINE)
        typer.echo(f"NOT COMPARABLE: {comparison['not_comparable_reason']}")
        typer.echo(
            "  两个 run 的统计口径或证据边界不同, 以下差值不构成任何方向性结论。"
        )
    typer.echo(LINE)
    typer.echo(f"{'METRIC':<16} {'A':>8} {'B':>8} {'DELTA':>9}  VERDICT")
    metric_rows: dict[str, dict] = {}
    metric_rows.update(comparison["pass_at_k"])
    metric_rows.update(comparison["pass_power_k"])
    metric_rows["Avg Score"] = comparison["avg_score"]
    for label in sorted(
        metric_rows, key=lambda k: (k not in ("Avg Score",), k)
    ):
        entry = metric_rows[label]
        display = label.replace("pass_at_", "Pass@").replace("pass_power_", "Pass^")
        typer.echo(
            f"{display:<16} {_cmp_num(entry['a']):>8} {_cmp_num(entry['b']):>8} "
            f"{_cmp_delta(entry['delta']):>9}  {_cmp_verdict(entry, comparison)}"
        )

    for label, key, mark in (
        ("Regressions", "regressions", "-"),
        ("Improvements", "improvements", "+"),
    ):
        typer.echo("")
        typer.echo(f"{label}:")
        items = comparison[key]
        if not items:
            reason = (
                " (runs not comparable)"
                if not comparison["comparable"]
                else " (no significant difference)"
            )
            typer.echo(f"  (none){reason}")
        for it in items:
            typer.echo(
                f"  {mark} {it['task_id']}: {it['a']:.4f} -> {it['b']:.4f} "
                f"(delta {it['delta']:+.4f})"
            )


def _cmp_boundary(boundary: dict | None) -> str:
    """一行呈现 run 的证据边界; 历史 run 没有记录就说没有记录。"""
    if not boundary:
        return "未记录 (历史 run)"
    return (
        f"capture_args={'on' if boundary['capture_tool_arguments'] else 'off'}"
        f" spec={boundary['spec_version'] or '?'}"
        f" mapping={boundary['mapping_version'] or '?'}"
        f" redactor={boundary['redactor_identifier'] or '?'}"
    )


def _cmp_num(value: float | None) -> str:
    return INSUFFICIENT_LABEL if value is None else f"{value:.4f}"


def _cmp_delta(value: float | None) -> str:
    return "-" if value is None else f"{value:+.4f}"


def _cmp_verdict(entry: dict, comparison: dict) -> str:
    """一行差值的结论: 不可比 / 区间重叠不显著 / 显著。"""
    if not comparison["comparable"]:
        return "not comparable (different statistics caliber)"
    if entry.get("significant") is None:
        return "no interval available - significance undetermined"
    if not entry["significant"]:
        return "not significant (95% CI overlap)"
    extrap = " [extrapolated]" if entry.get("extrapolated") else ""
    return f"significant{extrap}"


# ─── power ───────────────────────────────────────────────────────────────────


def _power_line(label: str, value: str) -> str:
    return f"  {label:<44} {value}"


@app.command()
def power(
    delta: float = typer.Option(
        ...,
        "--delta",
        help=(
            "目标分辨率: 通过率 95% 区间的半宽 δ (0 < δ < 0.5)。与 --from-run 并用时"
            "兼作两版本分数差异 d"
        ),
    ),
    p: float | None = typer.Option(
        None,
        "--p",
        min=0.0,
        max=1.0,
        help="假设的基线通过率 (默认 0.5, 最保守); --from-run 存在时被实测值取代",
    ),
    from_run: str | None = typer.Option(
        None,
        "--from-run",
        help="从已落盘 run 取实测 pass@1 (p) 与分数标准差 (σ) 作为基线",
    ),
    db: str | None = typer.Option(
        None, "--db", envvar="AEVAL_DB", help="SQLite 结果库路径 (默认 ./aeval.db)"
    ),
) -> None:
    """样本量规划 (功效分析): 达到目标分辨率需要多少有效 trial。

    两种问法: --delta (假设基线 p, Wilson 区间宽度反解) 与
    --delta + --from-run (实测基线: p 走 Wilson 反解, 分数 σ 走双样本正态
    近似回答「分辨差异 d 需要 N」)。全部闭式公式, 输出自陈公式、假设与局限。
    退出码: 0 正常; 1 证据不足 (run 无有效样本, 不以 0/1 代算); 2 用法错误。
    """
    from agent_eval.core.metrics import normal_two_sample_size, wilson_sample_size

    if not 0.0 < delta < 0.5:
        typer.echo(f"error: --delta must be in (0, 0.5), got {delta}", err=True)
        raise typer.Exit(code=2)

    z = _z_score_display()

    typer.echo("Power analysis (sample size planning)")
    typer.echo(f"  Resolution: +/-{delta:.1%} half-width (delta)")

    measured_sigma: float | None = None
    sigma_note: str | None = None
    if from_run is not None:
        storage = _build_storage(db)
        run = asyncio.run(_get_run(storage, from_run))
        if run is None:
            typer.echo(
                f"error: run '{from_run}' not found in {_db_path(db)}", err=True
            )
            raise typer.Exit(code=1)
        summary = run.summary
        measured_p = _pass1(summary) if summary else None
        if measured_p is None:
            typer.echo(
                f"error: run '{from_run}' has no valid trials (pass@1 is "
                f"{INSUFFICIENT_LABEL}) — insufficient evidence for power analysis; "
                "refusing to plan with a substituted 0 or 1",
                err=True,
            )
            raise typer.Exit(code=1)
        dist = summary.score_distribution if summary else None
        measured_sigma = dist.std_dev if dist is not None else None
        valid = summary.valid_trials if summary else None
        typer.echo(
            f"  Run: {run.run_id}  status={run.status}  "
            f"valid={valid if valid is not None else 'unknown'}"
        )
        typer.echo(
            f"  Measured baseline: pass@1 = {measured_p:.1%}  (p taken from the run)"
        )
        baseline_p = measured_p
        if measured_sigma is None:
            sigma_note = (
                "insufficient_data (score axis needs >= 2 valid trials; "
                "historical runs may not have recorded one)"
            )
    else:
        baseline_p = p if p is not None else 0.5
        typer.echo(
            f"  Baseline: p = {baseline_p:.3f} (assumed; "
            + (
                "given via --p"
                if p is not None
                else "0.5 is the most conservative choice"
            )
            + ")"
        )

    n_rate = wilson_sample_size(baseline_p, delta)
    typer.echo(_power_line("Required trials (rate +/-delta):", f"N = {n_rate}"))
    typer.echo(
        f"  Formula (rate): Wilson 95% interval half-width w(p, N) <= delta "
        f"(z = {z} two-sided)"
    )

    if from_run is not None:
        if measured_sigma is not None:
            n_diff = normal_two_sample_size(delta, measured_sigma)
            typer.echo(
                f"  Measured score spread: sigma = {measured_sigma:.4f} "
                "(trial-to-trial SD of the weighted score)"
            )
            typer.echo(
                _power_line(
                    f"Required trials per version (difference d = {delta:g}):",
                    f"N = {n_diff}",
                )
            )
            typer.echo(
                f"  Formula (difference): n ~= 2 * (z_(alpha/2) * sigma / d)^2, "
                f"alpha = 0.05 (z = {z})"
            )
        else:
            typer.echo(f"  Measured score spread: {sigma_note}")
            typer.echo(
                "  Formula (difference): skipped — no measured sigma to plan with"
            )

    typer.echo(
        "  Limitation (rate): Wilson intervals are asymmetric — '+/-delta' is read "
        "as the half-width; the width depends on the true p, plan with margin"
    )
    typer.echo(
        "  Limitation (difference): the normal approximation is optimistic for "
        "small or skewed samples — treat N as a floor and keep margin"
    )


def _z_score_display() -> str:
    from agent_eval.core.metrics import Z_SCORE_AT_DEFAULT_CONFIDENCE

    return f"{Z_SCORE_AT_DEFAULT_CONFIDENCE:.3f}"


# ─── extensions ──────────────────────────────────────────────────────────


@app.command()
def extensions() -> None:
    """列出本次运行真正可用的扩展点及其来源 (内置的与被发现的)。

    这份清单与 REST /meta 能力清单读同一份发现结果 (spec: cli)。
    """
    from agent_eval.graders import DEFAULT_GRADERS

    registry = _discover_or_exit()
    catalog = registry.catalog()

    typer.echo("Built-in graders:")
    for grader in DEFAULT_GRADERS:
        typer.echo(f"  - {grader.name} (built-in)")

    discovered_any = False
    for kind, label in _EXTENSION_KIND_LABELS.items():
        entries = catalog.get(kind, [])
        typer.echo("")
        typer.echo(f"Discovered {kind} ({label}):")
        if not entries:
            typer.echo("  (none)")
            continue
        discovered_any = True
        for entry in entries:
            typer.echo(f"  - {entry['name']} (from {entry['source']})")

    if not discovered_any:
        typer.echo("")
        typer.echo(
            "No discovered extensions. Publish packages with entry-point groups "
            "agent_eval.graders / agent_eval.environments / agent_eval.simulators "
            "to make them available here (see docs/integration-guide.md)."
        )


# ─── serve ───────────────────────────────────────────────────────────────────


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址 (默认本机回环)"),
    port: int = typer.Option(8000, "--port", help="监听端口"),
) -> None:
    """启动独立 API 服务: 全部评测路由挂 /v1, 默认仅本机回环。"""
    import uvicorn

    from agent_eval.api.standalone import create_standalone_app

    typer.echo(f"Aeval standalone API on http://{host}:{port}/v1 (meta: /v1/meta)")
    uvicorn.run(create_standalone_app(), host=host, port=port)


def main() -> None:
    """Console-script entry point (pyproject [project.scripts])."""
    app()


if __name__ == "__main__":
    main()
