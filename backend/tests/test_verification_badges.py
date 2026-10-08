"""
The report carries verification in one place: a badge on each finding.

  verified            -> "Checked against {citation}"
  partially_verified  -> "Regulation found, confirm applicability"
  anything else       -> "Citation not confirmed, review before use"
  no regulation cited -> no badge (a recommendation never claimed to be law)

There is no report-wide banner, count or summary paragraph, and the words
"partially verified" / "not verified" appear nowhere a reader sees. The Word
export prints each finding's label as text; the badge opens the passages
returned on the evidence record, one per cited section.

Run: python -m pytest tests/test_verification_badges.py -v
"""

import io
import pathlib

import docx
import pytest

from app.models.schemas import (
    AnalysisResult, EvidenceChecks, EvidencePassage, EvidenceSource, GapRow, GapStatus,
    RiskLevel, VerificationEvidence, VerificationStatus,
)
from app.services.export_service import generate_docx
from app.services.verification_badge import verification_badge

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _row(status=None, citation="45 CFR § 164.404", passages=None):
    evidence = None
    if status is not None:
        evidence = VerificationEvidence(
            claim_id="f1", claim_text="A covered entity must notify each individual.", status=status,
            source=EvidenceSource(excerpt="text", passages=passages or []), checks=EvidenceChecks(),
        )
    return GapRow(
        clause="Breach notification", regulations=[citation], status=GapStatus.gap,
        risk_level=RiskLevel.high, finding="No deadline.", suggested_language="Notify individuals.",
        citation=citation, regulatory_requirement="A covered entity must notify each individual.",
        recommendations=["Report internally within 24 hours."], evidence=evidence,
    )


class TestTheRule:
    def test_verified_names_the_section_it_was_checked_against(self):
        row = _row(VerificationStatus.verified,
                   passages=[EvidencePassage(citation="45 CFR § 164.404", role="cited", text="...")])
        assert verification_badge(row) == ("green", "Checked against 45 CFR § 164.404")

    def test_a_part_citation_names_the_resolved_section(self):
        row = _row(VerificationStatus.verified, citation="45 CFR Part 164 Subpart D",
                   passages=[EvidencePassage(citation="45 CFR § 164.404", role="cited", text="...")])
        assert verification_badge(row)[1] == "Checked against 45 CFR § 164.404"

    def test_partial(self):
        assert verification_badge(_row(VerificationStatus.partially_verified)) == (
            "yellow", "Regulation found, confirm applicability")

    @pytest.mark.parametrize("status", [VerificationStatus.unverified, VerificationStatus.contradicted,
                                        VerificationStatus.cannot_determine, None])
    def test_everything_else(self, status):
        assert verification_badge(_row(status)) == ("red", "Citation not confirmed, review before use")

    def test_a_recommendation_gets_no_badge(self):
        row = _row(VerificationStatus.unverified,
                   citation="Organizational best practice — no regulatory citation applies.")
        assert verification_badge(row) is None


class TestTheExport:
    def _text(self, *rows):
        result = AnalysisResult(policy_type="P", audit_ready_summary="Model prose: training is mandatory.",
                                gap_table=list(rows))
        d = docx.Document(io.BytesIO(generate_docx(result, "policy.txt")))
        return "\n".join(p.text for p in d.paragraphs) + "\n".join(
            c.text for t in d.tables for r in t.rows for c in r.cells)

    def test_each_finding_carries_evidence_and_priority(self):
        text = self._text(
            _row(VerificationStatus.verified,
                 passages=[EvidencePassage(citation="45 CFR § 164.404", role="cited", text="...")]),
            _row(VerificationStatus.partially_verified),
            _row(VerificationStatus.unverified,
                 citation="Organizational best practice — no regulatory citation applies."),
        )
        assert "Evidence: [Verified requirement · 45 CFR § 164.404]" in text
        assert "Evidence: [Needs source review]" in text
        assert "Evidence: [Recommendation]" in text
        assert text.count("Priority: Must fix") == 3

    def test_the_summary_comes_from_the_findings_not_model_prose(self):
        text = self._text(_row(VerificationStatus.verified))
        assert "Model prose" not in text
        assert "This review produced 1 finding: 1 verified requirement" in text

    def test_no_top_block_and_no_banned_words(self):
        text = self._text(_row(VerificationStatus.partially_verified), _row(VerificationStatus.unverified))
        assert "READ FIRST" not in text
        assert "partially verified" not in text.lower()
        assert "not verified" not in text.lower()

    def test_a_recommendation_never_reads_as_legally_required(self):
        from app.models.schemas import ObligationType
        row = _row(VerificationStatus.verified)
        row.obligation_type = ObligationType.best_practice
        text = self._text(row)
        assert "LEGALLY REQUIRED" not in text
        assert "Evidence: [Recommendation]" in text


class TestTheScreen:
    """Source checks on the frontend, since the suite has no browser."""

    def _src(self, rel):
        path = ROOT / "frontend" / "src" / rel
        if not path.exists():
            pytest.skip("frontend not present")
        return path.read_text()

    def test_the_three_evidence_labels_and_two_priorities(self):
        findings = self._src("lib/findings.ts")
        for label in ("Verified requirement", "Needs source review", "Recommendation", "Must fix", "Should fix"):
            assert label in findings

    def test_the_overview_is_built_from_the_findings(self):
        index = self._src("pages/Index.tsx")
        assert "findingsSummary(rows)" in index
        assert "{ga.audit_ready_summary}" not in index
        assert "priority_findings?.slice" not in index

    def test_the_summary_wording_matches_the_backend(self):
        """lib/findings.ts and verification_badge.findings_summary must say the same thing."""
        findings = self._src("lib/findings.ts")
        for phrase in ("the cited regulation was checked and supports",
                       "source review (a regulation is cited, but its text did not confirm the requirement as stated)",
                       "(good practice, not ", "This review produced ", "By priority, "):
            assert phrase in findings

    @pytest.mark.parametrize("rel", ["pages/Index.tsx", "components/VerificationBadge.tsx",
                                     "components/ResultNotices.tsx", "lib/findings.ts"])
    def test_no_banned_words_in_rendered_strings(self, rel):
        import re
        src = self._src(rel)
        code = re.sub(r"//[^\n]*|/\*[\s\S]*?\*/", "", src)
        code = code.replace(r"\bNOT VERIFIED\b", "")
        assert "partially verified" not in code.lower()
        assert "not verified" not in code.lower()
        assert "verified against source" not in code.lower()
