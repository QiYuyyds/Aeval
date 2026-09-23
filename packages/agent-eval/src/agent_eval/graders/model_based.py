"""
Model-based grader — LLM-as-Judge scoring.

Sends the trial transcript and a rubric to an LLM, which returns
a score between 0 and 1.

Config schema:
    {
        "rubric": "The response must contain...",
        "dimensions": ["correctness", "completeness"],
        "threshold": 0.7,
        "model": "gpt-4o-mini",  # optional
    }

Requires either:
- A configured llm_fn callback, or
- An API key in the environment (OPENAI_API_KEY, etc.)
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable, Sequence
from typing import Any

from agent_eval.core.contract import EvalContext
from agent_eval.core.types import (
    EvalTask,
    GraderResult,
    GraderType,
    InvalidReason,
    ObservedBy,
    TrialResult,
    TrialVerdict,
)
from agent_eval.graders._evidence import consulted_levels, implementation_version_of
from agent_eval.graders._verdicts import no_criteria_result
from agent_eval.graders.presentation_probes import (
    JudgePresentation,
    PresentationJudgment,
)

# Type alias for LLM function: (system_prompt, user_message) -> str
LLMFn = Callable[[str, str], str]

# 未显式配置模型时的默认判定模型 (随每次判定落盘, 使「换了 judge」这件事可追溯)
DEFAULT_JUDGE_MODEL = "gpt-4o-mini"

# 判分口径常量: 生产路径与呈现探针共用同一份, 否则探针报的"结论翻转"不是生产那个结论
DEFAULT_DIMENSIONS: tuple[str, ...] = ("quality",)
DEFAULT_THRESHOLD = 0.7
SYSTEM_PROMPT = "You are an evaluation expert."
# 示例 JSON 里的预填值 —— 一个框架自己写进提示词的显式锚。默认值使提示词与探针之前
# 逐字节相同; 呈现探针的 anchor_value 算子以此为基线构造变体 (design D2)。
DEFAULT_JUDGE_ANCHOR_VALUE = "0.0"


def score_dimensions(scores: dict[str, float], dimensions: Sequence[str]) -> float:
    """多维度合成分 —— 生产与呈现探针共用的那一份口径。

    分母固定为配置的维度全集 (缺席维度不得靠"只平均已给分维度"充值)。求和序就是
    ``dimensions`` 序, 而浮点加法不可结合 —— 这是 design D2 里 ``dimension_order``
    算子的末位浮点混淆项来源。框架在同一份配置下今天是确定性的, 没有待修缺陷,
    为测试工具去改生产算术是反的, 所以这里刻意保持原样。
    """
    return sum(scores.values()) / len(dimensions)


class ModelBasedGrader:
    """LLM-as-Judge 评分器"""

    name = "model_based"
    # judge 读的是对话正文: 正文可能是 agent 自述, 因此声明到 subject 一级,
    # 由套件的 allow_subject 决定它能不能单独定案
    evidence_levels = (ObservedBy.HARNESS, ObservedBy.RUNNER, ObservedBy.SUBJECT)
    implementation_version = "3"

    def __init__(self, llm_fn: LLMFn | None = None):
        """
        Args:
            llm_fn: (system_prompt, user_message) → response text.
                    If None, uses OpenAI API with OPENAI_API_KEY.
        """
        self._llm_fn = llm_fn

    async def grade(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None = None,
    ) -> GraderResult:
        config = task.get_grader_config(self.name)
        rubric = config.get("rubric", "")
        dimensions = list(config.get("dimensions", DEFAULT_DIMENSIONS))
        threshold = config.get("threshold", DEFAULT_THRESHOLD)

        if not dimensions:
            return no_criteria_result(
                self.name, GraderType.MODEL, "dimensions", details={"rubric": rubric}
            )

        # Build judge prompt
        prompt = self._build_prompt(trial, rubric, dimensions)

        # Call LLM
        llm_fn = self.resolve_llm_fn(config)
        try:
            raw = llm_fn(SYSTEM_PROMPT, prompt)
        except Exception as e:
            # judge 不可用是评测侧故障, 不得折成 agent 的 0 分
            return GraderResult(
                grader_name=self.name,
                grader_type=GraderType.MODEL,
                score=0.0,
                passed=False,
                explanation=f"LLM call failed: {e}",
                verdict=TrialVerdict.INVALID,
                invalid_reason=InvalidReason.JUDGE_UNAVAILABLE,
            )

        # Parse scores
        scores = self._parse_scores(raw, dimensions)

        if not scores:
            return GraderResult(
                grader_name=self.name,
                grader_type=GraderType.MODEL,
                score=0.0,
                passed=False,
                explanation=f"LLM Judge 输出无法解析出任何维度分数: {raw!r}",
                details={"raw_response": raw, "dimensions": {}},
                verdict=TrialVerdict.INVALID,
                invalid_reason=InvalidReason.VERDICT_UNPARSEABLE,
            )

        # 分母固定为配置的全集维度数: 缺席维度不得被「只平均已给分维度」充值
        avg_score = score_dimensions(scores, dimensions)
        missing = [d for d in dimensions if d not in scores]
        judged = {
            "judge_model": config.get("model", DEFAULT_JUDGE_MODEL),
            "grader_version": implementation_version_of(self),
        }
        if missing:
            return GraderResult(
                grader_name=self.name,
                grader_type=GraderType.MODEL,
                score=max(0.0, min(1.0, avg_score)),
                passed=False,
                explanation=f"LLM Judge 未返回全部维度 (缺席: {missing})",
                details={"dimensions": scores, "missing": missing, "raw_response": raw, **judged},
                verdict=TrialVerdict.INVALID,
                invalid_reason=InvalidReason.VERDICT_UNPARSEABLE,
            )

        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.MODEL,
            score=max(0.0, min(1.0, avg_score)),
            passed=avg_score >= threshold,
            explanation=f"LLM Judge scores: {scores}",
            details={"dimensions": scores, "raw_response": raw, **judged},
            evidence_levels=consulted_levels(
                context.evidence if context is not None else None, "transcript"
            ),
        )

    def _build_prompt(
        self,
        trial: TrialResult,
        rubric: str,
        dimensions: list[str],
        anchor_value: str | None = DEFAULT_JUDGE_ANCHOR_VALUE,
    ) -> str:
        """构建 judge prompt

        ``anchor_value`` 是示例 JSON 里的预填值: 默认值使提示词与本字段出现前逐字节
        相同, 传 ``None`` 得到「不给值」的那一份呈现。它是呈现层的自由度, 不是判分
        内容 —— 证据正文、rubric、维度集合都不经它改变。
        """
        input_msg = trial.transcript[0] if trial.transcript else "N/A"
        output_msg = trial.transcript[-1] if trial.transcript else "N/A"

        # 提取工具调用摘要 —— 必须 sorted: set 迭代序随进程 hash 种子变化,
        # 不排序等于同一批归档字节每次重评读到不同的工具清单
        tools_used = sorted({
            msg.get("tool_name", "")
            for msg in trial.transcript
            if msg.get("role") == "tool_call"
        })

        example_value = anchor_value if anchor_value is not None else "..."
        dims_json = ", ".join(f'"{d}": {example_value}' for d in dimensions)

        return f"""请根据以下评分标准对 Agent 表现进行评分。

