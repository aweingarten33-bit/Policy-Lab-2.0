"""Can Policy Lab's verification run on OpenContracts authority data?

A compatibility proof, not a migration. The question is narrow and worth
stating precisely: Policy Lab's fail-closed verification rules are the part of
this product that is worth keeping, and they were written against a homemade
substrate -- Chroma chunks plus a SQLite section store. If those rules only
hold because of how that substrate happens to behave, the substrate cannot be
replaced and the rules are not really rules. If they hold identically against
an authority corpus owned by OpenContracts, the substrate is disposable.

So every assertion below is about a verdict, and every verdict is reached by
the existing pipeline with nothing swapped except where the authority came
from:

    citation -> OpenContracts canonical key -> their candidate_keys
      -> authority document + as_document_metadata()
      -> derive_source_status()          (their 15 publisher states -> our 5)
      -> _source_scope_text()            (is the cited paragraph actually here)
      -> specifics check                 (does the number in the claim appear)
      -> apply_claim_support()           (the single path to `verified`)

The five results this has to produce:

  A  a current authority + a claim its text supports        -> VERIFIED
  B  a citation to a section that does not exist            -> never verified
  C  proposed / superseded / unknown-standing authority     -> never a present duty
  D  a concrete number the authority does not state         -> never verified
  E  the authority text changes underneath a remembered
     verdict                                                -> not reused

Each of B through E is fed ClaimSupport.supported deliberately -- that is the
adversarial input. It stands in for a semantic classifier that has been talked
into agreeing, and the test is whether the deterministic gates hold anyway.

Running it
----------
    OPENCONTRACTS_SRC=/path/to/OpenContracts \\
        python -m pytest tests/spikes/test_opencontracts_authority_spike.py -v

Without OPENCONTRACTS_SRC the module skips, which is what happens in CI.
"""

from datetime import date

import pytest

from tests.spikes.opencontracts import oc_bootstrap

pytestmark = pytest.mark.skipif(
    not oc_bootstrap.available(),
    reason="set OPENCONTRACTS_SRC to an OpenContracts checkout to run the spike",
)

if oc_bootstrap.available():
    oc_bootstrap.boot()

from app.models.schemas import (  # noqa: E402
    ClaimSupport,
    SourceStatus,
    VerificationStatus,
)
from app.services.retrieval.models import (  # noqa: E402
    RetrievalContext,
    RetrievalResult,
    SourceChunk,
)
from app.services.retrieval.opencontracts_provider import (  # noqa: E402
    OpenContractsAuthorityProvider,
)
from app.services.retrieval.verification import VerificationService  # noqa: E402
from tests.spikes.opencontracts import fixtures  # noqa: E402
from tests.spikes.opencontracts.authority_corpus import (  # noqa: E402
    AuthorityFixture,
    InProcessAuthorityCorpus,
)

CITATION = "45 CFR § 164.404"
CITATION_A = "45 CFR § 164.404(a)"
CITATION_B = "45 CFR § 164.404(b)"

# The obligation under test comes from the sample hospital policy: section 3.2
# says affected individuals are "notified promptly where notification is
# required", and the analysis reports that as a gap against the breach
# notification rule. Deliberately a claim whose entire support is one
# provision -- a claim needing several sections would not isolate the question.
SAMPLE_POLICY_CLAUSE = (
    "3.2 Affected individuals are notified promptly where notification is required."
)
SUPPORTED_CLAIM = (
    "The policy does not require individual notification following discovery of a "
    "breach. A covered entity must notify each individual whose unsecured protected "
    "health information has been accessed, acquired, used, or disclosed as a result "
    "of the breach."
)


# ── harness ──

def _corpus_with(*loaded: AuthorityFixture) -> InProcessAuthorityCorpus:
    corpus = InProcessAuthorityCorpus()
    for fixture in loaded:
        corpus.load(fixture)
    return corpus


def _current_404(**overrides) -> AuthorityFixture:
    base = dict(
        canonical_key="cfr-45:164.404",
        title="45 CFR 164.404 — Notification to individuals",
        xml=fixtures.SECTION_164_404,
        status="CURRENT",
        effective_from=date(2013, 9, 23),
        current_version=True,
    )
    base.update(overrides)
    return AuthorityFixture(**base)


def _verifier(corpus: InProcessAuthorityCorpus) -> VerificationService:
    """A verifier whose authority comes from OpenContracts and nowhere else.

    Assigned after construction because the provider needs the verifier: scope
    resolution and citation matching are Policy Lab rules and are called back
    into rather than reimplemented, so the two hold a reference to each other
    by design.
    """
    service = VerificationService()
    service._authority = OpenContractsAuthorityProvider(service, corpus)
    return service


