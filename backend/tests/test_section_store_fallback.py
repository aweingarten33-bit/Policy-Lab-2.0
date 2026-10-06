"""
A citation must be checkable against the stored section even when retrieval
did not happen to return it.

Production failure this encodes: retrieval keeps ~5 chunks per step. A gap
analysis whose top results were 42 CFR 482 and 42 CFR Part 2 could not verify
a single 45 CFR 164 finding, although every §164 section was in the baked
store -- verification only looked at the chunks retrieved for that request.

Run: python -m pytest tests/test_section_store_fallback.py -v
"""

import pytest

from app.models.schemas import VerificationStatus
from app.services.retrieval.models import (
    Jurisdiction, RetrievalContext, RetrievalResult, SourceCategory, SourceChunk,
    SourceMetadata, SourceStatus, SourceType,
)
from app.services.retrieval.verification import VerificationService

S164_404 = (
    "(a) Standard—(1) General rule. A covered entity shall, following the discovery "
    "of a breach of unsecured protected health information, notify each individual "
    "whose unsecured protected health information has been, or is reasonably believed "
    "by the covered entity to have been, accessed, acquired, used, or disclosed as a "
    "result of such breach. (b) Implementation specification: Timeliness of notification. "
    "A covered entity shall provide the notification without unreasonable delay and in "
    "no case later than 60 calendar days after discovery of a breach."
)


@pytest.fixture
def section_store(tmp_path, monkeypatch):
    from app.services.retrieval import section_store as section_module

    store = section_module.SectionStore(persist_dir=str(tmp_path / "kb"))
    monkeypatch.setattr(section_module, "_section_store", store)
    return store


def _put(store, status=SourceStatus.current_verified.value):
    store.put_many([{
        "citation": "45 CFR § 164.404",
        "part_citation": "45 CFR Part 164",
        "source_name": "45 CFR Part 164 — HIPAA — Notification to individuals.",
        "authority": "eCFR",
        "url": "https://www.ecfr.gov/current/title-45/part-164#p-164.404",
        "full_text": S164_404,
        "retrieved_date": "2026-10-05",
        "last_verified_date": "2026-10-05",
        "source_status": status,
    }])


def _context_without_hipaa():
    """What the failing request actually retrieved: hospital CoPs, no HIPAA."""
    meta = SourceMetadata(
        source_name="42 CFR Part 482 — Conditions of Participation (Hospitals)",
        source_type=SourceType.retrieved_source,
        category=SourceCategory.federal_regulation,
        jurisdiction=Jurisdiction.federal,
        citation="42 CFR § 482.13",
        part_citation="42 CFR Part 482",
        source_status=SourceStatus.current_verified,
        collection="federal_regulation",
    )
    chunk = SourceChunk(id="c1", text="Patient rights. A hospital must protect and promote each patient's rights.", metadata=meta)
    return RetrievalContext(query="privacy policy", retrieved_chunks=[RetrievalResult(chunk=chunk, score=0.8, query="q")])


def test_citation_outside_retrieved_chunks_is_found_in_the_section_store(section_store):
    _put(section_store)
    attribution = VerificationService().create_source_attribution(
        "45 CFR § 164.404(b)", _context_without_hipaa(),
        claim_text="Notification must be sent within 60 calendar days of discovery.",
    )
    assert attribution.verification_status == VerificationStatus.partially_verified
    assert attribution.source_citation == "45 CFR § 164.404"
    assert "60 calendar days" in attribution.retrieved_text


def test_missing_subsection_is_still_unverified(section_store):
    """The fallback finds the section; it does not excuse a subsection that
    the regulation does not contain."""
    _put(section_store)
    attribution = VerificationService().create_source_attribution(
        "45 CFR § 164.404(z)", _context_without_hipaa()
    )
    assert attribution.verification_status == VerificationStatus.unverified


def test_unknown_standing_fails_closed(section_store):
    _put(section_store, status="something-new")
    attribution = VerificationService().create_source_attribution(
        "45 CFR § 164.404(a)", _context_without_hipaa()
    )
    assert attribution.verification_status == VerificationStatus.cannot_determine


def test_section_not_in_store_is_not_invented(section_store, monkeypatch):
    service = VerificationService()
    monkeypatch.setattr(service.store.__class__, "query_all_collections", lambda *a, **k: [])
    attribution = service.create_source_attribution("45 CFR § 164.530(j)", _context_without_hipaa())
    assert attribution.verification_status != VerificationStatus.partially_verified
