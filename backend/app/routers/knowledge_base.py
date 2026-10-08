"""
Knowledge Base Management Router — API endpoints for managing the curated knowledge base.

Endpoints:
  - GET  /api/kb/stats          — Knowledge base statistics
  - POST /api/kb/ingest          — Ingest a source document
  - POST /api/kb/seed            — Seed the knowledge base with foundational content
  - GET  /api/kb/collections     — List all collections with chunk counts
  - GET  /api/kb/sources         — Chunk counts per source document (read-only)
  - DELETE /api/kb/collections/{name} — Reset a specific collection
"""

import logging
from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    IngestRequest, IngestResponse, KnowledgeBaseStatsResponse,
)
from app.services.retrieval.store import get_store
from app.services.retrieval.ingestion import ingest_source_document, get_collection_stats
from app.services.retrieval.seed_data import seed_knowledge_base
from app.services.retrieval.models import SourceCategory, Jurisdiction

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/kb", tags=["Knowledge Base"])

# When to start advising a rebuild. Deliberately generous: codified regulation
# changes slowly, and live .gov research covers recent developments regardless,
# so this flags "worth refreshing", not "broken".
CORPUS_STALE_AFTER_DAYS = 180


# Where to look when a provider rejects a request, keyed by the model's provider prefix.
_PROVIDER_INFO = {
    "deepseek": ("DeepSeek", "DEEPSEEK_API_KEY", "platform.deepseek.com"),
    "gemini": ("Gemini", "GEMINI_API_KEY", "aistudio.google.com"),
    "groq": ("Groq", "GROQ_API_KEY", "console.groq.com"),
    "anthropic": ("Anthropic", "ANTHROPIC_API_KEY", "console.anthropic.com"),
    "mistral": ("Mistral", "MISTRAL_API_KEY", "console.mistral.ai"),
    "openrouter": ("OpenRouter", "OPENROUTER_API_KEY", "openrouter.ai"),
}


def _describe_provider_failure(model: str, error: Exception) -> str:
    """One-line diagnosis of a failed model call that names its provider."""
    prefix = model.split("/", 1)[0] if "/" in model else "openai"
    name, env_var, console = _PROVIDER_INFO.get(
        prefix, ("OpenAI", "OPENAI_API_KEY", "platform.openai.com"))
    # The provider layer wraps the real error; report the underlying one.
    root = error.__cause__ or error
    text = f"{type(root).__name__}: {root}"
    lowered = text.lower()
    if "429" in text or "rate" in lowered:
        return f"{name} ({model}) is rate limiting requests. Wait a few minutes and retry."
    if "402" in text or "credit" in lowered or "balance" in lowered:
        return (f"{name} ({model}) rejected the request for billing reasons. "
                f"Check the account balance at {console}.")
    if "401" in text or "403" in text or "authentication" in lowered:
        return f"{name} ({model}) rejected the API key. Check {env_var} in the environment."
    return f"{name} ({model}) call failed: {text[:300]}"


@router.get("/stats", response_model=KnowledgeBaseStatsResponse)
async def kb_stats():
    """Get knowledge base statistics."""
    try:
        store = get_store()
        stats = store.get_all_stats()
        total = sum(stats.values())
        return KnowledgeBaseStatsResponse(
            total_chunks=total,
            total_collections=len(stats),
            collections=stats,
        )
    except Exception as e:
        logger.error(f"KB stats error: {e}")
        raise HTTPException(status_code=500, detail="Knowledge base operation failed.") from None


