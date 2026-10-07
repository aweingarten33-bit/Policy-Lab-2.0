"""
A finding citing a regulation that IS in the knowledge base must resolve,
however the citation is written. One that is not must stay unverified.

From the live app: 45 CFR Part 164 was loaded (260 chunks), yet findings citing
it were stamped "not found in current authoritative source material". The
lookup matched the citation string almost verbatim, so "45 C.F.R. § 164.404",
"HIPAA Breach Notification Rule, 45 CFR §164.404" and "45 CFR Part 164, §164.404"
missed a section that was stored, and a part, subpart or range citation
("45 CFR Part 164, Subpart D") could never match a section-level record.

Run: python -m pytest tests/test_citation_forms.py -v
"""

import pytest

from app.models.schemas import VerificationStatus
from app.services.retrieval.cfr_citation import canonical_citation, parse_cfr_citation
from app.services.retrieval.models import (
    Jurisdiction, RetrievalContext, RetrievalResult, SourceCategory, SourceChunk,
    SourceMetadata, SourceStatus, SourceType,
)
from app.services.retrieval.verification import VerificationService

S164_404 = (
    "(a) Standard—(1) General rule. A covered entity shall, following the discovery of a breach of "
    "unsecured protected health information, notify each individual whose unsecured protected health "
    "information has been, or is reasonably believed to have been, accessed, acquired, used, or disclosed "
    "as a result of such breach. (b) Implementation specification: Timeliness of notification. A covered "
    "entity shall provide the notification without unreasonable delay and in no case later than 60 "
    "calendar days after discovery of a breach."
)
S164_530 = (
    "(j) Standard: documentation. (2) Implementation specification: retention period. A covered entity "
    "must retain the documentation required by paragraph (j)(1) of this section for 6 years."
)
BREACH_CLAIM = (
    "The policy must require notifying each individual of a breach of unsecured protected health "
    "information within 60 calendar days of discovery."
)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    from app.services.retrieval import section_store as section_module

    store = section_module.SectionStore(persist_dir=str(tmp_path / "kb"))
    monkeypatch.setattr(section_module, "_section_store", store)
    for section, text in (("164.404", S164_404), ("164.530", S164_530)):
        store.put_many([{
            "citation": f"45 CFR § {section}", "part_citation": "45 CFR Part 164",
            "source_name": f"45 CFR Part 164 — § {section}", "full_text": text,
            "source_status": SourceStatus.current_verified.value, "retrieved_date": "2026-10-06",
        }])
    return VerificationService()


def _hospital_only_context():
    """What retrieval returned in the failing run: no Part 164 chunk at all."""
    meta = SourceMetadata(
        source_name="42 CFR Part 482", source_type=SourceType.retrieved_source,
        category=SourceCategory.federal_regulation, jurisdiction=Jurisdiction.federal,
        citation="42 CFR § 482.13", part_citation="42 CFR Part 482",
        source_status=SourceStatus.current_verified, collection="federal_regulation",
    )
    chunk = SourceChunk(id="h", text="Patient's rights. A hospital must protect each patient's rights.", metadata=meta)
    return RetrievalContext(query="q", retrieved_chunks=[RetrievalResult(chunk=chunk, score=0.8, query="q")])


@pytest.mark.parametrize("citation", [
    "45 CFR § 164.404(b)",
    "45 CFR 164.404",
    "45 C.F.R. § 164.404",
    "HIPAA Breach Notification Rule, 45 CFR §164.404",
    "45 CFR Part 164, §164.404",
    "45 CFR §§ 164.400-164.414",
    "45 CFR Part 164, Subpart D",
    "45 CFR Part 164 Subpart D — Notification in the Case of Breach",
    "45 CFR Part 164",
])
def test_a_stored_regulation_resolves_in_every_citation_form(svc, citation):
    ev = svc.build_claim_evidence("f1", BREACH_CLAIM, citation, _hospital_only_context())
    assert ev.status is VerificationStatus.partially_verified, ev.reason
    assert ev.checks.citation_exists is True
    assert "164.404" in (ev.source.name or "")
    assert ev.source.excerpt
    assert ev.citation == citation  # the reader still sees what the finding cited


def test_a_part_level_citation_names_the_section_it_was_checked_against(svc):
    ev = svc.build_claim_evidence("f1", BREACH_CLAIM, "45 CFR Part 164, Subpart D", _hospital_only_context())
    assert "checked against 45 CFR § 164.404" in ev.reason


@pytest.mark.parametrize("citation", [
    "29 CFR §1910.95(m)",          # a part not in the knowledge base
    "45 C.F.R. § 164.404(z)",      # a stored section, nonexistent subsection
    "HIPAA, 45 CFR §164.404(q)(9)",
])
def test_what_is_not_in_the_knowledge_base_stays_unverified(svc, citation):
    ev = svc.build_claim_evidence("f1", BREACH_CLAIM, citation, _hospital_only_context())
    assert ev.status is VerificationStatus.unverified
    assert ev.checks.citation_exists is not True


def test_a_part_citation_does_not_borrow_an_unrelated_section(svc):
    ev = svc.build_claim_evidence(
        "f1", "Forklift operators require certification and annual evaluation.",
        "45 CFR Part 164", _hospital_only_context(),
    )
    assert ev.status is VerificationStatus.unverified


def test_a_range_only_considers_sections_inside_it(svc):
    """§164.530 is in Part 164 but outside 164.400-164.414, so a documentation
    claim cited to the breach range must not resolve to it."""
    ev = svc.build_claim_evidence(
        "f1", "Documentation must be retained for 6 years under the retention period standard.",
        "45 CFR §§ 164.400-164.414", _hospital_only_context(),
    )
    assert "164.530" not in (ev.source.name or "")


class TestParsing:
    @pytest.mark.parametrize("raw, canonical", [
        ("45 C.F.R. § 164.404(b)(1)", "45 CFR § 164.404(b)(1)"),
        ("HIPAA Privacy Rule, 45 CFR §164.530(j)", "45 CFR § 164.530(j)"),
        ("45 CFR Part 164, §164.404", "45 CFR § 164.404"),
        ("45 CFR §§ 164.400–164.414", "45 CFR §§ 164.400-164.414"),
        ("45 CFR Part 164, Subpart D", "45 CFR Part 164"),
        ("Organizational best practice — no regulatory citation applies.", "Organizational best practice — no regulatory citation applies."),
    ])
    def test_canonical_form(self, raw, canonical):
        assert canonical_citation(raw) == canonical

    def test_subpart_is_recorded(self):
        ref = parse_cfr_citation("45 CFR Part 164 Subpart D")
        assert ref.part == "164" and ref.subpart == "D" and not ref.is_section