def _context(corpus: InProcessAuthorityCorpus, verifier, *keys: str) -> RetrievalContext:
    """The retrieval context, populated from the OpenContracts corpus.

    Retrieval still finds candidate passages semantically -- that is not what
    is being replaced. What matters here is that the material verification sees
    carries OpenContracts provenance rather than ours.
    """
    provider = verifier._authority
    results = []
    for key in keys:
        doc = corpus.get_authority_document(key)
        meta_dict = doc["metadata"]
        metadata = provider._source_metadata(meta_dict, "", doc["text"])
        results.append(
            RetrievalResult(
                chunk=SourceChunk(id=f"oc:{key}", text=doc["text"], metadata=metadata),
                score=0.82,
                query=SAMPLE_POLICY_CLAUSE,
            )
        )
    return RetrievalContext(
        query=SAMPLE_POLICY_CLAUSE,
        retrieved_chunks=results,
        total_sources_found=len(results),
    )


def _verify(corpus, citation, claim, support=ClaimSupport.supported):
    """Run one claim through the whole existing path and return the record."""
    verifier = _verifier(corpus)
    ctx = _context(corpus, verifier, *corpus._docs.keys())
    evidence = verifier.build_claim_evidence(
        claim_id="finding-1", claim_text=claim, citation=citation, retrieval_context=ctx
    )
    if evidence.checks.citation_exists and evidence.source.excerpt:
        verifier.apply_claim_support(evidence, support, "the excerpt supports the claim")
    return evidence


# ── A. the authority resolves and the claim verifies ──

class TestAVerifiedAgainstOpenContracts:
    def test_the_authority_is_resolved_through_opencontracts(self):
        corpus = _corpus_with(_current_404())
        evidence = _verify(corpus, CITATION_A, SUPPORTED_CLAIM)

        assert evidence.checks.citation_exists is True
        assert evidence.source.excerpt, "no authoritative passage came back"
        assert "notify each individual" in evidence.source.excerpt

    def test_a_supported_claim_on_a_current_provision_verifies(self):
        corpus = _corpus_with(_current_404())
        evidence = _verify(corpus, CITATION_A, SUPPORTED_CLAIM)

        assert evidence.status is VerificationStatus.verified, evidence.reason
        assert evidence.checks.source_status is SourceStatus.current_verified
        assert evidence.checks.source_status_current is True
        assert evidence.checks.source_is_binding_law is True

    def test_the_dates_stay_the_dates_they_are(self):
        """OpenContracts' effective_from is an effective date and nothing else.

        The Phase 0 rule that a retrieval timestamp must never be presented as
        an effective date is a Policy Lab rule, and it has to survive a change
        of substrate or the substrate is dictating the semantics.
        """
        corpus = _corpus_with(_current_404())
        evidence = _verify(corpus, CITATION_A, SUPPORTED_CLAIM)

        assert evidence.source.effective_date == "2013-09-23"
        assert evidence.source.retrieved_date != evidence.source.effective_date

    def test_opencontracts_resolved_the_key_not_us(self):
        """The subsection roll-up is theirs: cfr-45:164.404(a) -> cfr-45:164.404."""
        from opencontractserver.enrichment.authorities import candidate_keys

        from app.services.retrieval.authority_source import canonical_key_for

        assert canonical_key_for(CITATION_A) == "cfr-45:164.404(a)"
        assert candidate_keys("cfr-45:164.404(a)") == [
            "cfr-45:164.404(a)",
            "cfr-45:164.404",
        ]


# ── B. a citation to nothing ──

class TestBAFabricatedCitationCannotVerify:
    def test_a_nonexistent_section_resolves_to_nothing(self):
        corpus = _corpus_with(_current_404())
        evidence = _verify(corpus, "45 CFR § 164.9999", SUPPORTED_CLAIM)

        assert evidence.checks.citation_exists is False
        assert evidence.status is not VerificationStatus.verified
        assert not evidence.source.excerpt

    def test_a_nonexistent_subsection_of_a_real_section_cannot_verify(self):
        """The section is real, the paragraph is not. Their key roll-up would
        happily answer with the section; the scope check is what stops it."""
        corpus = _corpus_with(_current_404())
        evidence = _verify(corpus, "45 CFR § 164.404(z)(9)", SUPPORTED_CLAIM)

        assert evidence.status is not VerificationStatus.verified
        assert evidence.checks.citation_exists is False


# ── C. standing ──

