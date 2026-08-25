"""Regulatory text is not silently cut, and the full cited scope stays reachable.

The eCFR parser capped every section at 3,000 characters before anything else
saw it. CFR sections routinely run several times that, so the corpus looked
complete while missing the back half of the longest and most heavily cited
provisions — and when verification could not find a subsection that had simply
been thrown away, it reported the claim unverifiable as though the regulation
were at fault.

Two things are checked. That the parser keeps the whole section, and that
verification can still resolve a subsection the retrieval chunk does not
contain, via the authoritative section store.

Run: python -m pytest tests/evaluation/test_no_silent_truncation.py -v
"""

import pytest

from app.models.schemas import ClaimSupport, VerificationStatus
from app.services.retrieval.ecfr_client import ECFRClient
from app.services.retrieval.section_store import SectionStore, citation_key

from tests.evaluation.cases import (
    LONG_SECTION_TEXT,
    make_context,
    make_result,
)


def _xml_with_section(section_no: str, paragraphs) -> str:
    body = "".join(f"<P>{p}</P>" for p in paragraphs)
    return (
        '<DIV5 TYPE="PART" N="999">'
        f'<DIV8 TYPE="SECTION" N="{section_no}">'
        f"<SECTNO>§ {section_no}</SECTNO><SUBJECT>Notification requirements.</SUBJECT>"
        f"{body}"
        "</DIV8></DIV5>"
    )


class TestTheParserKeepsWholeSections:
    def test_a_long_section_survives_intact(self):
        """The regression case: a section far longer than the old 3,000 cap."""
        paragraphs = [
            "(a) A covered organization shall provide notification.",
            "Filler paragraph establishing context. " * 200,
            "(z) The final paragraph states the retention period of seven years.",
        ]
        xml = _xml_with_section("999.45", paragraphs)

        parsed = ECFRClient()._parse_ecfr_xml(xml, 99, 999, "2026-08-25")
        (section,) = parsed["sections"]

        assert len(section["text"]) > 3000, "the fixture must exceed the old cap"
        assert "(z) The final paragraph" in section["text"], (
            "the closing paragraph was cut — this is the truncation defect"
        )

    def test_nothing_is_capped_at_exactly_3000(self):
        paragraphs = ["x" * 5000, "(b) Trailing subsection."]
        parsed = ECFRClient()._parse_ecfr_xml(
            _xml_with_section("999.50", paragraphs), 99, 999, "2026-08-25"
        )
        text = parsed["sections"][0]["text"]
        assert len(text) != 3000
        assert text.endswith("(b) Trailing subsection.")

    def test_each_section_keeps_its_own_citation(self):
        """Sections are ingested individually so a claim citing §999.45 can be
        matched. Concatenating a part into one document under the part-level
        citation meant no section-level claim could ever match anything."""
        xml = (
            '<DIV5 TYPE="PART" N="999">'
            '<DIV8 TYPE="SECTION" N="999.45"><SECTNO>§ 999.45</SECTNO>'
            "<SUBJECT>Notification.</SUBJECT><P>" + "a" * 100 + "</P></DIV8>"
            '<DIV8 TYPE="SECTION" N="999.46"><SECTNO>§ 999.46</SECTNO>'
            "<SUBJECT>Alternative.</SUBJECT><P>" + "b" * 100 + "</P></DIV8>"
            "</DIV5>"
        )
        parsed = ECFRClient()._parse_ecfr_xml(xml, 99, 999, "2026-08-25")
        citations = {s["citation"] for s in parsed["sections"]}
        assert citations == {"99 CFR § 999.45", "99 CFR § 999.46"}


class TestTheAuthoritativeSectionStore:
    @pytest.fixture
    def store(self, tmp_path):
        return SectionStore(persist_dir=str(tmp_path))

    def test_a_stored_section_round_trips_whole(self, store):
        store.put_many([{"citation": "99 CFR § 999.45", "full_text": LONG_SECTION_TEXT}])
        assert store.get_text("99 CFR § 999.45") == LONG_SECTION_TEXT

    @pytest.mark.parametrize("written_as", [
        "99 CFR § 999.45",
        "99 CFR §999.45",
        "99 CFR 999.45",
        "99 cfr section 999.45",
        "99 CFR § 999.45(c)(1)",
    ])
    def test_a_citation_resolves_however_it_is_written(self, store, written_as):
        """A finding writes a citation in whatever style the model produced; a
        lookup that only matched one spelling would miss most of them."""
        store.put_many([{"citation": "99 CFR § 999.45", "full_text": LONG_SECTION_TEXT}])
        assert store.get_text(written_as) == LONG_SECTION_TEXT

    def test_a_missing_section_is_none_not_an_error(self, store):
        assert store.get_text("99 CFR § 111.11") is None

    def test_subsection_markers_do_not_split_the_key(self, store):
        assert citation_key("99 CFR § 999.45(c)(1)") == citation_key("99 CFR § 999.45")


