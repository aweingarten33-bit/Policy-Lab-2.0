"""An unverified obligation must read as unverified, not just be badged as one.

Reported from a real production run. All six findings were correctly labelled
UNVERIFIED REQUIREMENT — and every one of them still said "the policy must...",
named an exact deadline, and cited a section as though the requirement had been
confirmed. The executive summary described the same requirements in the same
confident terms.

A reader acts on the sentence, not the badge. A finding that says "Records must
be retained for six years under 45 CFR § 164.316(b)(2)(i)" has told the reader
the law requires that, whatever colour the label beside it is. Labelling alone
was already found insufficient once before, for the obligation type; this is the
same failure one layer out, in the prose.

The fix stamps the prose rather than rewriting it. A rewrite would need a model
call, would be one more place a claim could be invented, and could mangle a
quoted provision. A prefix is deterministic and leaves the original wording
intact for a reader who wants to judge it.

Run: python -m pytest tests/evaluation/test_unverified_stays_unverified_in_prose.py -v
"""

from datetime import datetime

import pytest

from app.models.schemas import (
    AnalysisResult, ClaimSupport, ComplianceActionPackage, EvidenceChecks,
    EvidenceSource, GapRow, GapStatus, ObligationType, PackageStatus,
    SourceStatus, VerificationEvidence, VerificationStatus,
)
from app.services.package_integrity import (
    GUIDANCE_FINDING_PREFIX,
    UNVERIFIED_FINDING_PREFIX,
    UNVERIFIED_LANGUAGE_PREFIX,
    reconcile_package_verification,
)

# Deliberately written the way the model actually writes: mandatory verb, exact
# figure, real-looking citation. This is the sentence that must not stand
# unqualified when nothing confirmed it.
MANDATORY_FINDING = (
    "The policy does not require retention of breach notifications. Records must be "
    "retained for six years under 45 CFR § 164.9001(c)."
)
MANDATORY_LANGUAGE = (
    "A copy of each notification shall be retained for six years from issuance, as "
    "required by 45 CFR § 164.9001(c)."
)
CONFIDENT_SUMMARY = (
    "The policy is missing several mandatory provisions. Breach notification records "
    "must be retained for six years and workforce sanctions must be documented."
)


def _row(obligation=ObligationType.required, evidence=None):
    return GapRow(
        clause="Notification retention",
        regulations=["45 CFR § 164.9001"],
        status=GapStatus.gap,
        finding=MANDATORY_FINDING,
        suggested_language=MANDATORY_LANGUAGE,
        citation="45 CFR § 164.9001(c)",
        obligation_type=obligation,
        evidence=evidence,
    )


def _verified_evidence():
    return VerificationEvidence(
        claim_id="c", claim_text="x", status=VerificationStatus.verified,
        source=EvidenceSource(excerpt="shall retain a copy for six years"),
        checks=EvidenceChecks(
            citation_exists=True, claim_support=ClaimSupport.supported,
            specifics_supported=True, source_status=SourceStatus.current_verified,
            source_status_current=True, source_is_binding_law=True,
        ),
    )


def _package(*rows, summary=CONFIDENT_SUMMARY):
    return ComplianceActionPackage(
        package_id="p", created_at=datetime.now().isoformat(), policy_type="t",
        gap_analysis=AnalysisResult(
            policy_type="t", audit_ready_summary=summary, gap_table=list(rows)
        ),
        status=PackageStatus.complete, completed_outputs=["gap_analysis"],
    )


class TestTheProseIsStamped:
    def test_an_unverified_finding_says_so_in_its_own_text(self):
        pkg = reconcile_package_verification(_package(_row()))
        row = pkg.gap_analysis.gap_table[0]

        assert row.obligation_type is ObligationType.unverified_requirement
        assert row.finding.startswith(UNVERIFIED_FINDING_PREFIX), (
            "the finding still reads as a confirmed legal requirement"
        )

    def test_the_suggested_language_says_so_too(self):
        """This is the field a user copies into their actual policy."""
        row = reconcile_package_verification(_package(_row())).gap_analysis.gap_table[0]
        assert row.suggested_language.startswith(UNVERIFIED_LANGUAGE_PREFIX)
        assert "your organization's own standard" in row.suggested_language

    def test_the_original_wording_is_preserved_after_the_marker(self):
        """Stamped, not rewritten -- the reader can still judge the finding."""
        row = reconcile_package_verification(_package(_row())).gap_analysis.gap_table[0]
        assert MANDATORY_FINDING in row.finding
        assert MANDATORY_LANGUAGE in row.suggested_language

    def test_a_guidance_backed_finding_gets_its_own_marker(self):
        evidence = _verified_evidence()
        evidence.checks.source_is_binding_law = False
        row = reconcile_package_verification(
            _package(_row(ObligationType.guidance, evidence))
        ).gap_analysis.gap_table[0]

        assert row.finding.startswith(GUIDANCE_FINDING_PREFIX)
        assert "not a legal obligation" in row.finding


