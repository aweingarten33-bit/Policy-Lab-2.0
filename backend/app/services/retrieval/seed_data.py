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
from app.services.retrieval.models import Jurisdiction, SourceStatus, SourceType

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


async def _seed_guidance() -> Dict[str, int]:
    """Load federal compliance-program guidance that has no CFR equivalent.

    The seven elements of an effective compliance program -- the framework
    every healthcare compliance officer and every HCCA certification is built
    around -- appear in OIG guidance, not in the Code of Federal Regulations.
    Without this the app named those elements purely from model memory while
    presenting itself as source-grounded.

    Failures here are logged and skipped: guidance is additive, and losing it
    must never prevent the regulatory corpus from loading.
    """
    from app.services.retrieval.guidance_client import (
        get_guidance_client, GUIDANCE_DOCUMENTS,
    )
    from app.services.retrieval.models import SourceCategory

    client = get_guidance_client()
    results: Dict[str, int] = {}
    today = date.today().isoformat()

    logger.info(f"Seeding compliance guidance ({len(GUIDANCE_DOCUMENTS)} documents)...")

    for doc in GUIDANCE_DOCUMENTS:
        try:
            fetched = await client.fetch_document(doc)
            if not fetched or not fetched.get("sections"):
                logger.warning(f"Guidance: no content for {doc.label} — skipping")
                results[doc.label] = 0
                continue

            total = 0
            for section in fetched["sections"]:
                total += ingest_source_document(
                    source_name=f"{doc.label} — {section['heading']}",
                    text=section["text"],
                    category=SourceCategory.federal_guidance,
                    jurisdiction=Jurisdiction.federal,
                    citation=doc.citation,
                    url=fetched["url"],
                    authority=doc.authority,
                    # `doc.published` is the date the guidance was published.
                    # It was previously written into effective_date, which is a
                    # different fact and one these documents do not state --
                    # guidance is not a rule with a commencement date.
                    publication_date=doc.published,
                    retrieved_date=today,
                    last_verified_date=today,
                    # These are the current, in-force editions of the OIG/HCCA
                    # guidance, shipped with the repo and superseded only when
                    # the agency issues a replacement.
                    source_status=SourceStatus.current_verified,
                    section=section["heading"],
                    source_type=SourceType.retrieved_source,
                )

            results[doc.label] = total
            logger.info(f"  ✓ {doc.label}: {total} chunks")

        except Exception as e:
            logger.error(f"Failed to seed guidance {doc.label}: {e}", exc_info=True)
            results[doc.label] = 0

    return results


def ingest_cfr_part_sections(
    chunks,
    *,
    title: int,
    part: int,
    category,
    fetched_date: str,
) -> int:
    """Ingest one CFR part, one section at a time, and keep the full text.

    Shared by the initial seed and the nightly refresh. They previously had
    separate copies of this, and the copies disagreed -- so a fix applied to
    seeding was quietly undone the next night when the refresh re-ingested the
    same parts the old way.

    Each section is its own document under its own section-level citation, so
    a finding citing §164.404 can be matched against it. The part-level
    citation rides along in part_citation, which is what industry scoping
    filters on. The complete untruncated section text also goes to the
    authoritative section store, so verification can resolve a subsection the
    retrieval chunks do not happen to contain.

    Returns the number of embedded chunks created.
    """
    from app.services.retrieval.section_store import get_section_store

    part_citation = f"{title} CFR Part {part}"
    total = 0
    authoritative = []

    for chunk in chunks:
        meta = chunk.metadata
        total += ingest_source_document(
            source_name=meta.source_name,
            text=chunk.text,
            category=category,
            jurisdiction=Jurisdiction.federal,
            citation=meta.citation,
            part_citation=part_citation,
            url=meta.url,
            authority=meta.authority,
            section=meta.section,
            retrieved_date=fetched_date,
            last_verified_date=fetched_date,
            source_status=meta.source_status,
            source_type=SourceType.retrieved_source,
            # One namespace per part, so the nightly refresh can clear the
            # previous version of exactly this part before writing the new one.
            id_prefix=f"ecfr_{title}_{part}",
        )
        authoritative.append({
            "citation": meta.citation,
            "part_citation": part_citation,
            "source_name": meta.source_name,
            "authority": meta.authority,
            "url": meta.url,
            "full_text": chunk.text,
            "retrieved_date": fetched_date,
            "last_verified_date": fetched_date,
            "source_status": meta.source_status.value if meta.source_status else None,
        })

    stored = get_section_store().put_many(authoritative)
    logger.info(
        f"  {part_citation}: {total} chunks embedded, {stored} full sections stored"
    )
    return total


async def _async_seed() -> Dict[str, int]:
    """Async implementation: fetch eCFR and ingest into ChromaDB."""
    from app.services.retrieval.store import get_store
    from app.services.retrieval.ecfr_client import parts_to_source_chunks
    from app.services.retrieval.section_store import get_section_store

    import time

    store = get_store()
    client = get_ecfr_client()
    results: Dict[str, int] = {}
    today = date.today().isoformat()

    # Guidance first, deliberately. The bundled OIG/HCCA documents load from
    # disk with no network at all, so seeding them before the eCFR preflight
    # means a government API outage costs the regulatory corpus but still
    # leaves the compliance-program guidance in place -- partial grounding
    # instead of none.
    results.update(await _seed_guidance())

    # Preflight: confirm eCFR is reachable ONCE before attempting every target.
    # Each part retries across several snapshot dates in two formats, so with
    # ~30 targets an unreachable eCFR would otherwise grind through hundreds of
    # timeouts -- long enough to hang a Docker build for over an hour. One cheap
    # check up front turns that into an immediate, clear failure.
    probe_title = ECFR_TARGETS[0][0] if ECFR_TARGETS else 45
    if await client.get_title_as_of(probe_title) is None:
        message = (
            "eCFR is unreachable (titles.json returned no usable data), so no regulatory "
            "text can be downloaded. Skipping all targets rather than retrying each one."
        )
        if sum(results.values()) > 0:
            # Guidance did load. Raising here would throw away a usable partial
            # corpus and report total failure, so report loudly and return what
            # was actually obtained.
            logger.error(f"{message} Continuing with guidance-only grounding.")
            return results
        raise SeedingFailedError(message)

    deadline = time.monotonic() + settings.kb_seed_timeout_seconds
    logger.info(f"Seeding knowledge base from eCFR ({len(ECFR_TARGETS)} targets, as of {today})...")

    for title, part, label, category in ECFR_TARGETS:
        if time.monotonic() > deadline:
            logger.error(
                f"Seeding budget of {settings.kb_seed_timeout_seconds}s exhausted — "
                f"stopping with {sum(results.values())} chunks loaded. Remaining targets skipped."
            )
            break
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

            # Ingested one SECTION at a time, not as one concatenated part.
            #
            # This used to join every section of a part into a single document
            # and ingest it under the part-level citation "45 CFR Part 164".
            # That threw away the per-section citations the parser had just
            # produced, and verification matches a finding's citation against
            # the stored one: a claim citing "45 CFR §164.404(c)" was compared
            # against "45 CFR Part 164", the citation keys did not match, and
            # no eCFR chunk could support any section-level claim at all.
            n = ingest_cfr_part_sections(
                chunks, title=title, part=part, category=category, fetched_date=today
            )

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
