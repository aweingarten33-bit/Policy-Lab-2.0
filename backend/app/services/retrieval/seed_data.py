"""
Knowledge Base Initialization — Pulls live regulatory content from eCFR on first startup.

Replaces the previous static seed data with current in-force regulatory text
fetched directly from the Electronic Code of Federal Regulations API (ecfr.gov).

No hardcoded regulatory text. Every chunk carries the date it was fetched
and a direct URL back to the authoritative source.

If eCFR is unreachable at startup, the KB initializes empty and the nightly
scheduler will populate it at 02:00 UTC.
"""

import asyncio
import logging
from datetime import date
from typing import Dict

from app.services.retrieval.ecfr_client import get_ecfr_client, ECFR_TARGETS
from app.services.retrieval.ingestion import ingest_source_document

logger = logging.getLogger(__name__)


def seed_knowledge_base() -> Dict[str, int]:
    """
    Populate the knowledge base from live eCFR content.
    Synchronous wrapper — runs the async fetch in a new event loop.

    Returns:
        Dict mapping source label -> number of chunks ingested.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already inside an event loop (FastAPI startup) — schedule as a task
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _async_seed())
                return future.result(timeout=120)
        else:
            return loop.run_until_complete(_async_seed())
    except Exception as e:
        logger.error(f"KB seed failed: {e}", exc_info=True)
        return {}


async def _async_seed() -> Dict[str, int]:
    """Async implementation: fetch eCFR and ingest into ChromaDB."""
    from app.services.retrieval.store import get_store
    from app.services.retrieval.ecfr_client import parts_to_source_chunks

    store = get_store()
    client = get_ecfr_client()
    results: Dict[str, int] = {}
    today = date.today().isoformat()

    logger.info(f"Seeding knowledge base from eCFR (as of {today})...")

    for title, part, label, category in ECFR_TARGETS:
        try:
            part_data = await client.fetch_part(title, part)

            if not part_data or not part_data.get("sections"):
                logger.warning(f"eCFR returned no sections for {label} — skipping")
                results[label] = 0
                continue

            part_data["label"] = label
            part_data["category"] = category
            chunks = parts_to_source_chunks(part_data, category)

            if not chunks:
                logger.warning(f"No chunks extracted for {label}")
                results[label] = 0
                continue

            # Ingest as a single document (ingestion module handles chunking)
            combined_text = "\n\n".join(c.text for c in chunks)

            ingest_result = ingest_source_document(
                store=store,
                title=f"{label} [eCFR {today}]",
                text=combined_text,
                category=category,
                citation=f"{title} CFR Part {part}",
                url=f"https://www.ecfr.gov/current/title-{title}/part-{part}",
                authority="eCFR — Electronic Code of Federal Regulations (current, in-force text)",
                effective_date=today,
                jurisdiction="federal",
                source_type="curated_source",
            )

            n = ingest_result.get("chunks_added", 0)
            results[label] = n
            logger.info(f"  ✓ {label}: {n} chunks from eCFR")

        except Exception as e:
            logger.error(f"Failed to seed {label}: {e}")
            results[label] = 0

    total = sum(results.values())
    logger.info(f"KB seeded from eCFR: {total} total chunks across {len(results)} sources")
    return results
