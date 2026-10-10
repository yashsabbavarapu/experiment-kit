"""Re-run the A/A and A/B fixture scenarios across many seeds to measure the kit itself.

Reports the verdict distribution per scenario, plus the mean estimated lift
and CI coverage of the true lift. These are the numbers quoted in the README.

Run: PYTHONPATH=src python scripts/calibrate.py [SEEDS]
"""
from __future__ import annotations

import statistics
import sys
from collections import Counter

from experiment_kit.events import parse_timestamp
from experiment_kit.models import Event
from experiment_kit.readout import compute_readout
from gen_fixtures import SCENARIOS, hypothesis, simulate


def main(seeds: int) -> None:
    for key in ("aa", "ab"):
        s = SCENARIOS[key]
        h = hypothesis(s)
        metric = h.guardrail_metrics[0]
        true_lift = s.rates[1] - s.rates[0]
        verdicts: Counter[str] = Counter()
        lifts: list[float] = []
        covered = 0
        for seed in range(seeds):
            events = [
                Event(r[1], r[0], parse_timestamp(r[2]), r[3] == "1", {metric: int(r[4])})
                for r in simulate(h, s, seed)
            ]
            r = compute_readout(h, events, h.end_date)
            verdicts[r.verdict.value] += 1
            assert r.test is not None
            lifts.append(r.test.diff)
            covered += int(r.test.ci_low <= true_lift <= r.test.ci_high)
        dist = ", ".join(f"{v} {n}" for v, n in sorted(verdicts.items()))
        print(f"{h.name} (true lift {true_lift * 100:+.1f} pp), {seeds} seeds: {dist}")
        print(f"  mean estimated lift {statistics.mean(lifts) * 100:+.2f} pp, CI covers truth {covered / seeds:.1%}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 200)
