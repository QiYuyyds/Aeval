"""判分呈现不变性探针 (spec: graders「判分器可被呈现探针检视且探针不产出结论」)。

这组测试用**构造性替身**双向钉住机制 (design D6):

- 构造敏感的替身必须被报出敏感 —— 否则探针发现不了东西;
- 构造不敏感的替身必须不被误报 —— 否则探针只是"总能报出点什么"。

**两条合起来只证明机制有效, 不证明真实 judge 敏感或不敏感。** 真实锚定敏感度要等
一把可用的 judge 凭证 (宿主四把候选截至 2026-09-22 全为 401/402)。这里出现的每一个
数字都是替身判据的产物, 引用时不得冠以"真实 LLM 的翻转率"。

全程离线、零凭证: 替身就是 llm_fn, 不碰任何 API、不碰存储。
"""

from __future__ import annotations

import inspect
import itertools
import re

import pytest

from agent_eval.core import discovery
from agent_eval.core.comparison import runs_comparable
from agent_eval.core.metrics import MIN_ALIGNED_RATINGS_FOR_AGREEMENT
from agent_eval.core.runner import CALIBER_AXES, EvalRunner
from agent_eval.core.types import (
    EvalSuite,
    EvalTask,
    GradeAttempt,
    GraderConfig,
    GraderResult,
    GraderType,
    TrialResult,
)
from agent_eval.examples.mock_runner import MockAgentRunner, MockTraceProvider
from agent_eval.graders import model_based, presentation_probes
from agent_eval.graders.model_based import DEFAULT_JUDGE_ANCHOR_VALUE, ModelBasedGrader
from agent_eval.graders.presentation_probes import (
    DEFAULT_PROBE_OPERATORS,
    SUMMATION_ORDER_QUALIFIER,
    AnchorValueProbe,
    DimensionOrderProbe,
    JudgePresentation,
    PresentationProbeOperator,
    PresentationVariant,
    draw_trials,
    presentation_probe_cost_quote,
    run_presentation_probes,
)
from agent_eval.storage.memory import MemoryStorage

DIMENSIONS = ["quality", "completeness"]
THRESHOLD = 0.7
SEED = 2026_0922
N_TRIALS = 6

EXAMPLE_JSON = re.compile(r"^\{.*\}$", re.MULTILINE)

# 探针之前 (change make-judge-prompt-deterministic 落地后) 生产实际送出的那一份判分
# 输入。逐字节冻在这里, 是为了让"探针不得改变不开探针时的任何行为"这条要求有一个
# 可以撞的目标, 而不是靠人读 diff。
P0_FROZEN_PROMPT = (
    "请根据以下评分标准对 Agent 表现进行评分。\n"
    "\n"
    "## 评分标准\n"
    "报告须含订单总数\n"
    "\n"
    "## 评分维度\n"
    "correctness, completeness\n"
    "\n"
    "## Agent 执行记录\n"
    "- 输入: {'role': 'user', 'content': '查订单'}\n"
    "- 输出: {'role': 'assistant', 'content': '完成'}\n"
    "- 使用的工具: 无\n"
    "\n"
    "请以 JSON 格式返回各维度评分 (0.0-1.0):\n"
    "```json\n"
    '{"correctness": 0.0, "completeness": 0.0}\n'
    "```"
)


def judge_task(
    dimensions: list[str] | None = None, threshold: float = THRESHOLD
) -> EvalTask:
    return EvalTask(
        id="t1",
        prompt="统计这批订单并把总数写进报告",
        max_trials=N_TRIALS,
        graders=[
            GraderConfig(
                type=GraderType.MODEL,
                name="model_based",
                config={
                    "rubric": "报告须含订单总数",
                    "dimensions": dimensions or DIMENSIONS,
                    "threshold": threshold,
                },
            )
        ],
    )


def trials(n: int = N_TRIALS) -> list[TrialResult]:
    """n 份内容相同的归档证据 —— 对照组只需要"同一份证据的不同呈现"。"""
    return [
        TrialResult(
            trial_index=i,
            transcript=[
                {"role": "user", "content": "统计这批订单"},
                {"role": "assistant", "content": "订单总数 42, 报告已写好"},
            ],
        )
        for i in range(n)
    ]


def example_line(prompt: str) -> str:
    """提示词里那行示例 JSON —— 模板预填值就住在这里。"""
    found = EXAMPLE_JSON.search(prompt)
    assert found is not None, prompt
    return found.group(0)


