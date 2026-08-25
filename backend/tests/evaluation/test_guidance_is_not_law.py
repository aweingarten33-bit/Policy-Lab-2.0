"""Agency guidance cannot establish a legal requirement.

Found by the pre-merge regression run, not by a unit test — which is the point
of running one. A finding reading "The Compliance Officer is required by law not
to report to finance", cited to the OIG General Compliance Program Guidance,
came back VERIFIED / REQUIRED BY LAW. The passage it verified against says, in
its own words:

    "This guidance does not create any new law or legal obligations..."

Two things had to be true for that to happen. The guidance categories were not
excluded from proving a legal duty, so document class was never consulted. And
the modality check only fires on affirmatively permissive wording ("may",
"should") — a disclaimer is neither permissive nor mandatory, so the check sat
inert and the claim went through on the classifier's say-so alone.

This was not a regression. Before Phase 0, guidance never reached retrieval at
all because of the industry-scoping filter bug, so it was safe by accident while
also being useless. Fixing that filter made guidance usable, and usable made it
misusable.

The fix is a document-class gate, checked on category rather than wording —
because wording is exactly what failed. Guidance quotes statutes, paraphrases
requirements and uses "must" freely while disclaiming any obligation of its own.

Run: python -m pytest tests/evaluation/test_guidance_is_not_law.py -v
"""

import pytest

from app.models.schemas import ClaimSupport, SourceStatus, VerificationStatus
from app.services.retrieval.models import SourceCategory
from app.services.retrieval.verification import (
    _LEGALLY_BINDING_CATEGORIES,
    VerificationService,
)

from tests.evaluation.cases import make_context, make_result

# Verbatim from the OIG General Compliance Program Guidance, as retrieved from
# the bundled PDF during the regression run. This exact passage is what the
# system verified a legal mandate against.
OIG_DISCLAIMER = (
    "Other Standards: Overview of Certain Federal Laws This guidance does not "
    "create any new law or legal obligations, and the discussions in this "
    "guidance are not intended to present detailed or comprehensive summaries "
    "of lawful or unlawful activity. Critical to understanding compliance risks "
    "and the framework is a working knowledge of the Federal laws that apply."
)

# Guidance that uses mandatory-sounding words while still being guidance. This
# is the common shape and the reason a wording check cannot carry this job.
OIG_MANDATORY_SOUNDING = (
    "An effective compliance program requires that the compliance officer must "
    "have direct access to the board, and shall not report to the general "
    "counsel or the chief financial officer."
)

MANDATE_CLAIM = "The Compliance Officer is required by law not to report to finance."
NON_MANDATE_CLAIM = "OIG guidance describes direct board access for the compliance officer."


@pytest.fixture
def verifier(tmp_path, monkeypatch):
    from app.services.retrieval import section_store as section_module
    from app.services.retrieval import store as store_module

    monkeypatch.setattr(
        store_module, "_store", store_module.ChromaStore(persist_dir=str(tmp_path / "kb"))
    )
    monkeypatch.setattr(
        section_module, "_section_store",
        section_module.SectionStore(persist_dir=str(tmp_path / "kb")),
    )
    return VerificationService()


def _guidance_ctx(text=OIG_DISCLAIMER):
    return make_context(make_result(
        "OIG General Compliance Program Guidance",
        text,
        category=SourceCategory.federal_guidance,
        part_citation=None,
        publication_date="2023-11-06",
        status=SourceStatus.current_verified,
    ))


def _run(verifier, claim, ctx, citation="OIG General Compliance Program Guidance"):
    ev = verifier.build_claim_evidence("g", claim, citation, ctx)
    # The classifier is given its most generous possible answer. A deterministic
    # guard that only holds when the model agrees is not a guard.
    verifier.apply_claim_support(ev, ClaimSupport.supported)
    return ev


class TestTheRegressionCase:
    def test_the_exact_finding_that_slipped_through_no_longer_verifies(self, verifier):
        ev = _run(verifier, MANDATE_CLAIM, _guidance_ctx())
        assert ev.status is not VerificationStatus.verified, ev.reason
        assert ev.checks.source_is_binding_law is False

    def test_the_reason_tells_the_reader_it_is_guidance(self, verifier):
        ev = _run(verifier, MANDATE_CLAIM, _guidance_ctx())
        assert "not codified law" in ev.reason
        assert "does not" in ev.reason and "legal obligation" in ev.reason

    def test_mandatory_sounding_guidance_still_cannot_establish_law(self, verifier):
        """The harder case. This passage says "must" and "shall not" — a wording
        check would pass it. Document class is what settles it."""
        ev = _run(verifier, MANDATE_CLAIM, _guidance_ctx(OIG_MANDATORY_SOUNDING))
        assert ev.status is not VerificationStatus.verified, ev.reason
        assert ev.checks.source_is_binding_law is False


