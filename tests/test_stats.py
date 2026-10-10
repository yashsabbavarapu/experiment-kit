"""The math, checked against values computed by hand (shown in the comments)."""
from __future__ import annotations

import math

import pytest

from experiment_kit.stats import (
    required_sample_size,
    srm_chi_square,
    two_proportion_z_test,
    two_sided_p_from_z,
    z_critical,
)


def test_z_test_matches_hand_computation() -> None:
    # control 100/1000 = 0.10, treatment 130/1000 = 0.13
    # pooled p = 230/2000 = 0.115
    # SE = sqrt(0.115 * 0.885 * (1/1000 + 1/1000)) = sqrt(0.00020355) = 0.0142671
    # z = 0.03 / 0.0142671 = 2.10274
    # p = erfc(2.10274 / sqrt(2)) = 0.03549
    t = two_proportion_z_test(100, 1000, 130, 1000)
    assert t.p1 == pytest.approx(0.10)
    assert t.p2 == pytest.approx(0.13)
    assert t.diff == pytest.approx(0.03)
    assert t.relative == pytest.approx(0.30)
    assert t.se_pooled == pytest.approx(0.0142671, abs=1e-6)
    assert t.z == pytest.approx(2.10274, abs=1e-4)
    assert t.p_value == pytest.approx(0.03549, abs=1e-4)
    assert t.significant
    # CI: SE_u = sqrt(0.09/1000 + 0.1131/1000) = 0.0142513, margin = 1.959964 * SE_u = 0.027932
    assert t.ci_low == pytest.approx(0.03 - 0.027932, abs=1e-5)
    assert t.ci_high == pytest.approx(0.03 + 0.027932, abs=1e-5)


def test_z_test_symmetric_and_null() -> None:
    forward = two_proportion_z_test(100, 1000, 130, 1000)
    backward = two_proportion_z_test(130, 1000, 100, 1000)
    assert backward.z == pytest.approx(-forward.z)
    assert backward.p_value == pytest.approx(forward.p_value)
    same = two_proportion_z_test(50, 500, 50, 500)
    assert same.z == 0.0 and same.p_value == pytest.approx(1.0)
    assert not same.significant


def test_z_test_degenerate_zero_variance() -> None:
    t = two_proportion_z_test(0, 100, 0, 100)
    assert t.z == 0.0 and t.p_value == 1.0 and t.relative is None


def test_z_test_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        two_proportion_z_test(1, 0, 1, 10)
    with pytest.raises(ValueError):
        two_proportion_z_test(11, 10, 1, 10)


def test_normal_helpers() -> None:
    assert z_critical(0.05) == pytest.approx(1.959964, abs=1e-6)
    assert two_sided_p_from_z(1.959964) == pytest.approx(0.05, abs=1e-6)
    assert two_sided_p_from_z(0.0) == pytest.approx(1.0)


def test_srm_chi_square_hand_values() -> None:
    # 5100 vs 4900 on 50/50: E = 5000 each, chi2 = (100^2 + 100^2) / 5000 = 4.0
    # p = erfc(sqrt(4 / 2)) = erfc(1.41421) = 0.0455
    srm = srm_chi_square((5100, 4900), (0.5, 0.5))
    assert srm.expected == (5000.0, 5000.0)
    assert srm.chi2 == pytest.approx(4.0)
    assert srm.p_value == pytest.approx(0.0455, abs=1e-4)
    # chi2 with df = 1 is z^2, so the p-value must equal the two-sided normal p of sqrt(chi2)
    assert srm.p_value == pytest.approx(two_sided_p_from_z(math.sqrt(4.0)))


def test_srm_respects_custom_split() -> None:
    assert srm_chi_square((900, 100), (0.9, 0.1)).p_value == pytest.approx(1.0)
    # 500/500 against a planned 90/10 is a massive mismatch
    assert srm_chi_square((500, 500), (0.9, 0.1)).p_value < 1e-10
    with pytest.raises(ValueError):
        srm_chi_square((0, 0), (0.5, 0.5))


def test_required_sample_size_hand_value() -> None:
    # p1 = 0.10, p2 = 0.125, pbar = 0.1125, k = 1
    # (1.959964 * sqrt(2 * 0.1125 * 0.8875) + 0.841621 * sqrt(0.09 + 0.109375))^2 / 0.025^2
    # = (0.875843 + 0.375800)^2 / 0.000625 = 2506.5, rounded up to 2507
    assert required_sample_size(0.10, 0.025) == (2507, 2507)


def test_required_sample_size_unequal_split_and_bounds() -> None:
    n_c, n_t = required_sample_size(0.10, 0.025, split=(0.8, 0.2))
    assert n_t == math.ceil(0.25 * n_c)
    # an unbalanced test needs more users in total than a balanced one
    assert n_c + n_t > 2 * 2507
    # a smaller effect needs more users
    assert required_sample_size(0.10, 0.01)[0] > required_sample_size(0.10, 0.025)[0]
    with pytest.raises(ValueError):
        required_sample_size(0.0, 0.02)
    with pytest.raises(ValueError):
        required_sample_size(0.99, 0.02)