def anchor_of(prompt: str) -> str:
    return re.search(r":\s*([^,}]+)", example_line(prompt)).group(1).strip()


def scores_json(values: dict[str, float]) -> str:
    return "{\n" + ",\n".join(f'"{d}": {v}' for d, v in values.items()) + "\n}"


def probe_for(judge, *, dimensions=None, threshold=THRESHOLD, n_trials=N_TRIALS, **kwargs):
    kwargs.setdefault("sample", 1.0)
    kwargs.setdefault("seed", SEED)
    return run_presentation_probes(
        ModelBasedGrader(llm_fn=judge),
        task=judge_task(dimensions, threshold),
        trials=trials(n_trials),
        **kwargs,
    )


class AnchorReactiveJudge:
    """构造敏感替身: 判据按定义把分数回归到提示词展示的预填值。

    这正是 judge 的已知行为之一 (回归到展示值), 也是本探针要能发现的那类效应。
    """

    def __init__(self) -> None:
        self.calls = 0
        self.seen_anchors: list[str] = []

    def __call__(self, system: str, user: str) -> str:
        self.calls += 1
        anchor = anchor_of(user)
        self.seen_anchors.append(anchor)
        # 读得出锚就回归到锚; 读不出 (不给值的那一份) 时退到证据正文给个中间分
        value = float(anchor) if re.fullmatch(r"\d+(?:\.\d+)?", anchor) else 0.5
        return scores_json({d: value for d in DIMENSIONS})


class BodyOnlyJudge:
    """构造不敏感替身: 只读被评正文, 对呈现细节完全无感。"""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, system: str, user: str) -> str:
        self.calls += 1
        output = re.search(r"^- 输出: (.*)$", user, re.MULTILINE).group(1)
        value = 1.0 if "订单总数" in output else 0.0
        dims = re.search(r"^## 评分维度\n(.*)$", user, re.MULTILINE).group(1)
        return scores_json({d.strip(): value for d in dims.split(",")})


class DriftOnlyJudge:
    """分数随锚定值移动, 但两份都在阈值同一侧 —— 漂移不该被读成翻转。"""

    def __call__(self, system: str, user: str) -> str:
        value = 0.78 if anchor_of(user) == "0.0" else 0.72
        return scores_json({d: value for d in DIMENSIONS})


class FixedScoresJudge:
    """判据只认维度名, 与呈现无关。

    两份呈现之间唯一还能差的东西就只剩 ``sum(scores.values())`` 的求和次序 ——
    用它来钉 design D2 那条末位浮点限定的存在感。
    """

    def __init__(self, values: dict[str, float]) -> None:
        self.values = dict(values)

    def __call__(self, system: str, user: str) -> str:
        return scores_json(self.values)


class UnavailableJudge:
    """无凭证那一类: judge 根本问不到 —— 探针必须报"没测", 不得报"不敏感"。"""

    def __init__(self, error: str = "401 unauthorized") -> None:
        self.error = error
        self.calls = 0

    def __call__(self, system: str, user: str) -> str:
        self.calls += 1
        raise RuntimeError(self.error)


# ── 1. 协议本身 ─────────────────────────────────────────────────────────────


class TestOperatorProtocol:
    """1.1 无不变量声明的算子在构造期即被拒绝, 不留到运行时。"""

    def test_operator_without_an_invariant_is_rejected_at_construction(self):
        class LabelPolarity(PresentationProbeOperator):
            name = "label_polarity"  # D2 判读: 说不清保持什么 —— 拒绝成为算子
            invariant = ""

            def variants(self, presentation):
                return (PresentationVariant("极性翻转", None, presentation.dimensions),)

        with pytest.raises(ValueError, match="不变量声明"):
            LabelPolarity()

    def test_declaration_must_survive_a_subclass_editing_it(self):
        """dimension_order 的限定语被抹掉时, 构造同样要失败 (task 2.2 的 MUST)。"""

        class SloppyOrder(DimensionOrderProbe):
            invariant = "各维度独立打分后取平均"

        with pytest.raises(ValueError, match="末位浮点限定"):
            SloppyOrder()

    def test_protocol_exposes_no_third_party_registration_surface(self):
        """1.2 + Non-Goals: 不加 entry-point 组, 加了就是提前承诺扩展形状。"""
        assert set(discovery.EXTENSION_KINDS) == {"graders", "environments", "simulators"}
        assert not [
            name for name in dir(presentation_probes) if name.startswith(("register", "discover"))
        ]
        assert not [
            name for name in dir(presentation_probes) if name.endswith("ENTRY_POINT_GROUP")
        ]

    def test_two_operators_of_different_mechanism_share_one_protocol(self):
        """D3 的全部理由: 两个算子机制不同, 才证明协议装的不是特例。"""
        assert [op.name for op in DEFAULT_PROBE_OPERATORS] == [
            "anchor_value",
            "dimension_order",
        ]
        presentation = JudgePresentation(DEFAULT_JUDGE_ANCHOR_VALUE, tuple(DIMENSIONS))
        for operator in DEFAULT_PROBE_OPERATORS:
            variants = operator.variants(presentation)
            assert sum(1 for v in variants if v.is_baseline) == 1
            assert len({v.identity for v in variants}) == len(variants)


