"""AChat 0.4.0 真流量活跑驱动 (tasks 9.3–9.5)。

在宿主 (AChat) 的 venv 里运行 —— aeval-framework 以 editable 指向本仓库,
因此跑的是 0.4.0 新代码:

    cd D:/java/project/bitdance-agenthub-main/backend
    .venv/Scripts/python.exe D:/java/project/Aeval-publish/examples/achat/run_live_acceptance.py

三条活跑断言 (对照 tasks.md 9.3/9.4):
  1. 预写话术 + 事件注入 (conversation-suite.yaml): 轮数不进分母、模拟用户
     读数记 harness 且带时刻、事件后判据真生效、entry-point 发现可达;
  2. 目标驱动模拟器 (conversation-goal-suite.yaml): 收尾判定归框架
     (end_reason=goal_achieved), 复核轮数是否落在合理区间;
  3. 读回: 与 0.3.0 历史 run (无环境身份记录) 比较应报不可比 —— 环境身份
     边界真生效。

发现语义: 自定义判据 achat_session_gate 经 entry-point 组
agent_eval.graders 发现 (achat-eval-ext 已 pip install 进宿主 venv),
EvalRunner 拿到的是 discover_extensions() 注册表 —— 与 CLI 同一条装配路径,
非 Python 手工装配实例。多轮消费由本驱动的 MultiTurnAChatRunner 包装
(宿主仓库在本沙箱外, 无法直接修改; 包装只复用宿主适配器的既有方法)。
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

AEVAL_REPO = Path(r"D:\java\project\Aeval-publish")
ACHAT_BACKEND = Path(r"D:\java\project\bitdance-agenthub-main\backend")

sys.path.insert(0, str(ACHAT_BACKEND))
sys.path.insert(0, str(AEVAL_REPO / "packages" / "agent-eval" / "src"))

from agent_eval.core.discovery import discover_extensions  # noqa: E402
from agent_eval.core.runner import EvalRunner  # noqa: E402
from agent_eval.core.suite import load_suite  # noqa: E402
from agent_eval.storage.sqlite import SqliteStorage  # noqa: E402
from app.eval_integration.config import create_aeval_runner  # noqa: E402
from app.eval_integration.errors import AgentRunError  # noqa: E402
from app.eval_integration.runner import PROBE_WORKSPACE_FILES  # noqa: E402


class MultiTurnAChatRunner:
    """在宿主 AChatAgentRunner 之上加多轮消费 (会话句柄 → 后续轮次)。

    复用宿主适配器的全部既有方法 (会话解析/种子/发送等待/清单/取证),
    只把「发一轮就收尾」改成「循环取下一轮, 会话结束才收尾」。
    """

    def __init__(self, inner):
        self.inner = inner
        self.name = f"multi-turn({inner.name})" if hasattr(inner, "name") else "multi-turn-achat"

    async def run(self, view, session):  # noqa: ANN001
        inner = self.inner
        started = time.monotonic()

        spec = inner._resolve_conversation(view)
        lead_agent = spec.agent_ids[0]
        agent_tag = "" if lead_agent == inner.agent_id else f"@{lead_agent}"
        conversation_id = await inner.client.create_conversation(
            title=f"{inner.conversation_title_prefix} {view.id}{agent_tag}".strip(),
            agent_id=lead_agent,
            mode=spec.mode,
            agent_ids=spec.agent_ids,
            dispatch_mode=spec.dispatch_mode,
        )
        trial = inner.coordinator.begin(conversation_id) if inner.coordinator else None

        try:
            pre_seed = await inner._collect_listing(conversation_id)
            if trial is not None:
                trial.pre_seed_files = pre_seed

            seeds = inner._seed_files(view)
            for path in sorted(seeds):
                await inner.client.fs_write(conversation_id, path, seeds[path])

            post_seed = await inner._collect_listing(conversation_id)
            if trial is not None:
                trial.post_seed_listing = post_seed

            run_ids = await inner._send_and_wait(conversation_id, view.prompt, started)
            all_run_ids = list(run_ids)

            # ── ⑤ 多轮循环: 轮次由框架经会话句柄供给 (预写话术/目标驱动) ──
            while True:
                next_turn = await session.next_user_message()
                if next_turn is None:
                    break
                turn_run_ids = await inner._send_and_wait(
                    conversation_id, next_turn, started
                )
                all_run_ids.extend(turn_run_ids)

            messages = await inner.client.list_messages(conversation_id)
            transcript = inner._normalize_transcript(messages)
            trace_id = await inner._resolve_trace_id(all_run_ids)
            artifacts = await inner.client.list_artifacts(conversation_id)
            probe_readings = await session.harness_probe(PROBE_WORKSPACE_FILES)
            if trial is not None and trial.final_listing is None:
                trial.final_listing = await inner._collect_listing(conversation_id)

            return inner._build_evidence(
                trace_id=trace_id,
                conversation_id=conversation_id,
                run_ids=all_run_ids,
                transcript=transcript,
                artifacts=artifacts,
                seed_files=sorted(seeds),
                probe_readings=probe_readings,
            )
        except AgentRunError as e:
            raise inner._with_elapsed(e, started) from None
        except Exception as e:
            raise inner._with_elapsed(
                AgentRunError(f"AChat multi-turn run failed: {e}", status="error"),
                started,
            ) from e
        finally:
            if inner.coordinator is None and inner.cleanup_conversations:
                await inner._safe_delete(conversation_id)


def _boundary(run) -> dict:
    b = run.evidence
    return {
        "environment_identity": b.environment_identity,
        "environment_version": b.environment_version,
        "redactor": b.redactor_identifier,
        "capture_tool_args": b.capture_tool_arguments,
    }


def _load_host_env_local() -> dict[str, str]:
    """解析宿主 backend/.env.local (驱动进程独立于后端 shell, 需自取凭证)。"""
    env: dict[str, str] = {}
    path = ACHAT_BACKEND / ".env.local"
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def _build_goal_llm_fn(settings):
    """目标驱动模拟器的 LLMFn 回退: 复用宿主已配置的 DeepSeek 凭证。

    宿主 .env.local 的 GUIDE_AGENT_API_KEY / SUMMARY_LLM_API_KEY 都是
    DeepSeek key (provider=deepseek); judge 专用凭证 (AEVAL_JUDGE_*) 未配
    时据此回退, 优先级与宿主 Guide Agent 同源。
    """

    import httpx

    host_env = _load_host_env_local()
    candidates = [  # (key 来源说明, key, base_url, model)
        ("GUIDE_AGENT_API_KEY", host_env.get("GUIDE_AGENT_API_KEY"),
         host_env.get("GUIDE_AGENT_API_BASE_URL") or "https://api.deepseek.com/v1",
         host_env.get("GUIDE_AGENT_MODEL_ID") or "deepseek-v4-flash"),
        ("JUDGE_LLM_API_KEY", host_env.get("JUDGE_LLM_API_KEY"),
         host_env.get("JUDGE_LLM_API_URL") or "https://api.deepseek.com/v1",
         host_env.get("JUDGE_LLM_MODEL") or "deepseek-chat"),
        ("SUMMARY_LLM_API_KEY", host_env.get("SUMMARY_LLM_API_KEY"),
         host_env.get("SUMMARY_LLM_API_URL") or "https://api.deepseek.com/v1",
         host_env.get("SUMMARY_LLM_MODEL") or "deepseek-chat"),
        ("settings.summary_llm_api_key", settings.summary_llm_api_key,
         "https://api.deepseek.com/v1", "deepseek-chat"),
        ("settings.deepseek_api_key", settings.deepseek_api_key,
         "https://api.deepseek.com/v1", "deepseek-chat"),
    ]
    candidates = [
        (src, k, u, m) for src, k, u, m in candidates if k
    ]
    if not candidates:
        return None
    base_url = candidates[0][2]
    model = candidates[0][3]
    print(f"goal-simulator llm_fn: {len(candidates)} credential candidate(s); "
          f"first: {candidates[0][0]} model={model} base={base_url}")

    async def llm_fn(system: str, user: str) -> str:
        last_error: Exception | None = None
        for source, key, url, model_id in candidates:
            try:
                async with httpx.AsyncClient(timeout=90.0) as client:
                    resp = await client.post(
                        f"{url}/chat/completions",
                        headers={"Authorization": f"Bearer {key}"},
                        json={
                            "model": model_id,
                            "messages": [
                                {"role": "system", "content": system},
                                {"role": "user", "content": user},
                            ],
                            # LongCat-2.0 属推理模型: 上下文一长, 300 全花在
                            # reasoning 上, 200 响应里没有 content (2026-09-13
                            # 活跑 run_88cdc382b877 实测) —— 给足余量
                            "max_tokens": 1500,
                        },
                    )
                    resp.raise_for_status()
                    text = str(
                        resp.json()["choices"][0]["message"].get("content") or ""
                    ).strip()
                    if not text:
                        raise RuntimeError(
                            "响应 200 但 message.content 为空 "
                            "(推理模型耗尽 max_tokens?)"
                        )
                    return text
            except Exception as e:  # noqa: BLE001 — 换下一个候选凭证
                last_error = e
                print(f"goal-simulator credential {source} failed: "
                      f"{type(e).__name__}: {str(e)[:120]}")
        raise RuntimeError(
            f"goal-simulator llm_fn: 所有候选凭证均失败 "
            f"({len(candidates)} 个): {last_error}"
        )

    return llm_fn


def _print_round(title: str, run) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")
    print(f"run_id: {run.run_id}  status: {run.status}")
    print(f"statistics_version: {run.statistics_version}")
    s = run.summary
    print(f"denominator: total={s.total_trials} valid={s.valid_trials} "
          f"invalid={s.invalid_trials} pending={s.pending_trials}")
    for task_id, trials in run.trials.items():
        for t in trials:
            print(f"\n  task={task_id} trial#{t.trial_index} "
                  f"verdict={t.verdict.value} reason={t.invalid_reason}")
            print(f"  session_diagnostics: {t.session_diagnostics}")
            ev = t.evidence
            if ev is not None:
                channels = [o.channel for o in ev.user_inputs]
                moments = [round(o.observed_at, 3) for o in ev.user_inputs]
                print(f"  user_inputs channels: {channels}")
                print(f"  user_inputs moments : {moments}")
                simulated = [
                    o for o in ev.transcript if o.channel == "simulated_user"
                ]
                for o in simulated:
                    print(f"  simulated_user: observed_by={o.observed_by.value} "
                          f"at={round(o.observed_at, 3)} "
                          f"content={str(o.value.get('content', ''))[:60]!r}")
                events = [
                    o for o in ev.transcript if o.channel == "environment_event"
                ]
                for o in events:
                    print(f"  injected_event: observed_by={o.observed_by.value} "
                          f"at={round(o.observed_at, 3)} "
                          f"event={str(o.value.get('event', ''))[:60]!r}")
            for g in t.grader_results:
                print(f"  grader {g.grader_name}: passed={g.passed} "
                      f"verdict={g.verdict.value} moment={g.judgment_moment} "
                      f"| {str(g.explanation)[:110]}")


async def main() -> None:
    # 驱动进程独立于后端服务, 需要先初始化宿主应用 DB (token 铸造要查用户表)
    from app.db.engine import init_db

    await init_db()

    base = await create_aeval_runner()
    registry = discover_extensions()
    catalog = registry.catalog()
    print("discovered extensions:", catalog)
    assert any(e["name"] == "achat_session_gate" for e in catalog.get("graders", [])), (
        "achat_session_gate 未被 entry-point 发现 —— 请先 pip install -e examples/achat/eval-ext"
    )

    multi = MultiTurnAChatRunner(base.agent_runner)
    storage = SqliteStorage(
        db_path=str(AEVAL_REPO / "aeval-acceptance-040.db")
    )
    await storage.initialize()

    from app.config import get_settings

    goal_llm_fn = base.llm_fn or _build_goal_llm_fn(get_settings())

    runner = EvalRunner(
        agent_runner=multi,
        trace_provider=base.trace_provider,
        trace_mapping=base.trace_mapping,
        storage=storage,
        environment=base.environment,
        graders=list(base._graders.values()),
        llm_fn=goal_llm_fn,
        metrics_registry=base.metrics_registry,
        extensions=registry,
        concurrency=1,
        per_trial_timeout=900.0,
    )

    # ── 9.3: 预写话术 + 事件注入 (零模拟器 LLM) ──
    suite1 = load_suite(AEVAL_REPO / "examples" / "achat" / "conversation-suite.yaml")
    run1 = await runner.run_suite(suite1)
    _print_round("ROUND 1: scripted turns + event injection", run1)

    # ── 9.4: 目标驱动模拟器 (收尾判定归框架) ──
    suite2 = load_suite(
        AEVAL_REPO / "examples" / "achat" / "conversation-goal-suite.yaml"
    )
    run2 = await runner.run_suite(suite2)
    _print_round("ROUND 2: goal-driven simulator", run2)

    # ── 9.5: 环境身份边界 —— 与 0.3.0 历史 run (无环境身份记录) 比较 ──
    print(f"\n{'=' * 70}\nENVIRONMENT IDENTITY BOUNDARY\n{'=' * 70}")
    print("run1 boundary:", _boundary(run1))
    print("run2 boundary:", _boundary(run2))
    comparable, reason = run1.evidence.compare_with(run2.evidence)
    print(f"run1 vs run2 (same env): comparable={comparable} reason={reason}")

    from agent_eval.api.routes.runs import _build_comparison

    historic = await base.storage.list_runs(limit=50)
    old_run = next(
        (r for r in historic
         if r.run_id not in (run1.run_id, run2.run_id)
         and r.evidence.environment_identity is None),
        None,
    )
    if old_run is not None:
        cmp_old = _build_comparison(run1, old_run)
        print(f"run1 vs 0.3.0 run {old_run.run_id}: "
              f"comparable={cmp_old['comparable']} "
              f"reason={cmp_old['not_comparable_reason']}")
    else:
        print("(无 0.3.0 历史 run 可对照 — 跳过该断言)")

    print(f"\nSUMMARY: round1={run1.run_id} round2={run2.run_id}")


if __name__ == "__main__":
    asyncio.run(main())
