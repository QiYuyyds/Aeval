"""判分输入必须对同一份归档证据确定 (spec: graders「判分输入必须对同一份归档证据确定」)。

子进程各自以随机 hash 种子启动, 集合迭代序因此逐进程不同 —— 集合派生内容一旦泄进
判分输入, 两个子进程构造出的提示词就不会逐字节相等 (design D2)。

刻意不给子进程设 ``PYTHONHASHSEED``: 固定种子会让两次构建必然一致, 于是这条守护
测试永远绿 —— 那正是它要防的那种测试。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest

from agent_eval.core.types import (
    EVIDENCE_STRENGTH,
    EvidenceKind,
    Observation,
    ObservedBy,
    TrialEvidence,
    TrialResult,
)
from agent_eval.graders._evidence import channel_levels
from agent_eval.graders.model_based import ModelBasedGrader
from agent_eval.metrics.llm_judge import render_trajectory_block

# 工具清单刻意取 5 个互异的名字: 旧实现 (``list(set(...))``) 下两个子进程给出同一
# 次序的概率只有 1/120, 再加逐字节比较与「等于有序渲染」这一条, 漏检空间可忽略。
TOOL_NAMES = ["web_search", "fs_write", "fs_read", "calculator", "sql_query"]


def multi_tool_transcript() -> list[dict]:
    """一份含多种工具调用的被评记录 (每个工具调用两次, 使清单走的是去重分支)。"""
    messages: list[dict] = [{"role": "user", "content": "统计一下这批订单并写进报告"}]
    for index, tool in enumerate(TOOL_NAMES + list(reversed(TOOL_NAMES))):
        messages.append({"role": "tool_call", "tool_name": tool, "content": f"call {index}"})
    messages.append({"role": "assistant", "content": "报告已写好"})
    return messages


def _build_in_child(case: str, payload: dict) -> bytes:
    """在与测试进程相互独立的子进程里构建一次判分输入, 返回其 UTF-8 字节。"""
    script = r"""
import json, sys
case, raw = sys.argv[1], json.loads(sys.argv[2])
if case == "model_based_prompt":
    from agent_eval.core.types import TrialResult
    from agent_eval.graders.model_based import ModelBasedGrader
    trial = TrialResult(trial_index=0, transcript=raw["transcript"])
    text = ModelBasedGrader()._build_prompt(trial, raw["rubric"], raw["dimensions"])
elif case == "trajectory_block":
    from agent_eval.core.types import Observation
    from agent_eval.metrics.llm_judge import render_trajectory_block
    text = render_trajectory_block([Observation(**item) for item in raw["observations"]])
elif case == "probe_variants":
    from agent_eval.core.types import TrialResult
    from agent_eval.graders.model_based import ModelBasedGrader
    from agent_eval.graders.presentation_probes import (
        DEFAULT_PROBE_OPERATORS,
        JudgePresentation,
    )

    trial = TrialResult(trial_index=0, transcript=raw["transcript"])
    presentation = JudgePresentation(raw["anchor_value"], tuple(raw["dimensions"]))
    text = "\n".join(
        ModelBasedGrader()._build_prompt(
            trial, raw["rubric"], list(v.dimensions), anchor_value=v.anchor_value
        )
        for operator in DEFAULT_PROBE_OPERATORS
        for v in operator.variants(presentation)
    )
else:
    raise SystemExit("unknown case: " + case)
