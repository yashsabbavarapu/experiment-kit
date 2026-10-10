"""Hypothesis records: validation, both file formats, immutability, closing rules."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import BASE, FIXTURES, MakeHypothesis
from experiment_kit import yamlish
from experiment_kit.__main__ import main
from experiment_kit.registry import RegistryError, close, from_record, load_file, parse_hypothesis, to_record
from experiment_kit.storage import Store, StoreError


def test_yaml_and_json_records_are_equivalent(tmp_path: Path) -> None:
    yaml_h = parse_hypothesis(load_file(FIXTURES / "hypotheses" / "ab-checkout-copy.yaml"))
    as_json = tmp_path / "ab.json"
    as_json.write_text(json.dumps(load_file(FIXTURES / "hypotheses" / "ab-checkout-copy.yaml")))
    json_h = parse_hypothesis(load_file(as_json))
    assert yaml_h.name == json_h.name == "ab-checkout-copy"
    assert yaml_h.guardrail_metrics == ("checkout_error",)
    assert yaml_h.expected_lift == json_h.expected_lift == 0.025
    assert yaml_h.min_sample == json_h.min_sample == (2507, 2507)
    assert "explainer" in yaml_h.rationale


def test_min_sample_is_computed_when_omitted(make_hypothesis: MakeHypothesis) -> None:
    h = make_hypothesis(min_sample_per_arm=None, expected_lift=0.025)
    assert h.min_sample == (2507, 2507)
    assert make_hypothesis(min_sample_per_arm=[900, 100]).min_sample == (900, 100)


def test_missing_and_unknown_fields_are_reported() -> None:
    data = {k: v for k, v in BASE.items() if k not in ("owner", "rationale")}
    data["colour"] = "blue"
    with pytest.raises(RegistryError) as err:
        parse_hypothesis(data)
    text = str(err.value)
    assert "owner" in text and "rationale" in text and "colour" in text


def test_every_bad_value_is_collected() -> None:
    data = dict(BASE, name="Bad Name", baseline_rate=1.5, end_date="2026-08-01", split=[50], alpha=0.9)
    with pytest.raises(RegistryError) as err:
        parse_hypothesis(data)
    assert len(err.value.problems) >= 4


def test_primary_cannot_be_a_guardrail() -> None:
    with pytest.raises(RegistryError, match="primary_metric"):
        parse_hypothesis(dict(BASE, guardrail_metrics=["converted"]))


def test_record_round_trip(make_hypothesis: MakeHypothesis) -> None:
    h = make_hypothesis(split=[90, 10])
    assert from_record(json.loads(json.dumps(to_record(h)))) == h
    assert h.split == pytest.approx((0.9, 0.1))


def test_preregistration_is_immutable(tmp_path: Path, make_hypothesis: MakeHypothesis) -> None:
    store = Store(tmp_path / "k.db")
    store.add_hypothesis(make_hypothesis())
    with pytest.raises(StoreError, match="immutable"):
        store.add_hypothesis(make_hypothesis(end_date="2026-12-31"))
    stored = store.get_hypothesis("test-exp")
    assert stored is not None and stored.end_date.isoformat() == "2026-09-14"
    store.close()


def test_shipping_against_the_kit_needs_override_and_note(make_hypothesis: MakeHypothesis) -> None:
    h = make_hypothesis()
    with pytest.raises(RegistryError, match="override"):
        close(h, "ship", None, "INCONCLUSIVE", override=False)
    with pytest.raises(RegistryError, match="override"):
        close(h, "ship", None, "INCONCLUSIVE", override=True)
    closed = close(h, "ship", "legal required the new copy", "INCONCLUSIVE", override=True)
    assert closed.status == "closed" and closed.override
    assert close(h, "ship", None, "SHIP", override=False).override is False
    assert close(h, "kill", None, "INVALID", override=False).decision == "kill"
    with pytest.raises(RegistryError, match="already closed"):
        close(closed, "kill", None, None, override=False)


def test_cli_hypothesis_lifecycle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = str(tmp_path / "k.db")
    files = sorted(str(p) for p in (FIXTURES / "hypotheses").iterdir())
    assert main(["hypothesis", "add", *files, "--db", db]) == 0
    assert main(["hypothesis", "add", files[0], "--db", db]) == 2
    assert "immutable" in capsys.readouterr().err
    assert main(["hypothesis", "close", "aa-homepage-hero", "--decision", "abandon", "--db", db]) == 0
    capsys.readouterr()
    assert main(["hypothesis", "list", "--status", "running", "--db", db]) == 0
    listed = capsys.readouterr().out
    assert "ab-checkout-copy" in listed and "aa-homepage-hero" not in listed
    assert main(["hypothesis", "show", "aa-homepage-hero", "--db", db]) == 0
    assert "abandon" in capsys.readouterr().out


def test_cli_rejects_invalid_record(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"name": "x"}))
    assert main(["hypothesis", "add", str(bad), "--db", str(tmp_path / "k.db")]) == 2
    assert "missing required fields" in capsys.readouterr().err


def test_yamlish_subset() -> None:
    data = yamlish.loads(
        "# comment\nname: demo\nratio: 0.5\ncount: 3\nflag: true\nnothing: null\n"
        "quoted: \"a: b # not a comment\"\nsplit: [90, 10]\nitems:\n  - one\n  - 'two'\n"
    )
    assert data == {
        "name": "demo", "ratio": 0.5, "count": 3, "flag": True, "nothing": None,
        "quoted": "a: b # not a comment", "split": [90, 10], "items": ["one", "two"],
    }


@pytest.mark.parametrize("text", ["a:\n  b: 1\n", "a: {b: 1}\n", "- x\n", "a: 1\na: 2\n", "a: [1, [2]]\n"])
def test_yamlish_refuses_what_it_cannot_parse(text: str) -> None:
    with pytest.raises(yamlish.YamlError):
        yamlish.loads(text)
