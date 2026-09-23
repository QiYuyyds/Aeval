"""判分呈现探针 —— 算子协议、两个内置算子与探针入口。

探针问的是一个此前框架无法回答的问题：**同一个 judge 对不该影响结论的呈现细节
敏不敏感。** κ 高不等于准 —— 两个 judge 若共享同一偏置，它们一致地错，而评分者间
信度对此完全无感 (spec: statistics)。

算子协议 (design D2)
    一个算子 = 一句「保持什么不变」+ 一份变体构造。不变量声明是**构造期**硬门槛:
    说不清保持什么的改动不是探针，是另一个任务 —— 它测出的差异无法归因到"呈现"，
    留到运行时只会产出一个没人读得懂的数。

没有第三方注册表面 (Non-Goals)
    本模块**刻意不提供** ``agent_eval.*`` entry-point 组。协议先在内核里立住，
    泛化性由第二个算子证明；开一个注册面等于提前承诺一个还没被第二个用例验证过的
    扩展形状。同理：无 YAML / CLI / REST / 看板表面、不落库 (design D7) —— 探针的
    配置形状正是这次测量要 inform 的东西。

七类候选呈现的取舍 (判读表全文见 ``docs/grader-reference.md`` 的「判分呈现探针」节)
    纳入 ``anchor_value`` 与 ``dimension_order``；``label_polarity`` 因说不清保持什么
    被拒绝成为算子；``irrelevant_prefix`` / ``head_trunc`` / ``tail_trunc`` /
    ``paraphrase`` 因破坏不变量或引入新变量而不纳入；``candidate_order`` 无宿主
    (框架内没有成对比较判据，位置偏置那一类今天测不了)。
"""

from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from agent_eval.core.metrics import FAIL_LABEL, PASS_LABEL, presentation_operator_report
from agent_eval.core.types import EvalTask, PresentationInvarianceReport, TrialResult

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注，避免 graders 之间的导入环
    from agent_eval.core.types import PresentationOperatorReport

# 报告输出里必须自带的那句话: 替身数字只证明机制, 不证明真实 judge 敏不敏感 (design D6)
MECHANISM_ONLY_NOTE = (
    "本报告只证明呈现探针能发现敏感、且不虚报敏感；它不证明真实 judge 敏感或不敏感。"
    "替身判据下的数字不是真实 LLM 的翻转率 —— 真实幅度要等一把可用的 judge 凭证，"
    "测不出来不是没测。"
)

# 基线 = 生产今天实际送出去的那一份呈现; 翻转必须有参照物
BASELINE_PRESENTATION = "基线（生产今天的呈现）"

# dimension_order 的不变量含一条准确限定 (design D2): 浮点加法不可结合, 逆序求和
# 的末位差异是算术伪影而不是 judge 敏感性。这句话必须出现在算子声明**与报告输出**里,
# 否则读者会把一个被限定过的否证当成普适的否证。
SUMMATION_ORDER_QUALIFIER = "求和次序造成的末位浮点差异不计为呈现敏感"
DIMENSION_ORDER_INVARIANT = f"各维度独立打分后取平均；{SUMMATION_ORDER_QUALIFIER}"


@dataclass(frozen=True)
class JudgePresentation:
    """一份判分配置在 judge 眼里的形状：模板预填值 + 维度列举次序。

    证据正文、rubric、threshold 与系统提示词都不在这里 —— 探针只动呈现，不动内容。
    """

    anchor_value: str | None
    dimensions: tuple[str, ...]


@dataclass(frozen=True)
class PresentationVariant:
    """一个呈现变体；``is_baseline`` 的那一份就是生产今天送出去的判分输入。"""

    label: str
    anchor_value: str | None
    dimensions: tuple[str, ...]
    is_baseline: bool = False

    @property
    def identity(self) -> tuple[Any, ...]:
        """呈现身份 = 真正送出去的那两个量。

        跨算子按它去重: 两个算子共享基线, 逐字节相同的呈现问第二遍只会把 judge 的
        自一致噪声混进呈现敏感里, 量不出我们要量的东西。
        """
        return (self.anchor_value, self.dimensions)

    @property
    def presentation(self) -> JudgePresentation:
        return JudgePresentation(self.anchor_value, self.dimensions)