class TestTheSummaryIsCorrected:
    def test_a_confident_summary_gains_an_explicit_correction(self):
        pkg = reconcile_package_verification(_package(_row()))
        summary = pkg.gap_analysis.audit_ready_summary

        assert "could not be confirmed as legal requirements" in summary
        assert "check the regulation directly" in summary

    def test_the_correction_counts_the_affected_findings(self):
        pkg = reconcile_package_verification(_package(_row(), _row()))
        assert "2 of 2 finding(s)" in pkg.gap_analysis.audit_ready_summary

    def test_the_original_summary_survives(self):
        pkg = reconcile_package_verification(_package(_row()))
        assert CONFIDENT_SUMMARY in pkg.gap_analysis.audit_ready_summary


class TestVerifiedFindingsAreLeftAlone:
    """The stamp must mark what is genuinely unverified and nothing else, or it
    becomes noise and stops being read."""

    def test_a_verified_requirement_keeps_its_wording(self):
        row = reconcile_package_verification(
            _package(_row(ObligationType.required, _verified_evidence()))
        ).gap_analysis.gap_table[0]

        assert row.obligation_type is ObligationType.required
        assert row.finding == MANDATORY_FINDING
        assert row.suggested_language == MANDATORY_LANGUAGE

    def test_a_fully_verified_package_summary_is_untouched(self):
        pkg = reconcile_package_verification(
            _package(_row(ObligationType.required, _verified_evidence()))
        )
        assert pkg.gap_analysis.audit_ready_summary == CONFIDENT_SUMMARY

    def test_an_organizational_choice_is_not_stamped(self):
        """It never claimed to be law, so there is nothing to correct."""
        row = reconcile_package_verification(
            _package(_row(ObligationType.organizational_choice, _verified_evidence()))
        ).gap_analysis.gap_table[0]
        assert row.finding == MANDATORY_FINDING


class TestStampingIsIdempotent:
    """Reconciliation runs on every response the API emits."""

    def test_repeated_reconciliation_does_not_stack_markers(self):
        pkg = _package(_row())
        for _ in range(5):
            pkg = reconcile_package_verification(pkg)

        row = pkg.gap_analysis.gap_table[0]
        assert row.finding.count(UNVERIFIED_FINDING_PREFIX) == 1
        assert row.suggested_language.count(UNVERIFIED_LANGUAGE_PREFIX) == 1

    def test_the_summary_correction_appears_once(self):
        pkg = _package(_row())
        for _ in range(5):
            pkg = reconcile_package_verification(pkg)
        assert pkg.gap_analysis.audit_ready_summary.count("could not be confirmed") == 1

    def test_an_empty_field_is_not_given_a_marker_alone(self):
        row = _row()
        row.suggested_language = ""
        out = reconcile_package_verification(_package(row)).gap_analysis.gap_table[0]
        assert out.suggested_language == ""


class TestInterimSnapshotsAreStillLeftAlone:
    def test_a_streaming_snapshot_is_not_stamped(self):
        """Same guard as the obligation label: an in-progress package has no
        evidence yet, and stamping it would mark every finding unverified
        before verification has had a chance to run."""
        pkg = _package(_row())
        pkg.status = PackageStatus.analyzing
        out = reconcile_package_verification(pkg)
        assert out.gap_analysis.gap_table[0].finding == MANDATORY_FINDING


class TestItReachesTheExport:
    """The Word export renders these same fields, so it inherits the stamp
    without needing its own copy of the rule."""

    def test_the_export_renders_the_stamped_text(self):
        from app.services.export_service import _OBLIGATION_LABELS  # noqa: F401

        row = reconcile_package_verification(_package(_row())).gap_analysis.gap_table[0]
        # The export writes row.finding / row.suggested_language verbatim.
        assert UNVERIFIED_FINDING_PREFIX in row.finding
        assert UNVERIFIED_LANGUAGE_PREFIX in row.suggested_language


class TestTheReasonNamesTheRightProblem:
    """A citation that matched nothing is a different problem from a citation
    that matched something non-current, and they need different fixes."""

    def _gate(self, evidence):
        from app.services.orchestrator import PackageOrchestrator
        result = AnalysisResult(
            policy_type="t", audit_ready_summary="s",
            gap_table=[_row(ObligationType.required, evidence)],
        )
        PackageOrchestrator.__new__(PackageOrchestrator)._gate_unproven_mandates(result)
        return result.gap_table[0]

    def test_an_unmatched_citation_is_not_reported_as_a_currency_problem(self):
        unmatched = VerificationEvidence(
            claim_id="c", claim_text="x", status=VerificationStatus.unverified,
            source=EvidenceSource(), checks=EvidenceChecks(),  # nothing matched
        )
        note = self._gate(unmatched).obligation_note

        assert "was not found in the regulatory material" in note
        assert "matching source" not in note, (
            "it reported a source that was never found"
        )

    def test_a_matched_but_superseded_source_still_reports_currency(self):
        superseded = VerificationEvidence(
            claim_id="c", claim_text="x", status=VerificationStatus.cannot_determine,
            source=EvidenceSource(excerpt="old text"),
            checks=EvidenceChecks(
                citation_exists=True, source_status=SourceStatus.superseded,
                source_status_current=False, source_is_binding_law=True,
            ),
        )
        note = self._gate(superseded).obligation_note
        assert "SUPERSEDED" in note
        assert "matching source" in note
