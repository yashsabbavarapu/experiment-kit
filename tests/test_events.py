"""Event ingestion: strict on columns, lenient on rows, idempotent in storage."""
from __future__ import annotations

from datetime import timezone
from pathlib import Path

import pytest

from conftest import FIXTURES
from experiment_kit.__main__ import main
from experiment_kit.events import EventsError, event_id, parse_events, parse_timestamp
from experiment_kit.storage import Store


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "events.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_required_column_rejects_the_file(tmp_path: Path) -> None:
    path = write(tmp_path, "user_id,experiment,timestamp\nu1,e,2026-09-01T00:00:00Z\n")
    with pytest.raises(EventsError, match="converted"):
        parse_events(path)


def test_bad_rows_are_skipped_with_line_numbers(tmp_path: Path) -> None:
    path = write(tmp_path, (
        "user_id,experiment,timestamp,converted,refunded\n"
        "u1,e,2026-09-01T10:00:00Z,0,0\n"
        "u2,e,yesterday,0,0\n"
        "u3,e,2026-09-01T10:00:00Z,2,0\n"
        ",e,2026-09-01T10:00:00Z,0,0\n"
        "u4,e,2026-09-01T10:00:00Z,1,maybe\n"
        "u5,e,2026-09-01T10:00:00Z,1,\n"
        "u6,e,2026-09-01T10:00:00Z,1,0,extra\n"
    ))
    result = parse_events(path)
    assert [e.user_id for e in result.events] == ["u1", "u5"]
    assert result.events[1].metrics == {"refunded": 0}  # blank metric means 0
    assert len(result.warnings) == 5
    assert result.warnings[0].startswith("events.csv:3: bad timestamp")
    assert any(":6: metric refunded" in w for w in result.warnings)


def test_timestamps_normalize_to_utc() -> None:
    assert parse_timestamp("2026-09-01").tzinfo == timezone.utc
    assert parse_timestamp("2026-09-01T02:00:00+02:00") == parse_timestamp("2026-09-01T00:00:00Z")


def test_ingestion_is_idempotent(tmp_path: Path) -> None:
    store = Store(tmp_path / "k.db")
    events = parse_events(FIXTURES / "events" / "ab_checkout_copy.csv").events
    inserted, skipped = store.save_events(events)
    assert skipped == 1  # the fixture carries one exact duplicate row
    assert inserted == len(events) - 1
    assert store.save_events(events) == (0, len(events))
    assert len(store.events_for("ab-checkout-copy")) == inserted
    store.close()


def test_event_id_is_content_based(tmp_path: Path) -> None:
    path = write(tmp_path, "user_id,experiment,timestamp,converted\nu1,e,2026-09-01,0\nu1,e,2026-09-01,1\n")
    a, b = parse_events(path).events
    assert event_id(a) != event_id(b)
    assert event_id(a) == event_id(parse_events(path).events[0])


def test_cli_ingest_reports_and_dedupes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = str(tmp_path / "k.db")
    csv_path = str(FIXTURES / "events" / "ab_checkout_copy.csv")
    assert main(["ingest", "--events", csv_path, "--db", db]) == 0
    first = capsys.readouterr().out
    assert "3 bad rows" in first and "bad timestamp" in first
    assert "unregistered experiments: ab-checkout-copy" in first
    assert main(["ingest", "--events", csv_path, "--db", db]) == 0
    assert " 0 new," in capsys.readouterr().out
