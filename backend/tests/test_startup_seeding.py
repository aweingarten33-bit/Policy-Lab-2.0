"""
Startup must not block on knowledge-base seeding.

Production symptom: eCFR was reachable, dates resolved, and 45 CFR Part 164
parsed into 41 sections -- yet the knowledge base stayed at 0 chunks across
every deploy.

Cause: seeding ran inline in the FastAPI lifespan, before `yield`, so the app
accepted no traffic until every CFR part was downloaded and embedded. On a cold
container that exceeds the platform health-check window, so the container is
restarted mid-seed and the work begins again from nothing -- an empty knowledge
base that can never fill.

Run: python -m pytest tests/test_startup_seeding.py -v
"""

import asyncio
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI

import app.main as main
from app.services.retrieval import seed_state


@pytest.fixture(autouse=True)
def _reset_state():
    seed_state.reset()
    yield
    seed_state.reset()


@pytest.mark.asyncio
async def test_startup_is_not_blocked_by_seeding():
    """The app must become ready immediately, however slow seeding is."""
    async def slow_seed():
        await asyncio.sleep(3.0)
        return {"45 CFR Part 164": 41}

    with patch("app.services.retrieval.seed_data.seed_knowledge_base_async", new=slow_seed):
        started = time.monotonic()
        async with main.lifespan(FastAPI()):
            elapsed = time.monotonic() - started
            assert elapsed < 2.0, (
                f"Startup took {elapsed:.2f}s — it is blocking on seeding, which is what "
                f"caused the platform to restart the container mid-seed."
            )


@pytest.mark.asyncio
async def test_seeding_completes_in_background_and_is_recorded():
    async def quick_seed():
        await asyncio.sleep(0.1)
        return {"45 CFR Part 164": 41}

    with patch("app.services.retrieval.seed_data.seed_knowledge_base_async", new=quick_seed):
        async with main.lifespan(FastAPI()):
            for _ in range(100):
                if seed_state.get_state()["status"] == "succeeded":
                    break
                await asyncio.sleep(0.1)

    state = seed_state.get_state()
    assert state["status"] == "succeeded"
    assert state["chunks_added"] == 41


@pytest.mark.asyncio
async def test_seeding_failure_is_recorded_not_swallowed():
    """A background job is silent unless its failure is captured."""
    async def failing_seed():
        raise RuntimeError("eCFR unreachable")

    with patch("app.services.retrieval.seed_data.seed_knowledge_base_async", new=failing_seed):
        async with main.lifespan(FastAPI()):
            for _ in range(50):
                if seed_state.get_state()["status"] == "failed":
                    break
                await asyncio.sleep(0.1)

    state = seed_state.get_state()
    assert state["status"] == "failed"
    assert "eCFR unreachable" in (state["error"] or "")


@pytest.mark.asyncio
async def test_zero_chunks_is_reported_as_failure():
    """Completing with nothing loaded is a grounding outage, not a success."""
    async def empty_seed():
        return {"45 CFR Part 164": 0}

    with patch("app.services.retrieval.seed_data.seed_knowledge_base_async", new=empty_seed):
        async with main.lifespan(FastAPI()):
            for _ in range(50):
                if seed_state.get_state()["status"] in ("failed", "succeeded"):
                    break
                await asyncio.sleep(0.1)

    assert seed_state.get_state()["status"] == "failed"


def test_diagnose_surfaces_seeding_state():
    """A background job must be observable from outside the process."""
    import inspect
    from app.routers.knowledge_base import kb_diagnose
    source = inspect.getsource(kb_diagnose)
    assert "seed_state" in source
    assert "Background seeding" in source
