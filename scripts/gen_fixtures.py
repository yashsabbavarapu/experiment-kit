"""Generate the demo fixtures: hypotheses are hand-written, events are simulated here.

Arms come from the kit's own hash assignment, so a readout re-derives exactly
the arms the simulator used. Every scenario uses a fixed RNG seed and the
generator asserts the verdict each one is meant to demonstrate.

One honest note on the peeking scenario: it has no true effect, and the
generator searches seeds for a run where the day-5 look crosses p < 0.05 by
chance. That is not cherry-picking the result, it is constructing the exact
situation the guardrail exists for. With daily peeking, about one null
experiment in five crosses p < 0.05 at some look (see scripts/demo.py, part 5).

Run: PYTHONPATH=src python scripts/gen_fixtures.py
"""
from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from experiment_kit.assignment import assign
from experiment_kit.events import parse_events
from experiment_kit.models import Hypothesis, Verdict
from experiment_kit.readout import compute_readout
from experiment_kit.registry import load_file, parse_hypothesis

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "fixtures"
HEADER_BASE = ["user_id", "experiment", "timestamp", "converted"]


@dataclass(frozen=True)
class Scenario:
    hypothesis_file: str
    events_file: str
    users: int
    rates: tuple[float, float]  # true conversion rate (control, treatment)
    guardrail_rate: float
    guardrail_on_conversion: bool
    seed: int
    drop_treatment_nonconverters: float = 0.0  # simulated exposure-logging bug
    dirty_rows: bool = False


SCENARIOS = {
    "aa": Scenario("aa-homepage-hero.json", "aa_homepage_hero.csv", 6000, (0.10, 0.10), 0.30, False, 11),
    "ab": Scenario(
        "ab-checkout-copy.yaml", "ab_checkout_copy.csv", 8000, (0.10, 0.13), 0.03, False, 21, dirty_rows=True
    ),
    "peek": Scenario("peek-pricing-page.json", "peek_pricing_page.csv", 8400, (0.10, 0.10), 0.03, False, 0),
    "srm": Scenario(
        "srm-onboarding-tour.json", "srm_onboarding_tour.csv", 6000, (0.10, 0.10), 0.02, False, 41,
        drop_treatment_nonconverters=0.15,
    ),
}


def hypothesis(s: Scenario) -> Hypothesis:
    return parse_hypothesis(load_file(FIXTURES / "hypotheses" / s.hypothesis_file))


def simulate(h: Hypothesis, s: Scenario, seed: int) -> list[list[str]]:
    rng = random.Random(seed)
    days = (h.end_date - h.start_date).days + 1
    metric = h.guardrail_metrics[0]
    rows: list[list[str]] = []
    for i in range(1, s.users + 1):
        user_id = f"u{i:05d}"
        arm = assign(h, user_id)
        rate = s.rates[0] if arm == "control" else s.rates[1]
        day = h.start_date + timedelta(days=(i - 1) * days // s.users)
        exposed = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(
            seconds=rng.randrange(0, 20 * 3600)
        )
        converted = rng.random() < rate
        flagged = rng.random() < s.guardrail_rate
        if arm == "treatment" and not converted and rng.random() < s.drop_treatment_nonconverters:
            continue  # the treatment's exposure event never fired for this user
        exposure_flag = "1" if flagged and not s.guardrail_on_conversion else "0"
        rows.append([user_id, h.name, exposed.isoformat().replace("+00:00", "Z"), "0", exposure_flag])
        if converted:
            conv = exposed + timedelta(minutes=rng.randrange(1, 180))
            conv_flag = "1" if flagged and s.guardrail_on_conversion else "0"
            rows.append([user_id, h.name, conv.isoformat().replace("+00:00", "Z"), "1", conv_flag])
    rows.sort(key=lambda r: (r[2], r[0]))
    return rows


def write(h: Hypothesis, s: Scenario, rows: list[list[str]]) -> Path:
    path = FIXTURES / "events" / s.events_file
    path.parent.mkdir(parents=True, exist_ok=True)
    out = list(rows)
    if s.dirty_rows:
        # Real exports are messy. These must be skipped with warnings, not crash ingestion.
        out.insert(10, ["u09991", h.name, "not-a-timestamp", "0", "0"])
        out.insert(20, ["u09992", h.name, "2026-09-02T10:00:00Z", "yes", "0"])
        out.insert(30, ["", h.name, "2026-09-02T11:00:00Z", "0", "0"])
        out.insert(40, list(out[39]))  # an exact duplicate: deduplicated on ingest
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(HEADER_BASE + [h.guardrail_metrics[0]])
        writer.writerows(out)
    return path


def verdict_at(h: Hypothesis, path: Path, as_of: date) -> Verdict:
    return compute_readout(h, parse_events(path).events, as_of).verdict


def main() -> None:
    for key in ("aa", "ab", "srm"):
        s = SCENARIOS[key]
        h = hypothesis(s)
        path = write(h, s, simulate(h, s, s.seed))
        v = verdict_at(h, path, h.end_date)
        expected = {"aa": Verdict.INCONCLUSIVE, "ab": Verdict.SHIP, "srm": Verdict.INVALID}[key]
        assert v is expected, f"{key}: expected {expected}, got {v}"
        print(f"{path.relative_to(REPO)}: {v.value} at end date")

    s = SCENARIOS["peek"]
    h = hypothesis(s)
    peek_day = h.start_date + timedelta(days=4)
    for seed in range(1000):
        rows = simulate(h, s, seed)
        path = write(h, s, rows)
        early = compute_readout(h, parse_events(path).events, peek_day)
        final = compute_readout(h, parse_events(path).events, h.end_date)
        if (
            early.naive_verdict is Verdict.SHIP
            and early.verdict is Verdict.INVALID
            and final.verdict is Verdict.INCONCLUSIVE
            and final.test is not None
            and final.test.p_value > 0.3
        ):
            print(f"{path.relative_to(REPO)}: seed {seed}, naive SHIP on {peek_day}, INCONCLUSIVE at end")
            break
    else:
        raise SystemExit("no seed found for the peeking scenario")

    users = FIXTURES / "users.csv"
    with users.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["user_id"])
        writer.writerows([[f"u{i:05d}"] for i in range(1, 21)])
    print(f"{users.relative_to(REPO)}: 20 users")


if __name__ == "__main__":
    main()
