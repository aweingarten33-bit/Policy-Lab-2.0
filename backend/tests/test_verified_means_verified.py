"""
"Verified" must mean the source supports the claim.

The product's whole promise is that a citation was checked rather than
recalled. A second attribution path -- still live, reached from the
orchestrator and the draft service -- awarded VerificationStatus.verified as
soon as the citation *string* matched something in the knowledge base, and in
one branch simply because embedding similarity cleared 0.7.

Neither checks the claim. A fabricated "30-day deadline" hung off a real
45 CFR 164.404 matched perfectly and came back with a green Verified badge --
a real citation attached to an invented requirement, which is the single most
dangerous output this tool can produce and precisely what it exists to catch.

Citation-exists and claim-supported are different facts. Only
apply_claim_support, which compares the claim against the cited passage, may
promote anything to verified.

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
        """The exact failure: correct section number, invented deadline."""
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
        assert attribution.verification_status != VerificationStatus.verified, (
            "a citation that merely exists was marked verified — this is how an "
            "invented requirement gets a green badge"
        )

    def test_the_located_source_is_still_reported(self, verifier):
        """Downgrading the status must not throw away the evidence."""
        context = _context_with("45 CFR § 164.404", "Notification within 60 calendar days.")
        attribution = verifier.create_source_attribution("45 CFR § 164.404", context, claim_text="x")

        assert attribution.source_citation == "45 CFR § 164.404"
        assert "60 calendar days" in (attribution.retrieved_text or "")
        assert attribution.warning, "the user needs to be told what was not checked"

    def test_high_similarity_does_not_earn_verified(self, verifier):
        """Embedding distance is retrieval evidence, not verification evidence."""
        context = _context_with("45 CFR § 164.404", "Breach notification requirements.", score=0.99)
        attribution = verifier.create_source_attribution("45 CFR § 164.404", context, claim_text="x")
        assert attribution.verification_status != VerificationStatus.verified

    def test_an_unknown_citation_is_unverified(self, verifier):
        """Unchanged behaviour, guarded so the fix doesn't over-correct."""
        attribution = verifier.create_source_attribution(
            "45 CFR § 999.999", _context_with("45 CFR § 164.404", "Something else."), claim_text="x"
        )
        assert attribution.verification_status == VerificationStatus.unverified
        assert attribution.source_type == SourceType.model_knowledge


class TestOnlyClaimSupportPromotes:
    def _evidence(self, verifier, citation_exists=True):
        from app.models.schemas import VerificationEvidence, EvidenceChecks
        return VerificationEvidence(
            claim_id="c1",
            claim_text="Notification must be sent within 30 days of discovery.",
            checks=EvidenceChecks(citation_exists=citation_exists),
            status=VerificationStatus.unverified,
        )

    def test_supported_plus_existing_citation_is_verified(self, verifier):
        evidence = verifier.apply_claim_support(
            self._evidence(verifier), ClaimSupport.supported
        )
        assert evidence.status == VerificationStatus.verified

    def test_supported_without_a_real_citation_is_not_verified(self, verifier):
        evidence = verifier.apply_claim_support(
            self._evidence(verifier, citation_exists=False), ClaimSupport.supported
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
