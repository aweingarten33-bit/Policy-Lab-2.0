"""Read complete objects out of a JSON document that is still being written.

The gap analysis is one JSON object, produced by one model call, and the whole
of it had to arrive before anything could be parsed. So the reader watched a
spinner for the entire generation and then got the report all at once — even
though the first finding was finished seconds in and was just sitting in a
buffer waiting for the last one.

This scans a partial response and returns the gap_table rows that are complete
so far. It never guesses at half-written content: a row is returned only once
its closing brace has arrived and it parses cleanly on its own.

Deliberately not a general JSON parser. It needs one array, in one known field,
from a document whose shape we define in the prompt, and a small exact reader is
easier to trust than a lenient one.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple


def _strip_fences(text: str) -> str:
    """Drop a leading ```json fence if the model opened with one."""
    stripped = text.lstrip()
    if stripped.startswith("```"):
        newline = stripped.find("\n")
        if newline != -1:
            return stripped[newline + 1:]
        return ""
    return text


def _find_array_start(text: str, field: str) -> int:
    """Index just past the '[' opening `field`'s array, or -1."""
    key = f'"{field}"'
    at = text.find(key)
    if at == -1:
        return -1
    bracket = text.find("[", at + len(key))
    return bracket + 1 if bracket != -1 else -1


def _scan_objects(text: str, start: int) -> Tuple[List[str], int]:
    """Return every balanced {...} block from `start`, and where scanning ended.

    Brace counting has to respect string literals, because policy text is full
    of braces and quotes -- a finding that quotes a policy section containing
    "{" would otherwise end the object early and produce a truncated row.
    """
    blocks: List[str] = []
    i = start
    depth = 0
    obj_start = -1
    in_string = False
    escaped = False
    consumed = start

    while i < len(text):
        ch = text[i]

        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and obj_start != -1:
                    blocks.append(text[obj_start:i + 1])
                    consumed = i + 1
                    obj_start = -1
        elif ch == "]" and depth == 0:
            # End of the array itself.
            break
        i += 1

    return blocks, consumed


def complete_rows(buffer: str, field: str = "gap_table") -> List[Dict[str, Any]]:
    """Every fully-written object in `field`'s array so far.

    Returns them in order. A partially written trailing object is ignored until
    it closes. Safe to call repeatedly on a growing buffer — callers de-duplicate
    by count, since the prefix is stable once an object has closed.
    """
    text = _strip_fences(buffer)
    start = _find_array_start(text, field)
    if start == -1:
        return []

    blocks, _ = _scan_objects(text, start)
    rows: List[Dict[str, Any]] = []
    for block in blocks:
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            # A closed brace that does not parse means the model wrote something
            # malformed, or the block is nested inside a row we already have.
            # Either way it is not a finished row; skip it rather than guess.
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def scalar_field(buffer: str, field: str) -> Optional[str]:
    """A completed top-level string field, so headings can render early.

    Returns None until the closing quote has arrived, so a half-written title
    is never shown.
    """
    text = _strip_fences(buffer)
    key = f'"{field}"'
    at = text.find(key)
    if at == -1:
        return None
    colon = text.find(":", at + len(key))
    if colon == -1:
        return None

    i = colon + 1
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    if i >= len(text) or text[i] != '"':
        return None

    i += 1
    out = []
    escaped = False
    while i < len(text):
        ch = text[i]
        if escaped:
            out.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == '"':
            try:
                return json.loads('"' + "".join(out).replace('"', '\\"') + '"')
            except json.JSONDecodeError:
                return "".join(out)
        else:
            out.append(ch)
        i += 1
    return None
