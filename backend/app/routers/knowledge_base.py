"""
Knowledge Base Management Router — API endpoints for managing the curated knowledge base.

Endpoints:
  - GET  /api/kb/stats          — Knowledge base statistics
  - POST /api/kb/ingest          — Ingest a source document
  - POST /api/kb/seed            — Seed the knowledge base with foundational content
  - GET  /api/kb/collections     — List all collections with chunk counts
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
