"""CLI: register hypotheses, assign traffic, ingest events, read out with guardrails."""
from __future__ import annotations

import argparse
import csv
import io
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from experiment_kit.assignment import assign_many, assignment_digest
from experiment_kit.events import EventsError, parse_events
from experiment_kit.models import Hypothesis
from experiment_kit.readout import compute_readout, pct, pp, render_readout
from experiment_kit.registry import DECISIONS, RegistryError, close, load_file, parse_hypothesis
from experiment_kit.storage import Store, StoreError
from experiment_kit.yamlish import YamlError

DEFAULT_DB = "experiment_kit.db"


class CliError(Exception):
    pass


def _require(store: Store, name: str) -> Hypothesis:
    h = store.get_hypothesis(name)
    if h is None:
        raise CliError(f"no hypothesis named {name!r}; register it with 'hypothesis add' first")
    return h


def _today() -> date:
    return datetime.now(timezone.utc).date()


def cmd_hypothesis_add(args: argparse.Namespace, store: Store) -> int:
    for path in args.files:
        h = parse_hypothesis(load_file(Path(path)))
        store.add_hypothesis(h)
        print(
            f"registered {h.name}: {h.start_date} to {h.end_date}, split "
            f"{h.split[0] * 100:.0f}/{h.split[1] * 100:.0f}, seed {h.seed!r}, "
            f"min sample {h.min_sample[0]:,} control / {h.min_sample[1]:,} treatment "
            f"(to detect {pp(h.expected_lift)} on {pct(h.baseline_rate)} at alpha {h.alpha:g}, power {h.power:g})"
        )
        if not h.guardrail_metrics:
            print(f"  warning: {h.name} has no guardrail metrics; nothing will stop a harmful win")
    return 0


def cmd_hypothesis_list(args: argparse.Namespace, store: Store) -> int:
    rows = store.list_hypotheses()
    if args.status:
        rows = [h for h in rows if h.status == args.status]
    if not rows:
        print("no hypotheses registered")
        return 0
    print(f"{'name':<26} {'status':<8} {'owner':<16} {'window':<24} {'last verdict':<13} decision")
    for h in rows:
        history = store.readout_history(h.name)
        last = history[-1][1] if history else "-"
        decision = h.decision or "-"
        if h.override:
            decision += " (override)"
        print(
            f"{h.name:<26} {h.status:<8} {h.owner[:16]:<16} {f'{h.start_date} to {h.end_date}':<24} "
            f"{last:<13} {decision}"
        )
    return 0


def cmd_hypothesis_show(args: argparse.Namespace, store: Store) -> int:
    h = _require(store, args.name)
    print(f"{h.name}  [{h.status}]  owner: {h.owner}")
    print(f"  change:      {h.change}")
    print(f"  rationale:   {h.rationale}")
    print(f"  primary:     {h.primary_metric} (baseline {pct(h.baseline_rate)}, expected {pp(h.expected_lift)})")
    print(f"  guardrails:  {', '.join(h.guardrail_metrics) or 'none'}")
    print(f"  window:      {h.start_date} to {h.end_date}")
    print(f"  split/seed:  {h.split[0] * 100:.0f}/{h.split[1] * 100:.0f}, seed {h.seed!r}")
    print(f"  min sample:  {h.min_sample[0]:,} control / {h.min_sample[1]:,} treatment")
    print(f"  alpha/power: {h.alpha:g} / {h.power:g}, SRM threshold {h.srm_alpha:g}")
    for as_of, verdict in store.readout_history(h.name):
        print(f"  readout as of {as_of}: {verdict}")
    if h.status == "closed":
        override = " (OVERRIDE of the kit's verdict)" if h.override else ""
        print(f"  closed {h.closed_at}: {h.decision}{override}; kit verdict {h.kit_verdict_at_close or 'none'}")
        if h.close_note:
            print(f"  note: {h.close_note}")
    return 0


def cmd_hypothesis_close(args: argparse.Namespace, store: Store) -> int:
    h = _require(store, args.name)
    history = store.readout_history(h.name)
    kit_verdict = history[-1][1] if history else None
    closed = close(h, args.decision, args.note, kit_verdict, args.override)
    store.update_hypothesis(closed)
    suffix = " (override recorded)" if closed.override else ""
    print(f"closed {h.name}: {args.decision}, kit verdict {kit_verdict or 'none'}{suffix}")
    return 0


