"""Monte Carlo of null experiments: why the peeking guardrail exists.

Every simulated experiment has no true effect (both arms convert at the same
rate). Reading once at the horizon declares a false winner about alpha of
the time. Reading every day and stopping at the first p < alpha declares
false winners far more often. That gap is the cost of peeking.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from experiment_kit.stats import two_proportion_z_test


@dataclass(frozen=True)
class PeekingResult:
    experiments: int
    days: int
    fixed_horizon_false_positives: int
    daily_peeking_false_positives: int

    @property
    def fixed_horizon_rate(self) -> float:
        return self.fixed_horizon_false_positives / self.experiments

    @property
    def daily_peeking_rate(self) -> float:
        return self.daily_peeking_false_positives / self.experiments


def simulate_peeking(
    experiments: int = 400,
    days: int = 14,
    users_per_arm_per_day: int = 150,
    rate: float = 0.10,
    alpha: float = 0.05,
    seed: int = 7,
) -> PeekingResult:
    rng = random.Random(seed)
    fixed = peeked = 0
    for _ in range(experiments):
        n = x1 = x2 = 0
        stopped = False
        for _day in range(days):
            n += users_per_arm_per_day
            x1 += sum(1 for _ in range(users_per_arm_per_day) if rng.random() < rate)
            x2 += sum(1 for _ in range(users_per_arm_per_day) if rng.random() < rate)
            if not stopped and two_proportion_z_test(x1, n, x2, n, alpha).significant:
                stopped = True
        peeked += int(stopped)
        fixed += int(two_proportion_z_test(x1, n, x2, n, alpha).significant)
    return PeekingResult(experiments, days, fixed, peeked)