@dataclass(frozen=True)
class PresentationJudgment:
    """一次呈现下的判分读数 —— 刻意**不是** GraderResult / TrialResult。

    探针的读数不是判定条目: 不进 ``grade_attempts``、不移动 ``current`` 指针、
    不进任何分母或门禁 (spec: graders 那条负向要求)。``score``/``passed`` 为 None
    表示这份呈现读不出结论 (judge 不可用、解析失败、维度不齐) —— 那是"没测",
    由报告报不可计算, 不冒充"通过"或"不通过"。
    """

    score: float | None
    passed: bool | None
    note: str | None = None


class PresentationProbeOperator(ABC):
    """呈现算子的协议: 声明保持的不变量 + 从一份判分配置构造变体。

    构造期拒绝无不变量声明的算子 (spec: 「无不变量声明的改动不被接受为算子」)。
    """

    name: str = ""
    invariant: str = ""

    def __init__(self) -> None:
        if not self.name.strip():
            raise ValueError("探针算子必须有名字 (报告按名引用它)")
        if not self.invariant.strip():
            raise ValueError(
                f"探针算子 '{self.name}' 缺少保持的不变量声明: 说不清保持什么的改动"
                "不是探针, 是另一个任务 —— 它测出的差异无法归因到呈现 (design D2)"
            )

    @abstractmethod
    def variants(self, presentation: JudgePresentation) -> tuple[PresentationVariant, ...]:
        """从一份判分配置构造 N 个呈现变体， MUST 逐字节可复现。"""
        raise NotImplementedError


class AnchorValueProbe(PresentationProbeOperator):
    """只换模板示例里的预填值，其余逐字节相同 (首个算子: 针对格式锚)。

    ``graders/model_based.py`` 给 judge 看的示例 JSON 里住着一个框架自己写进去的
    显式锚。judge 回归到展示值附近是已知现象，而它到底有没有影响结论，此前无人能答。
    """

    name = "anchor_value"
    invariant = "模板示例值只是格式说明，不该进分数"

    #: 基线值之外要试的预填值; ``None`` = 不给值 (示例里不留数字)
    alternative_anchors: tuple[str | None, ...] = ("1.0", None)

    def variants(self, presentation: JudgePresentation) -> tuple[PresentationVariant, ...]:
        out = [
            PresentationVariant(
                label=BASELINE_PRESENTATION,
                anchor_value=presentation.anchor_value,
                dimensions=presentation.dimensions,
                is_baseline=True,
            )
        ]
        for anchor in self.alternative_anchors:
            if anchor == presentation.anchor_value:
                continue  # 与生产同值的变体不是变体, 它只会虚增份数
            out.append(
                PresentationVariant(
                    label=f"锚定 {'不给值' if anchor is None else anchor}",
                    anchor_value=anchor,
                    dimensions=presentation.dimensions,
                )
            )
        return tuple(out)


class DimensionOrderProbe(PresentationProbeOperator):
    """只换维度的列举次序 (第二个算子: 针对列举序 —— 与锚定机制不同, 共用同一份协议)。

    它的不变量声明带一条不可省略的限定: ``sum(scores.values())`` 的求和序就是维度
    序, 而浮点加法不可结合, 所以这一轴上末位浮点差是算术伪影。框架在这件事上是
    确定性的, 没有待修缺陷 —— 为探针去改生产算术是反的 (design D2)。
    """

    name = "dimension_order"
    invariant = DIMENSION_ORDER_INVARIANT

    def __init__(self) -> None:
        super().__init__()
        if SUMMATION_ORDER_QUALIFIER not in self.invariant:
            raise ValueError(
                "dimension_order 的不变量声明必须含末位浮点限定: 少了这句, 读者会把"
                "「未检出敏感」读成普适的否证, 而它只覆盖到求和次序之外 (design D2)"
            )

    def variants(self, presentation: JudgePresentation) -> tuple[PresentationVariant, ...]:
        base = PresentationVariant(
            label=BASELINE_PRESENTATION,
            anchor_value=presentation.anchor_value,
            dimensions=presentation.dimensions,
            is_baseline=True,
        )
        reversed_dims = tuple(reversed(presentation.dimensions))
        if reversed_dims == presentation.dimensions:
            return (base,)  # 单维度: 逆序与配置序是同一份呈现, 虚增份数没有意义
        return (
            base,
            PresentationVariant(
                label="维度逆序",
                anchor_value=presentation.anchor_value,
                dimensions=reversed_dims,
            ),
        )


