"""An unconfirmed requirement carries its verdict on the finding, in one place.

History. A production run labelled every finding UNVERIFIED REQUIREMENT while
each one still said "the policy must...". The fix then stamped the prose
("[NOT VERIFIED — ...]") and appended a correction to the executive summary.

The report has since been redesigned so the verdict lives on each finding's
badge and nowhere else: green "Checked against {citation}", yellow "Regulation
found, confirm applicability", red "Citation not confirmed, review before use",
each opening the cited passage beside the claim. A red finding's text and
suggested language also start with the badge's own words, since suggested
language is pasted into real policies; the summary gains no global correction. What still holds, and is
tested here: an unconfirmed requirement is never labelled required by law, its
evidence status (which drives the badge) survives reconciliation, guidance is
still marked as not law, and a verified finding is untouched.

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
    NOT_CONFIRMED_PREFIX,
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


class TestTheVerdictIsOnTheFinding:
    def _unverified_evidence(self):
        return VerificationEvidence(
            claim_id="c", claim_text="x", status=VerificationStatus.unverified,
            source=EvidenceSource(), checks=EvidenceChecks(),
        )

    def test_an_unconfirmed_requirement_is_never_labelled_required(self):
        pkg = reconcile_package_verification(_package(_row()))
        row = pkg.gap_analysis.gap_table[0]
        assert row.obligation_type is ObligationType.unverified_requirement

    def test_the_status_that_drives_the_badge_survives(self):
        row = reconcile_package_verification(
            _package(_row(evidence=self._unverified_evidence()))
        ).gap_analysis.gap_table[0]
        assert row.evidence.status is VerificationStatus.unverified

    def test_a_red_finding_repeats_the_badge_in_its_own_words(self):
        """Suggested language gets pasted into real policies, so a finding whose
        citation was not confirmed says so at the start of both texts, in the
        badge's wording. Original wording follows intact."""
        row = reconcile_package_verification(
            _package(_row(evidence=self._unverified_evidence()))
        ).gap_analysis.gap_table[0]
        assert row.finding == NOT_CONFIRMED_PREFIX + MANDATORY_FINDING
        assert row.suggested_language == NOT_CONFIRMED_PREFIX + MANDATORY_LANGUAGE
        assert "NOT VERIFIED" not in row.finding + row.suggested_language

    def test_a_partially_verified_finding_is_not_prefixed(self):
        evidence = self._unverified_evidence()
        evidence.status = VerificationStatus.partially_verified
        row = reconcile_package_verification(_package(_row(evidence=evidence))).gap_analysis.gap_table[0]
        assert row.finding == MANDATORY_FINDING

    def test_the_prefix_does_not_stack(self):
        pkg = _package(_row(evidence=self._unverified_evidence()))
        for _ in range(5):
            pkg = reconcile_package_verification(pkg)
        assert pkg.gap_analysis.gap_table[0].finding.count(NOT_CONFIRMED_PREFIX) == 1

    def test_a_guidance_backed_finding_gets_its_own_marker(self):
        evidence = _verified_evidence()
        evidence.checks.source_is_binding_law = False
        row = reconcile_package_verification(
            _package(_row(ObligationType.guidance, evidence))
        ).gap_analysis.gap_table[0]

        assert row.finding.startswith(GUIDANCE_FINDING_PREFIX)
        assert "not a legal obligation" in row.finding


class TestNothingGlobal:
    def test_the_summary_gains_no_verification_paragraph(self):
        pkg = reconcile_package_verification(_package(_row(), _row()))
        assert pkg.gap_analysis.audit_ready_summary == CONFIDENT_SUMMARY


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
        evidence = _verified_evidence()
        evidence.checks.source_is_binding_law = False
        pkg = _package(_row(ObligationType.guidance, evidence))
        for _ in range(5):
            pkg = reconcile_package_verification(pkg)
        assert pkg.gap_analysis.gap_table[0].finding.count(GUIDANCE_FINDING_PREFIX) == 1

    def test_an_empty_field_is_not_given_a_marker_alone(self):
        evidence = _verified_evidence()
        evidence.checks.source_is_binding_law = False
        row = _row(ObligationType.guidance, evidence)
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
