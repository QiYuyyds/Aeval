"""AChat host-side Aeval extension: entry-point registered custom grader."""

from agent_eval.core.contract import EvalContext, Grader
from agent_eval.core.types import (
    GraderResult,
    GraderType,
    TrialResult,
    TrialVerdict,
)


class AchatSessionGateGrader(Grader):
    """会话完整性门: 该 task 声明了会话维度时, 要求轮次真被消费过。

    只信评测侧证据 (evidence.user_inputs 是框架落盘的用户侧输入序列,
    来源 harness) —— 被评方自报「我做了三轮」不算数。
    """

    name = "achat_session_gate"
    grader_type = GraderType.CUSTOM

    async def grade(
        self,
        trial: TrialResult,
        spans,
        task,
        context: EvalContext | None = None,
    ) -> GraderResult:
        declared_conversation = task.conversation is not None
        user_inputs = list(trial.evidence.user_inputs) if trial.evidence else []
        events = [
            o for o in user_inputs if o.channel == "environment_event"
        ]
        declared_events = bool(
            task.conversation and task.conversation.events
        )
        if declared_conversation and len(user_inputs) < 2:
            return GraderResult(
                grader_name=self.name,
                grader_type=self.grader_type,
                score=0.0,
                passed=False,
                explanation=(
                    "task 声明了会话, 但用户侧输入序列少于 2 条 "
                    "(首轮 + 至少一条后续话术) —— 会话未真正发生"
                ),
            )
        if declared_events and not events:
            return GraderResult(
                grader_name=self.name,
                grader_type=self.grader_type,
                score=0.0,
                passed=False,
                explanation="task 声明了事件注入, 但没有记录到任何注入事件",
            )
        if trial.verdict is TrialVerdict.INVALID:
            return GraderResult(
                grader_name=self.name,
                grader_type=self.grader_type,
                score=0.0,
                passed=False,
                explanation=f"trial 非 valid ({trial.invalid_reason})",
            )
        return GraderResult(
            grader_name=self.name,
            grader_type=self.grader_type,
            score=1.0,
            passed=True,
            explanation=(
                f"会话完整: {len(user_inputs)} 条用户侧输入 "
                f"(含 {len(events)} 条事件), 全部为 harness 来源"
            ),
        )
