"""
Outputs must not claim more than was done.

From a live audit:
  * Selecting New York made the interface say NY law was checked, with no NY
    text loaded. State coverage is now derived from what was retrieved.
  * An analysis said records "would not exist" -- no records were inspected.
    Findings are now document gaps or implementation questions, never
    compliance determinations.
  * Verification status sat in footer metadata while the document opened with
    the regulations applied. Limitations now open every export.

Run: python -m pytest tests/test_honest_outputs.py -v
"""

import io
import json

import docx

from app.models.schemas import AnalysisResult, FindingKind, RewrittenPolicy
from app.services.export_service import (
    generate_docx, generate_draft_policy_docx, generate_updated_policy_docx,
)
from app.services.industry_config import INDUSTRIES
from app.services.llm_service import ANALYTICAL_PROTOCOL, RESPONSE_SCHEMA, _build_user_prompt, _parse_llm_response
from app.services.retrieval.models import (
    Jurisdiction, RetrievalContext, RetrievalResult, SourceCategory, SourceChunk,
    SourceMetadata, SourceStatus, SourceType,
)
from app.services.state_coverage import state_coverage


def _result(category, source_type, name, url=None, jurisdiction=Jurisdiction.federal):
    meta = SourceMetadata(
        source_name=name, source_type=source_type, category=category, jurisdiction=jurisdiction,
        citation=name, url=url, source_status=SourceStatus.current_verified, collection=category.value,
    )
    return RetrievalResult(chunk=SourceChunk(id=name, text="t", metadata=meta), score=0.5, query="q")


FEDERAL = _result(SourceCategory.federal_regulation, SourceType.retrieved_source, "45 CFR § 164.404")


class TestStateCoverage:
    def test_no_state_selected_reports_nothing(self):
        assert state_coverage(RetrievalContext(query="q", retrieved_chunks=[FEDERAL]), None) is None

    def test_state_selected_with_no_state_material_says_so(self):
        cov = state_coverage(RetrievalContext(query="q", retrieved_chunks=[FEDERAL]), "NY")
        assert cov.jurisdiction == "NY"
        assert cov.sources_consulted == []
        assert cov.state_text_available is False
        assert "No NY state law text was available" in cov.summary
        assert "verified" not in cov.summary.lower().replace("not been verified", "")

    def test_live_search_pages_are_listed_and_not_called_statute_text(self):
        page = _result(SourceCategory.state_law, SourceType.live_research,
                       "NY DOH home care regulations page", url="https://health.ny.gov/x")
        ctx = RetrievalContext(query="q", retrieved_chunks=[FEDERAL], live_research_results=[page])
        cov = state_coverage(ctx, "NY")
        assert [s.name for s in cov.sources_consulted] == ["NY DOH home care regulations page"]
        assert cov.sources_consulted[0].kind == "live_search"
        assert cov.state_text_available is False
        assert "not the codified NY statute" in cov.summary

    def test_state_prompts_do_not_demand_state_citations_from_memory(self):
        for slug, cfg in INDUSTRIES.items():
            addendum = cfg.get("state_addendum")
            if not addendum:
                continue
            text = addendum.format(jurisdiction="NY")
            assert "Cite state law by code section" not in text, slug
            assert "MUST also check" not in text, slug
            assert "from memory" in text, slug
        user = _build_user_prompt("A policy text that is long enough to analyze.", "healthcare", "NY")
        assert "Include all applicable NY state regulations" not in user


class TestFindingKinds:
    def test_the_prompt_no_longer_asks_whether_records_exist(self):
        assert "would it exist" not in ANALYTICAL_PROTOCOL
        assert "whether it would exist" not in RESPONSE_SCHEMA
        assert "DOCUMENT GAP" in ANALYTICAL_PROTOCOL and "IMPLEMENTATION QUESTION" in ANALYTICAL_PROTOCOL
        assert '"finding_kind"' in RESPONSE_SCHEMA and '"implementation_question"' in RESPONSE_SCHEMA

    def _parse(self, kind, question=None):
        row = {
            "clause": "Complaint investigation", "regulations": ["42 CFR §484.50(e)"], "axes_passed": 1,
            "finding": "The policy does not require investigation of family complaints.",
            "suggested_language": "x", "citation": "42 CFR §484.50(e)(1)(i)",
            "finding_kind": kind,
        }
        if question:
            row["implementation_question"] = question
        return _parse_llm_response(json.dumps({"policy_type": "P", "gap_table": [row], "audit_ready_summary": "s"}))

    def test_a_compliance_determination_is_never_passed_through(self):
        """No records are inspected, so no finding may claim to determine compliance."""
        assert self._parse("compliance_determination").gap_table[0].finding_kind is FindingKind.document_gap

    def test_document_gaps_and_questions_are_kept(self):
        q = "Does the agency investigate complaints from family members?"
        parsed = self._parse("implementation_question", q).gap_table[0]
        assert parsed.finding_kind is FindingKind.implementation_question
        assert parsed.implementation_question == q
        assert self._parse("document_gap").gap_table[0].finding_kind is FindingKind.document_gap

    def test_unknown_or_missing_kind_defaults_to_document_gap(self):
        assert self._parse("nonsense").gap_table[0].finding_kind is FindingKind.document_gap
        assert self._parse(None).gap_table[0].finding_kind is FindingKind.document_gap


def _body_order(file_bytes):
    """Top-level blocks of a .docx in order: ('p'|'tbl', text)."""
    document = docx.Document(io.BytesIO(file_bytes))
    return [
        (el.tag.split("}")[1], el.xpath("string(.)").strip())
        for el in document.element.body.iterchildren()
        if el.xpath("string(.)").strip()
    ]


def _first_index(blocks, needle):
    return next(i for i, (_, text) in enumerate(blocks) if needle in text)


class TestLimitationsComeFirst:
    def test_draft_export_opens_with_limitations_before_regulations(self):
        blocks = _body_order(generate_draft_policy_docx({
            "policy_title": "Complaint Policy", "effective_date": "[EFFECTIVE DATE]", "version": "1.0",
            "regulations_applied": ["42 CFR §484.50"],
            "sections": [{"title": "I. Purpose", "content": "Body."}],
            "verification_overall": "6 of 6 citation-backed claim(s) are not fully verified.",
            "decisions_required": ["[ACCOUNTABLE ROLE 1]: who investigates"],
        }))
        banner = _first_index(blocks, "READ FIRST")
        assert "6 of 6" in blocks[banner][1]
        assert banner < _first_index(blocks, "REGULATORY FRAMEWORK")
        assert banner < _first_index(blocks, "I. PURPOSE")
        assert _first_index(blocks, "DECISIONS YOU NEED TO MAKE") < _first_index(blocks, "REGULATORY FRAMEWORK")

    def test_gap_report_opens_with_limitations_before_findings(self):
        blocks = _body_order(generate_docx(
            AnalysisResult(policy_type="P", audit_ready_summary="Summary."), "policy.txt",
        ))
        banner = _first_index(blocks, "READ FIRST")
        assert "No records, logs or practices were inspected" in blocks[banner][1]
        assert banner < _first_index(blocks, "Summary of Findings")

    def test_proposed_revision_export_says_it_was_not_rechecked(self):
        blocks = _body_order(generate_updated_policy_docx(RewrittenPolicy(
            policy_title="R", effective_date="", version_note="v", sections=[],
            full_text="Body text.", change_summary="c",
        )))
        banner = _first_index(blocks, "PROPOSED REVISION")
        assert "has not been re-analyzed" in blocks[banner][1]
        assert banner < _first_index(blocks, "Body text.")
