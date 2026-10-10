"""Deterministic, seed-controlled assignment."""
from __future__ import annotations

from pathlib import Path

import pytest

from conftest import FIXTURES, MakeHypothesis
from experiment_kit.__main__ import main
from experiment_kit.assignment import assign_arm, assign_many, assignment_digest, bucket

USERS = [f"u{i:05d}" for i in range(20_000)]


def test_bucket_is_pinned() -> None:
    # Golden value: if this changes, every running experiment would silently reshuffle.
    # sha256("seed-a:u00001"), first 8 bytes as an integer, divided by 2^64
    assert bucket("seed-a", "u00001") == 0.5125514883724613
    assert 0.0 <= bucket("x", "y") < 1.0


def test_same_input_same_output_twice(make_hypothesis: MakeHypothesis) -> None:
    h = make_hypothesis(seed="checkout-v1")
    first = assign_many(h, USERS)
    second = assign_many(h, USERS)
    assert first == second
    assert assignment_digest(first) == assignment_digest(second)


def test_seed_controls_assignment(make_hypothesis: MakeHypothesis) -> None:
    a = assign_many(make_hypothesis(seed="one"), USERS[:2000])
    b = assign_many(make_hypothesis(seed="two"), USERS[:2000])
    differ = sum(1 for (_, x), (_, y) in zip(a, b) if x != y)
    # independent seeds disagree on about half the users
    assert 800 < differ < 1200


def test_seed_defaults_to_name(make_hypothesis: MakeHypothesis) -> None:
    assert make_hypothesis(name="my-exp").seed == "my-exp"


@pytest.mark.parametrize("split,expected_treatment", [([50, 50], 0.5), ([90, 10], 0.1), ([1, 3], 0.75)])
def test_splits_hit_their_targets(
    make_hypothesis: MakeHypothesis, split: list[int], expected_treatment: float
) -> None:
    h = make_hypothesis(split=split)
    share = sum(1 for _, arm in assign_many(h, USERS) if arm == "treatment") / len(USERS)
    assert share == pytest.approx(expected_treatment, abs=0.01)


def test_assignment_is_independent_of_order(make_hypothesis: MakeHypothesis) -> None:
    h = make_hypothesis()
    forward = dict(assign_many(h, USERS[:500]))
    backward = dict(assign_many(h, list(reversed(USERS[:500]))))
    assert forward == backward
    assert assign_arm(h.seed, "u00042", h.split) == forward["u00042"]


def test_cli_assign_proves_determinism(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = str(tmp_path / "k.db")
    out = tmp_path / "a.csv"
    assert main(["hypothesis", "add", str(FIXTURES / "hypotheses" / "ab-checkout-copy.yaml"), "--db", db]) == 0
    args = ["assign", "--experiment", "ab-checkout-copy", "--users", str(FIXTURES / "users.csv"),
            "--output", str(out), "--db", db]
    assert main(args) == 0
    first_csv = out.read_text()
    text = capsys.readouterr().out
    assert "identical" in text and "20 new" in text
    assert first_csv.splitlines()[0] == "user_id,arm" and len(first_csv.splitlines()) == 21

    assert main(args) == 0
    assert out.read_text() == first_csv
    assert "0 new, 20 already stored and re-derived identically" in capsys.readouterr().out


def test_cli_assign_requires_registered_experiment(tmp_path: Path) -> None:
    assert main(["assign", "--experiment", "nope", "--users", str(FIXTURES / "users.csv"),
                 "--db", str(tmp_path / "k.db")]) == 2
