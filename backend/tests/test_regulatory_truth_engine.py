"""Adversarial tests for the regulatory truth layer.

These cases intentionally use plausible-looking citations and facts. The goal
is not merely to find a source; it is to prevent the system from attaching a
real authority to a claim that the authority does not actually establish.
"""

from app.models.schemas import (
    AnalysisResult,
    ClaimSupport,
    ComplianceActionPackage,
    EvidenceChecks,
    EvidenceSource,
    GapRow,
    GapStatus,
    ObligationType,
    PackageStatus,
    VerificationEvidence,
)
from app.services.package_integrity import reconcile_package_verification
from app.services.retrieval.models import (
    RetrievalContext,
    RetrievalResult,
    SourceCategory,
    SourceChunk,
    SourceMetadata,
    VerificationStatus,
)
from app.services.retrieval.verification import VerificationService


def _result(
    citation: str,
    text: str,
    *,
    category: SourceCategory = SourceCategory.federal_regulation,
    jurisdiction: str = "federal",
    current: bool = True,
    score: float = 0.95,
):
    from app.services.retrieval.models import Jurisdiction

    chunk = SourceChunk(
        id=f"src-{abs(hash((citation, text))) % 100000}",
        text=text,
        metadata=SourceMetadata(
            source_name=citation,
            category=category,
            jurisdiction=Jurisdiction(jurisdiction),
            citation=citation,
            url="https://www.ecfr.gov/current/test" if category == SourceCategory.federal_regulation else None,
            effective_date="2026-08-20",
            is_current=current,
            collection=category.value,
        ),
    )
    return RetrievalResult(chunk=chunk, score=score, query="q")


def _context(*results):
    return RetrievalContext(
        query="q",
        retrieved_chunks=list(results),
        total_sources_found=len(results),
    )


def _svc():
    return VerificationService()


def test_neighboring_regulation_cannot_rescue_wrong_deadline():
    """A matching number elsewhere in retrieval must not validate this cite."""
    ctx = _context(
        _result(
            "45 CFR § 164.404",
            "A covered entity shall notify without unreasonable delay and in no case later than 60 calendar days.",
        ),
        _result(
            "45 CFR § 164.530",
            "An unrelated administrative action may be completed within 30 calendar days.",
        ),
    )
    ev = _svc().build_claim_evidence(
        "f1",
        "45 CFR 164.404 requires notification within 30 calendar days.",
        "45 CFR § 164.404",
        ctx,
    )
    assert ev.checks.citation_exists is True
    assert ev.checks.specifics_supported is False
    assert ev.status is not VerificationStatus.verified
    assert "30 day" in ev.reason.lower()


def test_nested_cfr_citation_is_not_truncated():
    cites = _svc()._extract_citations("See 29 CFR §1910.95(g)(1)(ii)(A) for the requirement.")
    assert cites == ["29 CFR §1910.95(g)(1)(ii)(A)"]


def test_wrong_subsection_does_not_count_as_citation_exists():
    ctx = _context(
        _result(
            "29 CFR § 1910.95",
            "(m)(1) The employer shall retain noise exposure measurement records for two years. ",
        )
    )
    ev = _svc().build_claim_evidence(
        "f2",
        "Paragraph (m)(2) requires this record for two years.",
        "29 CFR § 1910.95(m)(2)",
        ctx,
    )
    assert ev.checks.citation_exists is False
    assert ev.status is VerificationStatus.unverified
    assert "exact subsection" in ev.reason.lower()


def test_exact_nested_subsection_can_be_located():
    ctx = _context(
        _result(
            "29 CFR § 1910.95",
            "(m)(1) Exposure records are retained for two years. (m)(2) The employer shall retain each employee audiometric test record for the duration of the affected employee's employment.",
        )
    )
    ev = _svc().build_claim_evidence(
        "f3",
        "The employer must retain the audiometric test record for the duration of employment.",
        "29 CFR § 1910.95(m)(2)",
        ctx,
    )
    assert ev.checks.citation_exists is True
    assert ev.source.excerpt


def test_wrong_state_authority_never_matches_same_section_number():
    ctx = _context(
        _result(
            "NY Public Health Law § 18",
            "New York patient access provision.",
            category=SourceCategory.state_law,
            jurisdiction="NY",
        )
    )
    ev = _svc().build_claim_evidence(
        "f4",
        "California requires this access right.",
        "CA Public Health Law § 18",
        ctx,
    )
    assert ev.checks.citation_exists is False


def test_policy_template_cannot_prove_a_legal_requirement():
    ctx = _context(
        _result(
            "45 CFR § 164.530",
            "Template language: workforce training should occur annually.",
            category=SourceCategory.policy_template,
        )
    )
    ev = _svc().build_claim_evidence(
        "f5",
        "45 CFR 164.530 requires annual workforce training.",
        "45 CFR § 164.530",
        ctx,
    )
    assert ev.checks.citation_exists is False
    assert ev.status is VerificationStatus.unverified