# ── 2. 变体构造的可复现性与"不开探针时零影响" ──────────────────────────────


def render_all(operator, presentation, trial, rubric="报告须含订单总数") -> bytes:
    """算子的变体构造渲染成判分输入字节 —— 可复现性要钉在字节上, 不是钉在对象上。"""
    grader = ModelBasedGrader()
    return "\n".join(
        grader._build_prompt(
            trial, rubric, list(v.dimensions), anchor_value=v.anchor_value
        )
        for v in operator.variants(presentation)
    ).encode("utf-8")


class TestVariantConstructionIsByteReproducible:
    PRESENTATION = JudgePresentation(DEFAULT_JUDGE_ANCHOR_VALUE, tuple(DIMENSIONS))

    @pytest.mark.parametrize(
        "operator", DEFAULT_PROBE_OPERATORS, ids=lambda op: op.name
    )
    def test_same_config_constructs_the_same_rendering(self, operator):
        """2.3 探针自己不得成为它要测的那个毛病 (design Risks)。"""
        trial = trials(1)[0]
        first = render_all(operator, self.PRESENTATION, trial)
        for _ in range(3):
            assert render_all(operator, self.PRESENTATION, trial) == first

    def test_each_operator_actually_moves_the_rendering_it_claims_to(self):
        trial = trials(1)[0]
        anchor = render_all(AnchorValueProbe(), self.PRESENTATION, trial).decode("utf-8")
        order = render_all(DimensionOrderProbe(), self.PRESENTATION, trial).decode("utf-8")
        assert set(EXAMPLE_JSON.findall(anchor)) == {
            '{"quality": 0.0, "completeness": 0.0}',
            '{"quality": 1.0, "completeness": 1.0}',
            '{"quality": ..., "completeness": ...}',
        }
        assert set(re.findall(r"^## 评分维度\n(.*)$", order, re.MULTILINE)) == {
            "quality, completeness",
            "completeness, quality",
        }


