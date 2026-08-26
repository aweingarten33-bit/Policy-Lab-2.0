"""Obligation memory reuses a verification the engine already earned.

Every analysis re-derived the same regulations from scratch: re-run the same
policy and the model was asked the identical question about the identical
passage and paid for the identical answer.

The memory remembers exactly one thing — the semantic entailment verdict —
keyed to the claim, the citation, and a content hash of the authoritative text
it was checked against. It remembers none of the deterministic checks: those
re-run in full against the current corpus on every analysis, before the store
is consulted. The cache can replace the model's opinion; it can never replace
the code's checks.

These tests are mostly about what the memory must REFUSE to do, because an
optimisation that quietly weakens verification would be worse than no
optimisation at all.

Run: python -m pytest tests/evaluation/test_obligation_memory.py -v
"""

import asyncio

import pytest

from app.config import settings
from app.models.schemas import (
    AnalysisResult, ClaimSupport, GapRow, GapStatus, ObligationType,
    SourceStatus, VerificationStatus,
)
from app.services.orchestrator import PackageOrchestrator
from app.services.retrieval.models import SourceCategory
from app.services.retrieval.obligation_memory import (
    ObligationMemory, claim_fingerprint, fingerprint_text,
)

from tests.evaluation.cases import make_context, make_result

SECTION_TEXT = (
    "§ 164.9002 Workforce training. (a) A covered entity shall train all members "
    "of its workforce on the policies and procedures required by this subpart, as "
    "necessary and appropriate for the members of the workforce to carry out their "
    "functions. (b) A covered entity shall document that the training described in "
    "paragraph (a) of this section has been provided."
)
CHANGED_SECTION_TEXT = SECTION_TEXT.replace(
    "shall document that the training", "shall retain records showing that the training"
)

CLAIM = "A covered entity shall train all members of its workforce on these procedures."
CITATION = "45 CFR § 164.9002"


class _CountingClassifier:
    """Stands in for the entailment model, and counts how often it is asked."""

    def __init__(self, label="SUPPORTED", delay=0.0):
        self.calls = 0
        self.claims_seen = 0
        self.label = label
        self.delay = delay

    async def __call__(self, pending):
        self.calls += 1
        self.claims_seen += len(pending)
        if self.delay:
            await asyncio.sleep(self.delay)
        return {p["id"]: {"label": self.label, "note": "checked by model"} for p in pending}


@pytest.fixture
def engine(tmp_path, monkeypatch):
    """An orchestrator with an isolated corpus and an isolated memory."""
    from app.services.retrieval import obligation_memory as memory_module
    from app.services.retrieval import section_store as section_module
    from app.services.retrieval import store as store_module

    monkeypatch.setattr(settings, "obligation_memory_enabled", True)
    monkeypatch.setattr(
        store_module, "_store", store_module.ChromaStore(persist_dir=str(tmp_path / "kb"))
    )
    monkeypatch.setattr(
        section_module, "_section_store",
        section_module.SectionStore(persist_dir=str(tmp_path / "kb")),
    )
    memory = ObligationMemory(persist_dir=str(tmp_path / "kb"))
    monkeypatch.setattr(memory_module, "_memory", memory)

    # _build_evidence imports the classifier from app.services.claim_support
    # inside the function body, so that module is the only effective patch
    # point -- patching the orchestrator's namespace does nothing.
    import app.services.claim_support as cs
    classifier = _CountingClassifier()
    monkeypatch.setattr(cs, "classify_claim_support", classifier)

    return PackageOrchestrator(), memory, classifier


def _result(claim=CLAIM, citation=CITATION, obligation=ObligationType.required):
    return AnalysisResult(
        policy_type="Workforce Training Policy",
        audit_ready_summary="Summary.",
        gap_table=[GapRow(
            clause="Workforce training",
            regulations=[citation],
            status=GapStatus.gap,
            finding=claim,
            suggested_language="",
            citation=citation,
            obligation_type=obligation,
        )],
    )


def _context(text=SECTION_TEXT, status=SourceStatus.current_verified,
             category=SourceCategory.federal_regulation):
    return make_context(make_result(CITATION, text, status=status, category=category))


def _run(orch, result, ctx):
    asyncio.run(orch._build_evidence(result, ctx))
    return result.gap_table[0]


# ── 1. First encounter derives and verifies normally ──


class TestFirstEncounter:
    def test_it_derives_and_verifies_through_the_normal_pipeline(self, engine):
        orch, memory, classifier = engine
        row = _run(orch, _result(), _context())

        assert classifier.calls == 1, "the model must be asked the first time"
        assert row.evidence.status is VerificationStatus.verified
        assert row.evidence.checks.reused_from_memory is False

    def test_the_verified_result_is_remembered(self, engine):
        orch, memory, _ = engine
        _run(orch, _result(), _context())
        assert memory.count() == 1


# ── 2. Second encounter reuses ──