def test_noncurrent_source_cannot_verify_present_requirement():
    ctx = _context(
        _result(
            "45 CFR § 164.530",
            "A covered entity must train its workforce.",
            current=False,
        )
    )
    ev = _svc().build_claim_evidence(
        "f6",
        "A covered entity must train its workforce.",
        "45 CFR § 164.530",
        ctx,
    )
    assert ev.checks.citation_exists is False
    assert ev.status is VerificationStatus.unverified
    assert "non-current" in ev.reason


def test_percent_money_age_ratio_and_distance_are_checked():
    source = (
        "The threshold is 80 percent. The maximum amount is $500. Eligibility applies at age 55. "
        "Staffing is maintained at a 1:10 ratio. The barrier shall be 25 feet from the entrance."
    )
    ctx = _context(_result("42 CFR § 999.1", source))

    correct = _svc().build_claim_evidence(
        "f7",
        "The rule sets 80%, $500, age 55, a 1:10 ratio, and 25 feet.",
        "42 CFR § 999.1",
        ctx,
    )
    assert correct.checks.specifics_supported is True

    wrong = _svc().build_claim_evidence(
        "f8",
        "The rule sets 90%, $600, age 60, a 1:12 ratio, and 30 feet.",
        "42 CFR § 999.1",
        ctx,
    )
    assert wrong.checks.specifics_supported is False
    assert wrong.status is not VerificationStatus.verified


def test_permissive_may_cannot_be_promoted_to_mandatory_must():
    ctx = _context(
        _result(
            "29 CFR § 1910.95",
            "The employer may make an allowance for protective equipment when calculating exposure.",
        )
    )
    svc = _svc()
    ev = svc.build_claim_evidence(
        "f9",
        "The employer must make an allowance for protective equipment.",
        "29 CFR § 1910.95",
        ctx,
    )
    assert ev.checks.citation_exists is True
    svc.apply_claim_support(ev, ClaimSupport.supported, "classifier attempted to pass")
    assert ev.checks.claim_support is ClaimSupport.not_supported
    assert ev.status is VerificationStatus.unverified


def test_current_exact_source_plus_supported_claim_can_verify():
    ctx = _context(
        _result(
            "45 CFR § 164.530",
            "A covered entity must train all members of its workforce on its policies and procedures.",
        )
    )
    svc = _svc()
    ev = svc.build_claim_evidence(
        "f10",
        "A covered entity must train all members of its workforce on its policies and procedures.",
        "45 CFR § 164.530",
        ctx,
    )
    svc.apply_claim_support(ev, ClaimSupport.supported, "The excerpt directly imposes the training duty.")
    assert ev.status is VerificationStatus.verified


def _row(evidence=None, obligation=ObligationType.required):
    return GapRow(
        clause="Test",
        regulations=["45 CFR § 164.530"],
        status=GapStatus.gap,
        finding="Training is legally required.",
        suggested_language="Train workforce.",
        citation="45 CFR § 164.530",
        obligation_type=obligation,
        evidence=evidence,
    )


def _package(*rows):
    return ComplianceActionPackage(
        package_id="p1",
        created_at="2026-08-20T00:00:00",
        policy_type="Test",
        gap_analysis=AnalysisResult(
            policy_type="Test",
            gap_table=list(rows),
            audit_ready_summary="summary",
        ),
        status=PackageStatus.complete,
    )


def test_api_boundary_downgrades_partially_verified_required_claim():
    ev = VerificationEvidence(
        claim_id="x",
        claim_text="Training is legally required.",
        citation="45 CFR § 164.530",
        status=VerificationStatus.partially_verified,
        source=EvidenceSource(excerpt="Related text"),
        checks=EvidenceChecks(
            citation_exists=True,
            claim_support=ClaimSupport.partially_supported,
        ),
    )
    package = reconcile_package_verification(_package(_row(ev)))
    row = package.gap_analysis.gap_table[0]
    assert row.obligation_type is ObligationType.unverified_requirement
    assert row.obligation_note


def test_api_boundary_downgrades_required_claim_when_verification_never_ran():
    package = reconcile_package_verification(_package(_row(None)))
    row = package.gap_analysis.gap_table[0]
    assert row.obligation_type is ObligationType.unverified_requirement
    assert package.unverified_claim_count == 1


def test_api_boundary_keeps_fully_verified_requirement():
    ev = VerificationEvidence(
        claim_id="x",
        claim_text="Training is legally required.",
        citation="45 CFR § 164.530",
        status=VerificationStatus.verified,
        source=EvidenceSource(excerpt="A covered entity must train all members of its workforce."),
        checks=EvidenceChecks(
            citation_exists=True,
            claim_support=ClaimSupport.supported,
        ),
    )
    package = reconcile_package_verification(_package(_row(ev)))
    row = package.gap_analysis.gap_table[0]
    assert row.obligation_type is ObligationType.required
    assert package.unverified_claim_count == 0
