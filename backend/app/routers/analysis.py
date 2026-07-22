"""
Analysis Router — health check.

The single-shot /analyze and /analyze-file endpoints that used to live here
were removed as dead code: the product exclusively uses the job-based
/api/action-package/* flow (see routers/action_package.py) for gap analysis,
so nothing calls this router's old endpoints anymore.
"""

import logging
from fastapi import APIRouter

from app.models.schemas import HealthResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["analysis"])


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    return HealthResponse(status="ok", version="3.0.0")