class TestProbeOffChangesNothing:
    """2.4 默认关闭时 model_based 的判分输入与 P0 落地后逐字节相同。"""

    def test_default_prompt_equals_the_frozen_p0_bytes(self):
        trial = TrialResult(
            trial_index=0,
            transcript=[
                {"role": "user", "content": "查订单"},
                {"role": "assistant", "content": "完成"},
            ],
        )
        assert (
            ModelBasedGrader()._build_prompt(
                trial, "报告须含订单总数", ["correctness", "completeness"]
            )
            == P0_FROZEN_PROMPT
        )

    async def test_the_production_grade_path_sends_exactly_the_frozen_prompt(self):
        """覆盖的是生产路径, 不只是那个辅助函数 —— 探针开着与否都得走同一份输入。"""
        seen: list[str] = []

        def judge(system, user):
            seen.append(user)
            return scores_json({d: 0.9 for d in ["correctness", "completeness"]})

        trial = TrialResult(
            trial_index=0,
            transcript=[
                {"role": "user", "content": "查订单"},
                {"role": "assistant", "content": "完成"},
            ],
        )
        await ModelBasedGrader(llm_fn=judge).grade(
            trial, [], judge_task(dimensions=["correctness", "completeness"])
        )
        assert seen == [P0_FROZEN_PROMPT]

    def test_only_the_example_line_differs_between_presentations(self):
        trial = trials(1)[0]
        grader = ModelBasedGrader()
        base = grader._build_prompt(trial, "r", DIMENSIONS)
        moved = grader._build_prompt(trial, "r", DIMENSIONS, anchor_value="1.0")
        ungiven = grader._build_prompt(trial, "r", DIMENSIONS, anchor_value=None)
        for other in (moved, ungiven):
            assert _differing_lines(base, other) == 1
        assert '"quality": ...' in ungiven

    def test_summation_order_follows_the_presentation_not_the_argument(self):
        """2.2b: 求和口径原样保留 —— 求和序就是 ``scores`` 的插入序 = 该变体的维度序。

        ``_parse_scores`` 按 ``for dim in dimensions`` 建字典, 所以是**呈现**决定了
        求和序。这条测试钉住的是"没有为探针改生产算术", 不是"改坏了会怎样"。
        """
        values = {"a": 0.1, "b": 0.2, "c": 0.3}
        forward = {d: values[d] for d in ("a", "b", "c")}
        backward = {d: values[d] for d in ("c", "b", "a")}
        assert model_based.score_dimensions(forward, list(forward)) == (
            sum([0.1, 0.2, 0.3]) / 3
        )
        assert model_based.score_dimensions(backward, list(backward)) == (
            sum([0.3, 0.2, 0.1]) / 3
        )
        # 同一个表达式、两种次序 —— 框架在这件事上确定, 不存在待修缺陷 (D2)
        assert model_based.score_dimensions(forward, list(forward)) == (
            model_based.score_dimensions(backward, list(backward))
        )

    def test_reverse_order_summation_moves_nothing_on_the_values_judges_emit(self):
        """D2 那个混淆项的真实量级: 在 judge 实际会给出的值域上, 末位差异一次都没有。

        穷举一位小数 (≤5 维, 判据的实际维度数量级) 与两位小数的三元组全部排列:
        逆序求和与正序求和逐位相同。所以这条轴上**构造不出**由算术引起的结论翻转,
        限定语留着是为了"如果哪天值域变了"仍然诚实, 不是为了掩盖一个已知的假阳。
        """
        def moved(vector: tuple[float, ...]) -> bool:
            return sum(vector) != sum(vector[::-1])

        one_decimal = [round(x / 10, 1) for x in range(11)]
        offenders = [
            vector
            for n in range(2, 6)
            for vector in itertools.product(one_decimal, repeat=n)
            if moved(vector)
        ]
        assert offenders == []
        two_decimal = [x / 100 for x in range(101)]
        assert [v for v in itertools.permutations(two_decimal, 3) if moved(v)] == []


def _differing_lines(a: str, b: str) -> int:
    return sum(1 for x, y in zip(a.splitlines(), b.splitlines(), strict=False) if x != y)


# ── 3. 报告语义 ─────────────────────────────────────────────────────────────


