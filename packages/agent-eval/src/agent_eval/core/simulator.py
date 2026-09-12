"""内置用户模拟器 (⑤: extension-contracts / 目标驱动模拟器)。

两种模式共用 ``UserSimulator`` 协议 (core/contract.py):

- ``ScriptedUserSimulator``: 预写话术按序弹出, 零模型调用 —— CI 与离线测试
  一律只跑这种 (确定性, 可复现);
- ``GoalDrivenUserSimulator``: 经既有 ``LLMFn`` 回调生成下一句, 缺配置返回
  带原因的不可用结论 (与 judge 缺配置同一条路), 不让 trial 崩溃。

模拟器的输入视图 ``SimulatorContext`` 从类型上不含期望输出与答案键 ——
答案无法经由对话被洗进被评系统。每句话术连同时刻与所用提示词由会话句柄
记入证据, 提示词在记录前过既有脱敏钩子。
"""

from __future__ import annotations

from typing import Any, Protocol

from agent_eval.core.contract import SimulatorContext, SimulatorReply

# LLMFn: (system, user) → text —— 与 metrics.llm_judge 同一注入约定
LLMFn = Any

# 目标驱动模拟器的收尾哨兵: 模型以它示意「目标已达成, 无需再追问」
GOAL_END_SENTINEL = "[END]"

DEFAULT_SYSTEM_PROMPT = (
    "你正在一次评测中扮演真实用户, 与一个 AI 助手多轮对话。"
    "只输出你(作为用户)的下一句话, 不要输出任何解释、前缀或引号。"
    "如果任务目标已经达成、你不会再追问任何内容, 只输出 [END]。"
)


class _Redactor(Protocol):
    def redact(self, value: Any) -> Any: ...


class ScriptedUserSimulator:
    """预写话术模拟器: 按序弹出, 用尽后返回 None (= 会话结束)。

    零模型调用、完全确定性 —— 同一声明重放得到同一会话。
    """

    name = "scripted"
    implementation_version = "1"

    def __init__(self, turns: list[str]):
        self._turns = list(turns)
        self._index = 0

    async def next_message(self, context: SimulatorContext) -> SimulatorReply | None:
        if self._index >= len(self._turns):
            return None
        text = self._turns[self._index]
        self._index += 1
        return SimulatorReply(text=text)


class GoalDrivenUserSimulator:
    """目标驱动模拟器: 依据目标与已有对话历史生成下一句用户话术。

    缺 LLM 配置时返回带原因的不可用结论 (``unavailable_reason``), 框架据此
    给 ``simulator_unavailable`` 的 invalid 判定 —— 不崩溃、不折成 agent 失败、
    同 run 其余 trial 照常完成。

    收尾判定权归框架 (design 开放问题 2 的既定倾向): 模拟器的 ``end`` 只是
    建议, 框架核对轮数上限/取消/预算边界后才结束会话。
    """

    name = "goal_driven"
    implementation_version = "1"

    def __init__(
        self,
        goal: str,
        llm_fn: LLMFn | None = None,
        *,
        redactor: _Redactor | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ):
        self.goal = goal
        self.llm_fn = llm_fn
        self.redactor = redactor
        self.system_prompt = system_prompt

    def _render_prompt(self, context: SimulatorContext) -> str:
        lines: list[str] = [f"任务描述: {context.description or '(无)'}"]
        if context.first_prompt:
            lines.append(f"你(用户)的第一条消息: {context.first_prompt}")
        lines.append(f"你的目标: {self.goal}")
        if context.history:
            lines.append("目前的对话:")
            for item in context.history:
                role = item.get("role", "?")
                channel = item.get("channel", "")
                content = item.get("content", "")
                if channel == "environment_event":
                    lines.append(f"  [环境事件] {content}")
                elif role == "assistant":
                    lines.append(f"  助手: {content}")
                else:
                    lines.append(f"  你: {content}")
        remaining = (
            f"已进行 {context.max_turns} 轮上限内的对话。" if context.max_turns else ""
        )
        lines.append(
            remaining
            + "请给出你的下一句话; 若目标已达成、无需继续, 只输出 [END]。"
        )
        return "\n".join(lines)

    def _redact(self, value: Any) -> Any:
        if self.redactor is None:
            return value
        try:
            return self.redactor.redact(value)
        except Exception:  # noqa: BLE001 — 脱敏钩子故障不阻断话术产出
            return value

    async def next_message(self, context: SimulatorContext) -> SimulatorReply | None:
        if self.llm_fn is None:
            return SimulatorReply(
                unavailable_reason=(
                    "目标驱动模拟器需要 LLM 回调 (llm_fn), 本次运行未注入: "
                    "请配置 EvalRunner(llm_fn=...) 或改用预写话术 (conversation.turns)"
                )
            )
        prompt = self._render_prompt(context)
        try:
            text = str(await self.llm_fn(self.system_prompt, prompt)).strip()
        except Exception as e:  # noqa: BLE001 — 生成故障 = 带原因的不可用
            return SimulatorReply(
                unavailable_reason=f"话术生成调用失败: {type(e).__name__}: {e}"
            )
        if not text:
            # 空回复当收尾信号处理不了, 按不可用报原因而不是静默结束
            return SimulatorReply(unavailable_reason="话术生成返回了空内容")
        end = GOAL_END_SENTINEL in text
        if end:
            cleaned = text.replace(GOAL_END_SENTINEL, "").strip()
            if not cleaned:
                # 纯 [END]: 目标达成, 无需再产出话术
                return SimulatorReply(text="", end=True, prompt=self._redact(prompt))
        return SimulatorReply(text=text, end=end, prompt=self._redact(prompt))
