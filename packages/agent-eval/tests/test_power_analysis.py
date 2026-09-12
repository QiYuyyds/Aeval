"""功效分析函数单测 (变更⑥ tasks 5.1–5.2)。

已知对照表对照手算值 (闭式公式独立求值); 反解与 ``wilson_interval`` 正向
互验 — 用算出的 N 反代回区间宽度必须 ≤ δ, 且 N-1 不满足 (最小性)。
"""

from __future__ import annotations

import math
from statistics import NormalDist

import pytest

from agent_eval.core.metrics import (
    Z_SCORE_AT_DEFAULT_CONFIDENCE,
    normal_two_sample_size,
    wilson_half_width,
    wilson_interval,
    wilson_sample_size,
)

Z95 = NormalDist().inv_cdf(0.975)


def _reference_half_width(p: float, n: int) -> float:
    """独立手算路径: 直接按 Wilson 公式展开 (不经过被测函数)。"""
    z = Z95
    denom = 1.0 + z * z / n
    return (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))


class TestWilsonSampleSize:
    def test_known_value_p_half_delta_ten_percent(self):
        """标准对照: p=0.5, δ=±10% → N=93 (手算: 二次方程正根 92.196 向上取整)。"""
        assert wilson_sample_size(0.5, 0.10) == 93
        # 手算值交叉验证: N=93 的半宽 ≤ 0.10 且 N=92 不满足
        assert _reference_half_width(0.5, 93) <= 0.10
        assert _reference_half_width(0.5, 92) > 0.10

    @pytest.mark.parametrize(
        ("p", "delta", "expected"),
        [
            (0.5, 0.10, 93),
            (0.5, 0.03, 1064),
            (0.7, 0.03, 894),  # spec 场景: 基线 p≈0.7, ±3% 区间半宽
            (0.7, 0.05, 320),
            (1.0, 0.10, 16),  # 端点 p: Wilson 区间不塌缩, 半宽仍有意义
        ],
    )
    def test_known_value_table(self, p, delta, expected):
        assert wilson_sample_size(p, delta) == expected

    def test_half_width_matches_independent_computation(self):
        for p in (0.0, 0.3, 0.5, 0.7, 1.0):
            for n in (1, 5, 30, 100, 1000):
                assert wilson_half_width(p, n) == pytest.approx(
                    _reference_half_width(p, n)
                )

    def test_forward_cross_validation_with_wilson_interval(self):
        """反解与 wilson_interval 正向互验: N 反代回区间宽度 ≤ δ (tasks 5.2)。"""
        for p, delta in ((0.5, 0.03), (0.7, 0.05), (0.3, 0.08)):
            n = wilson_sample_size(p, delta)
            successes = round(p * n)
            low, high = wilson_interval(successes, n)
            # p̂=c/n 与规划 p 有舍入差, 半宽互验允许该舍入级误差
            assert (high - low) / 2 <= delta + 0.005, (p, delta, n)
            assert wilson_half_width(p, n) <= delta

    def test_minimality(self):
        """N 是最小整数解: N-1 的半宽必须 > δ。"""
        for p, delta in ((0.5, 0.10), (0.7, 0.03), (0.9, 0.02)):
            n = wilson_sample_size(p, delta)
            if n > 1:
                assert wilson_half_width(p, n - 1) > delta

    def test_p_half_is_the_most_conservative_choice(self):
        """p=0.5 是最保守基线: 任何其他 p 的 N 都不超过它。"""
        for delta in (0.03, 0.05, 0.1):
            worst = wilson_sample_size(0.5, delta)
            for p in (0.1, 0.3, 0.7, 0.9):
                assert wilson_sample_size(p, delta) <= worst

    @pytest.mark.parametrize("delta", [0.0, -0.1, 0.5, 0.9])
    def test_delta_out_of_range_raises(self, delta):
        with pytest.raises(ValueError, match="delta"):
            wilson_sample_size(0.5, delta)

    @pytest.mark.parametrize("p", [-0.1, 1.1])
    def test_p_out_of_range_raises(self, p):
        with pytest.raises(ValueError, match="p "):
            wilson_sample_size(p, 0.05)


class TestNormalTwoSampleSize:
    @pytest.mark.parametrize(
        ("ratio", "expected"),
        [
            # n = 2·(z·σ/d)², z = 1.95996…: d/σ=0.2 → 192.07 → 193
            (0.2, 193),
            (0.5, 31),
            (1.0, 8),
        ],
    )
    def test_known_value_table(self, ratio, expected):
        """d/σ 比值对照手算值 (tasks 5.2)。"""
        assert normal_two_sample_size(ratio, 1.0) == expected

    def test_scales_with_sigma(self):
        assert normal_two_sample_size(0.05, 0.2) == normal_two_sample_size(0.1, 0.4)
        assert normal_two_sample_size(0.05, 0.3) > normal_two_sample_size(0.05, 0.2)

    def test_smaller_difference_needs_more_samples(self):
        assert normal_two_sample_size(0.01, 0.2) > normal_two_sample_size(0.05, 0.2)

    def test_zero_sigma_needs_at_most_one(self):
        assert normal_two_sample_size(0.05, 0.0) == 1

    def test_huge_effect_needs_at_least_one(self):
        assert normal_two_sample_size(10.0, 0.5) == 1

    def test_zero_difference_raises(self):
        with pytest.raises(ValueError, match="d "):
            normal_two_sample_size(0.0, 0.2)

    def test_negative_sigma_raises(self):
        with pytest.raises(ValueError, match="sigma"):
            normal_two_sample_size(0.05, -0.2)


def test_z_score_constant_matches_the_public_interval():
    """自陈公式用的 z 值与 wilson_interval 的实际置信水平一致。"""
    assert pytest.approx(1.959963984540054) == Z_SCORE_AT_DEFAULT_CONFIDENCE
