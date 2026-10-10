"""A tiny, strict YAML subset so hypothesis files can be YAML with zero deps.

Supported: a flat top-level mapping of ``key: value``, block lists of scalars
(``- item`` lines under ``key:``), flow lists like ``[50, 50]``, quoted and
plain scalars, ints, floats, booleans, null, and ``#`` comments. Anything
else (nesting, anchors, multi-line strings) raises ``YamlError`` instead of
being misparsed.
"""
from __future__ import annotations

import re

Scalar = str | int | float | bool | None
Value = Scalar | list[Scalar]


class YamlError(ValueError):
    pass


_INT = re.compile(r"^[-+]?\d+$")
_FLOAT = re.compile(r"^[-+]?(\d+\.\d*|\.\d+|\d+(\.\d*)?[eE][-+]?\d+)$")
_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(.*)$")


def _strip_comment(line: str) -> str:
    quote: str | None = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def _scalar(text: str, line_no: int) -> Scalar:
    s = text.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1]
    if s in ("", "~", "null", "Null", "NULL"):
        return None
    if s in ("true", "True", "TRUE"):
        return True
    if s in ("false", "False", "FALSE"):
        return False
    if _INT.match(s):
        return int(s)
    if _FLOAT.match(s):
        return float(s)
    if s[0] in "{&*|>!":
        raise YamlError(f"line {line_no}: unsupported YAML syntax: {s!r}")
    return s


def _flow_list(text: str, line_no: int) -> list[Scalar]:
    body = text.strip()[1:-1]
    items: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    for ch in body:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch == ",":
            items.append("".join(buf))
            buf = []
        elif ch in "[]{}":
            raise YamlError(f"line {line_no}: nested flow collections are not supported")
        else:
            buf.append(ch)
    if quote:
        raise YamlError(f"line {line_no}: unterminated quote")
    if "".join(buf).strip():
        items.append("".join(buf))
    return [_scalar(item, line_no) for item in items]


def loads(text: str) -> dict[str, Value]:
    out: dict[str, Value] = {}
    open_list: str | None = None
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw).rstrip()
        if not line.strip() or line.strip() == "---":
            continue
        stripped = line.lstrip()
        if stripped.startswith("- ") or stripped == "-":
            if open_list is None or line == stripped:
                raise YamlError(f"line {line_no}: list item outside an indented list")
            current = out[open_list]
            assert isinstance(current, list)
            current.append(_scalar(stripped[1:], line_no))
            continue
        if line != stripped:
            raise YamlError(f"line {line_no}: nested mappings are not supported")
        match = _KEY.match(line)
        if not match:
            raise YamlError(f"line {line_no}: expected 'key: value', got {line!r}")
        key, rest = match.group(1), match.group(2).strip()
        if key in out:
            raise YamlError(f"line {line_no}: duplicate key {key!r}")
        if rest == "":
            out[key] = []
            open_list = key
        elif rest.startswith("["):
            if not rest.endswith("]"):
                raise YamlError(f"line {line_no}: unterminated flow list")
            out[key] = _flow_list(rest, line_no)
            open_list = None
        else:
            out[key] = _scalar(rest, line_no)
            open_list = None
    return out
