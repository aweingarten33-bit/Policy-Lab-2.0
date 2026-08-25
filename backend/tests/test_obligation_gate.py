"""
A finding must not claim a legal mandate the source does not establish.

Reported by an outside reviewer against real output: the tool reported OSHA as
requiring 30-year retention of noise records. OSHA requires two years for noise
measurements and duration of employment for audiograms. Separately, it reported
a Must Fix for age correction, where the regulation says an allowance "may" be
made and the relevant appendix is expressly non-mandatory.

Neither is a retrieval failure -- the right regulation was found. Both are
classification failures: a company standard and an optional method were
presented as law, and the interface gave no way to tell the difference.

Two mechanisms, and both are needed. The model classifies each finding as
required / guidance / best practice / organizational choice. Then the
entailment gate re-checks every "required" against the source text and
downgrades any it cannot substantiate -- because the model asserting a mandate
is exactly what is not trusted here.

Run: python -m pytest tests/test_obligation_gate.py -v
"""

import pytest

from app.models.schemas import (
    AnalysisResult, ClaimSupport, EvidenceChecks, GapRow, GapStatus,
    ObligationType, SourceStatus, VerificationEvidence,
)
from app.services.orchestrator import PackageOrchestrator


def _row(obligation, support, specifics=None):
    return GapRow(
        clause="Records Retention",
        regulations=["29 CFR 1910.95"],
        status=GapStatus.gap,
        finding="OSHA requires audiometric records to be retained for 30 years.",
        suggested_language="Retain audiometric records for 30 years.",
        citation="29 CFR §1910.95(m)",
        obligation_type=obligation,
        evidence=VerificationEvidence(
            claim_id="finding-1",
            claim_text="OSHA requires 30-year retention.",
            checks=EvidenceChecks(
                citation_exists=True,
                claim_support=support,
                specifics_supported=specifics,
                # These cases isolate the entailment dimension, so the source's
                # standing is held at current throughout. A non-current source
                # is now its own reason to downgrade a claimed mandate, covered
                # separately in TestNonCurrentSourcesAreDowngraded below.
                source_status=SourceStatus.current_verified,
                source_status_current=True,
                # These fixtures cite CFR sections, i.e. codified law. Document
                # class is its own precondition, exercised separately in
                # TestGuidanceCannotEstablishLaw.
                source_is_binding_law=True,
            ),
        ),
    )


def _gate(rows):
    from app.models.schemas import AnalysisResult
    result = AnalysisResult(
        policy_type="Hearing Conservation",
        gap_table=rows,
        audit_ready_summary="x",
    )
    orch = PackageOrchestrator.__new__(PackageOrchestrator)
    orch._gate_unproven_mandates(result)
    return result.gap_table


class TestUnprovenMandatesAreDowngraded:
    def test_a_requirement_the_source_does_not_support(self):
        """The 30-year retention case."""
        row = _gate([_row(ObligationType.required, ClaimSupport.not_supported)])[0]
        assert row.obligation_type is ObligationType.unverified_requirement
        assert "does not establish this requirement" in row.obligation_note

    def test_a_requirement_the_source_contradicts(self):
        row = _gate([_row(ObligationType.required, ClaimSupport.contradicted)])[0]
        assert row.obligation_type is ObligationType.unverified_requirement
        assert "contradict" in row.obligation_note

    def test_a_requirement_whose_figure_is_absent(self):
        """A real citation carrying an invented number is the failure mode
        that started all of this."""
        row = _gate([_row(ObligationType.required, ClaimSupport.supported, specifics=False)])[0]
        assert row.obligation_type is ObligationType.unverified_requirement
        assert "specific figure" in row.obligation_note

    def test_the_finding_is_downgraded_not_deleted(self):
        """The underlying observation is often still worth acting on. It just
        cannot be presented as law."""
        row = _gate([_row(ObligationType.required, ClaimSupport.not_supported)])[0]
        assert row.finding
        assert row.suggested_language
        assert row.citation


