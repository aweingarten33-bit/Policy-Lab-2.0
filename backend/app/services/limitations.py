"""
The limits of an output, stated where a reader sees them first.

Verification status ("6/6 citation-backed claims not fully verified") used to
sit in footer metadata while the document opened with REGULATIONS APPLIED --
so the first thing a reader saw was the part that looked authoritative, and
the part that qualified it came last, if at all. These lines go at the top of
every export, before any finding or policy text.

Each function returns plain sentences; the exporters only lay them out.
"""

from __future__ import annotations

from typing import List, Optional

from app.models.schemas import AnalysisResult, StateCoverage, VerificationStatus
from app.services.retrieval.cfr_citation import is_uncited

DOCUMENT_ONLY = (
    "This analysis read the policy document only. No records, logs or practices were inspected, so "
    "findings describe what the document says or omits, not whether the organization complies."
)


def _live_search_line(live_research_used: bool) -> str:
    if live_research_used:
        return "A live search of government websites ran for this output; those results are web pages, not codified text."
    return (
        "No live government search ran for this output: the stored federal regulations covered the request. "
        "Nothing newer than the stored text was checked."
    )


def _state_line(state_coverage: Optional[StateCoverage]) -> Optional[str]:
    if state_coverage is None:
        return None
    if isinstance(state_coverage, dict):
        return state_coverage.get("summary")
    return state_coverage.summary


# Each status is reported as itself. A partially verified finding (the regulation
# was found, the passage does not fully establish the finding) is not the same
# failure as an unverified one, and "6 of 6 not fully verified" hid which was which.
_STATUS_LABELS = (
    (VerificationStatus.verified, "verified"),
    (VerificationStatus.partially_verified, "partially verified"),
    (VerificationStatus.unverified, "not verified"),
    (VerificationStatus.contradicted, "contradicted by the cited text"),
    (VerificationStatus.cannot_determine, "could not be determined"),
)


def verification_breakdown(rows) -> str:
    """'4 verified, 1 partially verified, 1 not verified' for findings that cite a regulation."""
    counts = {}
    for r in rows:
        status = r.evidence.status if getattr(r, "evidence", None) is not None else VerificationStatus.unverified
        counts[status] = counts.get(status, 0) + 1
    parts = [f"{counts[s]} {label}" for s, label in _STATUS_LABELS if counts.get(s)]
    total = len(rows)
    if counts.get(VerificationStatus.verified) == total:
        tail = "They still need review by counsel."
    else:
        notes = []
        if counts.get(VerificationStatus.partially_verified):
            notes.append("Partially verified: the regulation was found, but the passage does not fully establish the finding.")
        if counts.get(VerificationStatus.unverified):
            notes.append("Not verified: the finding could not be confirmed against the cited text.")
        tail = " ".join(notes + ["Check those before relying on them."])
    return f"{total} cited finding(s): {', '.join(parts)}. {tail}"


def analysis_limitations(
    result: AnalysisResult,
    live_research_used: bool = False,
    state_coverage: Optional[StateCoverage] = None,
) -> List[str]:
    all_rows = result.gap_table or []
    # A finding that cites no regulation (an organizational recommendation)
    # has nothing to verify; counting it as a verification failure overstated
    # the problem and hid how many legal claims actually failed.
    rows = [r for r in all_rows if not is_uncited(r.citation)]
    uncited = len(all_rows) - len(rows)
    lines = []
    if uncited:
        lines.append(
            f"{uncited} finding(s) cite no regulation: they are organizational recommendations, "
            f"not legal requirements, and are not counted below."
        )
    if rows:
        lines.append(f"Verification: {verification_breakdown(rows)}")
    lines.append(DOCUMENT_ONLY)
    lines.append(_live_search_line(live_research_used))
    state = _state_line(state_coverage)
    if state:
        lines.append(f"State law: {state}")
    return lines


def draft_limitations(policy: dict) -> List[str]:
    lines = []
    if policy.get("verification_overall"):
        lines.append(f"Verification: {policy['verification_overall']}")
    decisions = policy.get("decisions_required") or []
    if decisions:
        lines.append(
            f"This is a draft with {len(decisions)} open decision(s). Bracketed placeholders such as "
            f"[ACCOUNTABLE ROLE 1] and [TIMEFRAME] must be filled in by your organization; they were not "
            f"supplied, so nothing was invented for them."
        )
    missing = policy.get("missing_obligations") or []
    if missing:
        lines.append(
            f"{len(missing)} regulatory obligation(s) for this topic do not appear in the draft (listed below). "
            f"Add them before adoption."
        )
    lines.append(_live_search_line(bool(policy.get("live_research_used"))))
    state = _state_line(policy.get("state_coverage"))
    if state:
        lines.append(f"State law: {state}")
    return lines


def revision_limitations() -> List[str]:
    return [
        "This is a proposed revision written by AI from the gap analysis. It has not been re-analyzed: "
        "do not assume it resolves every finding until a fresh analysis of this text says so.",
        "Any new roles, deadlines or citations it introduces must be checked against your organization "
        "and the cited regulations before adoption.",
    ]


__all__ = ["DOCUMENT_ONLY", "analysis_limitations", "verification_breakdown", "draft_limitations", "revision_limitations"]