class TestReportSemantics:
    def test_flip_is_declared_only_when_the_conclusion_crosses_the_threshold(self):
        """3.2 + statistics spec 第三场景: 0.78 → 0.72 而阈值 0.7 → 不算翻转。"""
        report = probe_for(DriftOnlyJudge())
        anchor = report.by_operator["anchor_value"]
        assert anchor.status == "not_detected"
        assert anchor.flipped_trials == 0
        # 分数移动量作为另一维度单独呈现, 不与翻转混计
        assert anchor.max_score_shift == pytest.approx(0.06)
        assert anchor.mean_score_shift == pytest.approx(0.06)

    def test_aligned_sample_floor_is_reused_and_says_so(self):
        """3.3 复用既有 MIN_ALIGNED_RATINGS_FOR_AGREEMENT 语义, 不伪造数值。"""
        # 8 × 0.5 = 4 个抽中 trial, 落在门槛 (5) 之下 → 不可计算, 不是"未检出"
        report = probe_for(BodyOnlyJudge(), n_trials=8, sample=0.5)
        for operator in report.by_operator.values():
            assert operator.status == "not_computable"
            assert operator.aligned_trials == 4
            assert f"{4} < {MIN_ALIGNED_RATINGS_FOR_AGREEMENT}" in operator.reason
            assert operator.value is None and operator.measure is None

    def test_sample_and_seed_have_no_kernel_default(self):
        """3.4 (design D5): 抽样比例是唯一没被测量的自由度, 拍默认值就是拍脑袋。"""
        params = inspect.signature(run_presentation_probes).parameters
        for name in ("sample", "seed"):
            assert params[name].kind is inspect.Parameter.KEYWORD_ONLY
            assert params[name].default is inspect.Parameter.empty
        with pytest.raises(TypeError):
            run_presentation_probes(
                ModelBasedGrader(llm_fn=BodyOnlyJudge()),
                task=judge_task(),
                trials=trials(),
            )
        with pytest.raises(ValueError, match="抽样比例"):
            probe_for(BodyOnlyJudge(), sample=0.0)

    def test_sampling_is_reproducible_by_seed_and_actually_samples(self):
        def picked(seed: int) -> list[int]:
            return [t.trial_index for t in draw_trials(trials(10), sample=0.5, seed=seed)]

        assert picked(7) == picked(7)
        assert len(set(picked(7))) == 5
        assert picked(7) != picked(8)
        report = probe_for(BodyOnlyJudge(), n_trials=10, sample=0.5, seed=7)
        assert (report.seed, report.trials_sampled) == (7, 5)
        assert report.trials_total == 10

    def test_cost_quote_is_stated_at_the_call_site(self):
        """3.5 份数 × 抽中 trial 数, 显式给出、不藏进配置 (D3 的代价 = 每 trial 4 次)。"""
        quote = presentation_probe_cost_quote(n_trials=6, sample=1.0)
        assert quote == {"variants_per_trial": 4, "trials_sampled": 6, "judge_calls": 24}
        partial = presentation_probe_cost_quote(n_trials=6, sample=0.5)
        assert partial["trials_sampled"] == 3 and partial["judge_calls"] == 12
        report = probe_for(BodyOnlyJudge())
        assert report.variants_per_trial == quote["variants_per_trial"]
        assert report.judge_calls == quote["judge_calls"]

    def test_unavailable_judge_is_reported_as_not_measured(self):
        """3.6「不可计算」与「未检出」是两种措辞, 不得共用一个空值。"""
        judge = UnavailableJudge()
        report = probe_for(judge)
        assert report.status == "not_computable"
        assert judge.calls == report.judge_calls  # 钱花了, 结论一条没有 —— 也要如实报
        for operator in report.by_operator.values():
            assert operator.status == "not_computable"
            assert operator.reason.startswith("不可计算")
            assert "未检出" not in operator.reason
            assert "401 unauthorized" in operator.reason  # 点名原因, 不给人猜
            assert operator.value is None and operator.flip_rate is None

        # 同一份报告结构下, "测过了且未检出" 的措辞必须明显不同
        measured = probe_for(BodyOnlyJudge())
        for operator in measured.by_operator.values():
            assert operator.status == "not_detected"
            assert operator.reason.startswith("未检出")
            assert "不可计算" not in operator.reason


# ── 4. 协议承载第二个算子, 且证明的只是机制 ────────────────────────────────


class TestProtocolCarriesTheSecondOperator:
    """4.4 换一个算子不需要动探针本体 —— D3 要两个算子的全部理由。"""

    def test_running_only_the_second_operator_needs_no_probe_change(self):
        report = probe_for(
            BodyOnlyJudge(), operators=[DimensionOrderProbe()]
        )
        assert list(report.by_operator) == ["dimension_order"]
        assert report.variants_per_trial == 2  # 基线 + 逆序

    def test_anchor_sensitivity_is_not_smared_onto_the_order_axis(self):
        """逐算子各报各的: 锚定敏感不该被读成维度顺序敏感。"""
        report = probe_for(AnchorReactiveJudge(), operators=[DimensionOrderProbe()])
        order = report.by_operator["dimension_order"]
        assert order.status == "not_detected"
        assert order.flipped_trials == 0
        assert order.value == pytest.approx(1.0)

    def test_single_dimension_config_constructs_no_second_presentation(self):
        """单维度上逆序与配置序是同一份呈现 —— 虚增份数没有意义, 报价也得跟着变。"""
        report = probe_for(BodyOnlyJudge(), dimensions=["quality"])
        order = report.by_operator["dimension_order"]
        assert order.status == "not_computable"
        assert "只构造出 1 份呈现" in order.reason
        assert report.variants_per_trial == 3  # 只剩锚定轴三份

    def test_the_order_axis_discloses_its_qualifier_whichever_way_it_reads(self):
        """2.2 + D2: 限定语必须随报告输出走, 不能只活在 design 里。

        在 judge 实际会给出的值域上, 逆序求和与正序逐位相同 (本文件那条穷举测试证的),
        所以这条轴今天读不出翻转 —— 但报告仍必须带着那句限定: 看到"未检出敏感"的下
        一个人无法自行推断"这一轴在这份实现上结构性不可翻", 他只能相信报告说的话。
        """
        report = probe_for(
            FixedScoresJudge({"a": 0.1, "b": 0.2, "c": 0.3}), dimensions=["a", "b", "c"]
        )
        order = report.by_operator["dimension_order"]
        assert order.status == "not_detected"
        assert order.max_score_shift == pytest.approx(0.0)  # 两份呈现的合成分逐位相同
        assert SUMMATION_ORDER_QUALIFIER in order.invariant
        assert SUMMATION_ORDER_QUALIFIER in order.reason
        # 同一批数据上的锚定轴也没反应: 这份替身只认维度名, 两轴各自独立报
        assert report.by_operator["anchor_value"].status == "not_detected"

    def test_report_states_its_own_interpretation_limit(self):
        """4.5 最容易被误读的地方不能只靠 design 兜。"""
        report = probe_for(AnchorReactiveJudge())
        assert "不证明真实 judge 敏感或不敏感" in report.interpretation_note
        assert "测不出来不是没测" in report.interpretation_note
        # 机制结论与幅度是两件事: 替身报出敏感 ≠ 真实 judge 报出敏感
        assert report.by_operator["anchor_value"].status == "sensitive"


