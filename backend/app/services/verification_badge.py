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


# ── Evidence status and priority: two independent fields per finding ──
#
# Seen live: every finding said MUST FIX while most were unconfirmed, and the
# overview called annual training mandatory while its own card called it a
# best-practice recommendation. Urgency and legal standing were being read off
# the same signals and the summary was free text. Now each finding has an
# evidence status and a priority, and the summary is built from those.

VERIFIED_REQUIREMENT = "verified_requirement"
NEEDS_SOURCE_REVIEW = "needs_source_review"
RECOMMENDATION = "recommendation"

EVIDENCE_LABELS = {
    VERIFIED_REQUIREMENT: "Verified requirement",
    NEEDS_SOURCE_REVIEW: "Needs source review",
    RECOMMENDATION: "Recommendation",
}

# Obligation classes that never claimed to be law.
_NOT_LAW = {"best_practice", "organizational_choice", "guidance"}


def evidence_status(row) -> str:
    """Is this finding an established legal requirement? One of three answers."""
    citation = getattr(row, "citation", "") or ""
    obligation = getattr(getattr(row, "obligation_type", None), "value", None)
    if not citation.strip() or is_uncited(citation) or obligation in _NOT_LAW:
        return RECOMMENDATION
    status = getattr(getattr(row, "evidence", None), "status", None)
    if status is VerificationStatus.verified:
        return VERIFIED_REQUIREMENT
    return NEEDS_SOURCE_REVIEW


def evidence_label(row) -> str:
    status = evidence_status(row)
    if status == VERIFIED_REQUIREMENT:
        return f"{EVIDENCE_LABELS[status]} · {checked_citation(row)}"
    return EVIDENCE_LABELS[status]


def priority_of(row) -> str:
    """How urgent, from the risk level alone -- independent of evidence status."""
    risk = getattr(getattr(row, "risk_level", None), "value", getattr(row, "risk_level", None))
    return "must_fix" if risk in ("critical", "high") else "should_fix"


PRIORITY_LABELS = {"must_fix": "Must fix", "should_fix": "Should fix"}


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _join(parts):
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def findings_summary(rows) -> str:
    """The report summary, written from the finding objects only.

    Counts by evidence status and by priority, in fixed wording. It names no
    finding and repeats no model prose, so it cannot call a recommendation
    mandatory. The frontend builds the same text (lib/findings.ts).
    """
    rows = list(rows or [])
    if not rows:
        return "This review produced no findings: the document addresses each obligation reviewed."
    statuses = [evidence_status(r) for r in rows]
    v, n, r = (statuses.count(s) for s in (VERIFIED_REQUIREMENT, NEEDS_SOURCE_REVIEW, RECOMMENDATION))
    parts = []
    if v:
        parts.append(_n(v, "verified requirement", "verified requirements")
                     + " (the cited regulation was checked and supports " + ("it" if v == 1 else "them") + ")")
    if n:
        parts.append(_n(n, "finding needs", "findings need")
                     + " source review (a regulation is cited, but its text did not confirm the requirement as stated)")
    if r:
        parts.append(_n(r, "recommendation", "recommendations")
                     + " (good practice, not " + ("a legal requirement" if r == 1 else "legal requirements") + ")")
    priorities = [priority_of(x) for x in rows]
    m, s = priorities.count("must_fix"), priorities.count("should_fix")
    urgency = _join([p for p in (
        f"{m} {'is' if m == 1 else 'are'} Must fix" if m else "",
        f"{s} {'is' if s == 1 else 'are'} Should fix" if s else "",
    ) if p])
    return (f"This review produced {_n(len(rows), 'finding', 'findings')}: {_join(parts)}. "
            f"By priority, {urgency}.")
