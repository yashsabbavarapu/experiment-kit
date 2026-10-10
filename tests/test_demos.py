"""The three demo scenarios, end to end through the CLI on the shipped fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import FIXTURES
from experiment_kit.__main__ import main


@pytest.fixture
def db(tmp_path: Path) -> str:
    path = str(tmp_path / "demo.db")
    files = sorted(str(p) for p in (FIXTURES / "hypotheses").iterdir())
    events = sorted(str(p) for p in (FIXTURES / "events").iterdir())
    assert main(["hypothesis", "add", *files, "--db", path]) == 0
    assert main(["ingest", "--events", *events, "--db", path]) == 0
    return path


def readout(db: str, capsys: pytest.CaptureFixture[str], experiment: str, as_of: str) -> str:
    capsys.readouterr()
    assert main(["readout", "--experiment", experiment, "--as-of", as_of, "--db", db]) == 0
    return capsys.readouterr().out


def test_demo_1_aa_reads_null(db: str, capsys: pytest.CaptureFixture[str]) -> None:
    out = readout(db, capsys, "aa-homepage-hero", "2026-09-14")
    assert "Verdict: INCONCLUSIVE" in out
    assert "[FAIL]" not in out  # a valid test, and it found nothing, which is correct
    assert "naive dashboard" not in out


def test_demo_2_ab_with_real_lift_ships(db: str, capsys: pytest.CaptureFixture[str]) -> None:
    out = readout(db, capsys, "ab-checkout-copy", "2026-09-14")
    assert "Verdict: SHIP" in out
    assert "[FAIL]" not in out
    assert "[PASS] guardrail checkout_error" in out


def test_demo_3_peeked_early_is_invalid(db: str, capsys: pytest.CaptureFixture[str]) -> None:
    early = readout(db, capsys, "peek-pricing-page", "2026-09-05")
    assert "Verdict: INVALID" in early
    assert "peeking: read on 2026-09-05, 16 days before the planned end 2026-09-21" in early
    assert "underpowered" in early
    assert "A naive dashboard would have said: SHIP" in early
    assert "NOT decision-grade" in early

    final = readout(db, capsys, "peek-pricing-page", "2026-09-21")
    assert "Verdict: INCONCLUSIVE" in final  # the early "win" was noise
    assert "1 earlier look was logged before the end date" in final


def test_demo_3_shipping_the_peek_needs_an_override(db: str, capsys: pytest.CaptureFixture[str]) -> None:
    readout(db, capsys, "peek-pricing-page", "2026-09-05")
    assert main(["hypothesis", "close", "peek-pricing-page", "--decision", "ship", "--db", db]) == 2
    assert "not SHIP" in capsys.readouterr().err
    assert main(["hypothesis", "close", "peek-pricing-page", "--decision", "ship", "--override",
                 "--note", "VP call", "--db", db]) == 0
    capsys.readouterr()
    assert main(["hypothesis", "list", "--db", db]) == 0
    assert "ship (override)" in capsys.readouterr().out