class TestCOnlyACurrentAuthorityEstablishesADuty:
    """Each case is a different publisher fact arriving from OpenContracts, and
    each has to land somewhere other than CURRENT_VERIFIED."""

    def test_a_proposed_rule_cannot_establish_a_present_duty(self):
        corpus = _corpus_with(_current_404(status="PROPOSED", current_version=None))
        evidence = _verify(corpus, CITATION_A, SUPPORTED_CLAIM)

        assert evidence.checks.source_status is SourceStatus.proposed
        assert evidence.status is VerificationStatus.cannot_determine
        assert evidence.status is not VerificationStatus.verified

    def test_federal_register_provenance_beats_a_declared_status(self):
        """A section pulled out of an NPRM is a proposal even when the record
        says CURRENT. Provenance is checked before the status field precisely
        so a mislabelled feed cannot promote a proposal into law."""
        corpus = _corpus_with(
            _current_404(
                status="CURRENT",
                version_label="Notice of Proposed Rulemaking, 89 FR 12345",
            )
        )
        evidence = _verify(corpus, CITATION_A, SUPPORTED_CLAIM)

        assert evidence.checks.source_status is SourceStatus.proposed
        assert evidence.status is not VerificationStatus.verified

    def test_a_superseded_version_cannot_establish_a_present_duty(self):
        corpus = _corpus_with(
            _current_404(current_version=False, superseded_by_key="cfr-45:164.404")
        )
        evidence = _verify(corpus, CITATION_A, SUPPORTED_CLAIM)

        assert evidence.checks.source_status is SourceStatus.superseded
        assert evidence.status is not VerificationStatus.verified

    def test_an_expired_provision_cannot_establish_a_present_duty(self):
        corpus = _corpus_with(_current_404(status="EXPIRED"))
        evidence = _verify(corpus, CITATION_A, SUPPORTED_CLAIM)

        assert evidence.checks.source_status is SourceStatus.superseded
        assert evidence.status is not VerificationStatus.verified

    def test_a_withdrawn_provision_is_historical(self):
        corpus = _corpus_with(_current_404(status="WITHDRAWN"))
        evidence = _verify(corpus, CITATION_A, SUPPORTED_CLAIM)

        assert evidence.checks.source_status is SourceStatus.historical
        assert evidence.status is not VerificationStatus.verified

    def test_enacted_is_not_the_same_as_in_force(self):
        """"It was enacted" says nothing about whether it has since been
        amended. It is not enough, and it must not silently become enough."""
        corpus = _corpus_with(_current_404(status="ENACTED"))
        evidence = _verify(corpus, CITATION_A, SUPPORTED_CLAIM)

        assert evidence.checks.source_status is SourceStatus.status_unknown
        assert evidence.status is not VerificationStatus.verified

    def test_opencontracts_itself_refuses_a_status_it_does_not_know(self):
        """Worth recording, because it is a guarantee inherited rather than
        built: their record class validates the status at construction, so an
        unknown state cannot enter the corpus through the normal path at all."""
        from opencontractserver.enrichment.authority_sources import (
            normalize_source_status,
        )

        with pytest.raises(ValueError):
            normalize_source_status("PROVISIONALLY_RATIFIED")

    def test_an_unrecognised_publisher_state_is_unknown_not_current(self):
        """Their vocabulary will grow as they add publishers, and a Document's
        stored metadata outlives the version that wrote it -- so a state this
        product has never reasoned about will eventually arrive. It must not
        inherit the benefit of the doubt.

        Asserted against the mapping directly, because the record class above
        will not let such a value through to build a corpus fixture from."""
        from app.services.retrieval.authority_source import derive_source_status

        future_state = {
            "status": "PROVISIONALLY_RATIFIED",
            "current_version": True,
            "effective_from": "2013-09-23",
            "instrument_type": "REGULATION",
        }
        assert derive_source_status(future_state) is SourceStatus.status_unknown

    def test_current_without_a_stated_effective_date_is_unknown(self):
        """OpenContracts marks this itself, in as_document_metadata, as
        effective_date_review_status=UNKNOWN_NEEDS_REVIEW. Policy Lab honours
        their own caution instead of overriding it."""
        corpus = _corpus_with(_current_404(effective_from=None))
        doc = corpus.get_authority_document("cfr-45:164.404")
        assert doc["metadata"]["effective_date_review_status"] == "UNKNOWN_NEEDS_REVIEW"

        evidence = _verify(corpus, CITATION_A, SUPPORTED_CLAIM)
        assert evidence.checks.source_status is SourceStatus.status_unknown
        assert evidence.status is not VerificationStatus.verified

    def test_guidance_is_not_law_however_current_it_is(self):
        """Their authority_type carries the document class, and a guidance
        document cannot create an obligation no matter how current it is."""
        corpus = _corpus_with(
            _current_404(authority_type="guidance", instrument_type="FAQ")
        )
        evidence = _verify(corpus, CITATION_A, SUPPORTED_CLAIM)

        assert evidence.checks.source_is_binding_law is False
        assert evidence.status is not VerificationStatus.verified