# ── 5. 硬规则: 探针不产出结论 ───────────────────────────────────────────────


def make_runner(judge) -> EvalRunner:
    return EvalRunner(
        agent_runner=MockAgentRunner(success_rate=1.0, latency_range=(0.0, 0.01)),
        trace_provider=MockTraceProvider(default_spans=[]),
        storage=MemoryStorage(),
        graders=[ModelBasedGrader(llm_fn=judge)],
    )


async def graded_run(judge) -> tuple[EvalRunner, object, EvalTask]:
    runner = make_runner(judge)
    task = EvalTask(
        id="t1",
        prompt="统计这批订单",
        max_trials=3,
        graders=[
            GraderConfig(
                type=GraderType.MODEL,
                name="model_based",
                config={"rubric": "报告须含订单总数", "dimensions": DIMENSIONS},
            )
        ],
    )
    run = await runner.run_suite(
        EvalSuite(name="probe-guard", version="1.0.0", tasks=[task])
    )
    return runner, run, task


def probe_the_run(run, task: EvalTask, judge) -> None:
    """对**真实跑出来的** trial 开一次探针 —— 守护测试要看着它碰有结论的对象。"""
    run_presentation_probes(
        ModelBasedGrader(llm_fn=judge),
        task=task,
        trials=[t for group in run.trials.values() for t in group],
        sample=1.0,
        seed=SEED,
    )


async def _observation(runner: EvalRunner, run) -> dict:
    """一次 run 里探针 MUST NOT 移动的那些量。"""
    attempts = await runner.storage.list_grade_attempts(run.run_id)
    return {
        "attempts": [a.attempt_id for a in attempts],
        "current": sorted(
            # attempt_id 必须进来: 同一条 trial 被追加新判定时 (task, index) 集合不变,
            # 只有条目 id 会搬家 —— "MUST NOT 移动 current 指针"这样才看得见
            (a.task_id, a.trial_index, a.attempt_id)
            for a in attempts
            if a.is_current
        ),
        "trials": [
            t.model_dump() for group in run.trials.values() for t in group
        ],
        "denominators": {
            s.task_id: (s.valid_trials, s.total_trials, s.pass_at_k, s.avg_score)
            for s in run.summary.task_summaries
        },
        "agreement": {
            name: report.model_dump() for name, report in run.summary.agreement.items()
        },
        "statistics_version": run.statistics_version,
    }


