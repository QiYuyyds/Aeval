"""Unit tests for agent_eval core statistics (estimators, validity, intervals).

Each scenario name notes the `specs/statistics/spec.md` scenario it locks.
"""

import pytest

from agent_eval.core.metrics import (
    aggregate_metrics,
    bootstrap_ci,
    classify_trial,
    estimates_to_rates,
    extract_metrics,
    extract_process_metrics,
    has_enough_data,
    is_insufficient_data,
    p50,
    p95,
    pass_at_k,
    pass_power_k,
    percentile,
    split_trials_by_verdict,
    summarize_resources,
    summarize_scores,
    trial_invalid_reason,
    valid_trials,
    wilson_interval,
    worst_of_n,
)
from agent_eval.core.pricing import PriceTable, TokenPrices
from agent_eval.core.types import (
    EvidenceGap,
    InvalidReason,
    ResourceSummary,
    TrialResult,
    TrialVerdict,
)
from agent_eval.trace.normalize import normalize_spans
from agent_eval.trace.observations import is_missing


def trial(
    success: bool,
    index: int = 0,
    score: float | None = None,
    verdict: TrialVerdict = TrialVerdict.VALID,
    reason: InvalidReason | None = None,
) -> TrialResult:
    kwargs: dict = {"trial_index": index, "success": success, "verdict": verdict}
    if reason is not None:
        kwargs["invalid_reason"] = reason
    if score is not None:
        kwargs["grader_results"] = [
            {
                "grader_name": "g",
                "grader_type": "code",
                "score": score,
                "passed": score >= 0.7,
                "verdict": verdict,
            }
        ]
    return TrialResult(**kwargs)


def pending_trial(index: int = 0) -> TrialResult:
    """人工评分未回传的 trial (HumanGrader 的 details.status 形态)。"""
    return TrialResult(
        trial_index=index,
        success=False,
        grader_results=[
            {
                "grader_name": "human",
                "grader_type": "custom",
                "score": 0.0,
                "passed": False,
                "verdict": "pending",
                "details": {"status": "pending"},
            }
        ],
    )


class TestPassAtKMeasuredRange:
    """Requirement: pass@k 在实测区间使用有限样本无偏估计"""

    @pytest.mark.parametrize(
        ("k", "expected"),
        [(1, 1 / 3), (2, 2 / 3), (3, 1.0)],
    )
    def test_one_success_in_three(self, k, expected):
        """Scenario: 三次中一次成功 → 0.333 / 0.667 / 1.0"""
        trials = [trial(False), trial(True), trial(False)]
        assert pass_at_k(trials, k).value == pytest.approx(expected)

    @pytest.mark.parametrize("k", [1, 2, 3])
    def test_all_failures_stay_zero(self, k):
        """Scenario: 全失败仍为零"""
        trials = [trial(False)] * 3
        assert pass_at_k(trials, k).value == 0.0
        assert pass_at_k(trials, k).method == "measured"

    @pytest.mark.parametrize("n,c", [(1, 0), (1, 1), (3, 2), (5, 4), (7, 3), (10, 1)])
    def test_pass_at_1_equals_c_over_n(self, n, c):
        """pass@1 MUST 等于 c/n"""
        trials = [trial(True)] * c + [trial(False)] * (n - c)
        assert pass_at_k(trials, 1).value == pytest.approx(c / n)

    @pytest.mark.parametrize("k", [1, 2, 3])
    def test_independent_of_trial_order(self, k):
        trials_a = [trial(True), trial(False), trial(False)]
        trials_b = [trial(False), trial(False), trial(True)]
        assert pass_at_k(trials_a, k).value == pytest.approx(pass_at_k(trials_b, k).value)

    def test_all_success(self):
        trials = [trial(True)] * 3
        assert [pass_at_k(trials, k).value for k in (1, 2, 3)] == [1.0, 1.0, 1.0]


