"""Each guardrail on its own: it must be able to fail the readout by itself."""
from __future__ import annotations

from datetime import date, datetime, timezone

from conftest import FIXTURES, MakeEvents, MakeHypothesis
from experiment_kit.events import parse_events
from experiment_kit.models import Check, Event, Verdict
from experiment_kit.readout import Readout, compute_readout
from experiment_kit.registry import load_file, parse_hypothesis

END = date(2026, 9, 14)


def failed(r: Readout) -> dict[str, Check]:
    return {c.name: c for c in r.checks if not c.passed}


def test_clean_win_ships(make_hypothesis: MakeHypothesis, make_events: MakeEvents) -> None:
    h = make_hypothesis()
    r = compute_readout(h, make_events(h, (1500, 1500), (150, 225)), END)
    assert failed(r) == {}
    assert r.verdict is Verdict.SHIP


def test_srm_detected_on_skewed_fixture() -> None:
    h = parse_hypothesis(load_file(FIXTURES / "hypotheses" / "srm-onboarding-tour.json"))
    r = compute_readout(h, parse_events(FIXTURES / "events" / "srm_onboarding_tour.csv").events, h.end_date)
    assert r.verdict is Verdict.INVALID
    assert set(failed(r)) == {"sample ratio"}  # SRM alone fails this readout
    assert "sample-ratio mismatch" in r.reasons[0]


def test_srm_alone_overrides_a_real_win(make_hypothesis: MakeHypothesis, make_events: MakeEvents) -> None:
    h = make_hypothesis()
    r = compute_readout(h, make_events(h, (1500, 1300), (150, 220)), END)
    assert r.naive_verdict is Verdict.SHIP
    assert r.verdict is Verdict.INVALID and set(failed(r)) == {"sample ratio"}


def test_srm_uses_the_planned_split(make_hypothesis: MakeHypothesis, make_events: MakeEvents) -> None:
    h = make_hypothesis(split=[80, 20], min_sample_per_arm=[1000, 250])
    ok = compute_readout(h, make_events(h, (2000, 500), (200, 50)), END)
    assert "sample ratio" not in failed(ok)
    skewed = compute_readout(h, make_events(h, (1250, 1250), (125, 125)), END)
    assert "sample ratio" in failed(skewed)


def test_peeking_is_flagged_even_on_a_real_win(make_hypothesis: MakeHypothesis, make_events: MakeEvents) -> None:
    h = make_hypothesis()
    events = make_events(h, (1500, 1500), (150, 225), day="2026-09-03")
    r = compute_readout(h, events, date(2026, 9, 7))
    assert r.naive_verdict is Verdict.SHIP
    assert r.verdict is Verdict.INVALID
    assert set(failed(r)) == {"timing"}  # peeking alone fails it
    assert "7 days before the planned end" in r.reasons[0]


def test_reading_before_start_is_invalid(make_hypothesis: MakeHypothesis) -> None:
    r = compute_readout(make_hypothesis(), [], date(2026, 8, 1))
    assert r.verdict is Verdict.INVALID and "timing" in failed(r) and "sample ratio" in failed(r)


def test_earlier_peeks_are_counted_but_do_not_void_the_final_read(
    make_hypothesis: MakeHypothesis, make_events: MakeEvents
) -> None:
    h = make_hypothesis()
    events = make_events(h, (1500, 1500), (150, 225))
    history = [(date(2026, 9, 5), "INVALID"), (date(2026, 9, 9), "INVALID")]
    peek = compute_readout(h, events, date(2026, 9, 10), history)
    assert "look #3" in failed(peek)["timing"].detail
    final = compute_readout(h, events, END, history)
    assert final.verdict is Verdict.SHIP
    timing = next(c for c in final.checks if c.name == "timing")
    assert timing.passed and "2 earlier looks were logged" in timing.detail


def test_min_sample_gate_refuses_a_verdict(make_hypothesis: MakeHypothesis, make_events: MakeEvents) -> None:
    h = make_hypothesis(min_sample_per_arm=2000)
    r = compute_readout(h, make_events(h, (1500, 1500), (150, 225)), END)
    assert r.naive_verdict is Verdict.SHIP
    assert r.verdict is Verdict.INVALID
    assert set(failed(r)) == {"sample size"}  # the gate alone fails it
    assert "control 1,500/2,000" in r.reasons[0]


def test_min_sample_gate_is_per_arm(make_hypothesis: MakeHypothesis, make_events: MakeEvents) -> None:
    h = make_hypothesis(split=[90, 10], min_sample_per_arm=[1800, 200])
    r = compute_readout(h, make_events(h, (1800, 190), (180, 19)), END)
    assert set(failed(r)) == {"sample size"}
    assert "treatment 190/200" in r.reasons[0] and "control" not in r.reasons[0].split(" users")[0]


def test_breached_guardrail_metric_kills(make_hypothesis: MakeHypothesis, make_events: MakeEvents) -> None:
    h = make_hypothesis()
    r = compute_readout(h, make_events(h, (1500, 1500), (150, 225), guardrail=(30, 75)), END)
    assert r.naive_verdict is Verdict.SHIP  # the primary metric won
    assert r.verdict is Verdict.KILL  # but refunds went up significantly
    assert failed(r)["guardrail refunded"].forces is Verdict.KILL


def test_improved_guardrail_does_not_kill(make_hypothesis: MakeHypothesis, make_events: MakeEvents) -> None:
    h = make_hypothesis()
    r = compute_readout(h, make_events(h, (1500, 1500), (150, 225), guardrail=(75, 30)), END)
    assert r.verdict is Verdict.SHIP


def test_unmeasured_guardrail_is_invalid(make_hypothesis: MakeHypothesis, make_events: MakeEvents) -> None:
    h = make_hypothesis()
    r = compute_readout(h, make_events(h, (1500, 1500), (150, 225), with_metric=False), END)
    assert r.verdict is Verdict.INVALID
    assert "never measured" in failed(r)["guardrail refunded"].detail


def test_significant_loss_kills(make_hypothesis: MakeHypothesis, make_events: MakeEvents) -> None:
    h = make_hypothesis()
    r = compute_readout(h, make_events(h, (1500, 1500), (225, 150)), END)
    assert r.verdict is Verdict.KILL and "significantly worse" in r.reasons[0]


def test_flat_result_is_inconclusive(make_hypothesis: MakeHypothesis, make_events: MakeEvents) -> None:
    h = make_hypothesis()
    r = compute_readout(h, make_events(h, (1500, 1500), (150, 155)), END)
    assert r.verdict is Verdict.INCONCLUSIVE and "includes zero" in r.reasons[0]


def test_events_outside_the_window_are_ignored(make_hypothesis: MakeHypothesis, make_events: MakeEvents) -> None:
    h = make_hypothesis()
    events = make_events(h, (1500, 1500), (150, 155))
    late = datetime(2026, 9, 20, tzinfo=timezone.utc)
    events += [Event(h.name, f"late-{i}", late, True, {"refunded": 0}) for i in range(500)]
    r = compute_readout(h, events, date(2026, 9, 30))
    assert r.events_outside_window == 500
    assert r.control.users + r.treatment.users == 3000
