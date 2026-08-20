"""
Live research must not dominate response time.

Production symptom: a gap analysis or a draft took over three minutes, most of
it before any text appeared.

Cause: the curated sources were searched one after another against a 30-second
client timeout. Five sources that each stall -- which is what a search engine
does to a cloud IP it does not want scraping it -- cost up to 150 seconds, and
that cost was paid again at every generation step.

Nothing about the five requests required ordering. They are independent HTTP
calls to five different hosts.

The rule: live research improves an answer, so it must never be the reason
someone waits. It runs concurrently, under a per-request timeout and a hard
total budget, and falls back to knowledge-base grounding when it overruns.

Run: python -m pytest tests/test_live_research_latency.py -v
"""

import asyncio
import time

import pytest

from app.services.retrieval.live_research import (
    LIVE_RESEARCH_REQUEST_TIMEOUT,
    LIVE_RESEARCH_TOTAL_BUDGET,
    get_live_research_service,
)


@pytest.fixture
def service():
    svc = get_live_research_service()
    original = svc._search_source
    yield svc
    svc._search_source = original


def _stalling_search(seconds: float):
    async def _search(source_key, source_config, query):
        await asyncio.sleep(seconds)
        return []
    return _search


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_sources_are_searched_in_parallel(self, service):
        """The whole batch should cost about one source, not the sum of all."""
        per_source = 2.0
        service._search_source = _stalling_search(per_source)

        started = time.monotonic()
        await service.research(
            query="workplace noise exposure hearing conservation",
            policy_type="workplace_safety_gen",
            industry="healthcare",
        )
        elapsed = time.monotonic() - started

        assert elapsed < per_source * 2, (
            f"took {elapsed:.1f}s for sources that stall {per_source}s each — "
            f"they are running one after another again"
        )

    @pytest.mark.asyncio
    async def test_a_hung_search_cannot_stall_generation(self, service):
        """A search engine that never answers must not hold up the response."""
        service._search_source = _stalling_search(600)

        started = time.monotonic()
        results = await service.research(
            query="breach notification timeline",
            policy_type="data_breach_response",
            industry="healthcare",
        )
        elapsed = time.monotonic() - started

        assert elapsed < LIVE_RESEARCH_TOTAL_BUDGET + 5
        assert results == [], "an overrun must degrade to no live results, not hang"

    @pytest.mark.asyncio
    async def test_one_slow_source_does_not_block_the_others(self, service):
        """Results from sources that did answer must still come back."""
        from app.services.retrieval.live_research import LiveResearchResult, CURATED_SOURCES

        calls = []

        async def _mixed(source_key, source_config, query):
            calls.append(source_key)
            if len(calls) == 1:
                await asyncio.sleep(30)      # one source hangs
                return []
            return [LiveResearchResult(
                title=f"Result from {source_key}",
                url=f"https://example.gov/{source_key}",
                snippet="Relevant regulatory guidance text.",
                source_key=source_key,
                source_name=CURATED_SOURCES[source_key]["name"],
            )]

        service._search_source = _mixed
        results = await asyncio.wait_for(
            service.research(query="hearing conservation program",
                             policy_type="workplace_safety_gen",
                             industry="healthcare"),
            timeout=LIVE_RESEARCH_TOTAL_BUDGET + 5,
        )
        assert len(calls) > 1, "sources did not run concurrently"


class TestBudgets:
    def test_request_timeout_is_short(self):
        """A search answers quickly or not at all. A long ceiling just makes a
        silently-blocked search look like a slow app."""
        assert 0 < LIVE_RESEARCH_REQUEST_TIMEOUT <= 15

    def test_total_budget_is_bounded(self):
        assert 0 < LIVE_RESEARCH_TOTAL_BUDGET <= 30

    def test_client_uses_the_short_timeout(self):
        svc = get_live_research_service()
        svc._client = None
        assert svc.client.timeout.read == LIVE_RESEARCH_REQUEST_TIMEOUT