class TestExtrapolation:
    """Requirement: 超出实测样本的外推值必须可区分"""

    def test_extrapolated_k_carries_flag_and_wilson_lower_bound(self):
        """Scenario: 三次样本外推 k=5"""
        est = pass_at_k([trial(True), trial(False), trial(False)], k=5)
        assert est.extrapolated is True
        assert est.method == "extrapolated"
        assert est.value == pytest.approx(1.0 - (1.0 - 1 / 3) ** 5)
        assert est.value == pytest.approx(0.868, abs=0.001)
        assert est.p_point == pytest.approx(1 / 3)
        assert est.p_lower_bound == pytest.approx(wilson_interval(1, 3)[0])
        assert est.p_upper_bound == pytest.approx(wilson_interval(1, 3)[1])

    def test_measured_range_not_flagged(self):
        for k in (1, 2, 3):
            assert pass_at_k([trial(True), trial(False), trial(False)], k).extrapolated is False

    def test_power_extrapolation_formula_unchanged(self):
        est = pass_power_k([trial(True), trial(True), trial(False)], k=5)
        assert est.extrapolated is True
        assert est.value == pytest.approx((2 / 3) ** 5)

    def test_zero_success_extrapolation_is_zero(self):
        assert pass_at_k([trial(False)] * 3, k=5).value == 0.0
        assert pass_power_k([trial(False)] * 3, k=5).value == 0.0

    def test_full_success_extrapolation_is_one(self):
        assert pass_at_k([trial(True)] * 3, k=5).value == 1.0
        assert pass_power_k([trial(True)] * 3, k=5).value == 1.0

    @pytest.mark.parametrize("k", [1, 2, 5, 10])
    def test_no_valid_trials_extrapolates_nothing(self, k):
        """Scenario: 无有效样本时不外推"""
        for estimator in (pass_at_k, pass_power_k):
            est = estimator([], k)
            assert is_insufficient_data(est.value)
            assert est.method == "insufficient_data"
            assert est.extrapolated is False
            assert est.p_point is None
            assert est.p_lower_bound is None

    @pytest.mark.parametrize("k", [1, 5])
    def test_all_pending_is_insufficient_not_zero(self, k):
        """Scenario: 全部待人工评分 → 通过率为 insufficient_data"""
        trials = [pending_trial(0), pending_trial(1), pending_trial(2)]
        for estimator in (pass_at_k, pass_power_k):
            est = estimator(trials, k)
            assert est.value is None
            assert est.n == 0

    def test_k_non_positive_is_insufficient(self):
        assert pass_at_k([trial(True)], 0).value is None
        assert pass_power_k([trial(True)], -1).value is None


class TestPassPowerK:
    """Requirement: pass^k 与 trial 完成顺序无关"""

    def test_failing_trial_position_does_not_change_result(self):
        """Scenario: 失败 trial 换位不改变结果"""
        head = [trial(True, 0), trial(True, 1), trial(False, 2)]
        moved = [trial(False, 0), trial(True, 1), trial(True, 2)]
        for k in (2, 3):
            assert pass_power_k(head, k).value == pytest.approx(pass_power_k(moved, k).value)

    @pytest.mark.parametrize(
        ("k", "expected"),
        [(1, 1 / 3), (2, 0.0), (3, 0.0)],
    )
    def test_one_success_in_three(self, k, expected):
        trials = [trial(True), trial(False), trial(False)]
        assert pass_power_k(trials, k).value == pytest.approx(expected)

    def test_all_success_is_one(self):
        assert pass_power_k([trial(True)] * 3, 3).value == 1.0

    def test_never_exceeds_pass_at_k(self):
        trials = [trial(True), trial(True), trial(False)]
        for k in (1, 2, 3, 5):
            assert pass_power_k(trials, k).value <= pass_at_k(trials, k).value + 1e-12


