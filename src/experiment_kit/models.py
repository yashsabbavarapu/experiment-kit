"""Plain data types shared across the kit."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

# v1 is a two-arm framework: one control, one treatment. The split between
# them is configurable (50/50, 90/10, ...).
ARMS: tuple[str, str] = ("control", "treatment")


class Verdict(StrEnum):
    SHIP = "SHIP"
    KILL = "KILL"
    INCONCLUSIVE = "INCONCLUSIVE"
    INVALID = "INVALID"


@dataclass(frozen=True)
class Hypothesis:
    """A preregistered experiment. Everything a readout is judged against lives here."""

    name: str
    owner: str
    change: str
    rationale: str
    primary_metric: str
    guardrail_metrics: tuple[str, ...]
    baseline_rate: float
    expected_lift: float  # absolute, as a rate: 0.02 means +2 percentage points
    start_date: date
    end_date: date
    split: tuple[float, float]  # normalized weights for (control, treatment)
    seed: str
    min_sample: tuple[int, int]  # preregistered minimum users for (control, treatment)
    alpha: float = 0.05
    power: float = 0.8
    srm_alpha: float = 0.001
    status: str = "running"
    created_at: str = ""
    closed_at: str | None = None
    decision: str | None = None
    close_note: str | None = None
    kit_verdict_at_close: str | None = None
    override: bool = False


@dataclass(frozen=True)
class Event:
    """One exposure or conversion row. Any row implies the user was exposed."""

    experiment: str
    user_id: str
    timestamp: datetime
    converted: bool
    metrics: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ArmStats:
    arm: str
    users: int
    conversions: int

    @property
    def rate(self) -> float:
        return self.conversions / self.users if self.users else 0.0


@dataclass(frozen=True)
class Check:
    """One guardrail. A failed check carries the verdict it forces."""

    name: str
    passed: bool
    detail: str
    forces: Verdict | None = None  # INVALID or KILL when the check fails
