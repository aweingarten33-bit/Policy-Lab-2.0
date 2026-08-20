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
from app.config import settings
from app.services.retrieval.ingestion import ingest_source_document

logger = logging.getLogger(__name__)


class SeedingFailedError(RuntimeError):
    """Knowledge-base seeding failed outright (as opposed to seeding zero
    chunks because eCFR legitimately had nothing new)."""


async def seed_knowledge_base_async() -> Dict[str, int]:
    """Seed the knowledge base from within an existing event loop.

    Preferred over seed_knowledge_base() anywhere async is already available
    (notably FastAPI startup). The sync wrapper below has to run the work in a
    throwaway loop, and anything holding a connection created in that loop --
    such as the shared eCFR HTTP client -- is left bound to a loop that no
    longer exists the moment it finishes.
    """
    return await _async_seed()


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
                # Budget covers every CFR target plus retries. The old 120s could be
                # consumed by the first few parts alone, timing out the whole seed
                # and leaving the knowledge base empty.
                return future.result(timeout=settings.kb_seed_timeout_seconds)
        else:
            return loop.run_until_complete(_async_seed())
    except Exception as e:
        # Re-raise rather than returning {}. Collapsing a hard failure into an
        # empty dict let the caller log "seeded 0 chunks" as if that were a
        # normal outcome, so a total grounding failure looked identical to
        # "nothing to do" -- which is how the production KB stayed empty
        # without anyone noticing. The caller decides whether to continue.
        logger.error(f"KB seed failed: {e}", exc_info=True)
        raise SeedingFailedError(f"Knowledge base seeding failed: {e}") from e


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


# Path the knowledge base is baked into at image build time (see
# scripts/build_knowledge_base.py). Kept here so the runtime bootstrap can
# find it regardless of where KB_PERSIST_DIR points.
BAKED_KB_DIR = "/app/backend/knowledge_base"


def restore_baked_knowledge_base() -> int:
    """Copy the image-baked knowledge base into the configured persist dir.

    Only relevant when KB_PERSIST_DIR points somewhere else -- typically a
    mounted persistent disk. Without this, attaching a disk would silently
    discard the prebuilt corpus and force a slow re-download on first boot.

    Returns the number of files copied (0 if nothing to do).
    """
    import os
    import shutil

    target = os.path.abspath(settings.kb_persist_dir)
    baked = os.path.abspath(BAKED_KB_DIR)

    if target == baked:
        return 0  # already using the baked copy directly
    if not os.path.isdir(baked) or not os.listdir(baked):
        return 0  # nothing was baked
    if os.path.isdir(target) and os.listdir(target):
        return 0  # target already populated; never overwrite live data

    try:
        os.makedirs(os.path.dirname(target) or "/", exist_ok=True)
        shutil.copytree(baked, target, dirs_exist_ok=True)
        copied = sum(len(files) for _, _, files in os.walk(target))
        logger.info(
            f"Restored prebuilt knowledge base from the image into {target} "
            f"({copied} files) — no download needed."
        )
        return copied
    except Exception as e:
        logger.warning(f"Could not restore the prebuilt knowledge base into {target}: {e}")
        return 0
