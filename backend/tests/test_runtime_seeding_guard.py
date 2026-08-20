"""
An empty knowledge base must never take the site down.

Production symptom: after seeding was repaired, the deployed site stopped
loading entirely.

Cause: seeding costs roughly twice the memory serving does -- measured at
~1,025 MB peak against ~416 MB steady state, most of it a PDF parser that
retained every page it had read. On a small instance the container was killed
mid-seed. It then restarted to a still-empty knowledge base, started seeding
again, and was killed again: a crash loop, so the site was not merely
ungrounded but unreachable.

Before the repair this never happened, because seeding crashed instantly and
allocated nothing. Fixing seeding is what exposed the memory cost.

The rule these tests encode: running ungrounded is bad, being offline is
worse. Grounding is built into the image, and a container that finds itself
without one says so loudly and keeps serving.

Run: python -m pytest tests/test_runtime_seeding_guard.py -v
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.main as main
from app.services.retrieval import seed_state


@pytest.fixture(autouse=True)
def _empty_isolated_store(tmp_path, monkeypatch):
    from app.services.retrieval import store as store_module
    monkeypatch.setattr(
        store_module, "_store", store_module.ChromaStore(persist_dir=str(tmp_path / "kb"))
    )
    monkeypatch.setattr(
        "app.services.retrieval.seed_data.restore_baked_knowledge_base", lambda: 0
    )
    seed_state.reset()
    yield
    seed_state.reset()


def test_runtime_seeding_is_off_by_default():
    """The default must be the safe one. A deployment that has to remember to
    set an environment variable to avoid a crash loop is not fixed."""
    from app.config import Settings
    assert Settings().kb_seed_at_runtime is False


@pytest.mark.asyncio
async def test_empty_knowledge_base_does_not_trigger_seeding(monkeypatch):
    """The specific chain that took the site down: empty KB -> seed -> killed."""
    called = False

    async def _should_not_run():
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(main.settings, "kb_seed_at_runtime", False)
    monkeypatch.setattr(
        "app.services.retrieval.seed_data.seed_knowledge_base_async", _should_not_run
    )

    async with main.lifespan(FastAPI()):
        await asyncio.sleep(0.3)

    assert not called, "runtime seeding ran despite being disabled"


@pytest.mark.asyncio
async def test_the_app_still_starts_and_serves(monkeypatch):
    """Ungrounded but up. This is the whole point of the guard."""
    monkeypatch.setattr(main.settings, "kb_seed_at_runtime", False)

    with TestClient(main.app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["kb_grounded"] is False, "expected an honest ungrounded signal"


@pytest.mark.asyncio
async def test_the_missing_corpus_is_reported_not_hidden(monkeypatch):
    """Silence is what let an empty knowledge base ship repeatedly."""
    monkeypatch.setattr(main.settings, "kb_seed_at_runtime", False)

    async with main.lifespan(FastAPI()):
        await asyncio.sleep(0.3)

    state = seed_state.get_state()
    assert state["status"] == "failed"
    assert "runtime seeding is disabled" in (state["error"] or "")


@pytest.mark.asyncio
async def test_scheduled_refresh_also_declines(monkeypatch):
    """A nightly refresh re-embeds everything, so it would kill a container
    that is currently serving traffic -- a stale corpus becoming an outage."""
    from app.services.retrieval import scheduler

    monkeypatch.setattr(scheduler.settings, "kb_seed_at_runtime", False)

    fetched = False

    def _tripwire():
        nonlocal fetched
        fetched = True
        raise AssertionError("refresh should not have fetched anything")

    monkeypatch.setattr(
        "app.services.retrieval.ecfr_client.get_ecfr_client", _tripwire
    )

    await scheduler.refresh_ecfr_knowledge_base()
    assert not fetched


class TestMemoryDiscipline:
    """Guards the two changes that brought peak seeding memory down."""

    def test_pdf_extraction_releases_page_caches(self):
        """The parser retained every page it read; one 32-page document cost
        ~390 MB. Extraction must stay flat, not grow with page count."""
        import gc
        import os
        import resource

        from app.services.retrieval.guidance_client import BUNDLED_DIR, extract_pdf_text

        path = os.path.join(BUNDLED_DIR, "oig-gcpg-2023.pdf")
        if not os.path.exists(path):
            pytest.skip("bundled GCPG PDF not present in this checkout")

        with open(path, "rb") as handle:
            data = handle.read()

        gc.collect()
        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        text = extract_pdf_text(data)
        gc.collect()
        after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

        assert len(text) > 50_000, "extraction must still produce the full text"
        assert after - before < 200, (
            f"PDF extraction grew memory by {after - before:.0f} MB; page caches "
            f"are being retained again."
        )

    def test_embedding_batch_size_is_bounded(self):
        from app.config import Settings
        assert 0 < Settings().kb_embed_batch_size <= 64
