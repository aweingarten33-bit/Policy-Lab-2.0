"""
A finding may not claim a legal duty its own source does not impose.

Reported symptom: the tool stated that OSHA requires audiogram retention for
"employment plus 30 years", with a real citation to 29 CFR 1910.95(m). OSHA
requires two years for noise-exposure measurements and duration of employment
for audiograms. A longer period is a perfectly good company standard; it is
simply not what the regulation says.

The claim-support machinery already existed and already ran. What it did was
*label*: the finding kept its wording and gained a small unverified badge. But
the sentence a reader acts on still said "OSHA requires", so labelling did not
change what the document asserted.

This gate acts on the result. A finding presented as legally required, whose
cited excerpt does not establish that duty, is reclassified as an unverified
requirement and carries the reason. It is downgraded rather than deleted --
the underlying observation is often still worth acting on, it just cannot be
presented as law.

Run: python -m pytest tests/test_entailment_gate.py -v
"""

import pytest

from app.models.schemas import (
    AnalysisResult, ClaimSupport, EvidenceChecks, EvidenceSource, GapRow,
    GapStatus, ObligationType, RiskLevel, VerificationEvidence,
)
from app.services.orchestrator import PackageOrchestrator

OSHA_EXCERPT = (
    "The employer shall maintain an accurate record of all employee exposure "
    "measurements required by this section... and shall retain such records "
    "for two years."
)


def _finding(text, support, *, obligation=ObligationType.required, specifics=None,
             citation_exists=True, excerpt=OSHA_EXCERPT):
    return GapRow(
        clause="Records Retention",
        regulations=["29 CFR 1910.95(m)"],
        status=GapStatus.gap,
        risk_level=RiskLevel.high,
        finding=text,
        suggested_language="...",
        citation="29 CFR §1910.95(m)",
        obligation_type=obligation,
        evidence=VerificationEvidence(
            claim_id="f1", claim_text=text, citation="29 CFR §1910.95(m)",
            source=EvidenceSource(excerpt=excerpt),
            checks=EvidenceChecks(
                citation_exists=citation_exists,
                claim_support=support,
                specifics_supported=specifics,
            ),
        ),
    )


def _gate(*rows):
    result = AnalysisResult(
        policy_type="Hearing Conservation", gap_table=list(rows),
        audit_ready_summary="s", priority_findings=[],
    )
    PackageOrchestrator.__new__(PackageOrchestrator)._gate_unproven_mandates(result)
    return result.gap_table


class TestTheReportedError:
    def test_the_thirty_year_claim_is_downgraded(self):
        """The exact finding from the report."""
        row, = _gate(_finding(
            "OSHA requires audiogram retention for employment plus 30 years.",
            ClaimSupport.not_supported,
        ))
        assert row.obligation_type is ObligationType.unverified_requirement
        assert row.obligation_note

    def test_the_reason_tells_the_reader_what_to_do(self):
        row, = _gate(_finding(
            "OSHA requires 30-year retention.", ClaimSupport.not_supported))
        note = row.obligation_note.lower()
        assert "does not establish" in note
        assert "not shown to be legally mandated" in note

    def test_the_finding_is_kept_not_deleted(self):
        """The observation may still be worth acting on. Only the claim that
        it is legally required is withdrawn."""
        row, = _gate(_finding(
            "OSHA requires 30-year retention.", ClaimSupport.not_supported))
        assert "30-year retention" in row.finding
        assert row.suggested_language


class TestWhatGetsGated:
    def test_a_contradicted_mandate_is_downgraded(self):
        row, = _gate(_finding("OSHA prohibits retention beyond two years.",
                              ClaimSupport.contradicted))
        assert row.obligation_type is ObligationType.unverified_requirement
        assert "contradict" in row.obligation_note.lower()

    def test_an_unsupported_figure_is_downgraded(self):
        """Support can pass while a specific number still fails -- that is the
        invented-deadline case."""
        row, = _gate(_finding("Records must be kept 45 years.",
                              ClaimSupport.supported, specifics=False))
        assert row.obligation_type is ObligationType.unverified_requirement
        assert "does not appear in the cited source" in row.obligation_note

    def test_a_supported_mandate_survives(self):
        row, = _gate(_finding("Noise measurements must be retained two years.",
                              ClaimSupport.supported))
        assert row.obligation_type is ObligationType.required
        assert row.obligation_note is None

    def test_partial_support_is_left_alone(self):
        """Partial support is a weaker signal, not a refutation. Downgrading on
        it would bury real requirements under warnings."""
        row, = _gate(_finding("Records must be retained.",
                              ClaimSupport.partially_supported))
        assert row.obligation_type is ObligationType.required


class TestTheGateOnlyTouchesClaimedMandates:
    @pytest.mark.parametrize("obligation", [
        ObligationType.guidance,
        ObligationType.best_practice,
        ObligationType.organizational_choice,
    ])
    def test_non_mandates_are_untouched(self, obligation):
        """Only something asserted as required can be an unproven mandate. A
        finding already labelled a recommendation is making no legal claim."""
        row, = _gate(_finding("Consider longer retention.",
                              ClaimSupport.not_supported, obligation=obligation))
        assert row.obligation_type is obligation
        assert row.obligation_note is None

    def test_a_finding_without_evidence_is_untouched(self):
        row = _finding("Something.", ClaimSupport.not_checked)
        row.evidence = None
        result, = _gate(row)
        assert result.obligation_type is ObligationType.required

    def test_an_unchecked_claim_is_untouched(self):
        row, = _gate(_finding("Something.", ClaimSupport.not_checked))
        assert row.obligation_type is ObligationType.required


class TestTheReaderSeesIt:
    """A downgrade nobody can see is the same failure as not downgrading."""

    def _render(self, rows):
        import io
        from docx import Document
        from app.services.export_service import generate_docx

        result = AnalysisResult(policy_type="HC", gap_table=list(rows),
                                audit_ready_summary="s", priority_findings=[])
        doc = Document(io.BytesIO(generate_docx(result, file_name="t.docx")))
        return "\n".join(p.text for p in doc.paragraphs)

    def test_the_warning_appears_in_the_export(self):
        rows = _gate(_finding("OSHA requires 30-year retention.",
                              ClaimSupport.not_supported))
        text = self._render(rows)
        assert "UNVERIFIED REQUIREMENT" in text
        assert "not confirmed against the source" in text

    def test_the_warning_precedes_the_finding(self):
        """A caveat printed after the claim is read second, if at all."""
        rows = _gate(_finding("OSHA requires 30-year retention.",
                              ClaimSupport.not_supported))
        text = self._render(rows)
        assert text.index("UNVERIFIED REQUIREMENT") < text.index("Finding:")

    def test_a_confirmed_requirement_reads_as_required(self):
        rows = _gate(_finding("Noise measurements retained two years.",
                              ClaimSupport.supported))
        text = self._render(rows)
        assert "LEGALLY REQUIRED" in text
        assert "UNVERIFIED REQUIREMENT" not in text

    def test_guidance_is_not_dressed_up_as_law(self):
        rows = _gate(_finding("Age correction may be applied.",
                              ClaimSupport.supported,
                              obligation=ObligationType.guidance))
        text = self._render(rows)
        assert "recommended, not mandated" in text


def test_the_model_is_told_the_field_is_checked():
    """If the prompt does not say the classification is verified afterwards,
    'required' becomes an emphasis marker."""
    from app.services.llm_service import RESPONSE_SCHEMA
    assert "obligation_type" in RESPONSE_SCHEMA
    assert "checked against the source text" in RESPONSE_SCHEMA
