"""
Analysis Router — health check.

The single-shot /analyze and /analyze-file endpoints that used to live here
were removed as dead code: the product exclusively uses the job-based
/api/action-package/* flow (see routers/action_package.py) for gap analysis,
so nothing calls this router's old endpoints anymore.
"""

import logging
from fastapi import APIRouter

from app.config import settings
from app.models.schemas import HealthResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["analysis"])


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint, including knowledge-base grounding state.

    kb_grounded=false means the app is running in model-only mode: analyses
    and drafts will still be produced and will still look source-grounded,
    but no curated regulatory text is actually backing them. That is worth
    alerting on -- it is the difference between the product's core claim
    holding and not.
    """
    kb_chunks = 0
    kb_enabled = settings.kb_enabled

    if kb_enabled:
        try:
            from app.services.retrieval.store import get_store
            kb_chunks = sum(get_store().get_all_stats().values())
        except Exception as e:
            # Health must never fail because the KB is unreachable -- report
            # ungrounded rather than returning an error.
            logger.warning(f"Health check could not read KB stats: {e}")

    if kb_enabled and kb_chunks == 0:
        logger.warning(
            "Knowledge base is EMPTY — running in model-only mode. Grounded "
            "citations are not active. Re-seed via POST /api/kb/seed."
        )

    return HealthResponse(
        status="ok",
        version="3.0.0",
        kb_enabled=kb_enabled,
        kb_chunks=kb_chunks,
        kb_grounded=kb_enabled and kb_chunks > 0,
    )
