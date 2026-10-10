"""The statistics, implemented from scratch on the standard library.

Two-proportion z-test (pooled), for the primary metric and each guardrail:

    p1 = x1 / n1          p2 = x2 / n2          p = (x1 + x2) / (n1 + n2)
    SE = sqrt(p * (1 - p) * (1/n1 + 1/n2))
    z  = (p2 - p1) / SE
    p-value (two-sided) = 2 * (1 - Phi(|z|)) = erfc(|z| / sqrt(2))

Confidence interval for the difference uses the unpooled standard error:

    SE_u = sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    CI   = (p2 - p1) +/- z_(1 - alpha/2) * SE_u

Sample-ratio mismatch, Pearson chi-square goodness of fit with k = 2 arms:

    E_i  = N * w_i
    chi2 = sum((O_i - E_i)^2 / E_i)
    p-value = P(chi2_1 > chi2) = erfc(sqrt(chi2 / 2))     (df = 1, so chi2 = z^2)

Fixed-horizon sample size per arm, for baseline p1, target p2 = p1 + delta,
and allocation ratio k = n2 / n1:

    pbar = (p1 + k * p2) / (1 + k)
    n1 = (z_(1-alpha/2) * sqrt(pbar * (1 - pbar) * (1 + 1/k))
          + z_(1-beta) * sqrt(p1 * (1 - p1) + p2 * (1 - p2) / k))^2 / delta^2
    n2 = k * n1

Normal quantiles come from statistics.NormalDist (stdlib). Nothing here is
sequential or Bayesian: these p-values are valid once, at the planned horizon.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

_STD_NORMAL = NormalDist()


def two_sided_p_from_z(z: float) -> float:
    """P(|Z| >= |z|) for a standard normal Z."""
    return math.erfc(abs(z) / math.sqrt(2.0))


def z_critical(alpha: float) -> float:
    """The z value with alpha/2 in each tail (1.96 for alpha = 0.05)."""
    return _STD_NORMAL.inv_cdf(1.0 - alpha / 2.0)


@dataclass(frozen=True)
class ZTest:
    p1: float
    p2: float
    diff: float  # p2 - p1, absolute
    relative: float | None  # (p2 - p1) / p1, None when p1 == 0
    se_pooled: float
    z: float
    p_value: float
    ci_low: float
    ci_high: float
    alpha: float

    @property
    def significant(self) -> bool:
        return self.p_value < self.alpha


def two_proportion_z_test(x1: int, n1: int, x2: int, n2: int, alpha: float = 0.05) -> ZTest:
    """Compare treatment (x2/n2) against control (x1/n1)."""
    if n1 <= 0 or n2 <= 0:
        raise ValueError("both arms need at least one user")
    if not (0 <= x1 <= n1 and 0 <= x2 <= n2):
        raise ValueError("conversions must be between 0 and the arm size")
    p1 = x1 / n1
    p2 = x2 / n2
    pooled = (x1 + x2) / (n1 + n2)
    se = math.sqrt(pooled * (1.0 - pooled) * (1.0 / n1 + 1.0 / n2))
    diff = p2 - p1
    if se == 0.0:
        z, p_value = 0.0, 1.0
    else:
        z = diff / se
        p_value = two_sided_p_from_z(z)
    se_u = math.sqrt(p1 * (1.0 - p1) / n1 + p2 * (1.0 - p2) / n2)
    margin = z_critical(alpha) * se_u
    return ZTest(
        p1=p1,
        p2=p2,
        diff=diff,
        relative=(diff / p1) if p1 > 0 else None,
        se_pooled=se,
        z=z,
        p_value=p_value,
        ci_low=diff - margin,
        ci_high=diff + margin,
        alpha=alpha,
    )


@dataclass(frozen=True)
class SRMTest:
    observed: tuple[int, int]
    expected: tuple[float, float]
    chi2: float
    p_value: float


def srm_chi_square(observed: tuple[int, int], weights: tuple[float, float]) -> SRMTest:
    """Chi-square goodness of fit of observed arm sizes against the planned split."""
    total = sum(observed)
    if total <= 0:
        raise ValueError("no users to test")
    weight_sum = sum(weights)
    expected = (total * weights[0] / weight_sum, total * weights[1] / weight_sum)
    chi2 = sum((o - e) ** 2 / e for o, e in zip(observed, expected))
    return SRMTest(
        observed=observed,
        expected=expected,
        chi2=chi2,
        p_value=math.erfc(math.sqrt(chi2 / 2.0)),
    )


def required_sample_size(
    baseline: float,
    lift: float,
    alpha: float = 0.05,
    power: float = 0.8,
    split: tuple[float, float] = (0.5, 0.5),
) -> tuple[int, int]:
    """Minimum users per arm (control, treatment) to detect an absolute lift."""
    p1 = baseline
    p2 = baseline + lift
    if not 0.0 < p1 < 1.0:
        raise ValueError("baseline_rate must be strictly between 0 and 1")
    if lift == 0.0 or not 0.0 < p2 < 1.0:
        raise ValueError("baseline_rate + expected_lift must be strictly between 0 and 1, lift nonzero")
    k = split[1] / split[0]
    pbar = (p1 + k * p2) / (1.0 + k)
    z_a = z_critical(alpha)
    z_b = _STD_NORMAL.inv_cdf(power)
    numerator = (
        z_a * math.sqrt(pbar * (1.0 - pbar) * (1.0 + 1.0 / k))
        + z_b * math.sqrt(p1 * (1.0 - p1) + p2 * (1.0 - p2) / k)
    ) ** 2
    n1 = math.ceil(numerator / lift**2)
    n2 = math.ceil(k * n1)
    return n1, n2