class TestDenominatorFiltering:
    """Requirement: 每个聚合量声明自己的分母"""

    def test_invalid_trial_does_not_occupy_denominator(self):
        """Scenario: 无效 trial 不占分母 → n=2 且 invalid_trials 可见"""
        trials = [
            trial(True, 0),
            trial(False, 1),
            trial(False, 2, verdict=TrialVerdict.INVALID, reason=InvalidReason.GRADER_ERROR),
        ]
        est = pass_at_k(trials, 1)
        assert est.n == 2
        assert est.successes == 1
        assert est.value == pytest.approx(0.5)
        # 评测侧故障折出的 0 分不再拉低通过率 (同一组 trial 全为 valid 时是 1/3)
        all_valid = [trial(True, 0), trial(False, 1), trial(False, 2)]
        assert est.value > pass_at_k(all_valid, 1).value

    def test_counts_and_split_agree(self):
        trials = [
            trial(True, 0),
            trial(False, 1, verdict=TrialVerdict.INVALID, reason=InvalidReason.GRADER_TIMEOUT),
            pending_trial(2),
        ]
        buckets = split_trials_by_verdict(trials)
        assert [len(buckets[v]) for v in TrialVerdict] == [1, 1, 1]
        assert [i for i, _ in buckets[TrialVerdict.INVALID]] == [1]
        assert len(valid_trials(trials)) == 1

    @pytest.mark.parametrize(
        ("verdict", "reason", "expected"),
        [
            (TrialVerdict.VALID, None, TrialVerdict.VALID),
            (TrialVerdict.INVALID, InvalidReason.UNKNOWN_GRADER, TrialVerdict.INVALID),
            (TrialVerdict.INVALID, InvalidReason.TRIAL_TIMEOUT, TrialVerdict.INVALID),
        ],
    )
    def test_classify_trial(self, verdict, reason, expected):
        t = trial(True, verdict=verdict, reason=reason)
        assert classify_trial(t) is expected

    def test_pending_wins_over_invalid(self):
        """pending 不得被改写为 invalid (specs/orchestration)"""
        t = TrialResult(
            trial_index=0,
            success=False,
            verdict=TrialVerdict.INVALID,
            invalid_reason=InvalidReason.GRADER_ERROR,
            grader_results=[
                {
                    "grader_name": "human",
                    "grader_type": "custom",
                    "score": 0.0,
                    "passed": False,
                    "verdict": "pending",
                }
            ],
        )
        assert classify_trial(t) is TrialVerdict.PENDING

    def test_legacy_pending_details_status_recognised(self):
        """历史行仅有 details.status=pending (无 verdict) 仍识别为 pending"""
        t = TrialResult(
            trial_index=0,
            success=False,
            grader_results=[
                {
                    "grader_name": "human",
                    "grader_type": "custom",
                    "score": 0.0,
                    "passed": False,
                    "details": {"status": "pending"},
                }
            ],
        )
        assert classify_trial(t) is TrialVerdict.PENDING

    def test_invalid_reason_resolution(self):
        t = trial(False, verdict=TrialVerdict.INVALID, reason=InvalidReason.VERDICT_UNPARSEABLE)
        assert trial_invalid_reason(t) is InvalidReason.VERDICT_UNPARSEABLE
        assert trial_invalid_reason(trial(True)) is None

    def test_invalid_reason_falls_back_from_trial_to_grader(self):
        t = TrialResult(
            trial_index=0,
            success=False,
            verdict=TrialVerdict.INVALID,
            grader_results=[
                {
                    "grader_name": "boom",
                    "grader_type": "code",
                    "score": 0.0,
                    "passed": False,
                    "verdict": "invalid",
                    "invalid_reason": "no_criteria_configured",
                }
            ],
        )
        assert trial_invalid_reason(t) is InvalidReason.NO_CRITERIA_CONFIGURED

    def test_has_enough_data_entry_point(self):
        assert has_enough_data(0) is False
        assert has_enough_data(1) is True
        assert has_enough_data(4, minimum=5) is False
        assert has_enough_data(5, minimum=5) is True
        assert is_insufficient_data(None) is True
        assert is_insufficient_data(0.0) is False