class TestSecondEncounter:
    def test_an_identical_source_and_claim_reuses_the_verdict(self, engine):
        orch, memory, classifier = engine
        _run(orch, _result(), _context())
        assert classifier.calls == 1

        row = _run(orch, _result(), _context())

        assert classifier.calls == 1, "the model was asked again despite an exact hit"
        assert row.evidence.checks.reused_from_memory is True
        assert row.evidence.status is VerificationStatus.verified

    def test_the_deterministic_checks_still_run_on_the_reuse(self, engine):
        """The cache replaces the model's opinion, never the code's checks."""
        orch, _, _ = engine
        _run(orch, _result(), _context())
        row = _run(orch, _result(), _context())

        checks = row.evidence.checks
        assert checks.citation_exists is True
        assert checks.source_status_current is True
        assert checks.source_is_binding_law is True
        assert checks.source_fingerprint, "the scope was re-resolved, not assumed"
        assert row.evidence.source.excerpt

    def test_reuse_is_recorded_so_it_is_auditable(self, engine):
        orch, _, _ = engine
        _run(orch, _result(), _context())
        assert _run(orch, _result(), _context()).evidence.checks.reused_from_memory is True


# ── 3. Changed source invalidates ──


class TestChangedSourceInvalidates:
    def test_changed_regulation_text_forces_a_fresh_check(self, engine):
        orch, memory, classifier = engine
        _run(orch, _result(), _context())
        assert classifier.calls == 1

        row = _run(orch, _result(), _context(CHANGED_SECTION_TEXT))

        assert classifier.calls == 2, "a changed regulation reused a stale verdict"
        assert row.evidence.checks.reused_from_memory is False

    def test_the_fingerprint_is_what_changes(self):
        assert fingerprint_text(SECTION_TEXT) != fingerprint_text(CHANGED_SECTION_TEXT)

    def test_reflowed_whitespace_is_not_a_change(self):
        """Whitespace-only differences are formatting, not amendment."""
        reflowed = SECTION_TEXT.replace(" ", "  ").replace("(b)", "\n(b)")
        assert fingerprint_text(SECTION_TEXT) == fingerprint_text(reflowed)

    def test_a_different_claim_does_not_hit(self, engine):
        orch, _, classifier = engine
        _run(orch, _result(), _context())
        _run(orch, _result(claim="A covered entity shall encrypt all portable media."), _context())
        assert classifier.calls == 2

    def test_a_different_citation_does_not_hit(self, engine):
        orch, _, classifier = engine
        _run(orch, _result(), _context())
        other = make_context(make_result("45 CFR § 164.9004", SECTION_TEXT))
        _run(orch, _result(citation="45 CFR § 164.9004"), other)
        assert classifier.calls == 2


# ── 4. Non-current sources cannot populate current-obligation memory ──


class TestOnlyCurrentSourcesAreRemembered:
    @pytest.mark.parametrize("status", [
        SourceStatus.proposed,
        SourceStatus.superseded,
        SourceStatus.historical,
    ])
    def test_a_non_current_source_is_never_stored(self, engine, status):
        orch, memory, _ = engine
        row = _run(orch, _result(), _context(status=status))

        assert row.evidence.status is VerificationStatus.cannot_determine
        assert memory.count() == 0, f"{status.value} material entered the memory"

    def test_an_unknown_status_source_is_never_stored(self, engine):
        from app.services.retrieval.models import SourceType
        orch, memory, _ = engine
        ctx = make_context(make_result(
            CITATION, SECTION_TEXT, status=None, source_type=SourceType.live_research
        ))
        _run(orch, _result(), ctx)
        assert memory.count() == 0

    def test_a_source_that_later_loses_standing_is_not_reused(self, engine):
        """The strongest case: an entry earned legitimately, then the source is
        superseded. The deterministic gate must catch it before the lookup."""
        orch, memory, classifier = engine
        _run(orch, _result(), _context())
        assert memory.count() == 1

        row = _run(orch, _result(), _context(status=SourceStatus.superseded))

        assert row.evidence.checks.reused_from_memory is False
        assert row.evidence.status is VerificationStatus.cannot_determine
        assert "SUPERSEDED" in row.evidence.reason


# ── 5. Unverified extractions are never cached ──


