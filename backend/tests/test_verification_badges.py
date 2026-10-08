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
        result = AnalysisResult(policy_type="P", audit_ready_summary="Summary.", gap_table=list(rows))
        d = docx.Document(io.BytesIO(generate_docx(result, "policy.txt")))
        return "\n".join(p.text for p in d.paragraphs) + "\n".join(
            c.text for t in d.tables for r in t.rows for c in r.cells)

    def test_each_finding_carries_its_label(self):
        text = self._text(
            _row(VerificationStatus.verified,
                 passages=[EvidencePassage(citation="45 CFR § 164.404", role="cited", text="...")]),
            _row(VerificationStatus.partially_verified),
            _row(VerificationStatus.unverified),
        )
        assert "Verification: [Checked against 45 CFR § 164.404]" in text
        assert "Verification: [Regulation found, confirm applicability]" in text
        assert "Verification: [Citation not confirmed, review before use]" in text

    def test_no_top_block_and_no_banned_words(self):
        text = self._text(_row(VerificationStatus.partially_verified), _row(VerificationStatus.unverified))
        assert "READ FIRST" not in text
        assert "partially verified" not in text.lower()
        assert "not verified" not in text.lower()

    def test_recommendations_have_no_label(self):
        row = _row(VerificationStatus.unverified,
                   citation="Organizational best practice — no regulatory citation applies.")
        assert "Verification:" not in self._text(row)


class TestTheScreen:
    """Source checks on the frontend, since the suite has no browser."""

    def _src(self, rel):
        path = ROOT / "frontend" / "src" / rel
        if not path.exists():
            pytest.skip("frontend not present")
        return path.read_text()

    def test_no_report_banner(self):
        index = self._src("pages/Index.tsx")
        assert "analysisLimitations" not in index
        assert "analysisLimitations" not in self._src("components/ResultNotices.tsx")

    def test_the_three_labels(self):
        badge = self._src("components/VerificationBadge.tsx")
        for label in ("Checked against ${", "Regulation found, confirm applicability",
                      "Citation not confirmed, review before use"):
            assert label in badge

    @pytest.mark.parametrize("rel", ["pages/Index.tsx", "components/VerificationBadge.tsx",
                                     "components/ResultNotices.tsx"])
    def test_no_banned_words_in_rendered_strings(self, rel):
        import re
        src = self._src(rel)
        # Rendered strings only: skip comments and the marker-stripping regex.
        code = re.sub(r"//[^\n]*|/\*[\s\S]*?\*/", "", src)
        code = code.replace(r"\bNOT VERIFIED\b", "")
        assert "partially verified" not in code.lower()
        assert "not verified" not in code.lower()