## 评分标准
{rubric}

## 评分维度
{", ".join(dimensions)}

## Agent 执行记录
- 输入: {input_msg}
- 输出: {output_msg}
- 使用的工具: {tools_used or "无"}

请以 JSON 格式返回各维度评分 (0.0-1.0):
```json
{{{dims_json}}}
```"""

    def baseline_presentation(self, task: EvalTask) -> JudgePresentation:
        """生产今天实际送出去的那一份呈现 —— 呈现探针的基线。

        基线从配置现读, 不写死: 预填值哪天变了, 探针要比的"改变"就得跟着变。
        """
        config = task.get_grader_config(self.name)
        return JudgePresentation(
            anchor_value=DEFAULT_JUDGE_ANCHOR_VALUE,
            dimensions=tuple(config.get("dimensions", DEFAULT_DIMENSIONS)),
        )

    def judge_presentation(
        self,
        trial: TrialResult,
        task: EvalTask,
        presentation: JudgePresentation,
    ) -> PresentationJudgment:
        """就同一份归档证据，在指定呈现下问一次 judge。

        刻意不返回 ``GraderResult`` / ``TrialResult``: 探针的读数不是判定条目, 不进
        ``grade_attempts``、不移动 ``current``、不进任何分母或门禁 (spec: graders)。
        除此之外口径与 ``grade()`` 严格同源 —— 同一个提示词构造、同一个解析、同一个
        ``threshold`` 比较, 否则"结论翻转"量的就不是生产会做出的那个结论。
        """
        config = task.get_grader_config(self.name)
        threshold = config.get("threshold", DEFAULT_THRESHOLD)
        dimensions = list(presentation.dimensions)
        if not dimensions:
            return PresentationJudgment(None, None, note="判据未配置任何维度")

        prompt = self._build_prompt(
            trial, config.get("rubric", ""), dimensions, anchor_value=presentation.anchor_value
        )
        try:
            raw = self.resolve_llm_fn(config)(SYSTEM_PROMPT, prompt)
        except Exception as e:
            # judge 不可用是"没测", 不是"不敏感" —— 交出去由报告报不可计算, 不冒充结论
            return PresentationJudgment(None, None, note=f"LLM call failed: {e}")

        scores = self._parse_scores(raw, dimensions)
        if not scores:
            return PresentationJudgment(None, None, note=f"输出无法解析出维度分数: {raw!r}")
        avg_score = score_dimensions(scores, dimensions)
        score = max(0.0, min(1.0, avg_score))
        missing = [d for d in dimensions if d not in scores]
        if missing:
            # 生产在这条路径上判 invalid (缺席维度), 探针同样不给出通过/失败
            return PresentationJudgment(score, None, note=f"未返回全部维度 (缺席: {missing})")
        return PresentationJudgment(score, avg_score >= threshold)

    def _parse_scores(self, raw: str, dimensions: list[str]) -> dict[str, float]:
        """从 LLM 响应中解析维度分数。

        只返回 judge 实际给出的维度; 解析不出任何维度时返回空 dict,
        由调用方判为 invalid (不再给各维度兜底 0.5 分)。
        """
        scores: dict[str, float] = {}

        # 尝试提取 JSON
        try:
            # 查找 JSON 块
            json_start = raw.find("{")
            json_end = raw.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                json_str = raw[json_start:json_end]
                parsed = json.loads(json_str)
                for dim in dimensions:
                    if dim in parsed:
                        with contextlib.suppress(ValueError, TypeError):
                            scores[dim] = max(0.0, min(1.0, float(parsed[dim])))
        except json.JSONDecodeError:
            pass

        return scores

    def resolve_llm_fn(self, config: dict[str, Any]) -> LLMFn:
        """本次判定实际要调的那个 judge: 注入的回调优先, 否则按配置走默认实现。

        生产与探针共用它 —— 两条路径必须问同一把 judge, 否则比的就不是呈现。
        """
        return self._llm_fn or self._default_llm_fn(config)

    def _default_llm_fn(self, config: dict[str, Any]) -> LLMFn:
        """创建默认的 LLM 调用函数"""
        model = config.get("model", "gpt-4o-mini")

        def call_llm(system: str, user: str) -> str:
            try:
                from openai import OpenAI

                client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=0.0,
                    max_tokens=500,
                )
                return resp.choices[0].message.content or ""
            except ImportError as e:
                raise RuntimeError(
                    "openai package not installed. "
                    "Install with: pip install openai"
                ) from e
            except Exception as e:
                raise RuntimeError(f"OpenAI API call failed: {e}") from e

        return call_llm
