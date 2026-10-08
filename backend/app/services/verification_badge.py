"""
The verdict on one finding, as the badge on that finding shows it.

The report carries verification in one place only: a badge on each finding
whose regulatory requirement cites a regulation. There is no global summary.
The Word export prints the same label as text, so this is the single rule both
follow (the frontend mirrors it in components/VerificationBadge.tsx).

  verified            green   "Checked against {citation}"
  partially_verified  yellow  "Regulation found, confirm applicability"
  anything else       red     "Citation not confirmed, review before use"

A finding that cites no regulation (an organizational recommendation) gets no
badge: it never claimed to be regulation.
"""

from __future__ import annotations

from typing import Optional, Tuple

from app.models.schemas import VerificationStatus
from app.services.retrieval.cfr_citation import canonical_citation, is_uncited, parse_cfr_citation

GREEN, YELLOW, RED = "green", "yellow", "red"
CONFIRM_APPLICABILITY = "Regulation found, confirm applicability"
NOT_CONFIRMED = "Citation not confirmed, review before use"


def checked_citation(row) -> str:
    """The section the requirement was checked against, else the finding's own citation."""
    evidence = getattr(row, "evidence", None)
    source = getattr(evidence, "source", None)
    passages = getattr(source, "passages", None) or []
    if passages and passages[0].citation:
        return passages[0].citation
    citation = getattr(row, "citation", "") or ""
    ref = parse_cfr_citation(citation)
    return ref.canonical if ref else canonical_citation(citation)


def verification_badge(row) -> Optional[Tuple[str, str]]:
    """(state, label) for this finding's badge, or None when it gets no badge."""
    citation = getattr(row, "citation", "") or ""
    if not citation.strip() or is_uncited(citation):
        return None
    evidence = getattr(row, "evidence", None)
    status = getattr(evidence, "status", None)
    if status is VerificationStatus.verified:
        return GREEN, f"Checked against {checked_citation(row)}"
    if status is VerificationStatus.partially_verified:
        return YELLOW, CONFIRM_APPLICABILITY
    return RED, NOT_CONFIRMED