class TestVerificationReachesPastTheChunkBoundary:
    """The point of the store: retrieval chunks are ~800 characters, so the
    paragraph that decides a claim is frequently in the next chunk."""

    @pytest.fixture
    def verifier(self, tmp_path, monkeypatch):
        from app.services.retrieval import section_store as section_module
        from app.services.retrieval import store as store_module
        from app.services.retrieval.verification import VerificationService

        monkeypatch.setattr(
            store_module, "_store", store_module.ChromaStore(persist_dir=str(tmp_path / "kb"))
        )
        section_store = section_module.SectionStore(persist_dir=str(tmp_path / "kb"))
        monkeypatch.setattr(section_module, "_section_store", section_store)
        return VerificationService(), section_store

    def test_a_subsection_outside_the_retrieved_chunk_still_verifies(self, verifier):
        service, section_store = verifier
        section_store.put_many([
            {"citation": "99 CFR § 999.45", "full_text": LONG_SECTION_TEXT}
        ])

        # The retrieved chunk is the OPENING of the section and contains no (c).
        chunk_text = LONG_SECTION_TEXT[:800]
        assert "(c) Content of notification" not in chunk_text

        evidence = service.build_claim_evidence(
            claim_id="t1",
            claim_text=(
                "The organization shall retain a copy of each notification for "
                "seven years under 99 CFR § 999.45(c)."
            ),
            citation="99 CFR § 999.45(c)",
            retrieval_context=make_context(make_result("99 CFR § 999.45", chunk_text)),
        )

        assert evidence.checks.citation_exists is True, evidence.reason
        assert "seven years" in (evidence.source.excerpt or "")

        service.apply_claim_support(evidence, ClaimSupport.supported)
        assert evidence.status is VerificationStatus.verified, evidence.reason

    def test_without_the_store_the_same_claim_cannot_be_checked(self, verifier):
        """Shows the store is what makes the difference, not a looser check.

        With an empty store the subsection genuinely is not available, and the
        honest answer is that the citation could not be located — not a
        verified claim resting on the chunk that happened to be retrieved."""
        service, _ = verifier
        chunk_text = LONG_SECTION_TEXT[:800]

        evidence = service.build_claim_evidence(
            claim_id="t2",
            claim_text="Retention is seven years under 99 CFR § 999.45(c).",
            citation="99 CFR § 999.45(c)",
            retrieval_context=make_context(make_result("99 CFR § 999.45", chunk_text)),
        )
        assert evidence.checks.citation_exists is False
        assert evidence.status is not VerificationStatus.verified

    def test_the_store_cannot_manufacture_a_missing_subsection(self, verifier):
        """The fallback widens the search; it must not weaken the answer. A
        subsection absent from the complete section text is still absent."""
        service, section_store = verifier
        section_store.put_many([
            {"citation": "99 CFR § 999.45", "full_text": LONG_SECTION_TEXT}
        ])

        evidence = service.build_claim_evidence(
            claim_id="t3",
            claim_text="Paragraph (q) requires quarterly certification.",
            citation="99 CFR § 999.45(q)",
            retrieval_context=make_context(make_result("99 CFR § 999.45", LONG_SECTION_TEXT[:800])),
        )
        assert evidence.checks.citation_exists is False
        assert evidence.status is not VerificationStatus.verified


class TestTheEmbedBoundIsNotTheOldTruncation:
    """Bounding what is INDEXED must not bound what can be CHECKED.

    Corpus build time is linear in embedded characters, and removing the old
    3,000-character cut made every long section embed in full — which roughly
    doubled the build and pushed CI past its limit. The bound restores a budget
    on the index.

    It is a different thing from the cut it replaces, and this is where that has
    to be proven. The old cut discarded text before anything else saw it, so a
    subsection past it was gone from the system. The bound only limits the
    vector index; the complete section still goes to the authoritative section
    store, and verification reads from there.
    """

    @pytest.fixture
    def built(self, tmp_path, monkeypatch):
        from app.config import settings
        from app.services.retrieval import section_store as section_module
        from app.services.retrieval import store as store_module
        from app.services.retrieval.ecfr_client import parts_to_source_chunks
        from app.services.retrieval.models import SourceCategory
        from app.services.retrieval.seed_data import ingest_cfr_part_sections
        from app.services.retrieval.verification import VerificationService

        monkeypatch.setattr(settings, "kb_max_embed_chars_per_section", 1000)
        monkeypatch.setattr(
            store_module, "_store", store_module.ChromaStore(persist_dir=str(tmp_path / "kb"))
        )
        section_store = section_module.SectionStore(persist_dir=str(tmp_path / "kb"))
        monkeypatch.setattr(section_module, "_section_store", section_store)

        parsed = {
            "title": 99, "part": 999, "fetched_date": "2026-08-25",
            "sections": [{
                "section": "999.45",
                "heading": "Notification requirements.",
                "text": LONG_SECTION_TEXT,
                "citation": "99 CFR § 999.45",
            }],
        }
        chunks = parts_to_source_chunks(parsed, SourceCategory.federal_regulation)
        ingest_cfr_part_sections(
            chunks, title=99, part=999,
            category=SourceCategory.federal_regulation, fetched_date="2026-08-25",
        )
        return VerificationService(), section_store

    def test_the_full_section_is_still_stored_whole(self, built):
        _, section_store = built
        stored = section_store.get_text("99 CFR § 999.45")
        assert len(stored) > 3000
        assert "(c) Content of notification" in stored
        assert "seven years" in stored

    def test_a_claim_about_text_beyond_the_bound_still_verifies(self, built):
        """The bound is 1,000 characters here and the controlling paragraph sits
        past 3,000, so it is far outside anything embedded."""
        service, _ = built
        ctx = make_context(make_result("99 CFR § 999.45", LONG_SECTION_TEXT[:1000]))
        evidence = service.build_claim_evidence(
            claim_id="bound",
            claim_text=(
                "The organization shall retain a copy of each notification for "
                "seven years under 99 CFR § 999.45(c)."
            ),
            citation="99 CFR § 999.45(c)",
            retrieval_context=ctx,
        )
        assert evidence.checks.citation_exists is True, evidence.reason
        assert "seven years" in (evidence.source.excerpt or "")

        service.apply_claim_support(evidence, ClaimSupport.supported)
        assert evidence.status is VerificationStatus.verified, evidence.reason

    def test_the_bound_is_configurable_and_documented(self):
        from app.config import Settings
        field = Settings.model_fields["kb_max_embed_chars_per_section"]
        assert field.default >= 3000, "the bound must not be tighter than the cut it replaces"