class TestUncertainty:
    """Requirement: 聚合量必须附带不确定性与分布摘要"""

    def test_wilson_interval_bounds_and_degenerate_cases(self):
        low, high = wilson_interval(75, 100)
        assert 0.0 <= low < 0.75 < high <= 1.0
        # 极端比例仍给出非退化区间 (Wald 区间会坍缩)
        assert wilson_interval(0, 10)[0] > 0.0
        assert wilson_interval(0, 10)[1] > 0.0
        # p=1 时 Wilson 上界按构造为 1.0, 下界仍保守
        assert wilson_interval(10, 10)[1] == pytest.approx(1.0, abs=1e-5)
        assert wilson_interval(10, 10)[0] == pytest.approx(0.7225, abs=1e-4)
        assert wilson_interval(1, 3) == pytest.approx((0.0615, 0.7923), abs=1e-4)
        assert wilson_interval(5, 0) is None

    def test_smaller_confidence_gives_narrower_interval(self):
        wide = wilson_interval(1, 3, confidence=0.99)
        narrow = wilson_interval(1, 3, confidence=0.90)
        assert wide[0] < narrow[0] and narrow[1] < wide[1]

    def test_bootstrap_is_reproducible_with_seed(self):
        values = [0.9, 0.85, 0.2, 0.8, 0.7]
        first = bootstrap_ci(values, seed=42)
        second = bootstrap_ci(values, seed=42)
        assert first == second
        mean = sum(values) / len(values)
        assert first[0] <= mean <= first[1]

    def test_bootstrap_respects_1000_round_floor(self):
        """spec: 重采样不少于 1000 次"""
        values = [0.1, 0.9]
        assert bootstrap_ci(values, rounds=10, seed=1) == bootstrap_ci(values, seed=1)

    def test_bootstrap_of_single_value_collapses_to_it(self):
        assert bootstrap_ci([0.5], seed=7) == pytest.approx((0.5, 0.5))
        assert bootstrap_ci([]) is None

    def test_summarize_scores_reports_mean_and_floor_together(self):
        """Scenario: 可靠性地板与均值并列 (任一者都不被省略)"""
        dist = summarize_scores([0.9, 0.85, 0.2, 0.8, 0.7], seed=3)
        assert dist.worst_of_n == 0.2
        assert dist.mean == pytest.approx(0.69)
        assert dist.n == 5
        assert dist.method == "bootstrap"
        assert dist.ci_low is not None and dist.ci_high is not None
        assert dist.ci_low <= dist.mean <= dist.ci_high

    def test_summarize_scores_empty_is_insufficient(self):
        dist = summarize_scores([])
        assert dist.mean is None
        assert dist.worst_of_n is None
        assert dist.method == "insufficient_data"
        assert dist.n == 0

    @pytest.mark.parametrize(
        ("values", "q", "expected"),
        [
            ([1.0, 2.0, 3.0], 50.0, 2.0),
            ([1.0, 2.0, 3.0], 0.0, 1.0),
            ([1.0, 2.0, 3.0], 100.0, 3.0),
            ([1.0, 2.0], 50.0, 1.5),
            ([5.0], 95.0, 5.0),
            ([], 95.0, None),
        ],
    )
    def test_percentile(self, values, q, expected):
        assert percentile(values, q) == expected

    def test_p50_p95_and_worst_of_n(self):
        values = list(range(1, 101))
        assert p50([float(v) for v in values]) == pytest.approx(50.5)
        assert p95([float(v) for v in values]) == pytest.approx(95.05)
        assert worst_of_n([float(v) for v in values]) == 1.0
        assert worst_of_n([]) is None


class TestAggregateMetrics:
    """过程指标 MUST 附 p50 与 p95 (avg/min/max 的超集)"""

    def test_extends_avg_min_max_with_percentiles(self):
        t1 = trial(True)
        t1.metrics = {"n_turns": 2.0, "latency_ms": 100.0}
        t2 = trial(True)
        t2.metrics = {"n_turns": 4.0, "latency_ms": 300.0}
        agg = aggregate_metrics([t1, t2])
        assert agg["n_turns_avg"] == 3.0
        assert agg["n_turns_min"] == 2.0
        assert agg["n_turns_max"] == 4.0
        assert agg["latency_ms_avg"] == 200.0
        assert agg["n_turns_p50"] == 3.0
        assert agg["n_turns_p95"] == pytest.approx(3.9)
        assert agg["latency_ms_p50"] == 200.0

    def test_empty(self):
        assert aggregate_metrics([]) == {}

    def test_estimates_to_rates(self):
        est = pass_at_k([trial(True), trial(False), trial(False)], 1)
        rates = estimates_to_rates({1: est})
        assert rates == {1: pytest.approx(1 / 3)}
        assert estimates_to_rates({1: pass_at_k([], 1)}) == {1: None}


