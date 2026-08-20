"""
Regression tests for eCFR ingestion.

These exist because of a specific production failure: the XML parser regexed
for literal <SECTION> blocks, which do not appear in eCFR part-level XML (real
sections are DIV8 elements with TYPE="SECTION"). Every fetch therefore returned
sections=[], the knowledge base seeded 0 chunks, and the app silently served
model-only output that still looked source-grounded.

The lesson these tests encode: asserting HTTP 200 would NOT have caught it. The
fetch succeeded. Only asserting on extracted section *content* catches it, so
that is what these check.

Run: python -m pytest tests/test_ecfr_parser.py -v
"""

import xml.etree.ElementTree as ET

from app.services.retrieval.ecfr_client import ECFRClient

# Mirrors real eCFR part-level structure: DIV5(PART) > DIV6(SUBPART) > DIV8(SECTION)
ECFR_XML_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<ECFR>
 <DIV5 TYPE="PART" N="164">
  <HEAD>PART 164—SECURITY AND PRIVACY</HEAD>
  <DIV6 TYPE="SUBPART" N="C">
   <HEAD>Subpart C—Security Standards</HEAD>
   <DIV8 TYPE="SECTION" N="164.308">
    <SECTNO>§ 164.308</SECTNO>
    <SUBJECT>Administrative safeguards.</SUBJECT>
    <P>(a) A covered entity must implement policies and procedures to prevent,
    detect, contain, and correct security violations in accordance with this section.</P>
    <P>(b) Business associate contracts are required before disclosure of
    protected health information to a business associate.</P>
   </DIV8>
   <DIV8 TYPE="SECTION" N="164.530">
    <SECTNO>§ 164.530</SECTNO>
    <SUBJECT>Administrative requirements.</SUBJECT>
    <P>(j) A covered entity must retain the documentation required by this
    section for 6 years from the date of its creation or the date when it last
    was in effect, whichever is later.</P>
   </DIV8>
  </DIV6>
 </DIV5>
</ECFR>
"""


def _parse(xml: str = ECFR_XML_SAMPLE):
    return ECFRClient()._parse_ecfr_xml(xml, 45, 164, "2026-08-01")


def test_extracts_sections_from_real_ecfr_structure():
    """The core regression: DIV8 TYPE=SECTION must yield sections."""
    result = _parse()
    assert len(result["sections"]) > 0, (
        "No sections extracted — this is the exact failure that emptied the "
        "production knowledge base."
    )


def test_extracts_the_specific_expected_section():
    """Assert on content, not just a non-zero count."""
    result = _parse()
    assert any("164.530" in s["section"] for s in result["sections"])
    assert any("164.308" in s["section"] for s in result["sections"])


def test_citation_is_well_formed():
    """Citations must match the format the verification layer matches against."""
    result = _parse()
    section = next(s for s in result["sections"] if "164.530" in s["section"])
    assert section["citation"] == "45 CFR § 164.530"
    # The section symbol belongs in the citation, not the bare section number.
    assert "§" not in section["section"]


def test_section_text_and_heading_are_populated():
    result = _parse()
    section = next(s for s in result["sections"] if "164.530" in s["section"])
    assert section["heading"] == "Administrative requirements."
    assert "6 years" in section["text"], "Paragraph text must survive parsing"


def test_nested_inline_markup_is_flattened():
    """Inline tags inside <P> must not truncate or corrupt the text."""
    xml = ECFR_XML_SAMPLE.replace(
        "<P>(j) A covered entity must retain",
        "<P>(j) A <I>covered entity</I> must <E T='03'>retain</E>",
    )
    result = ECFRClient()._parse_ecfr_xml(xml, 45, 164, "2026-08-01")
    section = next(s for s in result["sections"] if "164.530" in s["section"])
    assert "covered entity" in section["text"]
    assert "retain" in section["text"]
    assert "<I>" not in section["text"]


def test_malformed_xml_degrades_without_raising():
    """A broken document must not take down seeding for every other part."""
    result = ECFRClient()._parse_ecfr_xml("<ECFR><DIV5 unclosed", 45, 164, "2026-08-01")
    assert result["sections"] == []


def test_old_literal_section_format_would_have_failed():
    """Documents the bug: the old <SECTION> shape isn't what eCFR returns."""
    root = ET.fromstring(ECFR_XML_SAMPLE)
    assert root.find(".//SECTION") is None, "eCFR does not use literal <SECTION> elements"
    assert any(
        (n.attrib.get("TYPE") or "").upper() == "SECTION" for n in root.iter()
    ), "sections are carried as TYPE='SECTION' attributes instead"
