"""The guardrailed readout: numbers, then checks, then a verdict in plain language.

Verdict precedence (first match wins):

1. INVALID  any integrity guardrail failed: no exposures, sample-ratio
            mismatch, read before the planned end date (peeking), fewer
            users than the preregistered minimum, or a preregistered
            guardrail metric that was never measured.
2. KILL     a guardrail metric got significantly worse, or the primary
            metric got significantly worse.
3. SHIP     the primary metric improved significantly and nothing above fired.
4. INCONCLUSIVE  otherwise: a valid test that found no detectable difference.

The kit is a fixed-horizon tool. It reads each experiment once, on or after
its end date, using only events inside [start_date, end_date]. An earlier
read still prints the numbers, labeled as not decision-grade, together with
what a naive dashboard would have concluded.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from experiment_kit.assignment import assign
from experiment_kit.models import ARMS, ArmStats, Check, Event, Hypothesis, Verdict
from experiment_kit.stats import ZTest, srm_chi_square, two_proportion_z_test


@dataclass(frozen=True)
class Readout:
    hypothesis: Hypothesis
    as_of: date
    window_end: date
    control: ArmStats
    treatment: ArmStats
    test: ZTest | None
    checks: list[Check]
    verdict: Verdict
    reasons: list[str]
    naive_verdict: Verdict | None
    events_outside_window: int
    earlier_reads: int
    earlier_early_reads: int
    guardrail_tests: dict[str, ZTest] = field(default_factory=dict)


def pct(x: float) -> str:
    return f"{x * 100:.2f}%"


def pp(x: float) -> str:
    return f"{x * 100:+.2f} pp"


def _naive(test: ZTest | None) -> Verdict | None:
    if test is None:
        return None
    if test.significant and test.diff > 0:
        return Verdict.SHIP
    if test.significant and test.diff < 0:
        return Verdict.KILL
    return Verdict.INCONCLUSIVE


def compute_readout(
    h: Hypothesis,
    events: Iterable[Event],
    as_of: date,
    history: list[tuple[date, str]] | None = None,
) -> Readout:
    history = history or []
    window_end = min(as_of, h.end_date)

    converted: dict[str, bool] = {}
    metric_hits: dict[str, dict[str, int]] = {}
    measured: set[str] = set()
    outside = 0
    for e in events:
        day = e.timestamp.date()
        if day < h.start_date or day > window_end:
            outside += 1
            continue
        converted[e.user_id] = converted.get(e.user_id, False) or e.converted
        hits = metric_hits.setdefault(e.user_id, {})
        for key, value in e.metrics.items():
            measured.add(key)
            hits[key] = max(hits.get(key, 0), value)

    users = {arm: 0 for arm in ARMS}
    conversions = {arm: 0 for arm in ARMS}
    metric_counts = {g: {arm: 0 for arm in ARMS} for g in h.guardrail_metrics}
    for user_id, did_convert in converted.items():
        arm = assign(h, user_id)
        users[arm] += 1
        conversions[arm] += int(did_convert)
        for g in h.guardrail_metrics:
            metric_counts[g][arm] += metric_hits[user_id].get(g, 0)

    control = ArmStats(ARMS[0], users[ARMS[0]], conversions[ARMS[0]])
    treatment = ArmStats(ARMS[1], users[ARMS[1]], conversions[ARMS[1]])
    both_arms = control.users > 0 and treatment.users > 0
    test = (
        two_proportion_z_test(control.conversions, control.users, treatment.conversions, treatment.users, h.alpha)
        if both_arms
        else None
    )

    checks: list[Check] = []
    planned = f"{h.split[0] * 100:.0f}/{h.split[1] * 100:.0f}"

    # 1. Sample-ratio mismatch.
    total = control.users + treatment.users
    if total == 0:
        checks.append(Check("sample ratio", False, "no exposures inside the experiment window", Verdict.INVALID))
    else:
        srm = srm_chi_square((control.users, treatment.users), h.split)
        observed = f"{control.users / total * 100:.1f}/{treatment.users / total * 100:.1f}"
        detail = (
            f"observed {observed} vs planned {planned} "
            f"(chi2 = {srm.chi2:.2f}, p = {srm.p_value:.2g}, threshold {h.srm_alpha:g})"
        )
        if srm.p_value < h.srm_alpha:
            checks.append(Check(
                "sample ratio", False,
                f"sample-ratio mismatch: {detail}. Assignment or exposure logging is broken; "
                "lift numbers from this split cannot be trusted",
                Verdict.INVALID,
            ))
        else:
            checks.append(Check("sample ratio", True, detail))

    # 2. Peeking: a fixed-horizon test is read at its horizon, not before.
    earlier_early = sum(1 for d, _ in history if d < h.end_date)
    if as_of < h.start_date:
        checks.append(Check(
            "timing", False, f"read on {as_of}, before the experiment started ({h.start_date})", Verdict.INVALID
        ))
    elif as_of < h.end_date:
        days_left = (h.end_date - as_of).days
        looks = earlier_early + 1
        checks.append(Check(
            "timing", False,
            f"peeking: read on {as_of}, {days_left} day{'s' if days_left != 1 else ''} before the planned end "
            f"{h.end_date} (look #{looks} before the end date). A fixed-horizon p-value is only valid "
            "at the planned horizon; stopping on an early good number inflates false positives",
            Verdict.INVALID,
        ))
    else:
        detail = f"read on {as_of}, at or after the planned end {h.end_date}"
        if earlier_early:
            detail += (
                f"; {earlier_early} earlier look{'s were' if earlier_early != 1 else ' was'} logged "
                "before the end date, but the test ran to its horizon, so this read stands"
            )
        checks.append(Check("timing", True, detail))

    # 3. Minimum-sample gate.
    short = [
        f"{arm.arm} {arm.users:,}/{need:,}"
        for arm, need in ((control, h.min_sample[0]), (treatment, h.min_sample[1]))
        if arm.users < need
    ]
    floor = f"{h.min_sample[0]:,} control / {h.min_sample[1]:,} treatment"
    if short:
        checks.append(Check(
            "sample size", False,
            f"underpowered: {', '.join(short)} users against the preregistered minimum ({floor}). "
            "No verdict below the sample the test was powered for",
            Verdict.INVALID,
        ))
    else:
        checks.append(Check(
            "sample size", True, f"{control.users:,} control / {treatment.users:,} treatment, minimum {floor}"
        ))

    # 4. Guardrail metrics: must be measured, must not get significantly worse.
    guardrail_tests: dict[str, ZTest] = {}
    for g in h.guardrail_metrics:
        name = f"guardrail {g}"
        if g not in measured:
            checks.append(Check(
                name, False,
                f"guardrail metric {g!r} was preregistered but never measured (no {g} column in the events)",
                Verdict.INVALID,
            ))
            continue
        if not both_arms:
            checks.append(Check(name, False, f"cannot test {g}: one arm has no users", Verdict.INVALID))
            continue
        gt = two_proportion_z_test(
            metric_counts[g][ARMS[0]], control.users, metric_counts[g][ARMS[1]], treatment.users, h.alpha
        )
        guardrail_tests[g] = gt
        detail = f"control {pct(gt.p1)}, treatment {pct(gt.p2)} ({pp(gt.diff)}, p = {gt.p_value:.2g})"
        if gt.significant and gt.diff > 0:
            checks.append(Check(
                name, False, f"guardrail breached: {g} got significantly worse, {detail}", Verdict.KILL
            ))
        else:
            checks.append(Check(name, True, detail))

    naive = _naive(test)
    invalid = [c.detail for c in checks if not c.passed and c.forces is Verdict.INVALID]
    kills = [c.detail for c in checks if not c.passed and c.forces is Verdict.KILL]
    if invalid:
        verdict, reasons = Verdict.INVALID, invalid
    elif kills:
        verdict, reasons = Verdict.KILL, kills
    else:
        assert test is not None
        effect = f"{pp(test.diff)} on {h.primary_metric} (p = {test.p_value:.2g})"
        if naive is Verdict.KILL:
            verdict, reasons = Verdict.KILL, [f"treatment is significantly worse: {effect}"]
        elif naive is Verdict.SHIP:
            verdict, reasons = Verdict.SHIP, [
                f"treatment beat control by {effect} with every guardrail passing"
            ]
        else:
            ci = f"[{pp(test.ci_low)}, {pp(test.ci_high)}]"
            verdict, reasons = Verdict.INCONCLUSIVE, [
                f"no detectable difference: {effect}, {(1 - h.alpha) * 100:.0f}% CI {ci} includes zero. "
                f"The test was powered for {pp(h.expected_lift)}; a smaller effect may exist. Do not ship on this"
            ]

    return Readout(
        hypothesis=h,
        as_of=as_of,
        window_end=window_end,
        control=control,
        treatment=treatment,
        test=test,
        checks=checks,
        verdict=verdict,
        reasons=reasons,
        naive_verdict=naive,
        events_outside_window=outside,
        earlier_reads=len(history),
        earlier_early_reads=earlier_early,
        guardrail_tests=guardrail_tests,
    )


def render_readout(r: Readout) -> str:
    h = r.hypothesis
    lines = [
        f"Readout: {h.name}  (as of {r.as_of}, events {h.start_date} to {r.window_end})",
        f"Owner: {h.owner}",
        f"Change: {h.change}",
        f"Primary metric: {h.primary_metric}   alpha {h.alpha:g}, power {h.power:g}, "
        f"expected lift {pp(h.expected_lift)} on a {pct(h.baseline_rate)} baseline",
        "",
    ]
    decision_grade = r.verdict is not Verdict.INVALID
    if not decision_grade:
        lines.append("Numbers below are NOT decision-grade (see guardrails).")
    lines.append(f"  {'arm':<10} {'users':>8} {'conversions':>12} {'rate':>8}")
    for arm in (r.control, r.treatment):
        lines.append(f"  {arm.arm:<10} {arm.users:>8,} {arm.conversions:>12,} {pct(arm.rate):>8}")
    if r.test is not None:
        t = r.test
        rel = f"{t.relative * 100:+.1f}% relative" if t.relative is not None else "relative n/a"
        lines += [
            "",
            f"  lift: {pp(t.diff)} absolute, {rel}, "
            f"{(1 - t.alpha) * 100:.0f}% CI [{pp(t.ci_low)}, {pp(t.ci_high)}]",
            f"  z = {t.z:.3f}, p = {t.p_value:.4f} (two-proportion z-test, two-sided)",
        ]
    if r.events_outside_window:
        lines.append(f"  ({r.events_outside_window:,} events outside the window were ignored)")
    lines += ["", "Guardrails"]
    for c in r.checks:
        lines.append(f"  [{'PASS' if c.passed else 'FAIL'}] {c.name}: {c.detail}")
    lines += ["", f"Verdict: {r.verdict.value}"]
    lines += [f"  - {reason}" for reason in r.reasons]
    if r.verdict is Verdict.INVALID and r.naive_verdict is not None and r.test is not None:
        lines += [
            "",
            f"A naive dashboard would have said: {r.naive_verdict.value} "
            f"({pp(r.test.diff)}, p = {r.test.p_value:.4f}). The kit issues no verdict on this data.",
        ]
    return "\n".join(lines) + "\n"
