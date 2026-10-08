"""
Is it verified, and how urgent: two independent fields on every finding.

Seen live: the report said most findings were unconfirmed, yet all six were
MUST FIX; the overview called annual training mandatory while the training
card called it a best-practice recommendation; REQUIRED BY LAW appeared next
to organizational choices. Every finding now has an evidence status
(Verified requirement | Needs source review | Recommendation) and a priority
(Must fix | Should fix), and the summary is built from those objects.

Run: python -m pytest tests/test_evidence_vs_priority.py -v
"""

from datetime import datetime

import pytest

from app.models.schemas import (
    AnalysisResult, ComplianceActionPackage, EvidenceChecks, EvidenceSource, GapRow, GapStatus,
    ObligationType, PackageStatus, RiskLevel, VerificationEvidence, VerificationStatus,
)
from app.services.package_integrity import NOT_CONFIRMED_PREFIX, reconcile_package_verification
from app.services.verification_badge import evidence_status, findings_summary, priority_of

UNCITED = "Organizational best practice — no regulatory citation applies."


def _row(status=VerificationStatus.verified, citation="45 CFR § 164.404", risk=RiskLevel.high,
         obligation=ObligationType.required, clause="Breach notification"):
    return GapRow(
        clause=clause, regulations=[citation], status=GapStatus.gap, risk_level=risk,
        finding="The policy sets no deadline.", suggested_language="Notify within the period the rule sets.",
        citation=citation, obligation_type=obligation,
        regulatory_requirement="A covered entity must notify each individual.",
        evidence=VerificationEvidence(claim_id="c", claim_text="x", status=status,
                                      source=EvidenceSource(excerpt="text"), checks=EvidenceChecks()),
    )


def _sample():
    """The live sample's shape: 2 verified, 3 needing review, 1 recommendation, all high risk."""
    V = VerificationStatus
    return [
        _row(V.verified), _row(V.verified),
        _row(V.partially_verified), _row(V.partially_verified), _row(V.unverified),
        _row(V.unverified, citation=UNCITED, obligation=ObligationType.best_practice,
             clause="Mandatory annual training"),
    ]


def _package(rows):
    return ComplianceActionPackage(
        package_id="p", created_at=datetime.now().isoformat(), policy_type="t",
        gap_analysis=AnalysisResult(policy_type="t", audit_ready_summary="Annual training is mandatory.",
                                    gap_table=rows),
        status=PackageStatus.complete, completed_outputs=["gap_analysis"],
    )


class TestTwoIndependentFields:
    def test_status_rules(self):
        V = VerificationStatus
        assert evidence_status(_row(V.verified)) == "verified_requirement"
        for s in (V.partially_verified, V.unverified, V.contradicted, V.cannot_determine):
            assert evidence_status(_row(s)) == "needs_source_review"
        assert evidence_status(_row(citation=UNCITED)) == "recommendation"
        for o in (ObligationType.best_practice, ObligationType.organizational_choice, ObligationType.guidance):
            assert evidence_status(_row(V.verified, obligation=o)) == "recommendation"

    def test_priority_does_not_follow_evidence(self):
        assert priority_of(_row(VerificationStatus.unverified, risk=RiskLevel.critical)) == "must_fix"
        assert priority_of(_row(VerificationStatus.verified, risk=RiskLevel.moderate)) == "should_fix"

    def test_reconciliation_sets_both_on_every_finding(self):
        rows = reconcile_package_verification(_package(_sample())).gap_analysis.gap_table
        assert [r.evidence_status for r in rows] == [
            "verified_requirement", "verified_requirement", "needs_source_review",
            "needs_source_review", "needs_source_review", "recommendation"]
        assert all(r.priority == "must_fix" for r in rows)


class TestTheSummary:
    def test_counts_equal_the_cards(self):
        pkg = reconcile_package_verification(_package(_sample()))
        rows = pkg.gap_analysis.gap_table
        summary = pkg.gap_analysis.findings_summary
        assert summary.startswith("This review produced 6 findings: 2 verified requirements")
        assert "3 findings need source review" in summary
        assert "1 recommendation (good practice, not a legal requirement)" in summary
        assert "By priority, 6 are Must fix." in summary
        assert summary == findings_summary(rows)

    def test_a_recommendation_is_never_called_mandatory(self):
        summary = findings_summary([_row(citation=UNCITED, clause="Mandatory annual training")])
        assert "mandatory" not in summary.lower()
        assert "required" not in summary.lower()
        assert "not a legal requirement" in summary

    def test_model_prose_is_not_used(self):
        pkg = reconcile_package_verification(_package(_sample()))
        assert "mandatory" not in pkg.gap_analysis.findings_summary.lower()


class TestTheRedPrefixStillWorks:
    def test_once_per_red_finding(self):
        pkg = _package(_sample())
        for _ in range(3):
            pkg = reconcile_package_verification(pkg)
        rows = pkg.gap_analysis.gap_table
        red = [r for r in rows if r.finding.startswith(NOT_CONFIRMED_PREFIX)]
        # Only the unconfirmed cited finding: not the verified, not the partial,
        # not the uncited recommendation.
        assert len(red) == 1
        assert red[0].finding.count(NOT_CONFIRMED_PREFIX) == 1
        assert red[0].suggested_language.count(NOT_CONFIRMED_PREFIX) == 1
        assert red[0].evidence_status == "needs_source_review"
