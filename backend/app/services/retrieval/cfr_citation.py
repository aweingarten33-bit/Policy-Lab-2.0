"""
Read the CFR reference out of a citation however the model wrote it.

The knowledge base stores one record per CFR section, under a canonical
citation ("45 CFR § 164.404"). Findings arrive in whatever form the model
chose, and verification looked them up more or less verbatim. So a finding
citing a section that IS stored still came back "not found in current
authoritative source material" whenever the citation was written as:

    45 C.F.R. § 164.404                          (periods in C.F.R.)
    HIPAA Breach Notification Rule, 45 CFR §164.404   (a rule name in front)
    45 CFR Part 164, §164.404                    (the part before the section)
    45 CFR §§ 164.400-164.414                    (a range)
    45 CFR Part 164, Subpart D                   (a part or subpart, no section)

Reproduced against the production code path with every one of those forms; the
last two can never match a section-level record at all. This module turns
each into a structured reference so lookups use the canonical form, and so a
part, subpart or range citation can be resolved to the specific section of
that part the finding is actually about.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

_TITLE_RE = re.compile(r"\b(?P<title>\d{1,2})\s*C\.?\s*F\.?\s*R\.?(?![A-Za-z])", re.IGNORECASE)
_SECTION_RE = re.compile(r"(?<![\d.])(?P<section>\d{1,4}\.\d+[a-z]?)(?P<subs>(?:\s*\([A-Za-z0-9]{1,4}\))*)")
_RANGE_RE = re.compile(
    r"(?<![\d.])(?P<lo>\d{1,4}\.\d+)\s*(?:-|–|—|to|through)\s*(?:§\s*)?(?P<hi>\d{1,4}\.\d+)",
    re.IGNORECASE,
)
_PART_RE = re.compile(r"\bPart\s+(?P<part>\d{1,4})\b", re.IGNORECASE)
_SUBPART_RE = re.compile(r"\bSubpart\s+(?P<subpart>[A-Z])\b", re.IGNORECASE)


@dataclass(frozen=True)
class CfrRef:
    title: str
    part: str
    section: Optional[str] = None        # "164.404" when a single section is cited
    subs: str = ""                       # "(b)(1)"
    section_range: Optional[Tuple[str, str]] = None
    subpart: Optional[str] = None

    @property
    def is_section(self) -> bool:
        return self.section is not None

    @property
    def part_citation(self) -> str:
        """The part-level citation as the knowledge base stores it."""
        return f"{self.title} CFR Part {self.part}"

    @property
    def canonical(self) -> str:
        """The citation in the form verification and the section store match on."""
        if self.section:
            return f"{self.title} CFR § {self.section}{self.subs}"
        if self.section_range:
            return f"{self.title} CFR §§ {self.section_range[0]}-{self.section_range[1]}"
        if self.subpart:
            return f"{self.part_citation} Subpart {self.subpart}"
        return self.part_citation

    def covers_section(self, section: str) -> bool:
        """Whether a stored section falls inside this part/range reference."""
        if not section or section.split(".")[0] != self.part:
            return False
        if not self.section_range:
            return True
        key = _section_key(section)
        return _section_key(self.section_range[0]) <= key <= _section_key(self.section_range[1])


def _section_key(section: str) -> Tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", section))


# Notes the model appends inside a citation ("[MODEL INFERENCE — NOT VERIFIED
# FROM LOADED SOURCES]", "(referenced in retrieved material as ...)") are not
# part of the reference, and may themselves contain another citation.
_ANNOTATION_RE = re.compile(r"\[[^\]]*\]|\([^()]*\b(?:referenced|see|cited|retrieved|as part of)\b[^()]*\)", re.IGNORECASE)


def split_citations(citation: str) -> List[str]:
    """The separate references in a combined citation string, annotations removed.

    "45 CFR Part 164 Subpart D — Breach [MODEL INFERENCE]; 42 CFR § 482.13(d)(1)"
    is two references. Reading it as one took the section number of the second
    (482.13) as the section of the first (45 CFR), producing "45 CFR § 482.13" --
    which does not exist -- and the finding was reported as not found.
    """
    cleaned = _ANNOTATION_RE.sub(" ", citation or "")
    parts = []
    for chunk in re.split(r";", cleaned):
        # A chunk can still hold two titles ("45 CFR 164.404 and 42 CFR 2.13").
        starts = [m.start() for m in _TITLE_RE.finditer(chunk)]
        if len(starts) <= 1:
            if chunk.strip():
                parts.append(chunk.strip())
            continue
        if starts[0] > 0 and chunk[: starts[0]].strip():
            parts.append(chunk[: starts[0]].strip())
        for a, b in zip(starts, starts[1:] + [len(chunk)]):
            parts.append(chunk[a:b].strip(" ,"))
    return [p for p in parts if p]


def parse_cfr_citations(citation: str) -> List[CfrRef]:
    """Every CFR reference in a combined citation string, in order."""
    refs = []
    for part in split_citations(citation):
        ref = _parse_one(part)
        if ref is not None:
            refs.append(ref)
    return refs


def parse_cfr_citation(citation: str) -> Optional[CfrRef]:
    """The first CFR reference in ``citation``, or None if it names no CFR title."""
    refs = parse_cfr_citations(citation)
    return refs[0] if refs else None


def _parse_one(citation: str) -> Optional[CfrRef]:
    """One CFR reference from a string holding at most one CFR title."""
    if not citation:
        return None
    title_match = _TITLE_RE.search(citation)
    if not title_match:
        return None
    title = title_match.group("title")
    rest = citation[title_match.end():]
    subpart_match = _SUBPART_RE.search(rest)
    subpart = subpart_match.group("subpart").upper() if subpart_match else None

    range_match = _RANGE_RE.search(rest)
    section_match = _SECTION_RE.search(rest)
    if range_match and (not section_match or range_match.start() <= section_match.start()):
        lo, hi = range_match.group("lo"), range_match.group("hi")
        if lo.split(".")[0] == hi.split(".")[0]:
            return CfrRef(title=title, part=lo.split(".")[0], section_range=(lo, hi), subpart=subpart)

    if section_match:
        section = section_match.group("section")
        subs = re.sub(r"\s+", "", section_match.group("subs") or "")
        return CfrRef(title=title, part=section.split(".")[0], section=section, subs=subs, subpart=subpart)

    part_match = _PART_RE.search(rest)
    if part_match:
        return CfrRef(title=title, part=part_match.group("part"), subpart=subpart)
    return None


_UNCITED_RE = re.compile(r"no (?:specific )?regulatory citation applies|organizational best practice", re.IGNORECASE)


def is_uncited(citation: str) -> bool:
    """True when a finding expressly cites no regulation (an organizational recommendation)."""
    return bool(_UNCITED_RE.search(citation or "")) and parse_cfr_citation(citation or "") is None


def canonical_citation(citation: str) -> str:
    """``citation`` rewritten to its canonical CFR form, or unchanged if it is not a CFR citation."""
    ref = parse_cfr_citation(citation)
    return ref.canonical if ref else citation


__all__ = ["CfrRef", "canonical_citation", "parse_cfr_citation", "parse_cfr_citations", "split_citations"]