class TestGuidanceStaysUsable:
    """The gate must not undo the guidance-retrieval fix.

    Loading the OIG/HCCA material was the whole point of an earlier change, and
    a gate that made guidance worthless would be a worse outcome than the bug.
    """

    def test_guidance_is_still_retrieved_and_matched(self, verifier):
        ev = _run(verifier, MANDATE_CLAIM, _guidance_ctx())
        assert ev.checks.citation_exists is True
        assert ev.source.excerpt, "the passage must still be shown to the reader"
        assert ev.source.name

    def test_a_non_mandate_claim_can_still_verify_from_guidance(self, verifier):
        """"OIG guidance describes X" is a claim about the guidance, and the
        guidance is the right authority for it."""
        ev = _run(verifier, NON_MANDATE_CLAIM, _guidance_ctx(OIG_MANDATORY_SOUNDING))
        assert ev.status is VerificationStatus.verified, ev.reason

    def test_guidance_is_still_authoritative_for_retrieval(self):
        """It must not be lumped in with templates and example policies."""
        from app.services.retrieval.verification import _NON_AUTHORITATIVE_CATEGORIES
        assert SourceCategory.federal_guidance not in _NON_AUTHORITATIVE_CATEGORIES


class TestTheCategoryBoundary:
    @pytest.mark.parametrize("category", [
        SourceCategory.federal_regulation,
        SourceCategory.state_law,
    ])
    def test_codified_law_can_establish_a_mandate(self, category):
        assert category in _LEGALLY_BINDING_CATEGORIES

    @pytest.mark.parametrize("category", [
        SourceCategory.federal_guidance,
        SourceCategory.ocr_guidance,
        SourceCategory.enforcement_action,
        SourceCategory.policy_template,
        SourceCategory.example_policy,
    ])
    def test_everything_else_cannot(self, category):
        assert category not in _LEGALLY_BINDING_CATEGORIES

    def test_a_regulation_backed_mandate_still_verifies(self, verifier):
        """The control. If this fails the gate has been made indiscriminate."""
        ctx = make_context(make_result(
            "45 CFR § 164.9002",
            "(a) A covered entity shall train all members of its workforce on the "
            "policies and procedures required by this subpart.",
        ))
        ev = _run(
            verifier,
            "A covered entity shall train all members of its workforce.",
            ctx,
            citation="45 CFR § 164.9002",
        )
        assert ev.checks.source_is_binding_law is True
        assert ev.status is VerificationStatus.verified, ev.reason


class TestTheReaderSeesTheRightLabel:
    def test_a_guidance_backed_mandate_is_relabelled_as_guidance(self, verifier):
        """Not "unverified requirement" — that says a check failed. "Guidance"
        says what the finding actually is, which is more use to a reader."""
        from app.models.schemas import AnalysisResult, GapRow, GapStatus, ObligationType
        from app.services.orchestrator import PackageOrchestrator

        ev = _run(verifier, MANDATE_CLAIM, _guidance_ctx(OIG_MANDATORY_SOUNDING))
        row = GapRow(
            clause="Compliance Officer reporting line",
            regulations=["OIG General Compliance Program Guidance"],
            status=GapStatus.gap,
            finding=MANDATE_CLAIM,
            suggested_language="The Compliance Officer shall report to the CEO.",
            citation="OIG General Compliance Program Guidance",
            obligation_type=ObligationType.required,
            evidence=ev,
        )
        result = AnalysisResult(policy_type="t", audit_ready_summary="s", gap_table=[row])
        PackageOrchestrator.__new__(PackageOrchestrator)._gate_unproven_mandates(result)

        assert row.obligation_type is ObligationType.guidance
        assert "agency guidance, not codified law" in row.obligation_note

    def test_the_finding_is_kept_not_deleted(self, verifier):
        """A reporting line that worries OIG is worth raising. It is just not law."""
        from app.models.schemas import AnalysisResult, GapRow, GapStatus, ObligationType
        from app.services.orchestrator import PackageOrchestrator

        ev = _run(verifier, MANDATE_CLAIM, _guidance_ctx())
        row = GapRow(
            clause="Compliance Officer reporting line",
            regulations=["OIG General Compliance Program Guidance"],
            status=GapStatus.gap, finding=MANDATE_CLAIM,
            suggested_language="Report to the CEO.",
            citation="OIG General Compliance Program Guidance",
            obligation_type=ObligationType.required, evidence=ev,
        )
        result = AnalysisResult(policy_type="t", audit_ready_summary="s", gap_table=[row])
        PackageOrchestrator.__new__(PackageOrchestrator)._gate_unproven_mandates(result)

        assert len(result.gap_table) == 1
        assert result.gap_table[0].finding
        assert result.gap_table[0].suggested_language
