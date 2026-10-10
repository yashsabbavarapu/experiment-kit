"""Shared builders: a valid hypothesis with overrides, and events with exact arm counts."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from experiment_kit.assignment import assign
from experiment_kit.models import Event, Hypothesis
from experiment_kit.registry import parse_hypothesis

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "fixtures"

BASE: dict[str, Any] = {
    "name": "test-exp",
    "owner": "tester",
    "change": "a change",
    "rationale": "a reason",
    "primary_metric": "converted",
    "guardrail_metrics": ["refunded"],
    "baseline_rate": 0.10,
    "expected_lift": 0.05,
    "start_date": "2026-09-01",
    "end_date": "2026-09-14",
    "min_sample_per_arm": 1000,
}

MakeHypothesis = Callable[..., Hypothesis]
MakeEvents = Callable[..., list[Event]]


@pytest.fixture
def make_hypothesis() -> MakeHypothesis:
    def make(**overrides: Any) -> Hypothesis:
        data = dict(BASE)
        data.update(overrides)
        return parse_hypothesis({k: v for k, v in data.items() if v is not None})

    return make


@pytest.fixture
def make_events() -> MakeEvents:
    def make(
        h: Hypothesis,
        n: tuple[int, int],
        x: tuple[int, int],
        guardrail: tuple[int, int] = (0, 0),
        day: str = "2026-09-05",
        with_metric: bool = True,
    ) -> list[Event]:
        """Exactly n users per arm, x of them converting, guardrail of them flagged."""
        ts = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
        seen = {"control": 0, "treatment": 0}
        index = {"control": 0, "treatment": 1}
        events: list[Event] = []
        i = 0
        while seen["control"] < n[0] or seen["treatment"] < n[1]:
            uid = f"user-{i}"
            i += 1
            arm = assign(h, uid)
            k = seen[arm]
            if k >= n[index[arm]]:
                continue
            seen[arm] += 1
            metrics = {g: int(k < guardrail[index[arm]]) for g in h.guardrail_metrics} if with_metric else {}
            events.append(Event(h.name, uid, ts, False, metrics))
            if k < x[index[arm]]:
                events.append(Event(h.name, uid, ts + timedelta(minutes=5), True, metrics))
        return events

    return make
