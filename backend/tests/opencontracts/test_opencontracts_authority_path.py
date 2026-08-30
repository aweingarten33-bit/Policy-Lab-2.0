"""The production federal-CFR authority path: OpenContracts fetches, Policy Lab proves.

One job, one owner. OpenContracts resolves a citation to an authority document
and supplies its text and the publisher's facts about it. Policy Lab decides
whether a present legal obligation may rest on that document, and nothing
reaches VERIFIED until every deterministic gate has passed.

These tests drive the real production objects -- ``CFRAuthorityClient``,
``OpenContractsAuthorityProvider``, ``VerificationService``,
``PackageOrchestrator._build_evidence``, ``ObligationMemory`` -- with exactly
one thing replaced: the eCFR HTTP call. Everything downstream of the returned
bytes is the code that runs in production, including OpenContracts' own parser,
record class and key resolver.

The end-to-end shape under test:

    policy text
      -> AI proposes a requirement            (the model, stubbed)
      -> citation resolved by OpenContracts   (their candidate_keys)
      -> authoritative text from OpenContracts (their CFR provider + record)
      -> Policy Lab status / scope / specifics gates
      -> semantic entailment
      -> VERIFIED only if every gate passed, otherwise fail closed

Run: OPENCONTRACTS_SRC=/path/to/OpenContracts python -m pytest tests/opencontracts -v
Without OPENCONTRACTS_SRC the module skips, which is what happens in CI.
"""

import asyncio

import pytest

from app.config import settings
from app.services.retrieval import opencontracts_runtime as ocr


def _runtime_available() -> bool:
    """Whether OpenContracts can be imported here, without caching a No.

    ``ocr.available()`` memoises its answer for the process, and a skip check
    that ran before the test settings were applied would poison it for every
    test that follows.
    """
    import os

    src = (settings.opencontracts_src or os.environ.get("OPENCONTRACTS_SRC") or "").strip()
    return bool(src and os.path.isdir(os.path.join(src, "opencontractserver")))


pytestmark = pytest.mark.skipif(
    not _runtime_available(),
    reason="set OPENCONTRACTS_SRC to an OpenContracts checkout to run the authority path tests",
)

from app.models.schemas import (  # noqa: E402
    AnalysisResult,
    ClaimSupport,
    GapRow,
    GapStatus,
    ObligationType,
    SourceStatus,
    VerificationStatus,
)
from app.services.retrieval.authority_source import (  # noqa: E402
    ChromaAuthorityProvider,
    LegacyFallbackAuthorityProvider,
    canonical_key_for,
    get_authority_provider,
)
from app.services.retrieval.models import (  # noqa: E402
    Jurisdiction,
    RetrievalContext,
    RetrievalResult,
    SourceCategory,
    SourceChunk,
    SourceMetadata,
)
from app.services.retrieval.opencontracts_client import CFRAuthorityClient  # noqa: E402
from app.services.retrieval.opencontracts_provider import (  # noqa: E402
    OpenContractsAuthorityProvider,
)
from app.services.retrieval.opencontracts_store import OpenContractsStore  # noqa: E402
from app.services.retrieval.verification import VerificationService  # noqa: E402
from tests.opencontracts import ecfr_stub  # noqa: E402
from tests.opencontracts.ecfr_stub import FakeECFR, serving  # noqa: E402

CITATION_404A = "45 CFR § 164.404(a)"
CITATION_404B = "45 CFR § 164.404(b)"
CITATION_316 = "45 CFR § 164.316(b)(2)(i)"

# From the sample hospital policy: "3.2 Affected individuals are notified
# promptly where notification is required." The analysis reports that as a gap
# against the breach notification rule. One provision carries the whole claim.
POLICY_CLAUSE = "3.2 Affected individuals are notified promptly where notification is required."
NOTIFICATION_CLAIM = (
    "The policy does not require individual notification following discovery of a breach. "
    "A covered entity must notify each individual whose unsecured protected health "
    "information has been accessed, acquired, used, or disclosed as a result of the breach."
)