class TestSupportedAndNonMandateFindingsAreLeftAlone:
    def test_a_supported_requirement_stays_required(self):
        row = _gate([_row(ObligationType.required, ClaimSupport.supported)])[0]
        assert row.obligation_type is ObligationType.required
        assert row.obligation_note is None

    @pytest.mark.parametrize("obligation", [
        ObligationType.guidance,
        ObligationType.best_practice,
        ObligationType.organizational_choice,
    ])
    def test_non_mandates_are_untouched(self, obligation):
        """Only a claimed mandate can be an unproven mandate. Downgrading a
        best practice would be meaningless."""
        row = _gate([_row(obligation, ClaimSupport.not_supported)])[0]
        assert row.obligation_type is obligation

    def test_an_unchecked_claim_is_not_downgraded(self):
        """Absence of a check is not evidence against the claim."""
        row = _gate([_row(ObligationType.required, ClaimSupport.not_checked)])[0]
        assert row.obligation_type is ObligationType.required

    def test_a_finding_without_evidence_is_not_downgraded(self):
        row = _row(ObligationType.required, ClaimSupport.not_checked)
        row.evidence = None
        assert _gate([row])[0].obligation_type is ObligationType.required


class TestTheReaderCanSeeIt:
    def test_the_model_cannot_assign_the_downgraded_class(self):
        """unverified_requirement is a server verdict. If the model could set
        it, it could also avoid setting it."""
        from app.services.llm_service import RESPONSE_SCHEMA
        assert "unverified_requirement" not in RESPONSE_SCHEMA

    def test_the_schema_offers_the_four_real_classes(self):
        from app.services.llm_service import RESPONSE_SCHEMA
        for name in ("required", "guidance", "best_practice", "organizational_choice"):
            assert name in RESPONSE_SCHEMA

    def test_the_export_labels_every_class(self):
        from app.services.export_service import _OBLIGATION_LABELS as OBLIGATION_LABELS
        for member in ObligationType:
            assert member.value in OBLIGATION_LABELS, f"{member.value} has no export label"

    def test_the_ui_labels_every_class(self):
        """The Word export showed this and the screen did not, so the
        distinction existed only for people who downloaded the document."""
        import pathlib
        index = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "Index.tsx"
        if not index.exists():
            pytest.skip("frontend not present")
        source = index.read_text()
        assert "OBLIGATION_MAP" in source
        for member in ObligationType:
            assert member.value in source, f"{member.value} is not rendered in the UI"


class TestNonCurrentSourcesAreDowngraded:
    """A claimed mandate is downgraded when its source cannot speak to present law.

    This is a separate reason from the entailment cases above. There, a current
    regulation was found and its text did not establish the duty. Here the text
    may match perfectly — but it comes from a proposal, a superseded version, or
    a page whose standing was never established, and none of those can say what
    is required today.
    """

    def _row_with_status(self, status):
        return GapRow(
            clause="Records Retention",
            regulations=["29 CFR 1910.95"],
            status=GapStatus.gap,
            finding="Records must be retained for two years.",
            suggested_language="Retain records for two years.",
            citation="29 CFR §1910.95(m)",
            obligation_type=ObligationType.required,
            evidence=VerificationEvidence(
                claim_id="finding-1",
                claim_text="Records must be retained for two years.",
                checks=EvidenceChecks(
                    citation_exists=True,
                    claim_support=ClaimSupport.supported,
                    specifics_supported=True,
                    source_status=status,
                    source_status_current=False,
                ),
            ),
        )

    @pytest.mark.parametrize("status", [
        SourceStatus.proposed,
        SourceStatus.superseded,
        SourceStatus.historical,
        SourceStatus.status_unknown,
    ])
    def test_a_mandate_from_a_non_current_source_is_not_left_as_required(self, status):
        result = AnalysisResult(
            policy_type="t", audit_ready_summary="s", gap_table=[self._row_with_status(status)]
        )
        PackageOrchestrator()._gate_unproven_mandates(result)

        row = result.gap_table[0]
        assert row.obligation_type is ObligationType.unverified_requirement, status
        assert status.value in (row.obligation_note or ""), (
            "the note must name the standing, so the reader knows what to go and check"
        )

    def test_the_finding_is_downgraded_not_deleted(self):
        """The underlying observation may still be worth acting on."""
        result = AnalysisResult(
            policy_type="t", audit_ready_summary="s",
            gap_table=[self._row_with_status(SourceStatus.proposed)],
        )
        PackageOrchestrator()._gate_unproven_mandates(result)
        assert len(result.gap_table) == 1
        assert result.gap_table[0].finding