@router.post("/ingest", response_model=IngestResponse)
async def ingest_source(request: IngestRequest):
    """Ingest a source document into the knowledge base."""
    try:
        # Validate category
        try:
            category = SourceCategory(request.category)
        except ValueError:
            valid = [c.value for c in SourceCategory]
            raise HTTPException(
                status_code=400,
                detail=f"Invalid category '{request.category}'. Must be one of: {valid}"
            )

        # Validate jurisdiction
        try:
            jurisdiction = Jurisdiction(request.jurisdiction)
        except ValueError:
            jurisdiction = Jurisdiction.federal

        chunk_count = ingest_source_document(
            source_name=request.source_name,
            text=request.text,
            category=category,
            jurisdiction=jurisdiction,
            citation=request.citation,
            url=request.url,
            effective_date=request.effective_date,
            authority=request.authority,
        )

        return IngestResponse(
            source_name=request.source_name,
            chunks_created=chunk_count,
            collection=category.value,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ingest error: {e}")
        raise HTTPException(status_code=500, detail="Knowledge base operation failed.") from None


@router.post("/seed")
async def seed_kb():
    """Seed the knowledge base with foundational regulatory content."""
    try:
        results = seed_knowledge_base()
        total_chunks = sum(results.values())
        return {
            "status": "ok",
            "total_chunks_created": total_chunks,
            "sources_seeded": len(results),
            "details": results,
        }
    except Exception as e:
        logger.error(f"Seed error: {e}")
        raise HTTPException(status_code=500, detail="Knowledge base operation failed.") from None


@router.get("/collections")
async def list_collections():
    """List all collections with chunk counts."""
    try:
        store = get_store()
        stats = store.get_all_stats()
        return {
            "collections": [
                {"name": name, "chunk_count": count}
                for name, count in stats.items()
            ]
        }
    except Exception as e:
        logger.error(f"Collections list error: {e}")
        raise HTTPException(status_code=500, detail="Knowledge base operation failed.") from None


# Metadata is read in pages so a large collection is never held in memory
# all at once on a small instance.
_SOURCES_PAGE_SIZE = 1000


def _source_labels() -> dict:
    """Display labels keyed by the citation each source is stored under."""
    from app.services.retrieval.ecfr_client import ECFR_TARGETS
    from app.services.retrieval.guidance_client import GUIDANCE_DOCUMENTS

    labels = {f"{t} CFR Part {p}": label for t, p, label, _ in ECFR_TARGETS}
    labels.update({doc.citation: doc.label for doc in GUIDANCE_DOCUMENTS})
    return labels


@router.get("/sources")
async def list_sources():
    """Chunk counts per source document, read from the knowledge base.

    Read-only, and returns metadata only -- never chunk text. CFR sources are
    grouped by part ("45 CFR Part 164"), everything else by its citation.
    """
    try:
        store = get_store()
        labels = _source_labels()
        groups: dict = {}
        for collection_name, count in store.get_all_stats().items():
            if count <= 0:
                continue
            collection = store.get_collection(collection_name)
            for offset in range(0, count, _SOURCES_PAGE_SIZE):
                page = collection.get(
                    include=["metadatas"], limit=_SOURCES_PAGE_SIZE, offset=offset
                )
                for meta in page.get("metadatas") or []:
                    meta = meta or {}
                    prefix = (
                        meta.get("part_citation")
                        or meta.get("citation")
                        or meta.get("source_name")
                        or "Unknown source"
                    )
                    group = groups.setdefault((collection_name, prefix), {
                        "source": labels.get(prefix) or meta.get("source_name") or prefix,
                        "citation_prefix": prefix,
                        "collection": collection_name,
                        "chunk_count": 0,
                        "fetched_as_of": None,
                    })
                    group["chunk_count"] += 1
                    fetched = meta.get("retrieved_date")
                    if fetched and (group["fetched_as_of"] is None or fetched > group["fetched_as_of"]):
                        group["fetched_as_of"] = fetched

        sources = sorted(groups.values(), key=lambda g: (g["collection"], g["citation_prefix"]))
        return {
            "total_chunks": sum(g["chunk_count"] for g in sources),
            "sources": sources,
        }
    except Exception as e:
        logger.error(f"KB sources error: {e}")
        raise HTTPException(status_code=500, detail="Knowledge base operation failed.") from None


@router.delete("/collections/{collection_name}")
async def reset_collection(collection_name: str):
    """Reset (delete and recreate) a specific collection."""
    valid_collections = [
        "federal_regulation", "ocr_guidance", "state_law",
        "policy_clause_library", "policy_template", "example_policy",
        "enforcement_action", "requirement_pack",
    ]
    if collection_name not in valid_collections:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid collection. Must be one of: {valid_collections}"
        )
    try:
        store = get_store()
        store.reset_collection(collection_name)
        return {"status": "ok", "message": f"Collection '{collection_name}' has been reset"}
    except Exception as e:
        logger.error(f"Reset collection error: {e}")
        raise HTTPException(status_code=500, detail="Knowledge base operation failed.") from None


