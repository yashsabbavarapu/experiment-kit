"""Calibration: the z-test holds alpha at the horizon, and peeking breaks it."""
from __future__ import annotations

from experiment_kit.simulate import simulate_peeking


def test_fixed_horizon_holds_alpha_and_peeking_inflates_it() -> None:
    result = simulate_peeking(experiments=300, days=14, users_per_arm_per_day=100, seed=3)
    # 300 null experiments: the fixed-horizon false-positive rate should sit near 5%
    assert 0.01 <= result.fixed_horizon_rate <= 0.10
    # stopping at the first daily p < 0.05 declares false winners several times as often
    assert result.daily_peeking_rate >= 0.12
    assert result.daily_peeking_rate >= 2 * result.fixed_horizon_rate
