"""Hypothesis records: load from JSON or YAML, validate, freeze.

A hypothesis is a preregistration. Every field a readout is judged against
(dates, split, seed, alpha, minimum sample, guardrails) is fixed when the
record is added, so nobody can move the goalposts after seeing the data.
If ``min_sample_per_arm`` is omitted it is computed from baseline_rate,
expected_lift, alpha, and power with the fixed-horizon formula in stats.py.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from experiment_kit import yamlish
from experiment_kit.models import Hypothesis
from experiment_kit.stats import required_sample_size

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

REQUIRED = (
    "name",
    "owner",
    "change",
    "rationale",
    "primary_metric",
    "guardrail_metrics",
    "baseline_rate",
    "expected_lift",
    "start_date",
    "end_date",
)
OPTIONAL = ("split", "seed", "min_sample_per_arm", "alpha", "power", "srm_alpha")


class RegistryError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def load_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if not isinstance(data, dict):
            raise RegistryError([f"{path}: top level must be an object"])
        return data
    if path.suffix.lower() in (".yaml", ".yml"):
        return dict(yamlish.loads(text))
    raise RegistryError([f"{path}: hypothesis files must be .json, .yaml, or .yml"])


def _as_date(value: Any, key: str, problems: list[str]) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        problems.append(f"{key} must be an ISO date (YYYY-MM-DD), got {value!r}")
        return None


def _as_float(value: Any, key: str, problems: list[str]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        problems.append(f"{key} must be a number, got {value!r}")
        return None
    return float(value)


def _as_text(value: Any, key: str, problems: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{key} must be a non-empty string")
        return ""
    return value.strip()


def parse_hypothesis(data: dict[str, Any]) -> Hypothesis:
    """Validate a raw record into a frozen Hypothesis. Collects every problem."""
    problems: list[str] = []
    missing = [k for k in REQUIRED if k not in data]
    if missing:
        problems.append("missing required fields: " + ", ".join(missing))
    unknown = sorted(set(data) - set(REQUIRED) - set(OPTIONAL))
    if unknown:
        problems.append("unknown fields: " + ", ".join(unknown))
    if problems:
        raise RegistryError(problems)

    name = _as_text(data["name"], "name", problems)
    if name and not _NAME.match(name):
        problems.append("name must be lowercase letters, digits, '-' or '_' (max 64 chars)")
    owner = _as_text(data["owner"], "owner", problems)
    change = _as_text(data["change"], "change", problems)
    rationale = _as_text(data["rationale"], "rationale", problems)
    primary = _as_text(data["primary_metric"], "primary_metric", problems)

    guardrails_raw = data["guardrail_metrics"]
    guardrails: tuple[str, ...] = ()
    if not isinstance(guardrails_raw, list) or not all(
        isinstance(g, str) and g.strip() for g in guardrails_raw
    ):
        problems.append("guardrail_metrics must be a list of metric names")
    else:
        guardrails = tuple(g.strip() for g in guardrails_raw)
        if len(set(guardrails)) != len(guardrails):
            problems.append("guardrail_metrics has duplicates")
        if primary and primary in guardrails:
            problems.append("primary_metric cannot also be a guardrail metric")

    baseline = _as_float(data["baseline_rate"], "baseline_rate", problems)
    lift = _as_float(data["expected_lift"], "expected_lift", problems)
    if baseline is not None and not 0.0 < baseline < 1.0:
        problems.append("baseline_rate must be strictly between 0 and 1")
    if lift is not None and lift == 0.0:
        problems.append("expected_lift must be nonzero (an absolute rate change, e.g. 0.02)")
    if baseline is not None and lift is not None and not 0.0 < baseline + lift < 1.0:
        problems.append("baseline_rate + expected_lift must stay strictly between 0 and 1")

    start = _as_date(data["start_date"], "start_date", problems)
    end = _as_date(data["end_date"], "end_date", problems)
    if start and end and end <= start:
        problems.append("end_date must be after start_date")

    split_raw = data.get("split", [50, 50])
    split: tuple[float, float] = (0.5, 0.5)
    if (
        not isinstance(split_raw, list)
        or len(split_raw) != 2
        or not all(isinstance(w, (int, float)) and not isinstance(w, bool) and w > 0 for w in split_raw)
    ):
        problems.append("split must be two positive weights [control, treatment], e.g. [50, 50]")
    else:
        total = float(split_raw[0]) + float(split_raw[1])
        split = (split_raw[0] / total, split_raw[1] / total)

    seed = data.get("seed", name)
    if not isinstance(seed, (str, int)) or isinstance(seed, bool) or str(seed) == "":
        problems.append("seed must be a non-empty string or integer")
    seed = str(seed)

    alpha = _as_float(data.get("alpha", 0.05), "alpha", problems)
    power = _as_float(data.get("power", 0.8), "power", problems)
    srm_alpha = _as_float(data.get("srm_alpha", 0.001), "srm_alpha", problems)
    for key, val, hi in (("alpha", alpha, 0.5), ("power", power, 1.0), ("srm_alpha", srm_alpha, 0.5)):
        if val is not None and not 0.0 < val < hi:
            problems.append(f"{key} must be between 0 and {hi}")

    if problems:
        raise RegistryError(problems)
    assert baseline is not None and lift is not None and start and end
    assert alpha is not None and power is not None and srm_alpha is not None

    min_raw = data.get("min_sample_per_arm")
    if min_raw is None:
        min_sample = required_sample_size(baseline, lift, alpha, power, split)
    elif isinstance(min_raw, int) and not isinstance(min_raw, bool) and min_raw > 0:
        min_sample = (min_raw, min_raw)
    elif (
        isinstance(min_raw, list)
        and len(min_raw) == 2
        and all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in min_raw)
    ):
        min_sample = (min_raw[0], min_raw[1])
    else:
        raise RegistryError(["min_sample_per_arm must be a positive integer or [control, treatment]"])

    return Hypothesis(
        name=name,
        owner=owner,
        change=change,
        rationale=rationale,
        primary_metric=primary,
        guardrail_metrics=guardrails,
        baseline_rate=baseline,
        expected_lift=lift,
        start_date=start,
        end_date=end,
        split=split,
        seed=seed,
        min_sample=min_sample,
        alpha=alpha,
        power=power,
        srm_alpha=srm_alpha,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def to_record(h: Hypothesis) -> dict[str, Any]:
    record = asdict(h)
    record["start_date"] = h.start_date.isoformat()
    record["end_date"] = h.end_date.isoformat()
    record["guardrail_metrics"] = list(h.guardrail_metrics)
    record["split"] = list(h.split)
    record["min_sample"] = list(h.min_sample)
    return record


def from_record(record: dict[str, Any]) -> Hypothesis:
    fields = dict(record)
    fields["start_date"] = date.fromisoformat(record["start_date"])
    fields["end_date"] = date.fromisoformat(record["end_date"])
    fields["guardrail_metrics"] = tuple(record["guardrail_metrics"])
    fields["split"] = (float(record["split"][0]), float(record["split"][1]))
    fields["min_sample"] = (int(record["min_sample"][0]), int(record["min_sample"][1]))
    return Hypothesis(**fields)


DECISIONS = ("ship", "kill", "abandon")


def close(
    h: Hypothesis, decision: str, note: str | None, kit_verdict: str | None, override: bool
) -> Hypothesis:
    """Close a hypothesis. Shipping against the kit's verdict needs an explicit override."""
    if h.status == "closed":
        raise RegistryError([f"{h.name} is already closed ({h.decision})"])
    if decision not in DECISIONS:
        raise RegistryError([f"decision must be one of {', '.join(DECISIONS)}"])
    if decision == "ship" and kit_verdict != "SHIP":
        verdict_text = kit_verdict or "no readout"
        if not override or not note:
            raise RegistryError([
                f"the kit's latest verdict for {h.name} is {verdict_text}, not SHIP; "
                "shipping anyway needs --override and a --note explaining why"
            ])
    else:
        override = False
    return replace(
        h,
        status="closed",
        closed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        decision=decision,
        close_note=note,
        kit_verdict_at_close=kit_verdict,
        override=override,
    )
