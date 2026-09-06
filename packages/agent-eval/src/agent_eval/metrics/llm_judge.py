"""LLM Judge infrastructure — protocol-injected LLM function + tolerant JSON parsing.

D2: the framework core only knows the LLMFn protocol
(async (system_prompt, user_message) -> raw text); no LLM SDK is bound here.
Assembly of a concrete implementation lives in eval_integration.config.

0.3.0: judge 指标按声明读取轨迹 (capability: llm-metrics) —— 声明含
transcript/steps 通道时, :func:`judge_user_prompt` 把轨迹 (经脱敏钩子后)
注入提示词; 未声明时提示词与 0.2.0 逐字节等价。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from agent_eval.core.types import EvidenceKind, MeasurementContext, Observation

# LLM 函数协议: (system_prompt, user_message) → raw text
LLMFn = Callable[[str, str], Awaitable[str]]

# 解析失败后的最大重试次数 (每次重试重新调用 LLM)
DEFAULT_MAX_RETRIES = 2

# 轨迹注入的提示词节标题: 要求引用具体消息编号 (不强制 —— judge 可以只给总分)
TRAJECTORY_PROMPT_HEADER = (
    "\n\n## Agent 轨迹 (按取信声明提供)\n"
    "以下按时间顺序列出该次执行的过程读数。给出结论时, 若引用轨迹中的具体"
    "事件或消息, 请标注其编号 (如 [消息 2] / [事件 1]); 不引用也可以。\n"
)


class LLMJudgeError(Exception):
    """LLM Judge 调用/解析最终失败 (重试用尽)。"""


class LLMNotConfiguredError(Exception):
    """未注入 LLM 函数却调用了依赖 LLM 的指标 — 明确配置错误。"""


def require_llm_fn(llm_fn: LLMFn | None) -> LLMFn:
    """断言 LLM 函数已注入, 否则抛出明确配置错误 (而非静默 0 分)。"""
    if llm_fn is None:
        raise LLMNotConfiguredError(
            "LLM function not configured — inject llm_fn (eval_integration "
            "assembles one from AEVAL_JUDGE_* / eval LLM settings) or pass a "
            "stub in tests."
        )
    return llm_fn


def extract_json_object(raw: str) -> dict[str, Any] | None:
    """
    容错提取 LLM 输出中的 JSON 对象。

    容忍 ```json 围栏、前后缀说明文本; 失败返回 None。
    """
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def judge_json(
    llm_fn: LLMFn,
    system_prompt: str,
    user_prompt: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict[str, Any]:
    """
    调用 LLM 并解析 JSON 响应; 解析失败带完整上下文重试。

    Raises:
        LLMNotConfiguredError: llm_fn 为 None
        LLMJudgeError: 重试用尽仍无法解析 (附最后一次原始输出)
    """
    require_llm_fn(llm_fn)

    last_raw = ""
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            last_raw = await llm_fn(system_prompt, user_prompt)
        except Exception as e:
            # 传输层错误同样重试 (judge 输出不稳定/网络抖动, design §Risks)
            last_error = e
            if attempt < max_retries:
                await asyncio.sleep(0)
                continue
            raise LLMJudgeError(f"LLM call failed after {max_retries + 1} attempts: {e}") from e

        parsed = extract_json_object(last_raw)
        if parsed is not None:
            return parsed
        last_error = LLMJudgeError("response is not a parsable JSON object")

    raise LLMJudgeError(
        f"LLM judge failed after {max_retries + 1} attempts ({last_error}); "
        f"last response: {last_raw[:200]!r}"
    )


# ─── 轨迹注入 (spec: llm-metrics judge 按声明读取轨迹) ────────────────────────


def _redact(value: Any, redactor: Any | None) -> Any:
    """轨迹正文过脱敏钩子; 未注入脱敏器时原样 (调用方自担明文进提示词的责任)。"""
    if redactor is None:
        return value
    return redactor.redact(value)


def _message_text(value: Any) -> tuple[str, str]:
    """一条 transcript 读数 → (角色, 正文); 非 dict 读数按事件正文处理。"""
    if isinstance(value, dict):
        return str(value.get("role", "unknown")), str(value.get("content", ""))
    return "event", str(value)


def render_trajectory_block(
    observations: list[Observation],
    *,
    redactor: Any | None = None,
) -> str:
    """把声明通道内的观测渲染为可引用的轨迹文本 (逐条编号, 经脱敏钩子)。

    transcript 读数编号为 ``[消息 N]``, steps 读数编号为 ``[事件 N]`` ——
    judge 的解释引用编号即可定位到具体事件。缺失读数不渲染 (没取到的
    读数冒充正文等于伪造轨迹)。
    """
    message_no = 0
    step_no = 0
    lines: list[str] = []
    for obs in observations:
        if obs.is_absent:
            continue
        if obs.kind is EvidenceKind.TRANSCRIPT:
            role, content = _message_text(obs.value)
            content = _redact(content, redactor)
            source = obs.observed_by.value
            lines.append(f"[消息 {message_no}] ({role}, 来源: {source}) {content}")
            message_no += 1
        elif obs.kind is EvidenceKind.STEP:
            detail = _redact(obs.value, redactor)
            lines.append(f"[事件 {step_no}] (来源: {obs.observed_by.value}) {detail!r}")
            step_no += 1
    return "\n".join(lines)


def judge_user_prompt(
    base_user_prompt: str,
    ctx: MeasurementContext,
    *,
    redactor: Any | None = None,
) -> str:
    """按声明把轨迹追加进 judge 提示词; 未声明时原样返回 (0.2.0 逐字节等价)。

    声明了轨迹通道 (transcript/steps) 才注入; 声明了但本次没有任何可渲染读数
    也不加节标题 —— 提示词里不出现空的「轨迹」承诺。
    """
    if not ctx.declaration.wants_trajectory:
        return base_user_prompt
    block = render_trajectory_block(ctx.observations, redactor=redactor)
    if not block:
        return base_user_prompt
    return f"{base_user_prompt}{TRAJECTORY_PROMPT_HEADER}{block}"
