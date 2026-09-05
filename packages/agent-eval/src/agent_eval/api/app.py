"""
FastAPI application factory for Aeval.

Creates a FastAPI app with all eval routes mounted.
Can be used standalone or mounted in an existing app.

Usage:
    # Standalone
    app = create_app(runner=my_runner)

    # Mounted in existing FastAPI app
    from fastapi import FastAPI
    app = FastAPI()
    eval_app = create_app(runner=my_runner)
    app.mount("/api/eval", eval_app)
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from agent_eval.api.routes import datasets, graders, metrics, runs, suites, tasks
from agent_eval.core.runner import EvalRunner
from agent_eval.core.types import (
    DEFAULT_BOOTSTRAP_ROUNDS,
    DEFAULT_CONFIDENCE_LEVEL,
    DEFAULT_INVALID_RATIO_LIMIT,
    MIN_VALID_TRIALS_FOR_SATURATION,
    STATISTICS_VERSION,
)
from agent_eval.graders import get_grader_catalog
from agent_eval.trace.mapping import ATTRIBUTE_MAPPING_VERSION, OTEL_GENAI_SPEC_VERSION

# Global runner reference (set by create_app)
_runner: EvalRunner | None = None


def _get_runner() -> EvalRunner | None:
    """Get the global EvalRunner instance"""
    return _runner


def set_runner(runner: EvalRunner | None) -> None:
    """Set the global EvalRunner instance.

    Used by the host app to inject the real runner during async startup
    (e.g. main.py lifespan → eval_integration.config.create_aeval_runner).
    """
    global _runner
    _runner = runner


DISTRIBUTION_NAME = "aeval-framework"  # PyPI 发行包名 (Python 模块为 agent_eval)


def package_version() -> str:
    """包版本: 优先取已安装元数据, 源码直跑时回退模块常量。"""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _metadata_version

    try:
        return _metadata_version(DISTRIBUTION_NAME)
    except PackageNotFoundError:
        from agent_eval import __version__

        return __version__


def statistics_defaults() -> dict[str, Any]:
    """统计口径版本与 D7 数值默认值 (经元信息接口公布, 供调用方判断可比性)。"""
    return {
        "version": STATISTICS_VERSION,
        "confidence_level": DEFAULT_CONFIDENCE_LEVEL,
        "bootstrap_rounds": DEFAULT_BOOTSTRAP_ROUNDS,
        "min_valid_trials_for_saturation": MIN_VALID_TRIALS_FOR_SATURATION,
        "gate_invalid_ratio_limit": DEFAULT_INVALID_RATIO_LIMIT,
    }


def meta_payload(
    api_prefix: str = "",
    endpoints: list[str] | None = None,
    version: str | None = None,
) -> dict[str, Any]:
    """元信息响应体: 版本 + 统计口径 + 能力清单。

    同一大版本内响应结构向后兼容, 但统计口径的数值语义可能变化 —— 口径版本
    必须显式公布, 调用方才能判断两个 run 是否可直接比较。
    """
    return {
        "name": "Aeval",
        "package": DISTRIBUTION_NAME,
        "version": version or package_version(),
        "api_prefix": api_prefix,
        "endpoints": endpoints or ["/suites", "/tasks", "/runs", "/compare", "/graders",
                                   "/datasets", "/metrics", "/health"],
        "statistics": statistics_defaults(),
        "evidence": {
            "spec_version": OTEL_GENAI_SPEC_VERSION,
            "mapping_version": ATTRIBUTE_MAPPING_VERSION,
            "tool_arguments_captured_by_default": False,
            "model_content_captured_by_default": False,
            "capture_is_one_declaration": True,
        },
        "capabilities": {
            "graders": [g["name"] for g in get_grader_catalog()],
            "storage": ["memory", "sqlite"],
            "trace_providers": ["phoenix (optional, lazily imported)"],
            "sse": True,
            "datasets": True,
            "metrics": True,
            "validity_verdicts": True,
            "confidence_intervals": True,
            "normalized_trace_observations": True,
            "termination_reasons": True,
            "cost_axis": True,
            "evidence_provenance_levels": True,
            "deferred_grading": True,
            # 重评分本期只有库层入口 (EvalRunner.regrade_run)。它牵涉同一大版本的
            # 响应结构兼容承诺, 暴露面另立变更 —— 列出来免得调用方以为 /runs 上能调。
            "regrade_over_http": False,
            "regrade_over_cli": False,
        },
        "not_exposed": {
            "regrade": (
                "证据归档与重评分已落地 (EvalRunner.regrade_run / verdict_drift), "
                "但 HTTP 与 CLI 入口本期未提供"
            )
        },
    }


def create_app(runner: EvalRunner | None = None) -> FastAPI:
    """
    Create a FastAPI app with Aeval routes.

    Args:
        runner: EvalRunner instance. If None, routes will return 503.

    Returns:
        FastAPI application
    """
    app = FastAPI(
        title="Aeval API",
        version="0.1.0",
        description="Agent Evaluation Framework API",
    )

    # Store runner in app state and global reference
    global _runner
    _runner = runner
    app.state.runner = runner

    # Include routers
    app.include_router(suites.router, prefix="/suites", tags=["suites"])
    app.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
    app.include_router(runs.router, prefix="/runs", tags=["runs"])
    # compare 挂在 /compare (spec §REST API), 不带 /runs 前缀
    app.include_router(runs.compare_router, tags=["compare"])
    app.include_router(graders.router, prefix="/graders", tags=["graders"])
    # 数据集管理 (change ③: 数据集构建闭环 — CRUD/导入/挖掘/生成/质量/to-suite)
    app.include_router(datasets.router, prefix="/datasets", tags=["datasets"])
    # 批量评测 (change ④: 对已有输出直接批量打分 — POST /metrics/batch)
    app.include_router(metrics.router, prefix="/metrics", tags=["metrics"])

    @app.get("/health")
    async def health():
        # 动态读取全局 runner — host app 会在 startup 阶段注入真实 runner
        return {"status": "ok", "runner_configured": _get_runner() is not None}

    @app.get("/meta")
    async def meta():
        """寄宿形态的元信息接口 (挂载前缀由宿主决定, 例如 /api/eval/meta)。"""
        return meta_payload()

    return app