def _read_user_ids(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if "user_id" not in [f.strip() for f in (reader.fieldnames or [])]:
            raise CliError(f"{path}: missing required column user_id")
        reader.fieldnames = [f.strip() for f in reader.fieldnames or []]
        ids = [(row["user_id"] or "").strip() for row in reader]
    return [uid for uid in ids if uid]


def cmd_assign(args: argparse.Namespace, store: Store) -> int:
    h = _require(store, args.experiment)
    user_ids = _read_user_ids(Path(args.users))
    first = assign_many(h, user_ids)
    second = assign_many(h, user_ids)
    d1, d2 = assignment_digest(first), assignment_digest(second)
    if d1 != d2:
        raise CliError("assignment was not deterministic; this is a bug")
    new, same, conflicts = store.save_assignments(h.name, first)

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["user_id", "arm"])
    writer.writerows(first)
    report = sys.stdout
    if args.output:
        Path(args.output).write_text(buf.getvalue(), encoding="utf-8")
        print(f"wrote {len(first):,} assignments to {args.output}")
    else:
        sys.stdout.write(buf.getvalue())
        report = sys.stderr
    counts = {arm: sum(1 for _, a in first if a == arm) for arm in ("control", "treatment")}
    print(
        f"assigned {len(first):,} users to {h.name} (seed {h.seed!r}, split "
        f"{h.split[0] * 100:.0f}/{h.split[1] * 100:.0f}): "
        f"control {counts['control']:,}, treatment {counts['treatment']:,}",
        file=report,
    )
    print(f"determinism: run 1 sha256 {d1[:16]}, run 2 sha256 {d2[:16]}: identical", file=report)
    print(f"stored: {new:,} new, {same:,} already stored and re-derived identically", file=report)
    if conflicts:
        print(f"CONFLICT: {len(conflicts)} stored assignments disagree, e.g. {conflicts[:3]}", file=report)
        return 1
    return 0


def cmd_ingest(args: argparse.Namespace, store: Store) -> int:
    known = {h.name for h in store.list_hypotheses()}
    for path in args.events:
        result = parse_events(Path(path))
        inserted, skipped = store.save_events(result.events)
        print(
            f"{path}: {len(result.events):,} valid rows, {inserted:,} new, "
            f"{skipped:,} duplicates skipped, {len(result.warnings)} bad rows"
        )
        for w in result.warnings[: args.max_warnings]:
            print(f"  skipped {w}")
        if len(result.warnings) > args.max_warnings:
            print(f"  ... and {len(result.warnings) - args.max_warnings} more")
        unregistered = sorted({e.experiment for e in result.events} - known)
        if unregistered:
            print(f"  warning: events for unregistered experiments: {', '.join(unregistered)}")
    return 0


def cmd_readout(args: argparse.Namespace, store: Store) -> int:
    h = _require(store, args.experiment)
    as_of = date.fromisoformat(args.as_of) if args.as_of else _today()
    readout = compute_readout(h, store.events_for(h.name), as_of, store.readout_history(h.name))
    store.log_readout(h.name, as_of, readout.verdict.value, readout.reasons)
    sys.stdout.write(render_readout(readout))
    return 0


def build_parser() -> argparse.ArgumentParser:
    db = argparse.ArgumentParser(add_help=False)
    db.add_argument("--db", default=DEFAULT_DB, help=f"SQLite path (default {DEFAULT_DB})")

    parser = argparse.ArgumentParser(
        prog="experiment-kit", description="Guardrailed experimentation: the discipline, not the platform"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_hyp = sub.add_parser("hypothesis", help="Register, list, show, and close hypotheses")
    hyp = p_hyp.add_subparsers(dest="hypothesis_command", required=True)
    p_add = hyp.add_parser("add", parents=[db], help="Register hypotheses from JSON or YAML files")
    p_add.add_argument("files", nargs="+", help="Hypothesis record(s), .json or .yaml")
    p_add.set_defaults(func=cmd_hypothesis_add)
    p_list = hyp.add_parser("list", parents=[db], help="List registered hypotheses")
    p_list.add_argument("--status", choices=["running", "closed"], default=None)
    p_list.set_defaults(func=cmd_hypothesis_list)
    p_show = hyp.add_parser("show", parents=[db], help="Show one hypothesis and its readout history")
    p_show.add_argument("name")
    p_show.set_defaults(func=cmd_hypothesis_show)
    p_close = hyp.add_parser("close", parents=[db], help="Close a hypothesis with a decision")
    p_close.add_argument("name")
    p_close.add_argument("--decision", required=True, choices=DECISIONS)
    p_close.add_argument("--note", default=None, help="Why; required when overriding the kit")
    p_close.add_argument("--override", action="store_true", help="Ship against the kit's verdict (logged)")
    p_close.set_defaults(func=cmd_hypothesis_close)

    p_assign = sub.add_parser("assign", parents=[db], help="Deterministically assign users to arms")
    p_assign.add_argument("--experiment", required=True)
    p_assign.add_argument("--users", required=True, help="CSV with a user_id column")
    p_assign.add_argument("--output", default=None, help="Write user_id,arm CSV here instead of stdout")
    p_assign.set_defaults(func=cmd_assign)

    p_ingest = sub.add_parser("ingest", parents=[db], help="Ingest exposure and conversion events (idempotent)")
    p_ingest.add_argument("--events", required=True, nargs="+", help="Events CSV file(s)")
    p_ingest.add_argument("--max-warnings", type=int, default=10, help="Bad rows to print per file")
    p_ingest.set_defaults(func=cmd_ingest)

    p_read = sub.add_parser("readout", parents=[db], help="Guardrailed analysis and verdict")
    p_read.add_argument("--experiment", required=True)
    p_read.add_argument("--as-of", default=None, help="Read as of this date (YYYY-MM-DD, default today UTC)")
    p_read.set_defaults(func=cmd_readout)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = Store(Path(args.db))
    try:
        return int(args.func(args, store))
    except (CliError, RegistryError, StoreError, EventsError, YamlError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
