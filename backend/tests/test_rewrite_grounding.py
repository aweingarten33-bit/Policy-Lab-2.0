import pytest

from app.config import settings
from app.models.schemas import AnalysisResult
from app.services.orchestrator import GroundingUnavailableError
from app.services.retrieval.models import RetrievalContext
from app.services.rewrite_service import generate_rewritten_policy


@pytest.mark.asyncio
async def test_rewrite_fails_closed_without_authoritative_sources(monkeypatch):
    monkeypatch.setattr(settings, "require_grounding", True)
    result = AnalysisResult(
        policy_type="Test Policy",
        audit_ready_summary="summary",
        gap_table=[],
    )
    empty_context = RetrievalContext(query="rewrite", retrieved_chunks=[])

    with pytest.raises(GroundingUnavailableError):
        await generate_rewritten_policy(
            original_text="A policy with enough text to rewrite.",
            gap_analysis=result,
            retrieval_context=empty_context,
            industry="healthcare",
        )