#: 默认算子集: 两个机制不同的偏置共用同一份协议与同一份报告结构 (design D3)
DEFAULT_PROBE_OPERATORS: tuple[PresentationProbeOperator, ...] = (
    AnchorValueProbe(),
    DimensionOrderProbe(),
)


class PresentationProbeTarget(Protocol):
    """探针需要的评分器能力 —— 由 ``ModelBasedGrader`` 提供。

    判分口径必须与生产同源: 同一个提示词构造、同一个解析、同一个 threshold 比较。
    否则探针报出的"结论翻转"量的就不是生产会做出的那个结论。
    """

    def baseline_presentation(self, task: EvalTask) -> JudgePresentation: ...

    def judge_presentation(
        self, trial: TrialResult, task: EvalTask, presentation: JudgePresentation
    ) -> PresentationJudgment: ...


def _require_sample(sample: float) -> None:
    if not isinstance(sample, (int, float)) or isinstance(sample, bool):
        raise TypeError(f"sample 必须是 (0, 1] 的抽样比例, 收到 {sample!r}")
    if not 0.0 < float(sample) <= 1.0:
        raise ValueError(
            f"sample 必须落在 (0, 1], 收到 {sample!r}: 探针是成本乘法, "
            "内核不给默认抽样比例 —— 抽多少才够取决于翻转率, 而那正是要测的量 (design D5)"
        )


def sampled_trial_count(n_trials: int, sample: float) -> int:
    """按比例抽中的 trial 数 (向上取整: 开了探针就至少问一份, 0 份不是"测过")。"""
    _require_sample(sample)
    if n_trials <= 0:
        return 0
    return min(n_trials, math.ceil(n_trials * float(sample)))


def draw_trials(trials: Sequence[TrialResult], *, sample: float, seed: int) -> list[TrialResult]:
    """按 seed 抽取 trial —— 抽样本身必须可复现, 否则探针成了新的不可复现源。"""
    ordered = sorted(trials, key=lambda t: t.trial_index)
    n = sampled_trial_count(len(ordered), sample)
    if n == len(ordered):
        return ordered
    picked = random.Random(seed).sample(range(len(ordered)), n)
    return [ordered[i] for i in sorted(picked)]


def _variants_by_identity(
    operators: Sequence[PresentationProbeOperator], baseline: JudgePresentation
) -> dict[tuple[Any, ...], PresentationVariant]:
    """跨算子去重后的呈现集 (同一份呈现只问 judge 一次)。"""
    seen: dict[tuple[Any, ...], PresentationVariant] = {}
    for operator in operators:
        for variant in operator.variants(baseline):
            seen.setdefault(variant.identity, variant)
    return seen


def presentation_probe_cost_quote(
    *,
    n_trials: int,
    sample: float,
    operators: Sequence[PresentationProbeOperator] = DEFAULT_PROBE_OPERATORS,
    presentation: JudgePresentation | None = None,
) -> dict[str, int]:
    """探针的显式成本报价 (份数 × 抽中 trial 数) —— 给在调用点, 不藏进配置。

    ``presentation`` 缺省时按两维度、带锚定值的典型判分配置估算；实际份数随配置变
    (单维度任务上 ``dimension_order`` 构造不出第二份呈现)，跑之前以 grader 报出的
    那份为准。
    """
    if not operators:
        raise ValueError("至少需要一个呈现算子")
    reference = presentation or JudgePresentation("0.0", ("quality", "completeness"))
    variants = _variants_by_identity(operators, reference)
    trials_sampled = sampled_trial_count(n_trials, sample)
    return {
        "variants_per_trial": len(variants),
        "trials_sampled": trials_sampled,
        "judge_calls": len(variants) * trials_sampled,
    }


