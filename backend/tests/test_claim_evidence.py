"""
Tests for evidence-based verification.

The failure these exist to prevent: a REAL citation attached to an
unsupported claim being reported as "verified". Citation existence is
necessary but not sufficient, and these assert that the pipeline treats it
that way.

Run: python -m pytest tests/test_claim_evidence.py -v
"""

import pytest

from app.models.schemas import ClaimSupport, VerificationStatus
from app.services.retrieval.verification import get_verification_service
from app.services.retrieval.models import (
    RetrievalContext, RetrievalResult, SourceChunk, SourceMetadata, SourceCategory,
)

REG_TEXT = (
    "45 CFR 164.530 Administrative requirements. "
    "(b) A covered entity must train all members of its workforce on the policies "
    "and procedures with respect to protected health information. "
    "(j) A covered entity must retain the documentation required by this section "
    "for 6 years from the date of its creation or the date when it last was in "
    "effect, whichever is later."
)


def _context():
    chunk = SourceChunk(
        id="c1",
        text=REG_TEXT,
        metadata=SourceMetadata(
            source_name="45 CFR Part 164 — HIPAA",
            category=SourceCategory.federal_regulation,
            citation="45 CFR § 164.530",
            url="https://www.ecfr.gov/current/title-45/part-164",
            effective_date="2026-08-06",
            collection="federal_regulation",
        ),
    )
    return RetrievalContext(
        query="q",
        retrieved_chunks=[RetrievalResult(chunk=chunk, score=0.9, query="q")],
        total_sources_found=1,
    )


@pytest.fixture
def svc():
    return get_verification_service()


def test_fabricated_citation_is_unverified(svc):
    """A citation not in retrieved material can never be verified."""
    ev = svc.build_claim_evidence(
        "f1", "Encrypt all PHI at rest.", "45 CFR § 164.999", _context()
    )
    assert ev.status == VerificationStatus.unverified
    assert ev.checks.citation_exists is False
    assert ev.source.excerpt in (None, "")


def test_real_citation_with_invented_duration_is_not_verified(svc):
    """The headline case: real citation, wrong number. Must not pass."""
    ev = svc.build_claim_evidence(
        "f2",
        "Retain documentation for 10 years as required by 45 CFR 164.530.",
        "45 CFR § 164.530",
        _context(),
    )
    assert ev.checks.citation_exists is True   # the citation IS real
    assert ev.checks.specifics_supported is False
    assert ev.status != VerificationStatus.verified
    assert "10 year" in ev.reason


def test_correct_duration_passes_the_specifics_check(svc):
    ev = svc.build_claim_evidence(
        "f3",
        "Retain documentation for 6 years as required by 45 CFR 164.530.",
        "45 CFR § 164.530",
        _context(),
    )
    assert ev.checks.specifics_supported is True


def test_citation_alone_does_not_reach_verified(svc):
    """Even a clean citation stays partially verified until the claim is tested."""
    ev = svc.build_claim_evidence(
        "f4", "Workforce training is required.", "45 CFR § 164.530", _context()
    )
    assert ev.checks.citation_exists is True
    assert ev.status == VerificationStatus.partially_verified
    assert ev.checks.claim_support == ClaimSupport.not_checked


def test_only_claim_support_promotes_to_verified(svc):
    ev = svc.build_claim_evidence(
        "f5", "Workforce training is required.", "45 CFR § 164.530", _context()
    )
    svc.apply_claim_support(ev, ClaimSupport.supported, "Excerpt requires training.")
    assert ev.status == VerificationStatus.verified


def test_not_supported_downgrades_to_unverified(svc):
    ev = svc.build_claim_evidence(
        "f6", "Annual penetration testing is required.", "45 CFR § 164.530", _context()
    )
    svc.apply_claim_support(ev, ClaimSupport.not_supported)
    assert ev.status == VerificationStatus.unverified


def test_contradicted_is_surfaced_as_contradicted(svc):
    ev = svc.build_claim_evidence(
        "f7", "Documentation need not be retained.", "45 CFR § 164.530", _context()
    )
    svc.apply_claim_support(ev, ClaimSupport.contradicted)
    assert ev.status == VerificationStatus.contradicted


def test_evidence_records_the_source_version(svc):
    """Auditability: which version of the regulation was actually checked."""
    ev = svc.build_claim_evidence(
        "f8", "Workforce training is required.", "45 CFR § 164.530", _context()
    )
    assert ev.source.version_date == "2026-08-06"
    assert ev.source.url
    assert ev.source.excerpt


def test_excerpt_is_relevant_to_the_claim(svc):
    """The excerpt must be the passage that bears on the claim."""
    ev = svc.build_claim_evidence(
        "f9", "Workforce members must be trained.", "45 CFR § 164.530", _context()
    )
    assert "train" in (ev.source.excerpt or "").lower()


def test_no_sources_at_all_is_unverified(svc):
    empty = RetrievalContext(query="q", retrieved_chunks=[], total_sources_found=0)
    ev = svc.build_claim_evidence("f10", "Anything.", "45 CFR § 164.530", empty)
    assert ev.status == VerificationStatus.unverified
    assert ev.checks.citation_exists is False


@pytest.mark.asyncio
async def test_classifier_failure_never_upgrades_status():
    """If the claim-support call fails, nothing may become verified."""
    from unittest.mock import patch
    from app.services.claim_support import classify_claim_support

    with patch("app.services.claim_support.get_provider", side_effect=RuntimeError("down")):
        result = await classify_claim_support(
            [{"id": "f1", "claim": "c", "citation": "45 CFR § 164.530", "excerpt": "e"}]
        )
    assert result == {}, "A failed classification must yield no upgrades"


def test_unknown_label_does_not_become_a_pass():
    """A malformed label must degrade to NOT_CHECKED, never to SUPPORTED."""
    from app.services.claim_support import _parse_response

    parsed = _parse_response('[{"id": "f1", "label": "DEFINITELY_FINE", "note": "x"}]')
    assert parsed["f1"]["label"] == ClaimSupport.not_checked.value
