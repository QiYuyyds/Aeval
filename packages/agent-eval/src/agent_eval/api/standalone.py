"""
Standalone API deployment for Aeval.

Exposes the full eval route set under the `/v1` prefix as a self-contained
FastAPI application, with a version header on every response and a `/v1/meta`
capabilities endpoint. The existing `create_app()` is reused unchanged —
host-app mounts (e.g. AChat's `/api/eval`) keep their behaviour.

Usage:
    # In-process (tests / mounting)
    app = create_standalone_app(runner=my_runner)

    # Service
    eval-suite serve --port 8900
"""

from __future__ import annotations

from fastapi import FastAPI

from agent_eval.api.app import create_app, meta_payload, package_version
from agent_eval.core.runner import EvalRunner

# /v1 下暴露的路由组 (与 create_app 内的挂载一致; /health 为探活)
CAPABILITY_ENDPOINTS = [
    "/suites",
    "/tasks",
    "/runs",
    "/compare",
    "/graders",
    "/datasets",
    "/metrics",
    "/health",
]

__all__ = [
    "CAPABILITY_ENDPOINTS",
    "create_standalone_app",
    "package_version",
    "meta_payload",
]


def _meta_payload() -> dict:
    """GET /v1/meta 响应体: 版本 + 统计口径 + 能力清单 (与寄宿形态同源)。"""
    return meta_payload(
        api_prefix="/v1",
        endpoints=CAPABILITY_ENDPOINTS,
        version=package_version(),
    )


def create_standalone_app(runner: EvalRunner | None = None) -> FastAPI:
    """
    Create the standalone Aeval API app (all routes under /v1).

    Args:
        runner: EvalRunner instance. If None, run routes return 503 while
            registry/storage-backed endpoints stay usable.

    Returns:
        FastAPI application serving /v1/* with X-Aeval-Version headers.
    """
    version = package_version()
    app = FastAPI(
        title="Aeval API",
        version=version,
        description="Standalone Agent Evaluation Framework API (/v1)",
    )

    @app.get("/v1/meta")
    async def meta() -> dict:
        # 注册在 mount 之前: Starlette 按注册顺序匹配, /v1/meta 优先于子应用
        return _meta_payload()

    @app.middleware("http")
    async def add_version_header(request, call_next):
        response = await call_next(request)
        response.headers["X-Aeval-Version"] = version
        return response

    # 复用既有 create_app 的全部路由集合, 整体挂 /v1 (既有 create_app 零改动,
    # 寄宿部署的 /api/eval 挂载不受影响 — 设计 D5)
    app.mount("/v1", create_app(runner=runner))

    return app
