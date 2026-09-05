"""
Run management routes.

GET    /runs              — List run history
POST   /runs              — Start a new run
GET    /runs/{run_id}     — Get run details
DELETE /runs/{run_id}     — Delete a run
GET    /runs/{run_id}/trials — Get trials for a run
GET    /runs/{run_id}/stream — SSE event stream (realtime progress)
POST   /runs/{run_id}/cancel — Cancel a running run
POST   /runs/{run_id}/human-scores — Submit a human score for a pending trial

Separate router (mounted at the API root, no /runs prefix):
POST   /compare           — Compare two runs
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from agent_eval.api.events import run_event_bus
from agent_eval.core.metrics import classify_trial, trial_invalid_reason
from agent_eval.core.types import STATISTICS_VERSION, TrialVerdict

router = APIRouter()

logger = logging.getLogger(__name__)

# run_id → 后台 asyncio task 注册表 (取消运行用)
_background_tasks: dict[str, asyncio.Task] = {}

# SSE 心跳间隔 (秒) — 防代理空闲断连 (§17.3)
_STREAM_HEARTBEAT_SECONDS = 15.0


@router.get("")
async def list_runs(suite_name: str | None = None, limit: int = 50):
    """列出运行历史"""
    from agent_eval.api.app import _get_runner

    runner = _get_runner()
    if runner is None:
        raise HTTPException(status_code=503, detail="EvalRunner not configured")

    runs = await runner.storage.list_runs(suite_name=suite_name, limit=limit)
    return {
        "runs": [
            {
                "run_id": r.run_id,
                "suite_name": r.suite_name,
                "status": r.status,
                "started_at": r.started_at,
                "completed_at": r.completed_at,
                "duration_ms": r.duration_ms,
                "statistics_version": r.statistics_version,
                "evidence": r.evidence.model_dump() if r.evidence else None,
                "task_count": len(r.trials),
                "summary": r.summary.model_dump() if r.summary else None,
            }
            for r in runs
        ]
    }


class CreateRunRequest(BaseModel):
    """启动运行的请求体"""
    suite_name: str
    config: dict[str, Any] = {}


@router.post("")
async def create_run(request: CreateRunRequest):
    """启动一次 suite 运行 (立即返回 run_id, 后台执行)"""
    from agent_eval.api.app import _get_runner

    runner = _get_runner()
    if runner is None:
        raise HTTPException(status_code=503, detail="EvalRunner not configured")

    # Load suite
    suite = await runner.storage.get_suite(request.suite_name)
    if suite is None:
        raise HTTPException(
            status_code=404,
            detail=f"Suite '{request.suite_name}' not found. Create it first.",
        )

    # 预生成 run_id, 便于客户端立即轮询
    run_id = f"run_{uuid.uuid4().hex[:12]}"

    # 先落一条 pending 记录, 保证 POST 返回后 GET /runs/{run_id} 立即可查
    from agent_eval.core.types import RunResult

    await runner.storage.save_run(
        RunResult(
            run_id=run_id,
            suite_name=suite.name,
            status="pending",
            statistics_version=STATISTICS_VERSION,
        )
    )

    async def _run():
        # 事件总线: runner 进度回调 → per-run fan-out (SSE 增量, 不落库)
        async def _emit(event: str, data: dict[str, Any]) -> None:
            run_event_bus.publish(run_id, event, data)

        try:
            result = await runner.run_suite(suite, callback=_emit, run_id=run_id)
            if result.status == "failed":
                run_event_bus.publish(run_id, "error", {"error": result.error or "run failed"})
            run_event_bus.publish(
                run_id,
                "run_complete",
                {
                    "status": result.status,
                    "summary": result.summary.model_dump() if result.summary else None,
                    "error": result.error,
                },
            )
        except asyncio.CancelledError:
            # run_suite 已将状态置为 cancelled 并保存; 观察端也需要终态
            run_event_bus.publish(run_id, "run_complete", {"status": "cancelled"})
        except Exception as e:
            logger.warning("Run %s failed: %s", run_id, e)
            run_event_bus.publish(run_id, "error", {"error": str(e)})
            run_event_bus.publish(run_id, "run_complete", {"status": "failed", "error": str(e)})
        finally:
            _background_tasks.pop(run_id, None)

    # Create task (runs in background)
    task = asyncio.create_task(_run())
    _background_tasks[run_id] = task

    # Return immediately with run info
    return {
        "run_id": run_id,
        "message": "Run started",
        "suite_name": request.suite_name,
        "task_count": len(suite.tasks),
        "status": "running",
    }


@router.get("/{run_id}")
async def get_run(run_id: str):
    """获取运行详情"""
    from agent_eval.api.app import _get_runner

    runner = _get_runner()
    if runner is None:
        raise HTTPException(status_code=503, detail="EvalRunner not configured")

    run = await runner.storage.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    return {
        "run_id": run.run_id,
        "suite_name": run.suite_name,
        "status": run.status,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "duration_ms": run.duration_ms,
        "error": run.error,
        "statistics_version": run.statistics_version,
        "evidence": run.evidence.model_dump() if run.evidence else None,
        "trials": {
            task_id: [
                {
                    "trial_index": t.trial_index,
                    "trace_id": t.trace_id,
                    "success": t.success,
                    "score": t.avg_score(),
                    "duration_ms": t.duration_ms,
                    "error": t.error,
                    "verdict": t.verdict.value,
                    "invalid_reason": t.invalid_reason.value if t.invalid_reason else None,
                    "termination_reason": (
                        t.termination_reason.value if t.termination_reason else None
                    ),
                    "metrics": t.metrics,
                    "evidence_gaps": [gap.model_dump() for gap in t.evidence_gaps],
                    "unrecognized_attributes": t.unrecognized_attributes,
                    "grader_results": [
                        {
                            "grader_name": gr.grader_name,
                            "score": gr.score,
                            "passed": gr.passed,
                            "explanation": gr.explanation,
                            "verdict": gr.verdict.value,
                            "invalid_reason": (
                                gr.invalid_reason.value if gr.invalid_reason else None
                            ),
                        }
                        for gr in t.grader_results
                    ],
                }
                for t in trials
            ]
            for task_id, trials in run.trials.items()
        },
        "summary": run.summary.model_dump() if run.summary else None,
    }


@router.delete("/{run_id}")
async def delete_run(run_id: str):
    """删除运行"""
    from agent_eval.api.app import _get_runner

    runner = _get_runner()
    if runner is None:
        raise HTTPException(status_code=503, detail="EvalRunner not configured")

    deleted = await runner.storage.delete_run(run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    # 派生物一并清除: 内容寻址的评分缓存可能缓存了该 run 采集到的证据内容
    runner.evict_run_cache(run_id)
    _background_tasks.pop(run_id, None)

    return {"deleted": True}


@router.get("/{run_id}/trials")
async def get_trials(run_id: str, task_id: str | None = None):
    """获取 trial 列表"""
    from agent_eval.api.app import _get_runner

    runner = _get_runner()
    if runner is None:
        raise HTTPException(status_code=503, detail="EvalRunner not configured")

    run = await runner.storage.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    if task_id:
        trials = run.trials.get(task_id, [])
        return {
            "task_id": task_id,
            "trials": [t.model_dump() for t in trials],
        }

    # Return all trials
    all_trials = {}
    for tid, trials in run.trials.items():
        all_trials[tid] = [t.model_dump() for t in trials]
    return {"trials": all_trials}


# ─── SSE 事件流 (任务 3.2, 协议 §17.3) ───────────────────────────────────────


def _terminal_event_from_run(run) -> dict[str, Any]:
    """从存储的 run 记录合成 run_complete 终态事件 (进程重启后 bus 无缓存)。"""
    return {
        "type": "run_complete",
        "run_id": run.run_id,
        "timestamp": time.time() * 1000,
        "status": run.status,
        "summary": run.summary.model_dump() if run.summary else None,
        "error": run.error,
    }


async def _stream_events(run_id: str):
    """SSE 事件生成器: 订阅即推增量; run_complete 收尾关流; 心跳注释行。

    断线恢复不在此处理 — 客户端按快照+增量协议重拉快照后重新订阅。
    """
    queue = run_event_bus.subscribe(run_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(
                    queue.get(), timeout=_STREAM_HEARTBEAT_SECONDS
                )
            except TimeoutError:
                yield {"comment": "heartbeat"}
                continue
            yield {"data": json.dumps(event, default=str)}
            if event.get("type") == "run_complete":
                return
    finally:
        run_event_bus.unsubscribe(run_id, queue)


@router.get("/{run_id}/stream")
async def stream_run(run_id: str):
    """SSE 推送运行事件 (task/trial 生命周期 + 终态)。

    - 运行中 run: 订阅即推增量, run_complete 后关流
    - 已完成 run: 立即推 run_complete 后关流 (bus 终态缓存或由存储合成)
    - 连接只是观察窗口: 断开不影响 run 执行 (后台任务持有生命周期)
    """
    from agent_eval.api.app import _get_runner

    runner = _get_runner()
    if runner is None:
        raise HTTPException(status_code=503, detail="EvalRunner not configured")

    run = await runner.storage.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    if run.status in ("completed", "failed", "cancelled"):
        cached = run_event_bus.terminal_event(run_id)

        async def _terminal_only():
            yield {"data": json.dumps(cached or _terminal_event_from_run(run), default=str)}

        return EventSourceResponse(
            _terminal_only(),
            headers={"Cache-Control": "no-cache, no-transform"},
        )

    return EventSourceResponse(
        _stream_events(run_id),
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str):
    """取消一个运行中的 run (已完成 trial 保留)"""
    import time

    from agent_eval.api.app import _get_runner

    runner = _get_runner()
    if runner is None:
        raise HTTPException(status_code=503, detail="EvalRunner not configured")

    task = _background_tasks.get(run_id)
    if task is None or task.done():
        run = await runner.storage.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
        raise HTTPException(
            status_code=409, detail=f"Run '{run_id}' is not running"
        )

    task.cancel()
    # 等后台任务收尾: run_suite 捕获 CancelledError 后保存 cancelled 状态;
    # 若任务尚未启动就被取消 (协程未执行), 这里兜底更新 run 记录。
    with suppress(asyncio.CancelledError):
        await asyncio.wait({task})
    run = await runner.storage.get_run(run_id)
    if run is not None and run.status not in ("completed", "failed", "cancelled"):
        run.status = "cancelled"
        run.completed_at = time.time() * 1000
        await runner.storage.save_run(run)

    return {"run_id": run_id, "cancelled": True}


class HumanScoreRequest(BaseModel):
    """人工评分回传请求体"""

    task_id: str
    trial_index: int = Field(..., ge=0)
    score: float = Field(..., ge=0.0, le=1.0, description="人工评分 0.0-1.0")
    explanation: str = Field("", description="评分理由/反馈")
    grader_name: str = Field("human", description="人工评分器名称")


@router.post("/{run_id}/human-scores")
async def submit_human_score(run_id: str, request: HumanScoreRequest):
    """人工评分回传: 更新已存 GraderResult 并重算该 task 汇总"""
    from agent_eval.api.app import _get_runner

    runner = _get_runner()
    if runner is None:
        raise HTTPException(status_code=503, detail="EvalRunner not configured")

    run = await runner.storage.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    trials = run.trials.get(request.task_id)
    if not trials:
        raise HTTPException(
            status_code=404,
            detail=f"Task '{request.task_id}' not found in run '{run_id}'",
        )
    trial = next(
        (t for t in trials if t.trial_index == request.trial_index), None
    )
    if trial is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Trial {request.trial_index} not found for task "
                f"'{request.task_id}'"
            ),
        )

    grader_result = next(
        (
            gr
            for gr in trial.grader_results
            if gr.grader_name == request.grader_name
        ),
        None,
    )
    if grader_result is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No grader result '{request.grader_name}' on task "
                f"'{request.task_id}' trial {request.trial_index}"
            ),
        )

    # 从 suite 中取 task 定义 (评分策略/阈值); suite 缺失时用默认阈值
    suite = await runner.storage.get_suite(run.suite_name)
    task = None
    if suite is not None:
        task = next((t for t in suite.tasks if t.id == request.task_id), None)

    threshold = task.score_threshold if task is not None else 0.7
    if task is not None:
        threshold = task.get_grader_config(request.grader_name).get(
            "threshold", threshold
        )

    # 更新已存的 GraderResult (评分回传 → 离开 pending 通道)
    grader_result.score = request.score
    grader_result.passed = request.score >= threshold
    grader_result.explanation = request.explanation or "人工评分"
    grader_result.confidence = 1.0
    grader_result.verdict = TrialVerdict.VALID
    grader_result.invalid_reason = None
    grader_result.details = {
        **grader_result.details,
        "status": "scored",
        "human_score": request.score,
        "human_explanation": request.explanation,
    }

    # 重算 trial 成功状态与 run 汇总 (含该 task 汇总)
    if task is not None:
        trial.success = runner._compute_trial_success(task, trial.grader_results)
    trial.verdict = classify_trial(trial)
    trial.invalid_reason = trial_invalid_reason(trial)
    if run.statistics_version is None:
        run.statistics_version = STATISTICS_VERSION
    run.summary = runner._compute_summary(run, suite) if suite is not None else run.summary
    await runner.storage.save_run(run)

    return {
        "run_id": run_id,
        "task_id": request.task_id,
        "trial_index": request.trial_index,
        "grader_name": request.grader_name,
        "score": grader_result.score,
        "passed": grader_result.passed,
        "trial_success": trial.success,
        "trial_verdict": trial.verdict.value,
        "summary": run.summary.model_dump() if run.summary else None,
    }


class CompareRequest(BaseModel):
    """对比请求体"""
    run_id_a: str
    run_id_b: str


# compare 挂载在 /api/eval/compare (spec §REST API), 不带 /runs 前缀
compare_router = APIRouter()


@compare_router.post("/compare")
async def compare_runs(request: CompareRequest):
    """对比两次运行"""
    from agent_eval.api.app import _get_runner

    runner = _get_runner()
    if runner is None:
        raise HTTPException(status_code=503, detail="EvalRunner not configured")

    run_a = await runner.storage.get_run(request.run_id_a)
    run_b = await runner.storage.get_run(request.run_id_b)

    if run_a is None:
        raise HTTPException(status_code=404, detail=f"Run '{request.run_id_a}' not found")
    if run_b is None:
        raise HTTPException(status_code=404, detail=f"Run '{request.run_id_b}' not found")

    if not run_a.summary or not run_b.summary:
        raise HTTPException(status_code=400, detail="Both runs must be completed")

    # Build comparison
    comparison = _build_comparison(run_a, run_b)

    return {
        "run_a": {
            "run_id": run_a.run_id,
            "suite_name": run_a.suite_name,
            "started_at": run_a.started_at,
        },
        "run_b": {
            "run_id": run_b.run_id,
            "suite_name": run_b.suite_name,
            "started_at": run_b.started_at,
        },
        "comparison": comparison,
    }


def _build_comparison(run_a, run_b) -> dict[str, Any]:
    """构建两次运行的对比 (口径版本不同则标注不可比; 区间重叠则不判方向)。

    既有字段 (`a`/`b`/`delta`/`regressions`/`improvements`/`tasks`) 的名称与类型
    保持不变; 分母为 0 时 `a`/`b`/`delta` 为 null 而非 0.0。
    """
    summary_a = run_a.summary
    summary_b = run_b.summary

    version_a = getattr(run_a, "statistics_version", None)
    version_b = getattr(run_b, "statistics_version", None)
    evidence_a = getattr(run_a, "evidence", None)
    evidence_b = getattr(run_b, "evidence", None)
    same_caliber = version_a is not None and version_a == version_b
    same_boundary, boundary_reason = _compare_evidence_boundaries(evidence_a, evidence_b)
    comparable = same_caliber and same_boundary

    all_k_values: set[int] = set()
    for summary in (summary_a, summary_b):
        for k in (summary.pass_at_k or {}):
            all_k_values.add(int(k))

    def _entry(
        a_val: float | None,
        b_val: float | None,
        a_ci: tuple[float | None, float | None] | None,
        b_ci: tuple[float | None, float | None] | None,
        extrapolated: bool,
    ) -> dict[str, Any]:
        overlapping = _intervals_overlap(a_ci, b_ci)
        return {
            "a": a_val,
            "b": b_val,
            "delta": _delta(a_val, b_val),
            "a_ci": _ci_list(a_ci),
            "b_ci": _ci_list(b_ci),
            "intervals_overlap": overlapping,
            "significant": (
                None if overlapping is None else (comparable and not overlapping)
            ),
            "extrapolated": extrapolated,
            "comparable": comparable,
        }

    def _k_map(summary, field: str) -> dict:
        raw = getattr(summary, field, None) or {}
        return {int(k): v for k, v in raw.items()}

    pass_at_k_comparison = {}
    pass_power_k_comparison = {}
    for k in sorted(all_k_values):
        est_a = _k_map(summary_a, "estimates").get(k)
        est_b = _k_map(summary_b, "estimates").get(k)
        pass_at_k_comparison[f"pass_at_{k}"] = _entry(
            est_a.value if est_a else None,
            est_b.value if est_b else None,
            _est_ci(est_a),
            _est_ci(est_b),
            bool(est_a and est_a.extrapolated) or bool(est_b and est_b.extrapolated),
        )

        pow_a = _k_map(summary_a, "power_estimates").get(k)
        pow_b = _k_map(summary_b, "power_estimates").get(k)
        pass_power_k_comparison[f"pass_power_{k}"] = _entry(
            pow_a.value if pow_a else None,
            pow_b.value if pow_b else None,
            _est_ci(pow_a),
            _est_ci(pow_b),
            bool(pow_a and pow_a.extrapolated) or bool(pow_b and pow_b.extrapolated),
        )

    dist_a = getattr(summary_a, "score_distribution", None)
    dist_b = getattr(summary_b, "score_distribution", None)
    avg_entry = _entry(
        summary_a.avg_score,
        summary_b.avg_score,
        _dist_ci(dist_a),
        _dist_ci(dist_b),
        False,
    )

    # 逐 task 对比: 只有区间不重叠才判为退化/提升 (D4)
    task_a_map = {ts.task_id: ts for ts in summary_a.task_summaries}
    task_b_map = {ts.task_id: ts for ts in summary_b.task_summaries}

    all_task_ids = set(task_a_map.keys()) | set(task_b_map.keys())
    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    task_comparisons: dict[str, Any] = {}

    for task_id in sorted(all_task_ids):
        ts_a = task_a_map.get(task_id)
        ts_b = task_b_map.get(task_id)
        if not (ts_a and ts_b):
            continue

        a_score = ts_a.avg_score
        b_score = ts_b.avg_score
        delta = _delta(a_score, b_score)
        overlapping = _intervals_overlap(
            _dist_ci(ts_a.score_distribution), _dist_ci(ts_b.score_distribution)
        )
        significant = (
            comparable and overlapping is False and delta is not None and abs(delta) > 0.1
        )
        task_comparisons[task_id] = {
            "a": a_score,
            "b": b_score,
            "delta": delta,
            "a_ci": _ci_list(_dist_ci(ts_a.score_distribution)),
            "b_ci": _ci_list(_dist_ci(ts_b.score_distribution)),
            "intervals_overlap": overlapping,
            "significant": (None if overlapping is None else (
                comparable and overlapping is False
            )),
            "comparable": comparable,
        }

        if not significant:
            continue
        row = {"task_id": task_id, "a": a_score, "b": b_score, "delta": delta}
        if delta < 0:
            regressions.append(row)
        else:
            improvements.append(row)

    return {
        "pass_at_k": pass_at_k_comparison,
        "pass_power_k": pass_power_k_comparison,
        "avg_score": avg_entry,
        "regressions": regressions,
        "improvements": improvements,
        "tasks": task_comparisons,
        "statistics_version": {"a": version_a, "b": version_b},
        "evidence_boundary": {
            "a": evidence_a.model_dump() if evidence_a else None,
            "b": evidence_b.model_dump() if evidence_b else None,
        },
        "comparable": comparable,
        "not_comparable_reason": _not_comparable_reason(
            comparable, same_caliber, version_a, version_b, boundary_reason
        ),
    }


def _compare_evidence_boundaries(
    evidence_a: Any, evidence_b: Any
) -> tuple[bool, str | None]:
    """两个 run 的证据边界是否一致 (缺记录即无法判定可比)。"""
    if evidence_a is None and evidence_b is None:
        return False, "两个 run 均未记录证据采集边界 (历史 run), 无法判定可比性"
    if evidence_a is None or evidence_b is None:
        missing = "a" if evidence_a is None else "b"
        return False, f"run_{missing} 未记录证据采集边界 (历史 run), 无法判定可比性"
    return evidence_a.compare_with(evidence_b)


def _not_comparable_reason(
    comparable: bool,
    same_caliber: bool,
    version_a: str | None,
    version_b: str | None,
    boundary_reason: str | None,
) -> str | None:
    if comparable:
        return None
    if not same_caliber:
        return (
            "两个 run 的统计口径版本不同"
            if version_a is not None and version_b is not None
            else "至少一个 run 未记录统计口径版本 (历史 run), 无法判定可比性"
        )
    return boundary_reason or "两个 run 的证据采集边界不同"


def _delta(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return round(b - a, 4)


def _est_ci(est) -> tuple[float | None, float | None] | None:
    if est is None or est.p_lower_bound is None or est.p_upper_bound is None:
        return None
    return (est.p_lower_bound, est.p_upper_bound)


def _dist_ci(dist) -> tuple[float | None, float | None] | None:
    if dist is None or dist.ci_low is None or dist.ci_high is None:
        return None
    return (dist.ci_low, dist.ci_high)


def _ci_list(ci: tuple[float | None, float | None] | None) -> list[float] | None:
    return [ci[0], ci[1]] if ci else None


def _intervals_overlap(
    a: tuple[float | None, float | None] | None,
    b: tuple[float | None, float | None] | None,
) -> bool | None:
    """两侧区间是否重叠 (D4)。任一侧无区间 → None (无法判定)。"""
    if a is None or b is None:
        return None
    return not (a[1] < b[0] or b[1] < a[0])
