"""
"Verified" must mean the source supports the claim.

A real citation alone is never enough. Verification requires an authoritative
excerpt and a successful claim-support check; missing evidence cannot be
promoted to a green Verified state.

Run: python -m pytest tests/test_verified_means_verified.py -v
"""

import pytest

from app.models.schemas import ClaimSupport
from app.services.retrieval.models import (
    RetrievalContext, RetrievalResult, SourceChunk, SourceMetadata,
    SourceCategory, SourceType, VerificationStatus,
)


def _context_with(citation: str, text: str, score: float = 0.95) -> RetrievalContext:
    chunk = SourceChunk(
        id="c1",
        text=text,
        metadata=SourceMetadata(
            source_name="45 CFR Part 164",
            category=SourceCategory.federal_regulation,
            citation=citation,
            collection="federal_regulation",
        ),
    )
    return RetrievalContext(
        query="breach notification",
        retrieved_chunks=[RetrievalResult(chunk=chunk, score=score, query="breach notification")],
    )


@pytest.fixture
def verifier(tmp_path, monkeypatch):
    from app.services.retrieval import store as store_module
    monkeypatch.setattr(
        store_module, "_store", store_module.ChromaStore(persist_dir=str(tmp_path / "kb"))
    )
    from app.services.retrieval.verification import VerificationService
    return VerificationService()


class TestCitationMatchIsNotVerification:
    def test_a_real_citation_alone_does_not_earn_verified(self, verifier):
        context = _context_with(
            "45 CFR § 164.404",
            "A covered entity shall notify each individual whose unsecured protected "
            "health information has been accessed without unreasonable delay and in no "
            "case later than 60 calendar days after discovery.",
        )
        attribution = verifier.create_source_attribution(
            "45 CFR § 164.404",
            context,
            claim_text="Notification must be sent within 30 days of discovery.",
        )
        assert attribution.verification_status != VerificationStatus.verified

    def test_the_located_source_is_still_reported(self, verifier):
        context = _context_with("45 CFR § 164.404", "Notification within 60 calendar days.")
        attribution = verifier.create_source_attribution("45 CFR § 164.404", context, claim_text="x")
        assert attribution.source_citation == "45 CFR § 164.404"
        assert "60 calendar days" in (attribution.retrieved_text or "")
        assert attribution.warning

    def test_high_similarity_does_not_earn_verified(self, verifier):
        context = _context_with("45 CFR § 164.404", "Breach notification requirements.", score=0.99)
        attribution = verifier.create_source_attribution("45 CFR § 164.404", context, claim_text="x")
        assert attribution.verification_status != VerificationStatus.verified

    def test_an_unknown_citation_is_unverified(self, verifier):
        attribution = verifier.create_source_attribution(
            "45 CFR § 999.999", _context_with("45 CFR § 164.404", "Something else."), claim_text="x"
        )
        assert attribution.verification_status == VerificationStatus.unverified
        assert attribution.source_type == SourceType.model_knowledge


class TestOnlyClaimSupportPromotes:
    def _evidence(self, verifier, citation_exists=True, with_excerpt=True):
        from app.models.schemas import VerificationEvidence, EvidenceChecks, EvidenceSource
        return VerificationEvidence(
            claim_id="c1",
            claim_text="Notification must be sent within 30 days of discovery.",
            checks=EvidenceChecks(citation_exists=citation_exists),
            source=EvidenceSource(
                excerpt="A covered entity must send notification within 30 days of discovery."
                if with_excerpt else None
            ),
            status=VerificationStatus.unverified,
        )

    def test_supported_plus_existing_citation_and_excerpt_is_verified(self, verifier):
        evidence = verifier.apply_claim_support(
            self._evidence(verifier), ClaimSupport.supported
        )
        assert evidence.status == VerificationStatus.verified

    def test_supported_without_a_real_citation_is_not_verified(self, verifier):
        evidence = verifier.apply_claim_support(
            self._evidence(verifier, citation_exists=False), ClaimSupport.supported
        )
        assert evidence.status != VerificationStatus.verified

    def test_supported_without_an_excerpt_is_not_verified(self, verifier):
        evidence = verifier.apply_claim_support(
            self._evidence(verifier, with_excerpt=False), ClaimSupport.supported
        )
        assert evidence.status != VerificationStatus.verified

    def test_not_supported_is_unverified(self, verifier):
        evidence = verifier.apply_claim_support(
            self._evidence(verifier), ClaimSupport.not_supported
        )
        assert evidence.status == VerificationStatus.unverified

    def test_contradicted_is_surfaced_loudly(self, verifier):
        evidence = verifier.apply_claim_support(
            self._evidence(verifier), ClaimSupport.contradicted
        )
        assert evidence.status == VerificationStatus.contradicted
        assert evidence.reason
