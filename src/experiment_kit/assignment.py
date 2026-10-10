"""Deterministic, hash-based traffic assignment.

bucket = first 8 bytes of sha256("{seed}:{user_id}") as an integer / 2^64,
a number in [0, 1). A user lands in control when bucket < control weight,
otherwise in treatment. No random module, no clock, no process state: the
same (seed, user_id, split) gives the same arm on every machine, forever.
Changing the seed reshuffles everyone, which is why the seed is frozen in
the preregistered hypothesis.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable

from experiment_kit.models import ARMS, Hypothesis


def bucket(seed: str, user_id: str) -> float:
    digest = hashlib.sha256(f"{seed}:{user_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def assign_arm(seed: str, user_id: str, split: tuple[float, float]) -> str:
    control_share = split[0] / (split[0] + split[1])
    return ARMS[0] if bucket(seed, user_id) < control_share else ARMS[1]


def assign(hypothesis: Hypothesis, user_id: str) -> str:
    return assign_arm(hypothesis.seed, user_id, hypothesis.split)


def assign_many(hypothesis: Hypothesis, user_ids: Iterable[str]) -> list[tuple[str, str]]:
    return [(uid, assign(hypothesis, uid)) for uid in user_ids]


def assignment_digest(pairs: Iterable[tuple[str, str]]) -> str:
    """A fingerprint of an assignment run, used to prove two runs are identical."""
    h = hashlib.sha256()
    for user_id, arm in pairs:
        h.update(f"{user_id},{arm}\n".encode("utf-8"))
    return h.hexdigest()
