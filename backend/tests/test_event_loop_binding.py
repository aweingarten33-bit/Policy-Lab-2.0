"""
Regression test: the eCFR HTTP client must survive an event-loop change.

Production symptom that prompted this: /api/kb/diagnose returned
"RuntimeError: Event loop is closed" and the knowledge base never recovered
on its own.

Cause: ECFRClient is a process-wide singleton holding an httpx.AsyncClient,
which is bound to the loop it was created in. Startup seeding ran in a
throwaway loop (ThreadPoolExecutor + asyncio.run), so the client was created
there and cached. Once that loop closed, every later call -- including the
nightly refresh meant to repair an empty knowledge base -- failed permanently.

Run: python -m pytest tests/test_event_loop_binding.py -v
"""

import asyncio
import concurrent.futures

import pytest

from app.services.retrieval.ecfr_client import ECFRClient


def _build_client_in_throwaway_loop(client: ECFRClient):
    """Create the httpx client inside a loop that then closes."""
    async def inner():
        return id(client.client)
    return asyncio.run(inner())


@pytest.mark.asyncio
async def test_client_is_rebuilt_after_its_loop_closes():
    client = ECFRClient()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        stale_id = pool.submit(_build_client_in_throwaway_loop, client).result()

    fresh = client.client  # accessed from a different, live loop
    assert id(fresh) != stale_id, (
        "Reused a client bound to a closed loop — this is the bug that made the "
        "knowledge base unable to recover."
    )
    assert not fresh.is_closed


@pytest.mark.asyncio
async def test_client_is_reused_within_the_same_loop():
    """Rebuilding must be limited to real loop changes, not every access."""
    client = ECFRClient()
    assert id(client.client) == id(client.client)


@pytest.mark.asyncio
async def test_request_works_after_rebuild():
    """The rebuilt client must actually function, not just be a new object."""
    from unittest.mock import patch, AsyncMock, MagicMock

    client = ECFRClient()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_build_client_in_throwaway_loop, client).result()

    response = MagicMock(
        status_code=200,
        json=MagicMock(return_value={"titles": [{"number": 45, "up_to_date_as_of": "2026-08-06"}]}),
    )
    with patch.object(client.client, "get", new=AsyncMock(return_value=response)):
        assert await client.get_title_as_of(45) == "2026-08-06"


def test_startup_seeds_without_crossing_event_loops():
    """Startup must await seeding directly rather than spawning a new loop."""
    import inspect
    from app.services.retrieval.seed_data import seed_knowledge_base_async
    import app.main as main

    assert inspect.iscoroutinefunction(seed_knowledge_base_async)
    source = inspect.getsource(main.lifespan)
    assert "await seed_knowledge_base_async()" in source
    assert "seed_knowledge_base()" not in source, (
        "Startup must not call the sync wrapper, which runs seeding in a "
        "throwaway loop and strands the shared HTTP client."
    )
