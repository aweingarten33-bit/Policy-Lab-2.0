"""
"Ask the AI" must ground on the analysis being discussed, not just the message.

Production failure this encodes: chat retrieval ran on the chat message alone,
so "explain finding 2" retrieved generic chunks and none of the HIPAA sections
the analysis cited, although the section store held every one of them.

Run: python -m pytest tests/test_chat_grounding.py -v
"""

import asyncio

import pytest

from app.services import chat_service
from app.services.retrieval.models import (
    Jurisdiction, RetrievalContext, RetrievalResult, SourceCategory, SourceChunk,
    SourceMetadata, SourceStatus, SourceType,
)
from app.services.retrieval.retriever import ComplianceRetriever

S164_404 = (
    "(a) Standard—(1) General rule. A covered entity shall, following the discovery "
    "of a breach of unsecured protected health information, notify each individual "
    "whose unsecured protected health information has been, or is reasonably believed "
    "by the covered entity to have been, accessed, acquired, used, or disclosed as a "
    "result of such breach."
)

CONTEXT = (
    "Policy type: Breach Notification Policy\n"
    "Regulations Reviewed: 45 CFR § 164.404\n"
    "Findings:\n"
    "1. Workforce training is not described.\n"
    "2. Individual notice content — 45 CFR § 164.404 — Must fix"
)


@pytest.fixture
def section_store(tmp_path, monkeypatch):
    from app.services.retrieval import section_store as section_module

    store = section_module.SectionStore(persist_dir=str(tmp_path / "kb"))
    monkeypatch.setattr(section_module, "_section_store", store)
    store.put_many([{
        "citation": "45 CFR § 164.404",
        "part_citation": "45 CFR Part 164",
        "source_name": "45 CFR Part 164 — HIPAA — Notification to individuals.",
        "authority": "eCFR",
        "url": "https://www.ecfr.gov/current/title-45/part-164#p-164.404",
        "full_text": S164_404,
        "retrieved_date": "2026-10-05",
        "last_verified_date": "2026-10-05",
        "source_status": SourceStatus.current_verified.value,
    }])
    return store


def _generic_chunk() -> RetrievalResult:
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
    chunk = SourceChunk(id="generic-1", text="Patient rights. A hospital must protect each patient's rights.", metadata=meta)
    return RetrievalResult(chunk=chunk, score=0.4, query="q")


class _MessageOnlyRetriever(ComplianceRetriever):
    """Semantic retrieval that finds nothing about HIPAA -- what "explain finding 2" got."""

    def __init__(self):
        super().__init__()
        self.queries: list[str] = []

    def retrieve_for_step(self, step_name, policy_text, jurisdiction=None, industry=None, **kw):
        self.queries.append(policy_text)
        return RetrievalContext(query=policy_text, retrieved_chunks=[_generic_chunk()], total_sources_found=1)


class _CapturingProvider:
    def __init__(self):
        self.messages = None

    async def complete_chat(self, system_prompt, messages, max_tokens, temperature):
        self.messages = messages
        return "Finding 2 concerns what the notice to individuals must contain."


class _NoLiveResearch:
    async def augment_retrieval_context(self, context, **kw):
        return context


@pytest.fixture
def wired(monkeypatch):
    retriever = _MessageOnlyRetriever()
    provider = _CapturingProvider()
    monkeypatch.setattr(chat_service, "get_retriever", lambda: retriever)
    monkeypatch.setattr(chat_service, "get_provider", lambda: provider)
    monkeypatch.setattr(chat_service, "get_live_research_service", lambda: _NoLiveResearch())

    async def _passthrough(text, ctx):
        return text
    monkeypatch.setattr(chat_service, "_verify_chat_response", _passthrough)
    return retriever, provider


def _source_material(provider) -> str:
    blocks = [m["content"] for m in provider.messages if m["content"].startswith("RETRIEVED SOURCE MATERIAL")]
    assert len(blocks) == 1
    return blocks[0]


def test_section_cited_in_context_reaches_chat_source_material(section_store, wired):
    retriever, provider = wired
    asyncio.run(chat_service.chat("explain finding 2", context_summary=CONTEXT))

    material = _source_material(provider)
    assert "45 CFR § 164.404" in material
    assert "notify each individual" in material
    # Message-based retrieval is kept alongside it.
    assert "42 CFR § 482.13" in material
    # The analysis context drives retrieval, and the message alone still runs.
    assert f"{CONTEXT}\n\nexplain finding 2" in retriever.queries
    assert "explain finding 2" in retriever.queries


def test_cited_section_is_placed_first(section_store, wired):
    retriever, _ = wired
    ctx = chat_service._retrieve_for_chat("explain finding 2", CONTEXT, "healthcare", None)
    citations = [r.chunk.metadata.citation for r in ctx.retrieved_chunks]
    assert citations[0] == "45 CFR § 164.404"
    # The two retrievals returned the same chunk: it appears once.
    assert citations.count("42 CFR § 482.13") == 1


def test_only_citations_literally_in_the_context_are_fetched(section_store, wired):
    # 164.404 is stored but not cited; 164.410 is cited but not stored.
    ctx = chat_service._retrieve_for_chat(
        "explain finding 2", "Regulations Reviewed: 45 CFR § 164.410", "healthcare", None,
    )
    citations = [r.chunk.metadata.citation for r in ctx.retrieved_chunks]
    assert "45 CFR § 164.404" not in citations
    assert "45 CFR § 164.410" not in citations


def test_without_context_retrieval_is_unchanged(section_store, wired):
    retriever, _ = wired
    ctx = chat_service._retrieve_for_chat("what does HIPAA require?", None, "healthcare", None)
    assert retriever.queries == ["what does HIPAA require?"]
    assert [r.chunk.metadata.citation for r in ctx.retrieved_chunks] == ["42 CFR § 482.13"]