@router.get("/diagnose")
async def kb_diagnose():
    """Plain-English diagnosis of why the knowledge base is or isn't populated.

    Exists because "kb_grounded: false" says something is wrong but not what,
    and the answer lives in whether eCFR is reachable from THIS host — which
    can't be determined from source code or a developer's machine. Runs the
    real seeding steps one at a time against one small CFR part and reports
    where the chain breaks.

    Read-only: fetches from a fixed government API and writes nothing.
    """
    from app.services.retrieval.ecfr_client import get_ecfr_client, ECFR_BASE, ECFR_TARGETS

    from app.services.retrieval import seed_state

    steps = []
    client = get_ecfr_client()

    def step(name, ok, detail):
        steps.append({"step": name, "ok": ok, "detail": detail})
        return ok

    # 0. What's in the store right now. Checked BEFORE seeding, because
    # whether seeding ran is only meaningful once you know whether it needed
    # to.
    try:
        stats = get_store().get_all_stats()
        total = sum(v for v in stats.values() if v > 0)
        step("Knowledge base contents", total > 0,
             f"{total} chunks stored. Per collection: {stats}")
    except Exception as e:
        total = 0
        step("Knowledge base contents", False, f"Could not read the store: {e}")

    # 0a. How old is the corpus? It is built into the image, so it is exactly
    # as current as the last rebuild -- and nothing said so. A knowledge base
    # can drift years out of date while every other check still reports a
    # healthy "5,714 chunks stored".
    if total > 0:
        try:
            corpus_date = get_store().get_corpus_date()
            if not corpus_date:
                step("Corpus age", True, "No dated chunks found — age unknown.")
            else:
                from datetime import date as _date
                built = _date.fromisoformat(corpus_date)
                days = (_date.today() - built).days
                months = days // 30
                fresh = days <= CORPUS_STALE_AFTER_DAYS
                if fresh:
                    detail = f"Regulations current as of {corpus_date} ({days} days old)."
                else:
                    detail = (
                        f"Regulations are from {corpus_date} — about {months} months old. "
                        f"Refresh with Manual Deploy → 'Clear build cache & deploy'. "
                        f"Live .gov research still covers recent developments in the meantime."
                    )
                step("Corpus age", fresh, detail)
        except Exception as e:
            step("Corpus age", True, f"Could not determine corpus age: {e}")

    # A populated knowledge base means seeding correctly had nothing to do:
    # the corpus is built into the image. Reporting "seeding never ran" as a
    # failure in that situation reads as an outage when everything is fine.
    seeding = seed_state.get_state()
    if total > 0 and seeding["status"] == "not_started":
        step("Background seeding", True,
             "Not needed — the corpus was built into the image, so this container "
             "had nothing to download.")
    else:
        steps.append({
            "step": "Background seeding",
            "ok": seeding["status"] in ("succeeded", "running"),
            "detail": seed_state.describe(),
        })

    # 0a2. Can the app actually reach a model? Everything else can be perfect
    # -- corpus loaded, research working -- and nothing will generate if the
    # provider is out of credit, rate limiting, or misconfigured. This page
    # checked every other link in the chain except the one that writes the
    # output, so "nothing is generating" had no diagnosis at all.
    try:
        from app.services.provider import get_provider

        import time as _t

        from app.config import settings as _settings
        provider = get_provider()
        configured = _settings.llm_cascade_models or []
        if not configured:
            step("AI provider", False,
                 "No model is configured — every provider API key is unset. "
                 "Set DEEPSEEK_API_KEY or GEMINI_API_KEY in the environment.")
        else:
            # Try each configured model on its own, in cascade order, so a
            # failure names the provider that actually failed rather than
            # whichever one happens to be first in the list.
            failures = []
            passed = None
            for model in configured:
                started = _t.monotonic()
                try:
                    reply = await provider.complete(
                        system_prompt="Reply with the single word: ok",
                        user_message="ping",
                        max_tokens=8,
                        temperature=0,
                        models=[model],
                    )
                except Exception as e:
                    failures.append(_describe_provider_failure(model, e))
                    continue
                if reply and reply.strip():
                    passed = f"{model} responded in {_t.monotonic() - started:.1f}s."
                    break
                failures.append(f"{model} returned an empty reply.")
            if passed:
                detail = " ".join([passed] + [f"(Earlier in the cascade: {f})" for f in failures])
                step("AI provider", True, detail)
            else:
                step("AI provider", False, " ".join(failures))
    except Exception as e:
        step("AI provider", False, f"Could not test the provider: {type(e).__name__}: {e}")

    # 0b. Is live research actually reaching the internet? This used to fail
    # silently: a blocked search engine returned an empty list, identical to
    # "nothing relevant found", so there was no way to tell whether the .gov
    # research half of the product was working at all.
    try:
        from app.services.retrieval.live_research import (
            get_live_research_service, TAVILY_API_KEY,
        )
        import time as _time

        backend = "Tavily" if TAVILY_API_KEY else "DuckDuckGo (no TAVILY_API_KEY set)"
        started = _time.monotonic()
        live_results = await get_live_research_service().research(
            query="HIPAA breach notification requirements",
            policy_type="data_breach_response",
            industry="healthcare",
        )
        took = _time.monotonic() - started
        step("Live research (.gov search)", bool(live_results),
             f"{backend}: {len(live_results)} results in {took:.1f}s"
             + ("" if live_results else
                " — no results. DuckDuckGo blocks cloud servers; set TAVILY_API_KEY "
                "to restore live research."))
    except Exception as e:
        step("Live research (.gov search)", False, f"{type(e).__name__}: {e}")

    # 1. Can this server reach eCFR at all?
    try:
        resp = await client.client.get(f"{ECFR_BASE}/titles.json")
        reachable = resp.status_code == 200
        step("Reach eCFR (titles.json)", reachable,
             f"HTTP {resp.status_code}" + ("" if reachable else
             " — the server cannot reach ecfr.gov. Outbound internet may be blocked."))
    except Exception as e:
        step("Reach eCFR (titles.json)", False,
             f"Request failed: {type(e).__name__}: {e}. The server likely has no outbound "
             f"internet access to ecfr.gov.")
        return {"summary": _summarize(steps), "seeding": seeding, "steps": steps}

    # 2. Does eCFR report a usable date for Title 45?
    as_of = await client.get_title_as_of(45)
    if not step("Get current date for Title 45", bool(as_of),
                f"eCFR reports Title 45 current as of {as_of}" if as_of
                else "titles.json did not contain a usable date for title 45"):
        return {"summary": _summarize(steps), "seeding": seeding, "steps": steps}

    # 3. Fetch + parse one real part end to end
    try:
        data = await client.fetch_part(45, 164)
        n = len(data.get("sections", [])) if data else 0
        step("Fetch and parse 45 CFR Part 164", n > 0,
             f"Parsed {n} sections." if n > 0 else
             "Downloaded but parsed 0 sections — the document structure may have changed.")
        if n:
            first = data["sections"][0]
            step("Sample parsed section", True,
                 f"{first.get('citation')} — {first.get('heading')} "
                 f"({len(first.get('text',''))} chars)")
    except Exception as e:
        step("Fetch and parse 45 CFR Part 164", False, f"{type(e).__name__}: {e}")

    return {
        "summary": _summarize(steps),
        "targets_configured": len(ECFR_TARGETS),
        "seeding": seeding,
        "steps": steps,
    }


def _summarize(steps) -> str:
    failed = [s for s in steps if not s["ok"]]
    # Seeding still in flight is the expected state right after a deploy, not
    # a fault -- say so instead of reporting the empty store as a failure.
    from app.services.retrieval import seed_state
    if seed_state.get_state()["status"] == "running":
        return ("Seeding is still running in the background. Wait a minute or two and "
                "reload this page; chunk counts should start climbing.")
    if not failed:
        return ("Everything checks out. eCFR is reachable and parsing works. If the knowledge "
                "base is still empty, trigger a re-seed (POST /api/kb/seed with the admin key) "
                "and check again.")
    first = failed[0]
    if "Reach eCFR" in first["step"]:
        return ("This server cannot reach ecfr.gov, so there is no regulatory text to load. "
                "This is a network/hosting issue, not an application bug.")
    if "Fetch and parse" in first["step"]:
        return ("eCFR is reachable but the regulation text could not be parsed into sections. "
                "The eCFR document format likely changed and the parser needs updating.")
    return f"First failure: {first['step']} — {first['detail']}"