sys.stdout.buffer.write(text.encode("utf-8"))
"""
    proc = subprocess.run(
        [sys.executable, "-c", script, case, json.dumps(payload)],
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return proc.stdout


def trajectory_observations() -> list[dict]:
    """跨三种来源、混合 transcript 与 step 两类通道, 外加一条「没取到」的读数。"""
    return [
        {"kind": "transcript", "observed_by": "runner", "value": {"role": "user", "content": "查订单"}},
        {
            "kind": "step",
            "observed_by": "harness",
            "value": {"tool": "fs_write", "ok": True},
        },
        {"kind": "transcript", "observed_by": "subject", "value": {"role": "assistant", "content": "完成"}},
        {"kind": "step", "observed_by": "runner", "value": {"tool": "web_search", "ok": True}},
        {
            "kind": "state",
            "observed_by": "harness",
            "absent_reason": "probe_timeout",
            "value": None,
        },
    ]


class TestJudgePromptIsDeterministicAcrossProcesses:
    def test_model_based_prompt_is_byte_identical_across_two_processes(self):
        payload = {
            "transcript": multi_tool_transcript(),
            "rubric": "报告须含订单总数",
            "dimensions": ["correctness", "completeness"],
        }
        first = _build_in_child("model_based_prompt", payload)
        second = _build_in_child("model_based_prompt", payload)
        assert first == second, (first.decode("utf-8"), second.decode("utf-8"))
        # 逐字节相等之外再钉住「等于按内容排序的那一份」: 只比两次结果的话,
        # 两个子进程同时抖到同一个错次序也会通过
        assert repr(sorted(TOOL_NAMES)).encode("utf-8") in first

    def test_trajectory_block_is_byte_identical_across_two_processes(self):
        """design D5 的「metrics 无集合派生内容进提示词」是判读结论, 这里钉成断言。"""
        payload = {"observations": trajectory_observations()}
        first = _build_in_child("trajectory_block", payload)
        second = _build_in_child("trajectory_block", payload)
        assert first == second, (first.decode("utf-8"), second.decode("utf-8"))
        assert first == render_trajectory_block(
            [Observation(**item) for item in payload["observations"]]
        ).encode("utf-8")

    def test_probe_variant_rendering_is_byte_identical_across_two_processes(self):
        """呈现探针的变体构造纳入本文件的覆盖面 (change add-judge-presentation-probes 2.3)。

        探针自己不得成为它要测的那个毛病: 同一份判分配置在两个进程里必须构造出
        逐字节相同的四份判分输入 (基线 1 + 锚定变体 2 + 逆序变体 1, design D3)。
        """
        payload = {
            "transcript": multi_tool_transcript(),
            "rubric": "报告须含订单总数",
            "dimensions": ["correctness", "completeness"],
            "anchor_value": "0.0",
        }
        first = _build_in_child("probe_variants", payload)
        second = _build_in_child("probe_variants", payload)
        assert first == second, (first.decode("utf-8"), second.decode("utf-8"))
        # 两个算子各自构造出来的呈现全都在这里 (锚定 3 + 次序 2); 跨算子去重到 4 次
        # 调用是探针的事, 由 test_presentation_probes 的成本报价那条钉住
        assert first.count("## 评分标准".encode()) == 5, "五份呈现都得以构造出来"

    def test_trajectory_block_only_holds_ordered_readings(self):
        """次序来自输入列表本身: 每条读数占一行、按声明次序编号, 不夹带任何派生清单。"""
        block = render_trajectory_block(
            [Observation(**item) for item in trajectory_observations()]
        )
        # 4 行 = 4 条可渲染读数 (第 5 条是「没取到」, 不渲染); 多出一行即派生内容
        assert len(block.splitlines()) == 4
        assert re.findall(r"\[(?:消息|事件) \d+\]", block) == [
            "[消息 0]",
            "[事件 0]",
            "[消息 1]",
            "[事件 1]",
        ]


class TestPromptRegressionGuards:
    def test_tool_listing_is_rendered_in_sorted_order(self):
        trial = TrialResult(trial_index=0, transcript=multi_tool_transcript())
        prompt = ModelBasedGrader()._build_prompt(trial, "r", ["quality"])
        assert f"- 使用的工具: {repr(sorted(TOOL_NAMES))}" in prompt


class TestEvidenceLevelStrengthsAreDistinct:
    """channel_levels 对 set 排序的确定性, 全靠各级强度两两不同 (design D4)。"""

    def test_every_level_has_a_pairwise_distinct_strength(self):
        levels = list(ObservedBy)
        strengths = [EVIDENCE_STRENGTH[level.value] for level in levels]
        assert len(set(strengths)) == len(levels), strengths

    def test_strength_table_covers_exactly_the_declared_levels(self):
        assert set(EVIDENCE_STRENGTH) == {level.value for level in ObservedBy}

    def test_channel_levels_orders_by_strength_not_by_set_iteration(self):
        evidence = _evidence_from_levels(
            [ObservedBy.SUBJECT, ObservedBy.HARNESS, ObservedBy.RUNNER]
        )
        assert channel_levels(evidence, "transcript") == [
            ObservedBy.HARNESS,
            ObservedBy.RUNNER,
            ObservedBy.SUBJECT,
        ]


def _evidence_from_levels(levels: list[ObservedBy]) -> TrialEvidence:
    return TrialEvidence(
        transcript=[
            Observation(kind=EvidenceKind.TRANSCRIPT, observed_by=level, value="v")
            for level in levels
        ]
    )


@pytest.mark.parametrize("dimensions", [["quality"], ["a", "b", "c"]])
def test_dimensions_render_in_configuration_order(dimensions):
    """维度是配置项, 按配置序渲染 —— 与工具清单的「按内容排序」是两条不同的序。"""
    prompt = ModelBasedGrader()._build_prompt(
        TrialResult(trial_index=0, transcript=[]), "r", dimensions
    )
    assert f"## 评分维度\n{', '.join(dimensions)}" in prompt
