"""
GET /api/kb/sources reports what the knowledge base actually holds, per source.

It exists because "is HIPAA in the corpus?" had no direct answer: a total chunk
count cannot show that one regulation is missing. The route is read-only, so it
must work without the admin key, and it must never return regulation text.

Run: python -m pytest tests/test_kb_sources_route.py -v
"""

import pytest
from fastapi.testclient import TestClient

from app.services.retrieval.ingestion import ingest_source_document
from app.services.retrieval.models import Jurisdiction, SourceCategory, SourceType

SECTION_TEXT = "A covered entity shall notify each individual whose unsecured PHI has been breached. " * 20
GUIDANCE_TEXT = "Compliance programs should designate a compliance officer. " * 20


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.services.retrieval import store as store_module
    import app.main as main

    monkeypatch.setattr(store_module, "_store", store_module.ChromaStore(persist_dir=str(tmp_path / "kb")))
    monkeypatch.setattr(main.settings, "kb_seed_at_runtime", False)

    ingest_source_document(
        source_name="45 CFR Part 164 — HIPAA Privacy, Security & Breach Notification — Notification to individuals.",
        text=SECTION_TEXT,
        category=SourceCategory.federal_regulation,
        jurisdiction=Jurisdiction.federal,
        citation="45 CFR § 164.404",
        part_citation="45 CFR Part 164",
        retrieved_date="2026-10-05",
        source_type=SourceType.retrieved_source,
    )
    ingest_source_document(
        source_name="OIG General Compliance Program Guidance (GCPG) — Overview",
        text=GUIDANCE_TEXT,
        category=SourceCategory.federal_guidance,
        jurisdiction=Jurisdiction.federal,
        citation="OIG General Compliance Program Guidance (November 2023)",
        retrieved_date="2026-10-01",
        source_type=SourceType.retrieved_source,
    )

    with TestClient(main.app) as c:
        yield c


def _by_prefix(body):
    return {s["citation_prefix"]: s for s in body["sources"]}


def test_reports_chunk_counts_per_source_without_an_admin_key(client):
    response = client.get("/api/kb/sources")  # no x-admin-key header
    assert response.status_code == 200
    body = response.json()

    sources = _by_prefix(body)
    hipaa = sources["45 CFR Part 164"]
    assert hipaa["collection"] == "federal_regulation"
    assert hipaa["chunk_count"] > 0
    assert hipaa["fetched_as_of"] == "2026-10-05"
    # Labelled from the configured eCFR target, not a single section's name.
    assert hipaa["source"].startswith("45 CFR Part 164 — HIPAA")

    guidance = sources["OIG General Compliance Program Guidance (November 2023)"]
    assert guidance["collection"] == "federal_guidance"
    assert guidance["source"] == "OIG General Compliance Program Guidance (GCPG)"

    assert body["total_chunks"] == hipaa["chunk_count"] + guidance["chunk_count"]


def test_never_returns_chunk_text(client):
    raw = client.get("/api/kb/sources").text
    assert "unsecured PHI" not in raw
    assert "designate a compliance officer" not in raw
    for source in client.get("/api/kb/sources").json()["sources"]:
        assert set(source) == {"source", "citation_prefix", "collection", "chunk_count", "fetched_as_of"}


def test_empty_knowledge_base_returns_an_empty_list(tmp_path, monkeypatch):
    from app.services.retrieval import store as store_module
    import app.main as main

    monkeypatch.setattr(store_module, "_store", store_module.ChromaStore(persist_dir=str(tmp_path / "empty")))
    monkeypatch.setattr(main.settings, "kb_seed_at_runtime", False)
    with TestClient(main.app) as c:
        body = c.get("/api/kb/sources").json()
    assert body == {"total_chunks": 0, "sources": []}