class TestOnlyVerifiedResultsAreRemembered:
    def test_a_not_supported_verdict_is_not_stored(self, engine, monkeypatch):
        import app.services.claim_support as cs
        orch, memory, _ = engine
        monkeypatch.setattr(
            cs, "classify_claim_support", _CountingClassifier("NOT_SUPPORTED")
        )
        row = _run(orch, _result(), _context())

        assert row.evidence.status is VerificationStatus.unverified
        assert memory.count() == 0

    def test_a_contradicted_verdict_is_not_stored(self, engine, monkeypatch):
        import app.services.claim_support as cs
        orch, memory, _ = engine
        monkeypatch.setattr(
            cs, "classify_claim_support", _CountingClassifier("CONTRADICTED")
        )
        _run(orch, _result(), _context())
        assert memory.count() == 0

    def test_a_claim_whose_citation_does_not_resolve_is_not_stored(self, engine):
        orch, memory, _ = engine
        row = _run(orch, _result(citation="45 CFR § 164.9099"), _context())
        assert row.evidence.checks.citation_exists is False
        assert memory.count() == 0

    def test_a_claim_with_an_unsupported_figure_is_not_stored(self, engine):
        """The concrete-fact gate settles it before the model is even asked."""
        orch, memory, classifier = engine
        _run(orch, _result(claim="Training must be repeated every 90 days."), _context())
        assert memory.count() == 0
        assert classifier.calls == 0

    def test_a_mandate_backed_only_by_guidance_is_not_stored(self, engine):
        orch, memory, _ = engine
        ctx = make_context(make_result(
            "OIG General Compliance Program Guidance",
            "Organizations must train their workforce as a matter of good practice.",
            category=SourceCategory.federal_guidance, part_citation=None,
        ))
        row = _run(
            orch,
            _result(claim="Training is required by law.",
                    citation="OIG General Compliance Program Guidance"),
            ctx,
        )
        assert row.evidence.status is not VerificationStatus.verified
        assert memory.count() == 0

    def test_the_store_refuses_a_bad_record_even_if_asked_directly(self, engine):
        """remember() re-checks its own preconditions rather than trusting the
        caller, so a future call site cannot bypass them."""
        from app.models.schemas import (
            EvidenceChecks, EvidenceSource, VerificationEvidence,
        )
        _, memory, _ = engine
        forged = VerificationEvidence(
            claim_id="x", claim_text=CLAIM, citation=CITATION,
            status=VerificationStatus.unverified,        # not verified
            source=EvidenceSource(excerpt="text"),
            checks=EvidenceChecks(
                citation_exists=True, claim_support=ClaimSupport.supported,
                source_status=SourceStatus.current_verified, source_status_current=True,
                source_is_binding_law=True, source_fingerprint="deadbeef",
            ),
        )
        assert memory.remember(forged, CLAIM) is False
        assert memory.count() == 0


# ── 6. Provenance survives ──


class TestProvenanceIsPreserved:
    def test_the_citation_and_excerpt_survive_a_reuse(self, engine):
        orch, _, _ = engine
        first = _run(orch, _result(), _context()).evidence
        second = _run(orch, _result(), _context()).evidence

        assert second.citation == first.citation == CITATION
        assert second.source.excerpt == first.source.excerpt
        assert second.source.name == first.source.name

    def test_the_stored_entry_records_what_produced_it(self, engine):
        orch, memory, _ = engine
        evidence = _run(orch, _result(), _context()).evidence
        remembered = memory.recall(CLAIM, CITATION, evidence.checks.source_fingerprint)

        assert remembered is not None
        assert remembered.citation == CITATION
        assert remembered.excerpt
        assert remembered.source_status == SourceStatus.current_verified.value
        assert remembered.verified_at


# ── 7. Fresh and reused results are materially equivalent ──


class TestFreshAndReusedAgree:
    def test_the_two_runs_produce_the_same_verdict_and_labels(self, engine):
        orch, _, _ = engine
        fresh = _run(orch, _result(), _context())
        cached = _run(orch, _result(), _context())

        assert cached.evidence.status is fresh.evidence.status
        assert cached.evidence.checks.claim_support is fresh.evidence.checks.claim_support
        assert cached.obligation_type is fresh.obligation_type
        assert cached.evidence.checks.source_fingerprint == fresh.evidence.checks.source_fingerprint

    def test_the_obligation_gate_still_runs_on_a_reused_verdict(self, engine):
        """A reused SUPPORTED verdict against guidance must still be downgraded."""
        orch, _, _ = engine
        assert _run(orch, _result(), _context()).obligation_type is ObligationType.required

    def test_disabling_the_memory_changes_nothing_but_the_model_calls(self, engine, monkeypatch):
        orch, _, classifier = engine
        with_memory = _run(orch, _result(), _context())
        _run(orch, _result(), _context())

        monkeypatch.setattr(settings, "obligation_memory_enabled", False)
        without = _run(orch, _result(), _context())

        assert without.evidence.status is with_memory.evidence.status
        assert without.evidence.checks.reused_from_memory is False


# ── The kill switch ──


class TestItCanBeTurnedOff:
    def test_disabled_never_reads(self, engine, monkeypatch):
        orch, memory, classifier = engine
        _run(orch, _result(), _context())
        monkeypatch.setattr(settings, "obligation_memory_enabled", False)
        _run(orch, _result(), _context())
        assert classifier.calls == 2, "a disabled memory was still consulted"

    def test_disabled_never_writes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "obligation_memory_enabled", False)
        memory = ObligationMemory(persist_dir=str(tmp_path))
        assert memory.recall(CLAIM, CITATION, "abc") is None
        assert memory.count() == 0


class TestKeyingIsExact:
    def test_case_and_whitespace_are_normalised_in_a_claim(self):
        assert claim_fingerprint("A Covered Entity  shall train.") == claim_fingerprint(
            "a covered entity shall train."
        )

    def test_a_word_difference_is_a_different_claim(self):
        assert claim_fingerprint("shall train annually") != claim_fingerprint(
            "shall train quarterly"
        )

    def test_a_missing_fingerprint_never_matches(self, tmp_path):
        memory = ObligationMemory(persist_dir=str(tmp_path))
        assert memory.recall(CLAIM, CITATION, "") is None
