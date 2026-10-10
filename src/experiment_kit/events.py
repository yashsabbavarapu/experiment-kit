"""Parse exposure and conversion events from CSV.

Strict on columns: user_id, experiment, timestamp, converted must all be in
the header or the whole file is rejected. Any extra column is a guardrail
metric (0/1 per row, blank means 0).

Lenient on rows: a malformed row is skipped with a line-numbered warning and
the rest of the file still loads.
"""
from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from experiment_kit.models import Event

REQUIRED_COLUMNS = ("user_id", "experiment", "timestamp", "converted")


class EventsError(ValueError):
    pass


@dataclass
class ParseResult:
    events: list[Event] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_timestamp(text: str) -> datetime:
    """ISO 8601 date or datetime. Naive values are taken as UTC."""
    value = datetime.fromisoformat(text.strip())
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _flag(text: str | None) -> int | None:
    value = (text or "").strip()
    if value in ("", "0"):
        return 0
    if value == "1":
        return 1
    return None


def parse_events(path: Path) -> ParseResult:
    result = ParseResult()
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        header = [h.strip() for h in (reader.fieldnames or [])]
        missing = [c for c in REQUIRED_COLUMNS if c not in header]
        if missing:
            raise EventsError(f"{path}: missing required columns: {', '.join(missing)}")
        if len(set(header)) != len(header):
            raise EventsError(f"{path}: duplicate column names in header")
        reader.fieldnames = header
        metric_columns = [c for c in header if c not in REQUIRED_COLUMNS]
        for row in reader:
            line = reader.line_num
            if None in row:
                result.warnings.append(f"{path.name}:{line}: too many fields, row skipped")
                continue
            user_id = (row["user_id"] or "").strip()
            experiment = (row["experiment"] or "").strip()
            if not user_id or not experiment:
                result.warnings.append(f"{path.name}:{line}: empty user_id or experiment, row skipped")
                continue
            try:
                ts = parse_timestamp(row["timestamp"] or "")
            except ValueError:
                result.warnings.append(f"{path.name}:{line}: bad timestamp {row['timestamp']!r}, row skipped")
                continue
            converted = _flag(row["converted"])
            if converted is None or (row["converted"] or "").strip() == "":
                result.warnings.append(f"{path.name}:{line}: converted must be 0 or 1, row skipped")
                continue
            metrics: dict[str, int] = {}
            bad_metric = None
            for col in metric_columns:
                value = _flag(row[col])
                if value is None:
                    bad_metric = col
                    break
                metrics[col] = value
            if bad_metric is not None:
                result.warnings.append(
                    f"{path.name}:{line}: metric {bad_metric} must be 0, 1, or blank, row skipped"
                )
                continue
            result.events.append(Event(experiment, user_id, ts, bool(converted), metrics))
    return result


def event_id(event: Event) -> str:
    """Content hash of an event, so re-ingesting the same row is a no-op."""
    metrics = ",".join(f"{k}={v}" for k, v in sorted(event.metrics.items()))
    key = f"{event.experiment}|{event.user_id}|{event.timestamp.isoformat()}|{int(event.converted)}|{metrics}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