class TestExtractMetrics:
    """过程指标一律由归一化观测派生 (specs/trace-provider + specs/statistics)。"""

    def test_counts_and_tokens_both_vocabularies(
        self, make_turn_span, make_tool_span, attribute_mapping
    ):
        spans = [make_turn_span(), make_turn_span(), make_tool_span("fs_write")]
        metrics = extract_metrics(
            normalize_spans(spans, mapping=attribute_mapping),
            ["n_turns", "n_toolcalls", "n_total_tokens"],
        )
        assert metrics["n_turns"] == 2.0
        assert metrics["n_toolcalls"] == 1.0
        assert metrics["n_total_tokens"] == 300.0  # (输入 100 + 输出 50) × 2 次调用

    def test_span_names_are_not_evidence(self, attribute_mapping):
        """名称写得像工具调用不算证据: 角色只按标准观测字段判定。"""
        lookalikes = [
            {"name": "tool.call", "attributes": {}, "status": {"status_code": "OK"}},
            {"name": "agent.turn", "attributes": {}, "status": {"status_code": "OK"}},
        ]
        metrics = extract_metrics(
            normalize_spans(lookalikes, mapping=attribute_mapping),
            ["n_turns", "n_toolcalls"],
        )
        assert metrics["n_turns"] == 0.0
        assert metrics["n_toolcalls"] == 0.0

    def test_unreadable_metric_is_reported_as_gap_not_zero(
        self, make_tool_span, attribute_mapping
    ):
        """只读到工具调用: token 无从得知 → 该键缺席并带原因, 不出现 0。"""
        observations = normalize_spans(
            [make_tool_span("fs_write")], mapping=attribute_mapping
        )
        result = extract_process_metrics(observations, ["n_total_tokens"])
        assert "n_total_tokens" not in result.metrics
        gap_fields = {gap.field: gap.reason for gap in result.gaps}
        assert gap_fields["n_total_tokens"] == "provider_not_covered"
        assert gap_fields["cost_usd"] == "price_table_not_configured"

    def test_provider_unavailable_leaves_counts_unknown(self, normalize):
        """后端不可用与「返回空」都按缺失归类, 不冒充 0 (spec 1.6)。"""
        unavailable = normalize([], source_status="unavailable", source_detail="未安装")
        assert extract_metrics(unavailable, ["n_turns", "n_toolcalls"]) == {}
        assert is_missing(unavailable.tool_call_count)
        # provider 答了但一条 span 都没有: 同样不给「0 次调用」的假象
        empty = normalize([])
        assert extract_metrics(empty, ["n_toolcalls"]) == {}
        assert is_missing(empty.tool_call_count)

    def test_real_zero_when_trace_has_no_tool_spans(
        self, make_turn_span, attribute_mapping
    ):
        observations = normalize_spans([make_turn_span()], mapping=attribute_mapping)
        assert extract_metrics(observations, ["n_toolcalls"])["n_toolcalls"] == 0.0

    def test_non_numeric_token_value_is_not_silently_dropped(
        self, attribute_mapping, normalize
    ):
        """读不懂的值不冒充 0: 该字段报「属性/值不认识」。"""
        attr = attribute_mapping.candidates("usage.input_tokens")[0]
        observations = normalize(
            [{"name": "agent.turn", "attributes": {attr: "abc"}}]
        )
        result = extract_process_metrics(observations, ["n_input_tokens"])
        assert "n_input_tokens" not in result.metrics
        assert observations.missing_fields["usage.input_tokens"] == "unrecognized_attribute"

    def test_unrecognized_attribute_names_are_listed(self, attribute_mapping, normalize):
        """映射不认识的品牌属性名进未识别清单 (供映射更新), 不静默丢弃。"""
        observations = normalize(
            [{"name": "x", "attributes": {"totally_unknown_attr": 1}}],
            mapping=attribute_mapping,
        )
        assert observations.unrecognized_attributes == ["totally_unknown_attr"]

    def test_token_four_way_split(self, make_turn_span, attribute_mapping):
        observations = normalize_spans(
            [make_turn_span(
                input_tokens=1000,
                output_tokens=200,
                reasoning_tokens=64,
                cache_read_tokens=128,
            )],
            mapping=attribute_mapping,
        )
        metrics = extract_metrics(observations, ["n_total_tokens"])
        assert metrics["n_input_tokens"] == 1000.0
        assert metrics["n_output_tokens"] == 200.0
        assert metrics["n_reasoning_tokens"] == 64.0
        assert metrics["n_cache_read_tokens"] == 128.0
        # 别名只等于输入+输出, 不把推理/缓存并进去
        assert metrics["n_total_tokens"] == 1200.0

    def test_cost_requires_price_table(self, make_turn_span, attribute_mapping):
        observations = normalize_spans(
            [make_turn_span(input_tokens=1_000_000, output_tokens=1_000_000)],
            mapping=attribute_mapping,
        )
        unpriced = extract_process_metrics(observations, ["n_total_tokens"])
        assert "cost_usd" not in unpriced.metrics
        assert any(gap.field == "cost_usd" for gap in unpriced.gaps)

        priced = extract_process_metrics(
            observations,
            ["n_total_tokens"],
            price_table=PriceTable(
                default=TokenPrices(input_per_mtok=3.0, output_per_mtok=6.0)
            ),
        )
        assert priced.metrics["cost_usd"] == pytest.approx(9.0)

    def test_reasoning_and_cache_tokens_priced_as_subset_of_parent(
        self, make_turn_span, attribute_mapping
    ):
        """四路分别计量不合并; 折算时明细桶从父桶扣出, 不重复计费。

        真实语义 (OTel GenAI 与 OpenInference 均如此): cache_read ⊆ input,
        reasoning ⊆ output。全量命中缓存 + 全量推理时, 只有两个明细桶产生成本。
        """
        observations = normalize_spans(
            [make_turn_span(
                input_tokens=1_000_000,
                output_tokens=1_000_000,
                reasoning_tokens=1_000_000,
                cache_read_tokens=1_000_000,
            )],
            mapping=attribute_mapping,
        )
        priced = extract_process_metrics(
            observations,
            ["n_total_tokens"],
            price_table=PriceTable(
                default=TokenPrices(
                    input_per_mtok=2.0,
                    output_per_mtok=4.0,
                    reasoning_per_mtok=16.0,
                    cache_read_per_mtok=0.2,
                )
            ),
        )
        # 非缓存输入 0×2.0 + 缓存读 1M×0.2 + 非推理输出 0×4.0 + 推理 1M×16
        assert priced.metrics["cost_usd"] == pytest.approx(16.2)
        # 四个桶仍各自上报, 没有被折叠成一个总数
        assert priced.metrics["n_input_tokens"] == 1_000_000.0
        assert priced.metrics["n_output_tokens"] == 1_000_000.0
        assert priced.metrics["n_cache_read_tokens"] == 1_000_000.0
        assert priced.metrics["n_reasoning_tokens"] == 1_000_000.0

    def test_disjoint_detail_buckets_are_added_not_subtracted(
        self, make_turn_span, attribute_mapping
    ):
        """Anthropic 风格的 input 不含缓存读 —— 显式声明后回到四桶相加。"""
        observations = normalize_spans(
            [make_turn_span(
                input_tokens=1_000_000,
                output_tokens=1_000_000,
                reasoning_tokens=1_000_000,
                cache_read_tokens=1_000_000,
            )],
            mapping=attribute_mapping,
        )
        priced = extract_process_metrics(
            observations,
            [],
            price_table=PriceTable(
                default=TokenPrices(
                    input_per_mtok=2.0,
                    output_per_mtok=4.0,
                    reasoning_per_mtok=16.0,
                    cache_read_per_mtok=0.2,
                ),
                detail_semantics="disjoint",
            ),
        )
        assert priced.metrics["cost_usd"] == pytest.approx(22.2)

    def test_detail_bucket_larger_than_parent_is_not_computable(
        self, make_turn_span, attribute_mapping
    ):
        """子集口径下明细大于父桶说明口径判断错了 —— 报不可计算, 不夹取为 0。"""
        observations = normalize_spans(
            [make_turn_span(input_tokens=1_000, cache_read_tokens=2_000)],
            mapping=attribute_mapping,
        )
        priced = extract_process_metrics(
            observations,
            [],
            price_table=PriceTable(default=TokenPrices(input_per_mtok=2.0)),
        )
        assert "cost_usd" not in priced.metrics
        cost_gap = next(gap for gap in priced.gaps if gap.field == "cost_usd")
        assert cost_gap.reason.startswith("detail_tokens_exceed_parent")

    def test_per_model_price_override(self, make_turn_span, attribute_mapping):
        observations = normalize_spans(
            [make_turn_span(input_tokens=1_000_000, output_tokens=0, model="cheap-model")],
            mapping=attribute_mapping,
        )
        table = PriceTable(
            default=TokenPrices(input_per_mtok=10.0),
            by_model={"cheap-model": TokenPrices(input_per_mtok=1.0)},
        )
        priced = extract_process_metrics(observations, [], price_table=table)
        assert priced.metrics["cost_usd"] == pytest.approx(1.0)

    def test_model_without_price_entry_is_not_computable(
        self, make_turn_span, attribute_mapping
    ):
        observations = normalize_spans(
            [make_turn_span(input_tokens=1000, model="unlisted-model")],
            mapping=attribute_mapping,
        )
        priced = extract_process_metrics(
            observations, [], price_table=PriceTable(by_model={"other": TokenPrices(input_per_mtok=1.0)})
        )
        assert "cost_usd" not in priced.metrics
        cost_gap = next(gap for gap in priced.gaps if gap.field == "cost_usd")
        assert cost_gap.reason == "model_price_not_covered"

    def test_step_efficiency_is_a_diagnostic_quantity(
        self, make_tool_span, attribute_mapping
    ):
        observations = normalize_spans(
            [make_tool_span(t) for t in ("a", "b", "c", "d")], mapping=attribute_mapping
        )
        metrics = extract_metrics(observations, [], optimal_steps=2)
        assert metrics["step_efficiency"] == pytest.approx(0.5)

    def test_respects_tracked_list_for_legacy_names(
        self, make_turn_span, make_tool_span, attribute_mapping
    ):
        observations = normalize_spans(
            [make_turn_span(), make_tool_span("fs_write")], mapping=attribute_mapping
        )
        metrics = extract_metrics(observations, ["n_toolcalls"])
        assert metrics["n_toolcalls"] == 1.0
        assert "n_turns" not in metrics


