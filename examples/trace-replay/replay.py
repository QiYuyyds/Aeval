"""Trace replay example — 线上 trace 回放通路的最小演示 (全程离线)。

通路形状: mock 导出的 trace 归档 → ``TraceMiner`` 挖掘成评测条目 →
``EvalDataset.to_suite()`` 套件化 → ``EvalRunner`` 执行 run (落库)。

这只是通路的**演示形状**: 真实场景里 trace 来自 Phoenix / OTLP / 宿主后端
的导出 (替换 ``ExportedTraceProvider`` 一处), 判据需要人工评审挖掘结果后
补写 (挖掘只产出 prompt 与溯源, 不替人下判断), 定时回归由外部调度
(cron / CI) 驱动 — 框架本身不引入任何在线服务。

Usage:
    python examples/trace-replay/replay.py
"""

from __future__ import annotations

import asyncio
from typing import Any

from agent_eval.core.contract import TraceProvider
from agent_eval.core.runner import EvalRunner
from agent_eval.core.types import GraderConfig
from agent_eval.dataset.models import EvalDataset
from agent_eval.dataset.sources.trace_mining import TraceMiner
from agent_eval.examples.mock_runner import MockAgentRunner, MockTraceProvider
from agent_eval.storage.sqlite import SqliteStorage

DB_PATH = "./aeval.db"


class ExportedTraceProvider(TraceProvider):
    """把「导出的线上 trace 归档」当数据源的最小 TraceProvider。

    真实接法只需替换这一处: ``get_trace_ids`` 列候选 trace,
    ``get_spans`` 取单条 trace 的 spans。这里用内存字典模拟导出产物,
    格式沿用框架各处通用的 spans (name / attributes / status) 表达。
    """

    def __init__(self, traces: dict[str, list[dict[str, Any]]]):
        self._traces = traces

    async def get_trace_ids(
        self, filters: dict[str, Any] | None = None, limit: int = 100
    ) -> list[str]:
        return list(self._traces)[:limit]

    async def get_spans(self, trace_id: str) -> list[dict[str, Any]]:
        return self._traces.get(trace_id, [])


def _turn_span(prompt: str, *, error: bool = False) -> dict[str, Any]:
    """一个 LLM turn span: 根 span 带 input.value (挖掘从这里取 prompt)。"""
    return {
        "name": "llm.turn",
        "attributes": {
            "input.value": prompt,
            "input_tokens": 120,
            "output_tokens": 80,
        },
        "start_time": "2026-09-01T10:00:00Z",
        "end_time": "2026-09-01T10:00:02Z",
        "status": {"status_code": "ERROR" if error else "OK"},
    }


# ① mock 一批「线上导出」的 trace: 一条以 ERROR 收尾 (failed_tasks 策略挑中),
#    其余正常 (不会被该策略选中, 展示筛选语义)
EXPORTED_TRACES = {
    "prod-trace-error-1": [_turn_span("帮我取消上一笔订单并退款", error=True)],
    "prod-trace-ok-1": [_turn_span("今天的头版头条是什么")],
    "prod-trace-ok-2": [_turn_span("把这段话翻译成英文")],
}


async def main() -> None:
    # ② 挖掘: 按策略筛选 trace, 从根 span 提取用户输入;
    #    没有用户输入的 trace 进 skipped (不猜 prompt), 条目保留 trace 溯源
    provider = ExportedTraceProvider(EXPORTED_TRACES)
    report = await TraceMiner(provider).mine("failed_tasks", limit=10)
    print(
        f"[mine] strategy={report.strategy} candidates={report.candidates} "
        f"mined={len(report.items)} skipped={len(report.skipped)}"
    )

    # ③ 判据补全: 挖掘只产出 prompt 与溯源; 判据由人工评审挖掘结果后补写
    #    (这里用离线 mock agent 的 transcript contains 检查演示)
    for item in report.items:
        item.graders = [
            GraderConfig(
                type="code",
                name="code_based",
                config={
                    "checks": [
                        {"type": "contains", "value": "Mock response", "target": "transcript"}
                    ]
                },
            )
        ]
        print(f"[item] {item.id}  source_ref={item.source_ref}  prompt={item.prompt[:20]}…")

    # ④ 套件化: 数据集 → suite (复用套件校验器, 非法条目拒绝转换);
    #    suite 元数据带着数据集 id/版本, run 结果可关联回挖掘批次
    dataset = EvalDataset(
        name="replayed-production-traces",
        description="mined from exported production traces (offline demo)",
        items=report.items,
    )
    suite = dataset.to_suite()
    print(f"[suite] {suite.name} v{suite.version} — {len(suite.tasks)} task(s)")

    # ⑤ 执行 run (落库 → eval-suite show <run_id> 复查; 之后的定时回归可用
    #    eval-suite run <suite.yaml> --baseline <本 run 的 run_id> 做基线门)
    storage = SqliteStorage(DB_PATH)
    await storage.initialize()
    runner = EvalRunner(
        agent_runner=MockAgentRunner(success_rate=1.0, latency_range=(0.0, 0.01)),
        trace_provider=MockTraceProvider(),
        storage=storage,
    )
    run = await runner.run_suite(suite)
    summary = run.summary
    assert summary is not None
    pass1 = summary.pass_at_k.get(1)
    print(
        f"[run] {run.run_id} status={run.status} "
        f"tasks={summary.total_tasks} trials={summary.total_trials} "
        f"pass@1={'insufficient_data' if pass1 is None else f'{pass1:.1%}'}"
    )
    print(f"[next] eval-suite show {run.run_id}  # 复查本轮结果")


if __name__ == "__main__":
    asyncio.run(main())
