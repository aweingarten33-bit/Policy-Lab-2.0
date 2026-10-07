"""
Keep a drafted policy from inventing facts about the organization.

A draft is written for an organization the model knows almost nothing about.
In a live audit a home-health draft named roles the agency does not have
("Director of Clinical Services", "Quality Improvement Coordinator"), set
deadlines nobody chose, and stated an adoption date of March 1, 2026 when no
date was supplied. Each of those reads as a decided fact, so a reader adopting
the draft adopts the invention with it.

The prompt asks the model to use placeholders for anything it was not told.
This module enforces that after generation, because the prompt alone is a
request, not a guarantee. A concrete date, numeric timeframe or capitalized
role title survives only when it appears in what the user supplied or in the
retrieved reference material (a regulation that fixes "60 calendar days" may
be quoted). Everything else becomes an explicit placeholder, and each
placeholder is listed as a decision the organization has to make.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Tuple

EFFECTIVE_DATE = "[EFFECTIVE DATE]"
DATE = "[DATE]"
TIMEFRAME = "[TIMEFRAME]"
ROLE = "[ACCOUNTABLE ROLE {n}]"

_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|"
    "November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)
_DATE_RE = re.compile(
    rf"\b(?:(?:{_MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}"
    rf"|\d{{1,2}}\s+(?:{_MONTHS})\s+\d{{4}}"
    rf"|\d{{1,2}}/\d{{1,2}}/\d{{2,4}}"
    rf"|\d{{4}}-\d{{2}}-\d{{2}})\b"
)

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fourteen": 14, "fifteen": 15, "twenty": 20, "thirty": 30,
    "forty-five": 45, "sixty": 60, "ninety": 90,
}
_TIMEFRAME_RE = re.compile(
    r"\b(?P<num>\d+|" + "|".join(_NUMBER_WORDS) + r")"
    r"(?:\s*\(\d+\))?"                      # "six (6) years"
    r"[\s-]+(?:(?P<kind>business|calendar|working)\s+)?"
    r"(?P<unit>hour|day|week|month|year)s?\b",
    re.IGNORECASE,
)

# Capitalized job titles: "Director of Clinical Services", "Quality
# Improvement Coordinator", "Chief Compliance Officer". Lower-case generic
# references ("staff", "the agency") are not titles and are left alone.
_ROLE_NOUNS = (
    "Officer|Coordinator|Director|Manager|Administrator|Supervisor|"
    "Specialist|Liaison|Committee|Chair|Chairperson|Lead|Designee"
)
_CAP_WORD = r"(?:[A-Z][A-Za-z&/-]*)"
_ROLE_RE = re.compile(
    rf"\b(?:{_CAP_WORD}\s+){{0,4}}(?:{_ROLE_NOUNS})\b"
    rf"(?:\s+of\s+{_CAP_WORD}(?:\s+(?:and\s+)?{_CAP_WORD}){{0,3}})?"
)
# Words that start a capitalized phrase but are not part of a title.
_LEADING_NOISE = {"The", "A", "An", "Each", "Every", "Any", "Our", "Its", "This", "That", "Agency", "Organization"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _timeframe_key(match: re.Match) -> Tuple[int, str]:
    raw = match.group("num").lower()
    number = int(raw) if raw.isdigit() else _NUMBER_WORDS.get(raw, -1)
    return number, match.group("unit").lower()


def _allowed_timeframes(text: str) -> set:
    return {_timeframe_key(m) for m in _TIMEFRAME_RE.finditer(text or "")}


def _strip_leading_noise(role: str) -> str:
    words = role.split()
    while words and words[0] in _LEADING_NOISE:
        words = words[1:]
    return " ".join(words)


class _Grounder:
    """Applies the substitutions consistently across every field of a draft."""

    def __init__(self, supplied: str, reference: str):
        self._allowed = _norm(f"{supplied}\n{reference}")
        self._allowed_timeframes = _allowed_timeframes(f"{supplied}\n{reference}")
        self.roles: Dict[str, str] = {}
        self.timeframes_replaced = 0
        self.dates_replaced = 0

    def _role_placeholder(self, title: str) -> str:
        key = _norm(title)
        if key not in self.roles:
            self.roles[key] = ROLE.format(n=len(self.roles) + 1)
        return self.roles[key]

    def apply(self, text: str) -> str:
        if not text:
            return text

        def date_sub(m: re.Match) -> str:
            if _norm(m.group(0)) in self._allowed:
                return m.group(0)
            self.dates_replaced += 1
            return DATE

        def timeframe_sub(m: re.Match) -> str:
            if _timeframe_key(m) in self._allowed_timeframes:
                return m.group(0)
            self.timeframes_replaced += 1
            return TIMEFRAME

        def role_sub(m: re.Match) -> str:
            full = m.group(0)
            title = _strip_leading_noise(full)
            if not title or len(title.split()) == 0:
                return full
            # A bare noun ("Committee", "Officer") with nothing naming it is
            # not an invented title.
            if " " not in title and title in _ROLE_NOUNS.split("|"):
                return full
            if _norm(title) in self._allowed:
                return full
            prefix = full[: len(full) - len(title)]
            return prefix + self._role_placeholder(title)

        text = _DATE_RE.sub(date_sub, text)
        text = _TIMEFRAME_RE.sub(timeframe_sub, text)
        text = _ROLE_RE.sub(role_sub, text)
        return text


def _auto_decisions(grounder: _Grounder, effective_date_placeholder: bool) -> List[str]:
    decisions: List[str] = []
    if effective_date_placeholder:
        decisions.append(f"{EFFECTIVE_DATE}: choose the date your organization adopts this policy.")
    for title_key, placeholder in grounder.roles.items():
        decisions.append(
            f"{placeholder}: name the role in your organization that holds this responsibility "
            f"(the draft had suggested a title that was not supplied)."
        )
    if grounder.timeframes_replaced:
        decisions.append(
            f"{TIMEFRAME} ({grounder.timeframes_replaced} place(s)): set each deadline or interval. "
            f"Where a regulation fixes one, confirm it against the cited text."
        )
    if grounder.dates_replaced:
        decisions.append(f"{DATE} ({grounder.dates_replaced} place(s)): fill in each date.")
    return decisions


def ground_draft_facts(data: dict, supplied_text: str, reference_text: str = "") -> dict:
    """Replace unsupplied dates, timeframes and role titles with placeholders.

    ``supplied_text`` is everything the user told us (the policy description,
    jurisdiction); ``reference_text`` is the retrieved source material. A fact
    found in either may stay. Mutates and returns ``data``; rebuilds full_text
    from the cleaned sections and sets ``decisions_required``.
    """
    grounder = _Grounder(supplied_text, reference_text)

    for section in data.get("sections", []) or []:
        section["content"] = grounder.apply(section.get("content", ""))

    for field in ("scope", "drafting_notes"):
        if isinstance(data.get(field), str):
            data[field] = grounder.apply(data[field])

    effective = str(data.get("effective_date") or "").strip()
    effective_is_placeholder = False
    if not effective or _norm(effective) not in _norm(supplied_text):
        data["effective_date"] = EFFECTIVE_DATE
        effective_is_placeholder = True

    model_decisions = data.get("decisions_required") or []
    if isinstance(model_decisions, str):
        model_decisions = [model_decisions]
    model_decisions = [grounder.apply(str(d)).strip() for d in model_decisions if str(d).strip()]

    decisions = _dedupe(_auto_decisions(grounder, effective_is_placeholder) + model_decisions)
    data["decisions_required"] = decisions

    data["full_text"] = "\n\n".join(
        f"{s.get('title', '')}\n\n{s.get('content', '')}" for s in data.get("sections", []) or []
    )
    return data


def _dedupe(items: Iterable[str]) -> List[str]:
    seen, out = set(), []
    for item in items:
        key = _norm(item)
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def find_unsupplied_facts(text: str, supplied_text: str, reference_text: str = "") -> List[str]:
    """Concrete dates, timeframes and role titles in ``text`` that were not supplied.

    Used by tests and checks: a grounded draft returns [].
    """
    allowed = _norm(f"{supplied_text}\n{reference_text}")
    allowed_tf = _allowed_timeframes(f"{supplied_text}\n{reference_text}")
    found: List[str] = []
    for m in _DATE_RE.finditer(text or ""):
        if _norm(m.group(0)) not in allowed:
            found.append(m.group(0))
    for m in _TIMEFRAME_RE.finditer(text or ""):
        if _timeframe_key(m) not in allowed_tf:
            found.append(m.group(0))
    for m in _ROLE_RE.finditer(text or ""):
        title = _strip_leading_noise(m.group(0))
        if not title or (" " not in title and title in _ROLE_NOUNS.split("|")):
            continue
        if _norm(title) not in allowed:
            found.append(title)
    return found


__all__ = [
    "DATE", "EFFECTIVE_DATE", "ROLE", "TIMEFRAME",
    "find_unsupplied_facts", "ground_draft_facts",
]