def run_presentation_probes(
    grader: PresentationProbeTarget,
    *,
    task: EvalTask,
    trials: Sequence[TrialResult],
    sample: float,
    seed: int,
    operators: Sequence[PresentationProbeOperator] = DEFAULT_PROBE_OPERATORS,
) -> PresentationInvarianceReport:
    """就同一份归档证据，用 N 份呈现重问判分器，报告结论是否随呈现变化。

    ``sample`` 与 ``seed`` 必填且无默认 (design D5)：前者是成本乘法里唯一没被测量的
    自由度，后者保证第二次能抽出同一批 trial。

    本函数**只读** trial，绝不产出 ``GraderResult`` / ``TrialResult``、绝不写存储、
    绝不触碰 runner —— 探针的产出只有诊断, 没有结论。
    """
    if seed is None:
        raise ValueError("seed 必填: 抽样本身必须可复现, 内核不给默认种子 (design D5)")
    if not operators:
        raise ValueError("至少需要一个呈现算子")
    names = [operator.name for operator in operators]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ValueError(f"呈现算子重名: {duplicates}")

    baseline = grader.baseline_presentation(task)
    drawn = draw_trials(trials, sample=sample, seed=seed)
    unique = _variants_by_identity(operators, baseline)
    variants_per_trial = len(unique)

    # 同一份呈现只问一次; 两个算子共享基线 (design D3 的代价按此计: 基线 1 + 变体 N)
    readings: dict[tuple[int, tuple[Any, ...]], PresentationJudgment] = {}
    for trial in drawn:
        for identity in unique:
            variant = unique[identity]
            readings[(trial.trial_index, identity)] = grader.judge_presentation(
                trial, task, variant.presentation
            )

    by_operator: dict[str, PresentationOperatorReport] = {}
    for operator in operators:
        variants = operator.variants(baseline)
        labels = [v.label for v in variants]
        units: list[dict[str, str | None]] = []
        shifts: list[float] = []
        unreadable: list[str] = []
        for trial in drawn:
            judgments = {
                v.label: readings[(trial.trial_index, v.identity)] for v in variants
            }
            units.append(
                {
                    label: (
                        None
                        if judgments[label].passed is None
                        else (PASS_LABEL if judgments[label].passed else FAIL_LABEL)
                    )
                    for label in labels
                }
            )
            base_score = judgments[BASELINE_PRESENTATION].score
            moves = [
                abs(judgments[v.label].score - base_score)
                for v in variants
                if not v.is_baseline
                and judgments[v.label].score is not None
                and base_score is not None
            ]
            if moves:
                shifts.append(max(moves))
            unreadable.extend(
                judgment.note
                for judgment in judgments.values()
                if judgment.passed is None and judgment.note
            )
        by_operator[operator.name] = presentation_operator_report(
            operator=operator.name,
            invariant=operator.invariant,
            presentations=labels,
            baseline=BASELINE_PRESENTATION,
            units=units,
            score_shifts=shifts,
            unreadable_reasons=unreadable,
        )

    if any(r.status == "sensitive" for r in by_operator.values()):
        status = "sensitive"
    elif any(r.status == "not_computable" for r in by_operator.values()):
        status = "not_computable"
    else:
        status = "not_detected"

    return PresentationInvarianceReport(
        seed=seed,
        sample=float(sample),
        trials_total=len(trials),
        trials_sampled=len(drawn),
        variants_per_trial=variants_per_trial,
        judge_calls=variants_per_trial * len(drawn),
        by_operator=by_operator,
        status=status,
        interpretation_note=MECHANISM_ONLY_NOTE,
    )