# ── D. concrete facts ──

class TestDAnUnstatedNumberCannotVerify:
    def test_a_deadline_the_authority_does_not_state_is_not_verified(self):
        """The provision says 60 calendar days. The claim says 30. A semantic
        classifier reading both as "about notification timing" is exactly how
        this gets through, so the number is checked deterministically."""
        corpus = _corpus_with(_current_404())
        evidence = _verify(
            corpus,
            CITATION_B,
            "Notification to affected individuals must be provided without unreasonable "
            "delay and in no case later than 30 calendar days after discovery of a breach.",
        )

        assert evidence.checks.specifics_supported is False
        assert evidence.status is not VerificationStatus.verified

    def test_the_deadline_the_authority_does_state_is_verified(self):
        """The control. If 60 also failed, the check above would prove nothing
        except that the specifics gate rejects everything."""
        corpus = _corpus_with(_current_404())
        evidence = _verify(
            corpus,
            CITATION_B,
            "Notification to affected individuals must be provided without unreasonable "
            "delay and in no case later than 60 calendar days after discovery of a breach.",
        )

        assert evidence.checks.specifics_supported is True
        assert evidence.status is VerificationStatus.verified, evidence.reason


# ── E. memory does not outlive the text it was derived from ──

class TestEChangedAuthorityInvalidatesMemory:
    def _memory(self, tmp_path, monkeypatch):
        from app.config import settings
        from app.services.retrieval import obligation_memory

        monkeypatch.setattr(settings, "obligation_memory_enabled", True, raising=False)
        monkeypatch.setattr(settings, "kb_persist_dir", str(tmp_path), raising=False)
        monkeypatch.setattr(obligation_memory, "_memory", None, raising=False)
        return obligation_memory.ObligationMemory(str(tmp_path / "memory.sqlite3"))

    def test_a_verified_obligation_is_remembered_and_recalled(self, tmp_path, monkeypatch):
        memory = self._memory(tmp_path, monkeypatch)
        corpus = _corpus_with(_current_404())
        evidence = _verify(corpus, CITATION_A, SUPPORTED_CLAIM)
        assert evidence.status is VerificationStatus.verified

        assert memory.remember(evidence, SUPPORTED_CLAIM) is True
        assert memory.recall(
            SUPPORTED_CLAIM, CITATION_A, evidence.checks.source_fingerprint
        ) is not None

    def test_amended_authority_text_makes_the_memory_unreachable(self, tmp_path, monkeypatch):
        """Not stale-but-reachable -- unreachable. The fingerprint of the exact
        authority text is part of the key, so when OpenContracts serves amended
        text the old verdict cannot be looked up at all."""
        memory = self._memory(tmp_path, monkeypatch)
        corpus = _corpus_with(_current_404())
        before = _verify(corpus, CITATION_A, SUPPORTED_CLAIM)
        memory.remember(before, SUPPORTED_CLAIM)

        corpus.replace_text("cfr-45:164.404", fixtures.SECTION_164_404_AMENDED)
        after = _verify(corpus, CITATION_A, SUPPORTED_CLAIM)

        assert after.checks.source_fingerprint != before.checks.source_fingerprint
        assert memory.recall(
            SUPPORTED_CLAIM, CITATION_A, after.checks.source_fingerprint
        ) is None

    def test_a_claim_that_the_amended_text_no_longer_supports_is_re_examined(
        self, tmp_path, monkeypatch
    ):
        """The amendment moves the deadline from 60 days to 30. A verdict
        remembered against the old text must not carry the old number forward."""
        memory = self._memory(tmp_path, monkeypatch)
        claim = (
            "Notification to affected individuals must be provided without unreasonable "
            "delay and in no case later than 60 calendar days after discovery of a breach."
        )
        corpus = _corpus_with(_current_404())
        before = _verify(corpus, CITATION_B, claim)
        assert before.status is VerificationStatus.verified
        assert memory.remember(before, claim) is True

        corpus.replace_text("cfr-45:164.404", fixtures.SECTION_164_404_AMENDED)
        after = _verify(corpus, CITATION_B, claim)

        assert memory.recall(claim, CITATION_B, after.checks.source_fingerprint) is None
        assert after.checks.specifics_supported is False
        assert after.status is not VerificationStatus.verified

    def test_an_unverified_result_never_becomes_memory(self, tmp_path, monkeypatch):
        memory = self._memory(tmp_path, monkeypatch)
        corpus = _corpus_with(_current_404(status="PROPOSED", current_version=None))
        evidence = _verify(corpus, CITATION_A, SUPPORTED_CLAIM)

        assert memory.remember(evidence, SUPPORTED_CLAIM) is False
        assert memory.count() == 0