# ── harness ──

@pytest.fixture(autouse=True)
def _oc_settings(tmp_path, monkeypatch):
    """Point the runtime at the checkout and give each test its own cache."""
    import os

    src = (settings.opencontracts_src or os.environ.get("OPENCONTRACTS_SRC") or "").strip()
    monkeypatch.setattr(settings, "opencontracts_src", src, raising=False)
    monkeypatch.setattr(settings, "authority_provider", "opencontracts", raising=False)
    monkeypatch.setattr(settings, "authority_fetch_enabled", True, raising=False)
    monkeypatch.setattr(settings, "kb_persist_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(settings, "ecfr_snapshot_date", "2026-01-02", raising=False)
    ocr.reset_for_tests()
    assert ocr.available(), f"OpenContracts runtime unavailable: {ocr.unavailable_reason()}"
    yield
    ocr.reset_for_tests()


def _client(tmp_path) -> CFRAuthorityClient:
    return CFRAuthorityClient(store=OpenContractsStore(str(tmp_path / "oc.sqlite3")))


def _verifier(client) -> VerificationService:
    """A verifier whose authority is OpenContracts and nothing else.

    Assigned after construction because the provider needs the verifier back:
    subsection scope resolution and citation matching are Policy Lab rules, so
    the provider calls into them rather than owning a second copy.
    """
    service = VerificationService()
    service._authority = OpenContractsAuthorityProvider(service, client)
    return service


def _context(*chunks: RetrievalResult) -> RetrievalContext:
    return RetrievalContext(
        query=POLICY_CLAUSE, retrieved_chunks=list(chunks), total_sources_found=len(chunks)
    )


def _retrieved(verifier, client, citation: str, score: float = 0.8) -> RetrievalResult:
    """A retrieval hit backed by the OpenContracts document for this citation.

    Retrieval still finds candidate passages; that job is not being replaced.
    What changed is that the material carries OpenContracts provenance.
    """
    doc = client.get_authority_document(canonical_key_for(citation).split("(")[0])
    assert doc is not None, f"OpenContracts did not serve {citation}"
    provider = getattr(verifier._authority, "_primary", verifier._authority)
    metadata = provider._source_metadata(doc["metadata"], citation, doc["text"])
    return RetrievalResult(
        chunk=SourceChunk(id=f"oc:{citation}", text=doc["text"], metadata=metadata),
        score=score,
        query=POLICY_CLAUSE,
    )


def _verify(client, citation, claim, *, support=ClaimSupport.supported, context=None):
    """One claim through the whole path, returning the evidence record."""
    verifier = _verifier(client)
    ctx = context if context is not None else _context(_retrieved(verifier, client, citation))
    evidence = verifier.build_claim_evidence(
        claim_id="finding-1", claim_text=claim, citation=citation, retrieval_context=ctx
    )
    if evidence.checks.citation_exists and evidence.source.excerpt:
        verifier.apply_claim_support(evidence, support, "the excerpt supports the claim")
    return evidence


def _serving_404(amendment_date="2013-01-25", xml=None, removed=False) -> FakeECFR:
    return FakeECFR().serve(
        "164.404", xml or ecfr_stub.SECTION_164_404,
        amendment_date=amendment_date, removed=removed,
    )


# ── 1-2. OpenContracts owns citation resolution ──

class TestOpenContractsResolvesTheCitation:
    def test_a_real_cfr_citation_resolves_to_an_opencontracts_document(self, tmp_path):
        client = _client(tmp_path)
        with serving(_serving_404()):
            doc = client.get_authority_document("cfr-45:164.404")

        assert doc is not None
        assert "notify each individual" in doc["text"]
        meta = doc["metadata"]
        # Their record, their projection: canonical key, content hash, status
        # vocabulary and the review marker are all OpenContracts' output.
        assert meta["canonical_key"] == "cfr-45:164.404"
        assert meta["citation"] == "45 CFR 164.404"
        assert meta["status"] == "CURRENT"
        assert meta["content_hash"]
        assert meta["effective_from"] == "2013-01-25"

    def test_subsection_rollup_uses_opencontracts_candidate_keys(self, tmp_path):
        """A subsection citation resolves to its section because OpenContracts
        says that is the resolution order -- not because Policy Lab has its own
        copy of the rule."""
        from opencontractserver.enrichment.authorities import candidate_keys

        assert canonical_key_for(CITATION_316) == "cfr-45:164.316(b)(2)(i)"
        assert candidate_keys("cfr-45:164.316(b)(2)(i)") == [
            "cfr-45:164.316(b)(2)(i)",
            "cfr-45:164.316",
        ]

        client = _client(tmp_path)
        fake = FakeECFR().serve("164.316", ecfr_stub.SECTION_164_316)
        with serving(fake):
            evidence = _verify(
                client, CITATION_316,
                "Documentation must be retained for 6 years from the date of its creation.",
            )
        assert evidence.checks.citation_exists is True
        assert evidence.status is VerificationStatus.verified, evidence.reason

    def test_resolution_no_longer_depends_on_what_retrieval_happened_to_find(self, tmp_path):
        """Retrieval finding nothing used to end verification, because on the
        legacy substrate the retrieved chunks WERE the authority. They are not
        any more, and the guard that assumed so is gone."""
        client = _client(tmp_path)
        with serving(_serving_404()):
            verifier = _verifier(client)
            evidence = verifier.build_claim_evidence(
                claim_id="f1", claim_text=NOTIFICATION_CLAIM, citation=CITATION_404A,
                retrieval_context=_context(),  # nothing retrieved
            )
            verifier.apply_claim_support(evidence, ClaimSupport.supported, "supports")

        assert evidence.checks.citation_exists is True
        assert evidence.status is VerificationStatus.verified, evidence.reason

    def test_an_empty_context_still_fails_a_citation_opencontracts_lacks(self, tmp_path):
        """Nothing was loosened: a citation the substrate does not hold lands on
        the same failure it always did."""
        client = _client(tmp_path)
        with serving(FakeECFR()):
            verifier = _verifier(client)
            evidence = verifier.build_claim_evidence(
                claim_id="f1", claim_text=NOTIFICATION_CLAIM, citation=CITATION_404A,
                retrieval_context=_context(),
            )
        assert evidence.checks.citation_exists is False
        assert evidence.status is not VerificationStatus.verified

    def test_the_authority_is_fetched_once_and_cached(self, tmp_path):
        client = _client(tmp_path)
        fake = _serving_404()
        with serving(fake):
            client.get_authority_document("cfr-45:164.404")
            client.get_authority_document("cfr-45:164.404")
        assert fake.full_text_calls == 1


# ── 3. a current authority can reach VERIFIED ──

class TestCorrectCurrentAuthorityVerifies:
    def test_a_supported_claim_on_a_current_provision_verifies(self, tmp_path):
        client = _client(tmp_path)
        with serving(_serving_404()):
            evidence = _verify(client, CITATION_404A, NOTIFICATION_CLAIM)

        assert evidence.status is VerificationStatus.verified, evidence.reason
        assert evidence.checks.source_status is SourceStatus.current_verified
        assert evidence.checks.source_status_current is True
        assert evidence.checks.source_is_binding_law is True
        assert "notify each individual" in evidence.source.excerpt

    def test_the_effective_date_is_the_publishers_not_the_fetch_date(self, tmp_path):
        """eCFR's amendment date is an effective date. The day we fetched is
        not, and the two must never be the same field -- a freshly indexed
        section looking like freshly effective law is exactly the failure this
        separation exists to prevent."""
        client = _client(tmp_path)
        with serving(_serving_404(amendment_date="2013-01-25")):
            evidence = _verify(client, CITATION_404A, NOTIFICATION_CLAIM)

        assert evidence.source.effective_date == "2013-01-25"
        assert evidence.source.effective_date != settings.ecfr_snapshot_date


# ── 4-5. citations that should not resolve ──

class TestCitationsThatMustNotVerify:
    def test_a_fabricated_section_does_not_verify(self, tmp_path):
        client = _client(tmp_path)
        verifier = _verifier(client)
        with serving(_serving_404()):
            real = _retrieved(verifier, client, CITATION_404A)
            evidence = _verify(
                client, "45 CFR § 164.9999", NOTIFICATION_CLAIM, context=_context(real)
            )

        assert evidence.checks.citation_exists is False
        assert evidence.status is not VerificationStatus.verified
        assert not evidence.source.excerpt

    def test_a_real_section_with_a_wrong_subsection_does_not_verify(self, tmp_path):
        """OpenContracts' rollup answers with the section, correctly. Policy
        Lab's scope check is what refuses a paragraph that is not in it."""
        client = _client(tmp_path)
        with serving(_serving_404()):
            evidence = _verify(client, "45 CFR § 164.404(z)(9)", NOTIFICATION_CLAIM)

        assert evidence.status is not VerificationStatus.verified
        assert evidence.checks.citation_exists is False


# ── 6-8. standing ──

class TestStandingGatesSurviveTheMigration:
    """OpenContracts reports what the publisher said. Whether a present duty may
    rest on it is decided here, and only CURRENT_VERIFIED clears."""

    def _with_status(self, tmp_path, **meta_overrides):
        client = _client(tmp_path)
        with serving(_serving_404()):
            doc = client.get_authority_document("cfr-45:164.404")
        doc["metadata"].update(meta_overrides)
        client._store.put("cfr-45:164.404", doc["text"], doc["metadata"])
        return _verify(client, CITATION_404A, NOTIFICATION_CLAIM)

    def test_a_proposed_rule_cannot_establish_a_present_duty(self, tmp_path):
        evidence = self._with_status(tmp_path, status="PROPOSED")
        assert evidence.checks.source_status is SourceStatus.proposed
        assert evidence.status is VerificationStatus.cannot_determine

    def test_federal_register_provenance_beats_a_declared_status(self, tmp_path):
        evidence = self._with_status(
            tmp_path, status="CURRENT",
            version_label="Notice of Proposed Rulemaking, 89 FR 12345",
        )
        assert evidence.checks.source_status is SourceStatus.proposed
        assert evidence.status is not VerificationStatus.verified

    def test_a_superseded_version_cannot_establish_a_present_duty(self, tmp_path):
        evidence = self._with_status(
            tmp_path, current_version=False, superseded_by_key="cfr-45:164.404"
        )
        assert evidence.checks.source_status is SourceStatus.superseded
        assert evidence.status is not VerificationStatus.verified

    def test_an_expired_provision_cannot_establish_a_present_duty(self, tmp_path):
        evidence = self._with_status(tmp_path, status="EXPIRED")
        assert evidence.checks.source_status is SourceStatus.superseded
        assert evidence.status is not VerificationStatus.verified

    def test_an_unmapped_publisher_status_is_unknown_not_current(self, tmp_path):
        """Their vocabulary grows as they add publishers, and stored metadata
        outlives the version that wrote it. A state never reasoned about here
        does not get the benefit of the doubt."""
        evidence = self._with_status(tmp_path, status="PROVISIONALLY_RATIFIED")
        assert evidence.checks.source_status is SourceStatus.status_unknown
        assert evidence.status is not VerificationStatus.verified

    def test_a_section_with_no_amendment_date_is_unknown(self, tmp_path):
        """eCFR gave no amendment date, so OpenContracts marks the record
        UNKNOWN_NEEDS_REVIEW itself. Policy Lab honours their caution rather
        than filling the gap with the date it happened to fetch on."""
        client = _client(tmp_path)
        with serving(_serving_404(amendment_date=None)):
            doc = client.get_authority_document("cfr-45:164.404")
            assert doc["metadata"]["effective_date_review_status"] == "UNKNOWN_NEEDS_REVIEW"
            evidence = _verify(client, CITATION_404A, NOTIFICATION_CLAIM)

        assert evidence.checks.source_status is SourceStatus.status_unknown
        assert evidence.status is not VerificationStatus.verified

    def test_a_section_ecfr_reports_removed_is_not_served_at_all(self, tmp_path):
        client = _client(tmp_path)
        with serving(_serving_404(removed=True)):
            assert client.get_authority_document("cfr-45:164.404") is None


# ── 9. guidance is not law ──

class TestGuidanceCannotProveARegulation:
    def test_a_guidance_document_cannot_establish_a_legal_requirement(self, tmp_path):
        """Read from the authority type OpenContracts carries, not from the
        document's wording -- guidance quotes statutes and uses "must" freely
        while disclaiming any obligation of its own."""
        client = _client(tmp_path)
        with serving(_serving_404()):
            doc = client.get_authority_document("cfr-45:164.404")
        doc["metadata"].update({"authority_type": "guidance", "instrument_type": "FAQ"})
        client._store.put("cfr-45:164.404", doc["text"], doc["metadata"])

        evidence = _verify(client, CITATION_404A, NOTIFICATION_CLAIM)
        assert evidence.checks.source_is_binding_law is False
        assert evidence.status is not VerificationStatus.verified


# ── 10. concrete facts ──

class TestUnstatedNumbersFailClosed:
    def test_a_deadline_the_authority_does_not_state_is_not_verified(self, tmp_path):
        client = _client(tmp_path)
        with serving(_serving_404()):
            evidence = _verify(
                client, CITATION_404B,
                "Notification must be provided without unreasonable delay and in no case "
                "later than 30 calendar days after discovery of a breach.",
            )
        assert evidence.checks.specifics_supported is False
        assert evidence.status is not VerificationStatus.verified

    def test_the_deadline_the_authority_does_state_is_verified(self, tmp_path):
        """The control: without it, the check above would only prove the
        specifics gate rejects everything."""
        client = _client(tmp_path)
        with serving(_serving_404()):
            evidence = _verify(
                client, CITATION_404B,
                "Notification must be provided without unreasonable delay and in no case "
                "later than 60 calendar days after discovery of a breach.",
            )
        assert evidence.checks.specifics_supported is True
        assert evidence.status is VerificationStatus.verified, evidence.reason


# ── 11. Obligation Memory ──

class TestChangedAuthorityInvalidatesMemory:
    def _memory(self, tmp_path, monkeypatch):
        from app.services.retrieval import obligation_memory

        monkeypatch.setattr(settings, "obligation_memory_enabled", True, raising=False)
        return obligation_memory.ObligationMemory(str(tmp_path / "memory.sqlite3"))

    def test_a_verified_obligation_is_remembered_then_recalled(self, tmp_path, monkeypatch):
        memory = self._memory(tmp_path, monkeypatch)
        client = _client(tmp_path)
        with serving(_serving_404()):
            evidence = _verify(client, CITATION_404A, NOTIFICATION_CLAIM)

        assert evidence.status is VerificationStatus.verified
        assert memory.remember(evidence, NOTIFICATION_CLAIM) is True
        assert memory.recall(
            NOTIFICATION_CLAIM, CITATION_404A, evidence.checks.source_fingerprint
        ) is not None

    def test_amended_opencontracts_text_makes_the_memory_unreachable(
        self, tmp_path, monkeypatch
    ):
        """Not stale-but-reachable: unreachable. The fingerprint of the exact
        authority text is part of the key, so amended text cannot look up the
        old verdict at all."""
        memory = self._memory(tmp_path, monkeypatch)
        client = _client(tmp_path)
        with serving(_serving_404()):
            before = _verify(client, CITATION_404B, self.SIXTY_DAY_CLAIM)
        assert before.status is VerificationStatus.verified
        assert memory.remember(before, self.SIXTY_DAY_CLAIM) is True

        # OpenContracts now serves the amended section.
        client._store.forget("cfr-45:164.404")
        with serving(_serving_404(xml=ecfr_stub.SECTION_164_404_AMENDED)):
            after = _verify(client, CITATION_404B, self.SIXTY_DAY_CLAIM)

        assert after.checks.source_fingerprint != before.checks.source_fingerprint
        assert memory.recall(
            self.SIXTY_DAY_CLAIM, CITATION_404B, after.checks.source_fingerprint
        ) is None
        # And the deterministic gates catch the substance, not just the key:
        # the amendment moved the deadline to 30 days.
        assert after.checks.specifics_supported is False
        assert after.status is not VerificationStatus.verified

    SIXTY_DAY_CLAIM = (
        "Notification must be provided without unreasonable delay and in no case later "
        "than 60 calendar days after discovery of a breach."
    )

    def test_an_unverified_result_never_becomes_memory(self, tmp_path, monkeypatch):
        memory = self._memory(tmp_path, monkeypatch)
        client = _client(tmp_path)
        with serving(_serving_404()):
            doc = client.get_authority_document("cfr-45:164.404")
        doc["metadata"]["status"] = "PROPOSED"
        client._store.put("cfr-45:164.404", doc["text"], doc["metadata"])

        evidence = _verify(client, CITATION_404A, NOTIFICATION_CLAIM)
        assert memory.remember(evidence, NOTIFICATION_CLAIM) is False
        assert memory.count() == 0


# ── 12. unavailability is not an excuse to guess ──

class TestUnavailableSubstrateFailsClosed:
    def test_an_ecfr_outage_yields_no_document(self, tmp_path):
        client = _client(tmp_path)
        fake = _serving_404()
        fake.offline = True
        with serving(fake):
            assert client.get_authority_document("cfr-45:164.404") is None

    def test_an_ecfr_outage_yields_an_unverified_claim_not_a_guessed_one(self, tmp_path):
        client = _client(tmp_path)
        verifier = _verifier(client)
        with serving(_serving_404()):
            cached = _retrieved(verifier, client, CITATION_404A)

        # A different section, never fetched, while eCFR is down.
        fake = FakeECFR()
        fake.offline = True
        with serving(fake):
            evidence = _verify(
                client, CITATION_316,
                "Documentation must be retained for 6 years.",
                context=_context(cached),
            )
        assert evidence.status is not VerificationStatus.verified
        assert evidence.checks.citation_exists is False

    def test_an_unavailable_runtime_resolves_nothing(self, tmp_path, monkeypatch):
        client = _client(tmp_path)
        with serving(_serving_404()):
            verifier = _verifier(client)
            ctx = _context(_retrieved(verifier, client, CITATION_404A))

        monkeypatch.setattr(ocr, "available", lambda: False)
        monkeypatch.setattr(ocr, "unavailable_reason", lambda: "test")
        evidence = verifier.build_claim_evidence(
            claim_id="f1", claim_text=NOTIFICATION_CLAIM,
            citation=CITATION_404A, retrieval_context=ctx,
        )
        assert evidence.status is not VerificationStatus.verified
        assert evidence.checks.citation_exists is False

    def test_the_legacy_fallback_covers_outage_only_never_a_verdict(self, tmp_path, monkeypatch):
        """The distinction the whole fallback turns on. A substrate that is down
        is an availability problem. A substrate that answered "not current" has
        answered, and retrying that against a second store would give the claim
        two chances to be believed."""
        calls = []

        class Recording(ChromaAuthorityProvider):
            def find_authority(self, citation, retrieval_context):
                calls.append(citation)
                return None

        client = _client(tmp_path)
        verifier = VerificationService()
        primary = OpenContractsAuthorityProvider(verifier, client)
        verifier._authority = LegacyFallbackAuthorityProvider(primary, Recording(verifier))

        with serving(_serving_404()):
            ctx = _context(_retrieved(verifier, client, CITATION_404A))
            # OpenContracts is up and says the fabricated section is not there.
            verifier.build_claim_evidence(
                claim_id="f1", claim_text=NOTIFICATION_CLAIM,
                citation="45 CFR § 164.9999", retrieval_context=ctx,
            )
        assert calls == [], "a resolution failure was retried against the legacy store"

        monkeypatch.setattr(ocr, "available", lambda: False)
        monkeypatch.setattr(ocr, "unavailable_reason", lambda: "test")
        verifier.build_claim_evidence(
            claim_id="f2", claim_text=NOTIFICATION_CLAIM,
            citation=CITATION_404A, retrieval_context=ctx,
        )
        assert calls == [CITATION_404A], "the fallback did not cover a genuine outage"


# ── 13-14. what the reader ends up with ──

class TestTheReportStillTellsTheTruth:
    """AI proposes, code proves -- run through the orchestrator's real evidence
    pass, with the model stubbed at both points it would be consulted."""

    def _analysis(self, *rows) -> AnalysisResult:
        return AnalysisResult(
            policy_type="HIPAA Breach Notification Policy",
            audit_ready_summary="Notification timing and documentation are underspecified.",
            gap_table=list(rows),
        )

    def _row(self, citation, finding) -> GapRow:
        return GapRow(
            clause="Breach notification", regulations=[citation.split("(")[0]],
            status=GapStatus.gap, axes_passed=1, finding=finding,
            suggested_language="", citation=citation,
            obligation_type=ObligationType.required,
        )

    def _run(self, client, analysis, ctx, monkeypatch, label="SUPPORTED"):
        from app.services import claim_support
        from app.services.orchestrator import PackageOrchestrator

        async def stub(pending):
            return {p["id"]: {"label": label, "note": "the excerpt supports the claim"}
                    for p in pending}

        monkeypatch.setattr(claim_support, "classify_claim_support", stub)
        orch = PackageOrchestrator()
        orch._verification = _verifier(client)
        asyncio.run(orch._build_evidence(analysis, ctx))
        return analysis

    def test_a_proven_finding_reaches_verified_and_keeps_its_wording(
        self, tmp_path, monkeypatch
    ):
        from app.services.package_integrity import reconcile_package_verification
        from app.models.schemas import ComplianceActionPackage, PackageStatus
        from datetime import datetime

        client = _client(tmp_path)
        with serving(_serving_404()):
            verifier = _verifier(client)
            ctx = _context(_retrieved(verifier, client, CITATION_404A))
            analysis = self._analysis(self._row(CITATION_404A, NOTIFICATION_CLAIM))
            self._run(client, analysis, ctx, monkeypatch)

        row = analysis.gap_table[0]
        assert row.evidence.status is VerificationStatus.verified, row.evidence.reason

        package = reconcile_package_verification(ComplianceActionPackage(
            package_id="p", created_at=datetime.now().isoformat(), policy_type="t",
            gap_analysis=analysis, status=PackageStatus.complete,
            completed_outputs=["gap_analysis"],
        ))
        out = package.gap_analysis.gap_table[0]
        assert out.obligation_type is ObligationType.required
        assert out.finding == NOTIFICATION_CLAIM, "a verified finding was stamped"

    def test_an_unproven_finding_reads_as_unverified(self, tmp_path, monkeypatch):
        from app.services.package_integrity import (
            UNVERIFIED_FINDING_PREFIX, reconcile_package_verification,
        )
        from app.models.schemas import ComplianceActionPackage, PackageStatus
        from datetime import datetime

        claim = (
            "Notification must be provided in no case later than 30 calendar days after "
            "discovery of a breach."
        )
        client = _client(tmp_path)
        with serving(_serving_404()):
            verifier = _verifier(client)
            ctx = _context(_retrieved(verifier, client, CITATION_404B))
            analysis = self._analysis(self._row(CITATION_404B, claim))
            self._run(client, analysis, ctx, monkeypatch)

        package = reconcile_package_verification(ComplianceActionPackage(
            package_id="p", created_at=datetime.now().isoformat(), policy_type="t",
            gap_analysis=analysis, status=PackageStatus.complete,
            completed_outputs=["gap_analysis"],
        ))
        out = package.gap_analysis.gap_table[0]
        assert out.obligation_type is ObligationType.unverified_requirement
        assert out.finding.startswith(UNVERIFIED_FINDING_PREFIX)
        assert "could not be confirmed" in package.gap_analysis.audit_ready_summary

    def test_the_memory_reused_verdict_matches_the_freshly_derived_one(
        self, tmp_path, monkeypatch
    ):
        """The reuse must be invisible in the result. Every deterministic gate
        reran; only the model's opinion came from the store."""
        from app.services.retrieval import obligation_memory

        monkeypatch.setattr(settings, "obligation_memory_enabled", True, raising=False)
        monkeypatch.setattr(
            obligation_memory, "_memory",
            obligation_memory.ObligationMemory(str(tmp_path / "m.sqlite3")),
            raising=False,
        )
        client = _client(tmp_path)
        with serving(_serving_404()):
            verifier = _verifier(client)
            ctx = _context(_retrieved(verifier, client, CITATION_404A))
            first = self._analysis(self._row(CITATION_404A, NOTIFICATION_CLAIM))
            self._run(client, first, ctx, monkeypatch)
            second = self._analysis(self._row(CITATION_404A, NOTIFICATION_CLAIM))
            self._run(client, second, ctx, monkeypatch, label="NOT_SUPPORTED")

        a, b = first.gap_table[0].evidence, second.gap_table[0].evidence
        assert b.checks.reused_from_memory is True
        assert b.status is a.status is VerificationStatus.verified
        # Reused verdict, freshly proven facts.
        assert b.checks.citation_exists is True
        assert b.checks.source_status_current is True
        assert b.checks.source_fingerprint == a.checks.source_fingerprint


# ── 15. what production selects ──

class TestProductionSelectsOpenContracts:
    def test_the_default_provider_is_opencontracts(self, monkeypatch):
        monkeypatch.setattr(settings, "authority_legacy_fallback_enabled", False, raising=False)
        provider = get_authority_provider(VerificationService.__new__(VerificationService))
        assert isinstance(provider, OpenContractsAuthorityProvider)

    def test_the_legacy_path_is_only_reached_by_pinning_it(self, monkeypatch):
        monkeypatch.setattr(settings, "authority_provider", "chroma", raising=False)
        provider = get_authority_provider(VerificationService.__new__(VerificationService))
        assert isinstance(provider, ChromaAuthorityProvider)

    def test_a_typo_does_not_silently_choose_the_legacy_store(self, monkeypatch):
        """Which store the law comes from must not be decided by a typo in an
        environment variable."""
        monkeypatch.setattr(settings, "authority_provider", "openconracts", raising=False)
        monkeypatch.setattr(settings, "authority_legacy_fallback_enabled", False, raising=False)
        provider = get_authority_provider(VerificationService.__new__(VerificationService))
        assert isinstance(provider, OpenContractsAuthorityProvider)

    def test_a_verification_service_built_with_no_arguments_uses_opencontracts(self):
        service = VerificationService()
        assert isinstance(
            service._authority, (OpenContractsAuthorityProvider, LegacyFallbackAuthorityProvider)
        )
        primary = getattr(service._authority, "_primary", service._authority)
        assert isinstance(primary, OpenContractsAuthorityProvider)
