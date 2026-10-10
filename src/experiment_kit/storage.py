"""SQLite storage (stdlib only). Tables: hypotheses, assignments, events, readouts.

- hypotheses: one JSON record per name. Adding a name twice is refused, so a
  preregistration cannot be quietly rewritten.
- assignments: (experiment, user_id) primary key. Re-assigning is a no-op,
  and a stored arm that disagrees with a fresh hash is reported as a conflict.
- events: keyed by a content hash of the row, so ingestion is idempotent.
- readouts: every readout request is logged. That log is what lets the kit
  count how many times an experiment was peeked at before its end date.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import date, datetime, timezone
from pathlib import Path

from experiment_kit.events import event_id
from experiment_kit.models import Event, Hypothesis
from experiment_kit.registry import from_record, to_record

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hypotheses(
    name TEXT PRIMARY KEY,
    record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assignments(
    experiment TEXT NOT NULL,
    user_id TEXT NOT NULL,
    arm TEXT NOT NULL,
    assigned_at TEXT NOT NULL,
    PRIMARY KEY (experiment, user_id)
);
CREATE TABLE IF NOT EXISTS events(
    event_id TEXT PRIMARY KEY,
    experiment TEXT NOT NULL,
    user_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    converted INTEGER NOT NULL,
    metrics_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_experiment ON events(experiment);
CREATE TABLE IF NOT EXISTS readouts(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    as_of TEXT NOT NULL,
    verdict TEXT NOT NULL,
    reasons_json TEXT NOT NULL
);
"""


class StoreError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn = sqlite3.connect(str(path))
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # hypotheses

    def add_hypothesis(self, h: Hypothesis) -> None:
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO hypotheses(name, record_json) VALUES (?, ?)",
                    (h.name, json.dumps(to_record(h), sort_keys=True)),
                )
        except sqlite3.IntegrityError:
            raise StoreError(
                f"hypothesis {h.name!r} already exists; preregistrations are immutable, "
                "register a new name instead"
            ) from None

    def update_hypothesis(self, h: Hypothesis) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE hypotheses SET record_json = ? WHERE name = ?",
                (json.dumps(to_record(h), sort_keys=True), h.name),
            )

    def get_hypothesis(self, name: str) -> Hypothesis | None:
        row = self._conn.execute(
            "SELECT record_json FROM hypotheses WHERE name = ?", (name,)
        ).fetchone()
        return from_record(json.loads(row[0])) if row else None

    def list_hypotheses(self) -> list[Hypothesis]:
        rows = self._conn.execute("SELECT record_json FROM hypotheses ORDER BY name").fetchall()
        return [from_record(json.loads(r[0])) for r in rows]

    # assignments

    def save_assignments(self, experiment: str, pairs: Iterable[tuple[str, str]]) -> tuple[int, int, list[str]]:
        """Store arms. Returns (new, already stored and matching, conflicting user_ids)."""
        new = same = 0
        conflicts: list[str] = []
        with self._conn:
            for user_id, arm in pairs:
                row = self._conn.execute(
                    "SELECT arm FROM assignments WHERE experiment = ? AND user_id = ?",
                    (experiment, user_id),
                ).fetchone()
                if row is None:
                    self._conn.execute(
                        "INSERT INTO assignments(experiment, user_id, arm, assigned_at) VALUES (?, ?, ?, ?)",
                        (experiment, user_id, arm, _now()),
                    )
                    new += 1
                elif row[0] == arm:
                    same += 1
                else:
                    conflicts.append(user_id)
        return new, same, conflicts

    def assignments_for(self, experiment: str) -> dict[str, str]:
        rows = self._conn.execute(
            "SELECT user_id, arm FROM assignments WHERE experiment = ?", (experiment,)
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    # events

    def save_events(self, events: Iterable[Event]) -> tuple[int, int]:
        """Insert events. Returns (inserted, duplicates skipped)."""
        inserted = skipped = 0
        with self._conn:
            for e in events:
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO events(event_id, experiment, user_id, ts, converted, metrics_json) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        event_id(e),
                        e.experiment,
                        e.user_id,
                        e.timestamp.isoformat(),
                        int(e.converted),
                        json.dumps(e.metrics, sort_keys=True),
                    ),
                )
                if cur.rowcount == 1:
                    inserted += 1
                else:
                    skipped += 1
        return inserted, skipped

    def events_for(self, experiment: str) -> list[Event]:
        rows = self._conn.execute(
            "SELECT user_id, ts, converted, metrics_json FROM events WHERE experiment = ? ORDER BY ts, user_id",
            (experiment,),
        ).fetchall()
        return [
            Event(experiment, r[0], datetime.fromisoformat(r[1]), bool(r[2]), json.loads(r[3]))
            for r in rows
        ]

    def experiments_with_events(self) -> set[str]:
        return {r[0] for r in self._conn.execute("SELECT DISTINCT experiment FROM events")}

    # readouts

    def log_readout(self, experiment: str, as_of: date, verdict: str, reasons: list[str]) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO readouts(experiment, requested_at, as_of, verdict, reasons_json) VALUES (?, ?, ?, ?, ?)",
                (experiment, _now(), as_of.isoformat(), verdict, json.dumps(reasons)),
            )

    def readout_history(self, experiment: str) -> list[tuple[date, str]]:
        rows = self._conn.execute(
            "SELECT as_of, verdict FROM readouts WHERE experiment = ? ORDER BY id", (experiment,)
        ).fetchall()
        return [(date.fromisoformat(r[0]), r[1]) for r in rows]