class TestProbesNeverProduceConclusions:
    """5.1–5.5 探针一旦混进判定链, verdict_drift 就会把一次扰动读成"judge 换代翻了 N 判"。"""

    def test_the_entry_point_cannot_reach_anything_that_records(self):
        """结构上门: 探针拿不到 runner/storage/run_id, 也就写不进判定序列。"""
        params = inspect.signature(run_presentation_probes).parameters
        for name in params:
            assert name not in {"storage", "runner", "run_id", "task_id"}
        assert "save_grade_attempt" not in inspect.getsource(presentation_probes)

    def test_a_report_contains_no_verdict_bearing_object(self):
        """5.1 探针的产出里没有 GraderResult / TrialResult —— 一个都没有。"""
        report = probe_for(AnchorReactiveJudge())
        graphs = [report, *report.by_operator.values()]
        leaked = [
            node
            for graph in graphs
            for node in _walk(graph.model_dump().values())
            if isinstance(node, (GraderResult, TrialResult))
        ]
        assert leaked == []
        assert {
            f
            for graph in graphs
            for f in graph.model_dump()
            if f in {"grader_results", "verdict", "invalid_reason", "success"}
        } == set()

    async def test_probe_changes_neither_entries_denominators_or_statistics(self):
        """5.2 同一 trial 开探针与关探针: 判定条目数、通过率分母、口径版本全同。"""
        runner, run, task = await graded_run(BodyOnlyJudge())
        before = await _observation(runner, run)
        assert before["attempts"], "前置: 这条 trial 序列本身得有判定条目"
        assert any(
            t.grader_results for group in run.trials.values() for t in group
        ), "前置: 判据得真的跑出结论"

        probe_the_run(run, task, BodyOnlyJudge())
        assert await _observation(runner, run) == before

    async def test_the_guard_actually_watches_the_leak_path(self):
        """5.4 反向验证: 泄漏一次, 5.2 看着的每一项都得动 —— 否则那条只是永远绿的测试。

        P0 的手法是临时把缺陷注回实现里再确认测试变红。这里不适用: 那个"缺陷"恰好是
        spec 明令 MUST NOT 的行为 (探针产出结论), 哪怕只注一瞬间也要往生产路径里写
        结论代码。于是反过来在**测试侧**模拟泄漏, 断言观测量确实跟着动。
        """
        runner, run, task = await graded_run(BodyOnlyJudge())
        baseline = await _observation(runner, run)
        trial = run.trials["t1"][0]

        # 泄漏一: 探针读数就地写进被评 trial —— 通过率分母与条目数的共同上游
        trial.grader_results.append(
            GraderResult(
                grader_name="model_based",
                grader_type=GraderType.MODEL,
                score=0.95,
                passed=True,
                explanation="TEMP: 模拟探针结论混进判定",
            )
        )
        leaked_trials = await _observation(runner, run)
        trial.grader_results.pop()
        assert leaked_trials["trials"] != baseline["trials"]
        assert (await _observation(runner, run))["trials"] == baseline["trials"]

        # 泄漏二: 探针结论进了 grade_attempts —— current 指针搬家, 条目数变多
        await runner.storage.save_grade_attempt(
            GradeAttempt(run_id=run.run_id, task_id="t1", trial_index=0, trial=trial)
        )
        leaked = await _observation(runner, run)
        assert len(leaked["attempts"]) == len(baseline["attempts"]) + 1
        pointers = {(t, idx): aid for t, idx, aid in leaked["current"]}
        before = {(t, idx): aid for t, idx, aid in baseline["current"]}
        assert set(pointers) == set(before), "条目数变了但被污染的 trial 集合没变"
        assert [k for k in pointers if pointers[k] != before[k]] == [("t1", 0)]

    async def test_run_read_by_a_probe_stays_comparable_to_a_fresh_run(self):
        """5.5 探针不得给跨 run 可比判定添一个新差异维度。"""
        _, probed, task = await graded_run(BodyOnlyJudge())
        probe_the_run(probed, task, BodyOnlyJudge())
        _, control, _ = await graded_run(BodyOnlyJudge())
        comparable, reason = runs_comparable(probed, control)
        assert comparable, reason
        assert reason is None
        assert probed.evidence == control.evidence

    def test_no_sixth_caliber_axis_exists_for_probes(self):
        """5.3 探针不产出 verdict, 没有翻判要归因 —— 别"顺手补上"第六根轴。"""
        assert CALIBER_AXES == (
            "mapping_version",
            "spec_version",
            "statistics_version",
            "judge_models",
            "grader_versions",
        )
        assert not [axis for axis in CALIBER_AXES if re.search(
            r"probe|presentation|bias|anchor", axis
        )]


def _walk(nodes):
    for node in nodes:
        yield node
        if isinstance(node, dict):
            yield from _walk(node.values())
        elif isinstance(node, (list, tuple, set)):
            yield from _walk(node)
