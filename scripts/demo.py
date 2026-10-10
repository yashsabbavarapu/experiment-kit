"""Run every demo scenario through the real CLI, against a scratch database.

Run: PYTHONPATH=src python scripts/demo.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from experiment_kit.__main__ import main
from experiment_kit.simulate import simulate_peeking

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "fixtures"
DB = REPO / "demo.db"


def banner(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def run(*args: str, expect: int = 0) -> None:
    print(f"\n$ experiment-kit {' '.join(args)}")
    sys.stdout.flush()
    code = main([*args, "--db", str(DB)])
    sys.stdout.flush()
    sys.stderr.flush()
    if code != expect:
        raise SystemExit(f"demo step failed: exit {code}, expected {expect}")


def rel(path: Path) -> str:
    return str(path.relative_to(REPO))


def demo() -> None:
    os.chdir(REPO)  # the demo prints repo-relative paths
    DB.unlink(missing_ok=True)
    hypotheses = sorted(rel(p) for p in (FIXTURES / "hypotheses").iterdir())
    events = sorted(rel(p) for p in (FIXTURES / "events").iterdir())

    banner("Setup: preregister four hypotheses, ingest events (twice, to show idempotency)")
    run("hypothesis", "add", *hypotheses)
    run("ingest", "--events", *events)
    run("ingest", "--events", rel(FIXTURES / "events" / "ab_checkout_copy.csv"))

    banner("Assignment: same users, same seed, same arms (run twice)")
    out = REPO / "assignments.csv"
    run("assign", "--experiment", "ab-checkout-copy", "--users", rel(FIXTURES / "users.csv"), "--output", rel(out))
    run("assign", "--experiment", "ab-checkout-copy", "--users", rel(FIXTURES / "users.csv"), "--output", rel(out))
    out.unlink()

    banner("Demo 1: A/A test. Both arms identical. Must read null")
    run("readout", "--experiment", "aa-homepage-hero", "--as-of", "2026-09-14")

    banner("Demo 2: A/B with a real lift. Must read SHIP")
    run("readout", "--experiment", "ab-checkout-copy", "--as-of", "2026-09-14")

    banner("Demo 3: peeked early. No true effect, but day 5 looks like a win")
    run("readout", "--experiment", "peek-pricing-page", "--as-of", "2026-09-05")
    print("\nTry to ship on the peek:")
    run("hypothesis", "close", "peek-pricing-page", "--decision", "ship", expect=2)
    print("\nWait for the planned end date instead:")
    run("readout", "--experiment", "peek-pricing-page", "--as-of", "2026-09-21")

    banner("Bonus: sample-ratio mismatch. Treatment exposure logging drops users")
    run("readout", "--experiment", "srm-onboarding-tour", "--as-of", "2026-09-14")

    banner("Why peeking is INVALID: 2,000 simulated experiments with no true effect")
    result = simulate_peeking(experiments=2000)
    once = f"read once, at day {result.days}:"
    print(f"\n{once:<32}{result.fixed_horizon_rate:6.1%} false winners (alpha is 5%)")
    print(f"{'peek daily, stop at p < 0.05:':<32}{result.daily_peeking_rate:6.1%} false winners")

    banner("Registry")
    run("hypothesis", "list")
    DB.unlink()


if __name__ == "__main__":
    demo()
