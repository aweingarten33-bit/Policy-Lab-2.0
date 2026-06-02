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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=500, detail=str(e))
