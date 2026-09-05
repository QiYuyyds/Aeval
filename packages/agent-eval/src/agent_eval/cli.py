"""
eval-suite — the Aeval command line.

Commands:
    run        Execute a suite (default runner: built-in MockAgentRunner).
               Exit 0 all passed / 1 failed tasks / 3 evaluation untrustworthy
    validate   Validate a suite YAML without running it
    list       List runs or suites from the storage DB
    show       Show one run's details (--task drills into a single task);
               reports valid/invalid/pending counts, pass@1 95% CI and
               extrapolation markers
    compare    A/B compare two runs; overlapping 95% intervals read as
               "not significant" with no directional verdict, and differing
               statistics versions or evidence boundaries read as not comparable
    serve      Serve the standalone API (/v1) via uvicorn

Runner selection (run): --runner option > AEVAL_RUNNER env var > "mock".
Custom runners register via the "agent_eval.runners" entry-point group
(name → zero-arg factory returning an AgentRunner).

Storage (list/show/compare and run persistence): SQLite, ./aeval.db by
default; override with --db or the AEVAL_DB environment variable.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime

import typer

from agent_eval.core.types import DEFAULT_INVALID_RATIO_LIMIT

DEFAULT_DB = "./aeval.db"
RUNNERS_ENTRY_POINT_GROUP = "agent_eval.runners"

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


def _pass1(summary_or_task) -> float | None:
    """实测 pass@1; 键缺失或值为 None 都算证据不足 (task 或 run 汇总通用)。"""
    rates = getattr(summary_or_task, "pass_at_k", None) or {}
    value = rates.get(1, rates.get("1"))
    return None if value is None else float(value)


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


def _trace_provider_for(agent_runner):
    """CLI 默认离线: 内置 mock trace; 注册的 runner 可暴露自己的 provider。"""
    if agent_runner is not None and hasattr(agent_runner, "trace_provider"):
        return agent_runner.trace_provider
    from agent_eval.examples.mock_runner import MockTraceProvider

    return MockTraceProvider()


def _print_run_summary(run) -> None:
    """§11.2 形态的汇总输出 (无 emoji, 兼容非 UTF-8 终端)。"""
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
    suite_path: str = typer.Argument(..., help="Suite YAML 文件路径"),
    trials: int | None = typer.Option(None, "--trials", help="覆盖每个任务的 trial 数"),
    concurrency: int | None = typer.Option(
        None, "--concurrency", min=1, help="trial 并发数 (默认串行)"
    ),
    runner: str | None = typer.Option(
        None,
        "--runner",
        envvar="AEVAL_RUNNER",
        help="AgentRunner 名称 (内置 mock, 或 agent_eval.runners entry-point 注册名)",
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
) -> None:
    """加载并执行 suite, 打印汇总。

    退出码: 0 全通过; 1 存在未通过任务; 3 评测本身不可信 (invalid 超阈或
    关键统计量为 insufficient_data)。
    """
    from agent_eval.core.runner import EvalRunner
    from agent_eval.core.suite import SuiteLoadError, load_suite

    try:
        suite = load_suite(suite_path)
    except SuiteLoadError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1) from None

    if trials is not None and trials < 1:
        typer.echo("error: --trials must be >= 1", err=True)
        raise typer.Exit(code=2)

    max_trials = trials or max(t.max_trials for t in suite.tasks)
    typer.echo(
        f"Starting eval run: {suite.name} v{suite.version} "
        f"({len(suite.tasks)} tasks, up to {max_trials} trials each)"
    )

    agent_runner = _resolve_agent_runner(runner)
    storage = _build_storage(db)

    eval_runner = EvalRunner(
        agent_runner=agent_runner,
        trace_provider=_trace_provider_for(agent_runner),
        storage=storage,
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
                typer.echo(f"  [{counter['n']}/{len(suite.tasks)}] "
                           f"{data.get('task_id', '?')}: {detail}")

        return await eval_runner.run_suite(suite, callback=_progress)

    run_result = asyncio.run(_execute())

    if run_result.status != "completed":
        typer.echo(f"error: run ended with status '{run_result.status}': "
                   f"{run_result.error}", err=True)
        raise typer.Exit(code=1)

    _print_run_summary(run_result)

    summary = run_result.summary
    if summary is None:
        raise typer.Exit(code=1)

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


# ─── validate ────────────────────────────────────────────────────────────────


@app.command()
def validate(
    suite_path: str = typer.Argument(..., help="Suite YAML 文件路径"),
) -> None:
    """只做加载校验: 输出结论, 校验失败退出码非 0。"""
    from agent_eval.core.suite import SuiteLoadError, load_suite

    try:
        suite = load_suite(suite_path)
    except SuiteLoadError as e:
        typer.echo(f"INVALID: {e}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(
        f"VALID: {suite.name} v{suite.version} — {len(suite.tasks)} task(s), "
        f"{sum(len(t.graders) for t in suite.tasks)} grader config(s)"
    )


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
                f"(score {t.avg_score():.4f}, {t.duration_ms:.0f}ms"
                + (f", error: {t.error}" if t.error else "") + ")"
            )
            for gr in t.grader_results:
                typer.echo(
                    f"    - {gr.grader_name} [{gr.grader_type.value}]: "
                    f"{gr.score:.4f} {'passed' if gr.passed else 'FAILED'}"
                    f" verdict={_verdict_text(gr)}"
                    + (f" — {gr.explanation}" if gr.explanation else "")
                )


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