class TestResourceAxis:
    """Scenario: 失败比成功更贵 → 两类分别呈现, 不被全局均值抹平。"""

    @staticmethod
    def _trial(success: bool, cost: float | None, tokens: float) -> TrialResult:
        metrics: dict[str, float] = {"n_total_tokens": tokens}
        gaps: list[EvidenceGap] = []
        if cost is None:
            gaps.append(EvidenceGap(field="cost_usd", reason="price_table_not_configured"))
        else:
            metrics["cost_usd"] = cost
        return TrialResult(
            trial_index=0, success=success, metrics=metrics, evidence_gaps=gaps
        )

    def test_passed_and_failed_resources_are_summarized_separately(self):
        trials = [
            self._trial(True, 1.0, 100.0),
            self._trial(True, 1.0, 100.0),
            self._trial(False, 3.0, 900.0),
            self._trial(False, 3.0, 900.0),
        ]
        summary = summarize_resources(trials)
        assert summary.passed.avg_cost_usd == pytest.approx(1.0)
        assert summary.failed.avg_cost_usd == pytest.approx(3.0)
        assert summary.failed.avg_total_tokens == pytest.approx(900.0)
        # 全局均值仍看得见差异存在 (两类均值比全局更靠近真实分布)
        assert summary.avg_cost_usd == pytest.approx(2.0)
        assert (summary.passed.trials, summary.failed.trials) == (2, 2)

    def test_unpriced_trials_are_tallied_not_zeroed(self):
        summary = summarize_resources(
            [self._trial(True, None, 100.0), self._trial(False, None, 200.0)]
        )
        assert summary.total_cost_usd is None
        assert summary.cost_unknown_trials == 2
        assert summary.cost_unknown_reason == "price_table_not_configured"
        # token 轴与成本轴各自独立: 成本算不出不拖累 token 汇总
        assert summary.avg_total_tokens == pytest.approx(150.0)

    def test_cost_never_enters_the_pass_rate_axis(self):
        """成本是与通过率并列的轴, 汇总里没有把两者折成一格的复合字段。"""
        fields = set(ResourceSummary.model_fields)
        assert not {"score", "composite", "weighted_score", "overall"} & fields
        assert {"total_cost_usd", "passed", "failed", "cost_unknown_trials"} <= fields
