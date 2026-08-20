"""
Federal compliance-program guidance must load from the bundled PDFs.

Why this matters: the seven elements of an effective compliance program are the
framework every healthcare compliance officer works from, and the framework the
app tags every gap finding against. They appear in OIG guidance, not in the
Code of Federal Regulations -- so no amount of eCFR seeding can ground them.
Before this loader existed the app named the seven elements purely from model
memory while presenting itself as source-grounded.

These tests run the real extractor over the real bundled PDFs where they are
present, and fall back to synthetic text for the parsing rules so the suite
still means something in a checkout without them.

Run: python -m pytest tests/test_guidance_loader.py -v
"""

import os

import pytest

from app.services.retrieval.guidance_client import (
    BUNDLED_DIR,
    GUIDANCE_DOCUMENTS,
    extract_pdf_text,
    find_pdf_link,
    split_into_sections,
    _MAX_SECTION_CHARS,
)
from app.services.retrieval.models import SourceCategory


def _bundled(doc):
    return os.path.join(BUNDLED_DIR, doc.bundled_filename) if doc.bundled_filename else None


BUNDLED_DOCS = [d for d in GUIDANCE_DOCUMENTS if d.bundled_filename]


class TestCatalogue:
    def test_the_two_core_documents_are_bundled(self):
        """These two are the ones that must never depend on network access."""
        keys = {d.key for d in BUNDLED_DOCS}
        assert "oig_gcpg_2023" in keys
        assert "oig_hcca_effectiveness" in keys

    def test_every_document_has_a_resolvable_route(self):
        for doc in GUIDANCE_DOCUMENTS:
            assert doc.bundled_filename or doc.pdf_urls or doc.landing_url, doc.key

    def test_guidance_has_its_own_collection(self):
        """Guidance must not be filed as federal_regulation -- it is not
        codified, and conflating the two would let the app present guidance as
        binding regulation."""
        from app.services.retrieval.store import ChromaStore
        assert SourceCategory.federal_guidance.value in ChromaStore.COLLECTION_NAMES

    def test_guidance_is_actually_searched_during_retrieval(self):
        """A collection nothing queries is dead weight."""
        from app.services.retrieval.retriever import ComplianceRetriever
        retriever = ComplianceRetriever.__new__(ComplianceRetriever)
        for step in ("gap_analysis", "remediation_plan", "board_summary"):
            assert "federal_guidance" in retriever._get_relevant_collections(step)


@pytest.mark.parametrize("doc", BUNDLED_DOCS, ids=lambda d: d.key)
class TestRealBundledPDFs:
    def test_file_is_present_and_is_a_pdf(self, doc):
        path = _bundled(doc)
        assert os.path.exists(path), f"missing bundled guidance: {path}"
        with open(path, "rb") as handle:
            assert handle.read(4) == b"%PDF"

    def test_extraction_yields_substantial_text(self, doc):
        with open(_bundled(doc), "rb") as handle:
            text = extract_pdf_text(handle.read())
        assert len(text) > 50_000, f"{doc.key} extracted only {len(text)} chars"

    def test_sections_are_bounded(self, doc):
        """A single 100,000-character section made every chunk inherit one
        useless label. Sections stay capped."""
        with open(_bundled(doc), "rb") as handle:
            sections = split_into_sections(extract_pdf_text(handle.read()))
        assert len(sections) >= 10
        assert max(len(s["text"]) for s in sections) <= _MAX_SECTION_CHARS


def test_gcpg_contains_the_seven_elements():
    """The specific claim the app makes on every healthcare analysis."""
    doc = next(d for d in GUIDANCE_DOCUMENTS if d.key == "oig_gcpg_2023")
    path = _bundled(doc)
    if not os.path.exists(path):
        pytest.skip("GCPG PDF not bundled in this checkout")

    with open(path, "rb") as handle:
        text = extract_pdf_text(handle.read()).lower()

    for element in (
        "written policies",
        "compliance leadership",
        "training and education",
        "lines of communication",
        "risk assessment",
        "corrective action",
    ):
        assert element in text, f"GCPG text is missing {element!r}"


class TestSectionSplitting:
    def test_oversized_sections_are_split_and_labelled(self):
        text = "OVERVIEW OF REQUIREMENTS\n" + ("compliance obligation text " * 900)
        sections = split_into_sections(text)
        assert len(sections) > 1
        assert all(len(s["text"]) <= _MAX_SECTION_CHARS for s in sections)
        assert sections[0]["heading"] == "OVERVIEW OF REQUIREMENTS"
        assert "cont." in sections[1]["heading"]

    def test_table_of_contents_rows_are_dropped(self):
        text = (
            "III. Compliance Program Infrastructure ......... 12\n"
            "IV. Other Matters .................. 45\n"
            "REAL SECTION HEADING\n"
            + ("substantive guidance content that should survive. " * 20)
        )
        sections = split_into_sections(text)
        assert not any("........." in s["text"] for s in sections)

    def test_unstructured_text_still_ingests(self):
        """A layout change must degrade quality, not drop the document."""
        sections = split_into_sections("plain prose with no headings at all. " * 200)
        assert sections
        assert sum(len(s["text"]) for s in sections) > 1000

    def test_empty_input_yields_nothing(self):
        assert split_into_sections("") == []


class TestLandingPageDiscovery:
    """OIG's direct PDF paths differ per document and have moved, so the
    loader resolves them from landing pages as a fallback."""

    def test_prefers_a_compliance_guidance_pdf(self):
        html = '''
            <a href="/documents/newsletter/2026-spring.pdf">Newsletter</a>
            <a href="/documents/compliance/10038/nursing-facility-icpg.pdf">ICPG</a>
        '''
        assert find_pdf_link(html, "https://oig.hhs.gov/compliance/x/").endswith(
            "nursing-facility-icpg.pdf"
        )

    def test_relative_links_become_absolute(self):
        html = '<a href="/documents/compliance-guidance/1135/gcpg.pdf">PDF</a>'
        assert find_pdf_link(html, "https://oig.hhs.gov/compliance/x/") == (
            "https://oig.hhs.gov/documents/compliance-guidance/1135/gcpg.pdf"
        )

    def test_no_pdf_returns_none(self):
        assert find_pdf_link("<a href='/about'>About</a>", "https://oig.hhs.gov/") is None


def test_non_pdf_bytes_do_not_crash_extraction():
    """A 200 response that is really an HTML error page must not raise."""
    assert extract_pdf_text(b"<html><body>Not Found</body></html>") == ""
