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
from collections.abc import Callable
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

# Type alias for LLM function: (system_prompt, user_message) -> str
LLMFn = Callable[[str, str], str]

# 未显式配置模型时的默认判定模型 (随每次判定落盘, 使「换了 judge」这件事可追溯)
DEFAULT_JUDGE_MODEL = "gpt-4o-mini"


class ModelBasedGrader:
    """LLM-as-Judge 评分器"""

    name = "model_based"
    # judge 读的是对话正文: 正文可能是 agent 自述, 因此声明到 subject 一级,
    # 由套件的 allow_subject 决定它能不能单独定案
    evidence_levels = (ObservedBy.HARNESS, ObservedBy.RUNNER, ObservedBy.SUBJECT)
    implementation_version = "2"

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
        dimensions = config.get("dimensions", ["quality"])
        threshold = config.get("threshold", 0.7)

        if not dimensions:
            return no_criteria_result(
                self.name, GraderType.MODEL, "dimensions", details={"rubric": rubric}
            )

        # Build judge prompt
        prompt = self._build_prompt(trial, rubric, dimensions)

        # Call LLM
        llm_fn = self._llm_fn or self._default_llm_fn(config)
        try:
            raw = llm_fn("You are an evaluation expert.", prompt)
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
        avg_score = sum(scores.values()) / len(dimensions)
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
    ) -> str:
        """构建 judge prompt"""
        input_msg = trial.transcript[0] if trial.transcript else "N/A"
        output_msg = trial.transcript[-1] if trial.transcript else "N/A"

        # 提取工具调用摘要
        tools_used = list(set(
            msg.get("tool_name", "")
            for msg in trial.transcript
            if msg.get("role") == "tool_call"
        ))

        dims_json = ", ".join(f'"{d}": 0.0' for d in dimensions)

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
