"""Known-answer cases for the Phase 0 evaluation gate.

Each case is a small, fully specified scenario: a source with a known standing
and known text, a claim, and the verification outcome that is correct for it.
The suite exists so a regression is caught by a red test rather than by someone
reading a generated policy carefully enough to notice.

Every regulation, section number and figure here is FICTIONAL. Part 999 does
not exist. That is deliberate: a fixture built from real law would pass or fail
partly on what the model already believes about that law, and the point is to
test the engine, not its memory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.models.schemas import SourceStatus, VerificationStatus
from app.services.retrieval.models import (
    Jurisdiction,
    RetrievalContext,
    RetrievalResult,
    SourceCategory,
    SourceChunk,
    SourceMetadata,
    SourceType,
)

# ── Fixture source text ──

# A long section whose controlling paragraph sits well past character 3,000 —
# the exact position where the old eCFR truncation cut. Padding is realistic
# filler so the section reads like a real one rather than a repeated string.
_FILLER_PARAGRAPH = (
    "The responsible official shall maintain documentation sufficient to "
    "demonstrate the manner in which each requirement of this subpart has been "
    "satisfied, and shall make that documentation available upon request. "
    "Documentation maintained under this paragraph may be kept in electronic "
    "form provided that its integrity is preserved. "
)

LONG_SECTION_LEAD = (
    "§ 999.45 Notification requirements. "
    "(a) General rule. A covered organization shall provide notification in "
    "accordance with this section. "
    "(b) Timing. Notification shall be provided without unreasonable delay. "
)

# The paragraph that actually decides the claim.
LONG_SECTION_CONTROLLING = (
    "(c) Content of notification. The notification required by paragraph (a) of "
    "this section shall include a description of the event, the categories of "
    "information involved, the steps the organization has taken, and a contact "
    "procedure. The organization shall retain a copy of each notification for "
    "seven years from the date it was issued. "
)

# Assembled so (c) begins past character 3,000.
LONG_SECTION_TEXT = (
    LONG_SECTION_LEAD
    + _FILLER_PARAGRAPH * 12
    + LONG_SECTION_CONTROLLING
    + "(d) Recordkeeping. Records shall be maintained in a retrievable form."
)

assert LONG_SECTION_TEXT.index("(c) Content of notification") > 3000, (
    "the fixture must place the controlling subsection past the old truncation point"
)

MANDATORY_SECTION_TEXT = (
    "§ 999.10 Training. (a) A covered organization shall train each member of "
    "its workforce on the procedures of this subpart within 30 days of hire, "
    "and shall document each training session."
)

PERMISSIVE_SECTION_TEXT = (
    "§ 999.20 Optional measures. (a) A covered organization may designate an "
    "additional reviewer, and should consider periodic refresher briefings "
    "where it determines that they would be useful."
)

GUIDANCE_TEXT = (
    "Effective Program Guidance. Organizations are encouraged to appoint a "
    "senior official responsible for the program and to review its operation "
    "regularly. This document expresses the agency's views and does not create "
    "or confer any rights."
)

NEIGHBOUR_SECTION_TEXT = (
    "§ 999.46 Alternative notification. (a) Where direct notification is not "
    "practicable, substitute notification may be provided. (b) Substitute "
    "notification shall remain available for 90 days."
)


# ── Case construction ──


def make_result(
    citation: str,
    text: str,
    *,
    status: Optional[SourceStatus] = SourceStatus.current_verified,
    category: SourceCategory = SourceCategory.federal_regulation,
    source_type: SourceType = SourceType.retrieved_source,
    effective_date: Optional[str] = None,
    publication_date: Optional[str] = None,
    retrieved_date: Optional[str] = "2026-08-25",
    last_verified_date: Optional[str] = "2026-08-25",
    part_citation: Optional[str] = "99 CFR Part 999",
    score: float = 0.95,
) -> RetrievalResult:
    chunk = SourceChunk(
        id=f"eval-{abs(hash((citation, text[:60]))) % 1000000}",
        text=text,
        metadata=SourceMetadata(
            source_name=f"Fixture — {citation}",
            source_type=source_type,
            category=category,
            jurisdiction=Jurisdiction.federal,
            citation=citation,
            part_citation=part_citation,
            url="https://example.invalid/999",
            effective_date=effective_date,
            publication_date=publication_date,
            retrieved_date=retrieved_date,
            last_verified_date=last_verified_date,
            source_status=status,
            is_current=status is not SourceStatus.superseded,
            collection=category.value,
        ),
    )
    return RetrievalResult(chunk=chunk, score=score, query="evaluation")


def make_context(*results: RetrievalResult) -> RetrievalContext:
    return RetrievalContext(
        query="evaluation",
        retrieved_chunks=list(results),
        total_sources_found=len(results),
    )


@dataclass(frozen=True)
class EvalCase:
    """One known-answer case.

    `forbidden_statuses` is the part that makes this a gate rather than a
    description: whatever else changes, these outcomes must never be produced
    for this input.
    """
    name: str
    claim: str
    citation: str
    results: tuple
    expect_status: Optional[VerificationStatus] = None
    forbidden_statuses: tuple = (VerificationStatus.verified,)
    expect_citation_exists: Optional[bool] = None
    expect_source_status: Optional[SourceStatus] = None
    # Claim support to feed in afterwards, simulating the entailment classifier
    # returning its most generous possible answer. A case that must not verify
    # has to survive the classifier saying "supported".
    apply_support: Optional[str] = None
    notes: str = ""
    tags: tuple = field(default_factory=tuple)


# ── The suite ──

CURRENT_RULE = make_result("99 CFR § 999.10", MANDATORY_SECTION_TEXT)
PROPOSED_RULE = make_result(
    "99 CFR § 999.10", MANDATORY_SECTION_TEXT, status=SourceStatus.proposed
)
SUPERSEDED_RULE = make_result(
    "99 CFR § 999.10", MANDATORY_SECTION_TEXT, status=SourceStatus.superseded
)
HISTORICAL_RULE = make_result(
    "99 CFR § 999.10", MANDATORY_SECTION_TEXT, status=SourceStatus.historical
)
UNKNOWN_RULE = make_result(
    "99 CFR § 999.10",
    MANDATORY_SECTION_TEXT,
    status=None,
    source_type=SourceType.live_research,
)
LONG_SECTION = make_result("99 CFR § 999.45", LONG_SECTION_TEXT)
PERMISSIVE_RULE = make_result("99 CFR § 999.20", PERMISSIVE_SECTION_TEXT)
GUIDANCE = make_result(
    "Agency Effective Program Guidance",
    GUIDANCE_TEXT,
    category=SourceCategory.federal_guidance,
    part_citation=None,
    publication_date="2024-03-01",
)
NEIGHBOUR = make_result("99 CFR § 999.46", NEIGHBOUR_SECTION_TEXT)


EVAL_CASES: tuple = (
    # ── Source status ──
    EvalCase(
        name="current_rule_can_verify",
        claim="Each workforce member must be trained within 30 days of hire.",
        citation="99 CFR § 999.10",
        results=(CURRENT_RULE,),
        expect_status=VerificationStatus.verified,
        forbidden_statuses=(VerificationStatus.contradicted,),
        expect_source_status=SourceStatus.current_verified,
        apply_support="SUPPORTED",
        notes="The control case. If this fails, the gate has been made unusable rather than strict.",
        tags=("source_status",),
    ),
    EvalCase(
        name="proposed_rule_cannot_verify",
        claim="Each workforce member must be trained within 30 days of hire.",
        citation="99 CFR § 999.10",
        results=(PROPOSED_RULE,),
        expect_status=VerificationStatus.cannot_determine,
        expect_citation_exists=False,
        expect_source_status=SourceStatus.proposed,
        apply_support="SUPPORTED",
        notes="Identical text to the control; only the standing differs.",
        tags=("source_status",),
    ),
    EvalCase(
        name="superseded_rule_cannot_verify",
        claim="Each workforce member must be trained within 30 days of hire.",
        citation="99 CFR § 999.10",
        results=(SUPERSEDED_RULE,),
        expect_status=VerificationStatus.cannot_determine,
        expect_source_status=SourceStatus.superseded,
        apply_support="SUPPORTED",
        tags=("source_status",),
    ),
    EvalCase(
        name="historical_rule_cannot_verify",
        claim="Each workforce member must be trained within 30 days of hire.",
        citation="99 CFR § 999.10",
        results=(HISTORICAL_RULE,),
        expect_status=VerificationStatus.cannot_determine,
        expect_source_status=SourceStatus.historical,
        apply_support="SUPPORTED",
        tags=("source_status",),
    ),
    EvalCase(
        name="unknown_status_authority_cannot_verify",
        claim="Each workforce member must be trained within 30 days of hire.",
        citation="99 CFR § 999.10",
        results=(UNKNOWN_RULE,),
        expect_status=VerificationStatus.cannot_determine,
        expect_source_status=SourceStatus.status_unknown,
        apply_support="SUPPORTED",
        notes="A live search result with no established standing.",
        tags=("source_status",),
    ),

    # ── Citation integrity ──
    EvalCase(
        name="fabricated_citation",
        claim="Annual certification is required by 99 CFR § 999.99.",
        citation="99 CFR § 999.99",
        results=(CURRENT_RULE, LONG_SECTION),
        expect_status=VerificationStatus.unverified,
        expect_citation_exists=False,
        apply_support="SUPPORTED",
        notes="The section does not exist in the retrieved material.",
        tags=("citation",),
    ),
    EvalCase(
        name="real_citation_wrong_proposition",
        claim="Workforce training must be repeated every 90 days under 99 CFR § 999.10.",
        citation="99 CFR § 999.10",
        results=(CURRENT_RULE,),
        expect_status=VerificationStatus.partially_verified,
        apply_support=None,
        notes=(
            "The citation is real and current, but 90 days is not in it. The "
            "concrete-fact check must catch the number before entailment runs."
        ),
        tags=("citation",),
    ),
    EvalCase(
        name="neighbouring_subsection_is_not_the_cited_one",
        claim="Substitute notification must remain available for 90 days per 99 CFR § 999.45(c).",
        citation="99 CFR § 999.45(c)",
        results=(NEIGHBOUR,),
        expect_citation_exists=False,
        apply_support="SUPPORTED",
        notes=(
            "§999.46 says 90 days; the claim cites §999.45(c). A neighbouring "
            "section containing the right number must not satisfy the wrong cite."
        ),
        tags=("citation",),
    ),
    EvalCase(
        name="subsection_past_the_old_truncation_point",
        claim=(
            "The organization shall retain a copy of each notification for seven "
            "years under 99 CFR § 999.45(c)."
        ),
        citation="99 CFR § 999.45(c)",
        results=(LONG_SECTION,),
        expect_citation_exists=True,
        expect_status=VerificationStatus.verified,
        forbidden_statuses=(VerificationStatus.contradicted,),
        apply_support="SUPPORTED",
        notes=(
            "The regression case for the 3,000-character cut. Paragraph (c) "
            "begins past character 3,000 and must still be reachable."
        ),
        tags=("citation", "truncation"),
    ),

    # ── Modality ──
    EvalCase(
        name="may_described_as_must",
        claim="A covered organization must designate an additional reviewer.",
        citation="99 CFR § 999.20",
        results=(PERMISSIVE_RULE,),
        apply_support="SUPPORTED",
        notes=(
            "The source says 'may'. A classifier calling this supported must be "
            "overridden by the deterministic modality check."
        ),
        tags=("modality",),
    ),
    EvalCase(
        name="guidance_described_as_legal_requirement",
        claim="The organization is required to appoint a senior official responsible for the program.",
        citation="Agency Effective Program Guidance",
        results=(GUIDANCE,),
        apply_support="SUPPORTED",
        notes=(
            "The document says 'encouraged' and disclaims creating rights. A "
            "mandate cannot be verified from it."
        ),
        tags=("modality",),
    ),

    # ── Dates ──
    EvalCase(
        name="publication_date_is_not_an_effective_date",
        claim="The guidance took effect on 2024-03-01.",
        citation="Agency Effective Program Guidance",
        results=(GUIDANCE,),
        apply_support="SUPPORTED",
        notes=(
            "The fixture carries a publication date and no effective date. "
            "Nothing may present the first as the second."
        ),
        tags=("dates",),
    ),
)


def cases_tagged(tag: str) -> tuple:
    return tuple(c for c in EVAL_CASES if tag in c.tags)
