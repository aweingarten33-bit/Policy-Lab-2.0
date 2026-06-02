"""
Document Ingestion Pipeline — Loads source material into the vector store.

Supports:
  - Programmatic ingestion from Python dicts
  - Bulk ingestion from structured data files
  - Auto-chunking with overlap for long documents
  - Metadata preservation for every chunk
  - Seed data loading for initial knowledge base population
"""

import logging
import uuid
import re
from typing import Optional, List, Dict, Any

from app.services.retrieval.models import (
    SourceChunk, SourceMetadata, SourceType, SourceCategory, Jurisdiction,
)
from app.services.retrieval.store import get_store

logger = logging.getLogger(__name__)

# ── Chunking Configuration ──

CHUNK_SIZE = 800       # Target characters per chunk
CHUNK_OVERLAP = 200    # Overlap between chunks to preserve context
MIN_CHUNK_SIZE = 100   # Minimum chunk size (smaller chunks are merged)


def _split_text_into_chunks(text: str, chunk_size: int = CHUNK_SIZE,
                             overlap: int = CHUNK_OVERLAP) -> List[str]:
    """
    Split text into overlapping chunks, respecting paragraph boundaries.

    Tries to split at paragraph breaks first, then at sentence boundaries,
    then at word boundaries. Never splits mid-word.
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        # If this is the last chunk, take everything
        if end >= len(text):
            chunks.append(text[start:].strip())
            break

        # Try to find a paragraph break within the last 20% of the chunk
        search_start = start + int(chunk_size * 0.8)
        search_end = end

        # Look for paragraph break
        best_split = -1
        for pattern in [r'\n\s*\n', r'\n', r'\. ', r'; ', r', ']:
            matches = list(re.finditer(pattern, text[search_start:search_end]))
            if matches:
                best_split = search_start + matches[-1].end()
                break

        if best_split > start + MIN_CHUNK_SIZE:
            end = best_split
        else:
            # Fall back to word boundary
            while end > start + MIN_CHUNK_SIZE and text[end] != ' ':
                end -= 1

        chunk = text[start:end].strip()
        if len(chunk) >= MIN_CHUNK_SIZE:
            chunks.append(chunk)

        start = end - overlap if end - overlap > start else end

    return chunks


def ingest_source_document(
    source_name: str,
    text: str,
    category: SourceCategory,
    jurisdiction: Jurisdiction = Jurisdiction.federal,
    citation: Optional[str] = None,
    url: Optional[str] = None,
    effective_date: Optional[str] = None,
    authority: Optional[str] = None,
    section: Optional[str] = None,
    is_current: bool = True,
    source_type: SourceType = SourceType.retrieved_source,
) -> int:
    """
    Ingest a single source document into the vector store.

    The document is automatically chunked with overlap, and each chunk
    carries the full metadata of the source document.

    Args:
        source_name: Human-readable name of the source
        text: Full text of the source document
        category: Source category (determines collection)
        jurisdiction: Federal or state-specific
        citation: Formal citation string
        url: Source URL
        effective_date: When this source became effective
        authority: Issuing authority
        section: Section within the document
        is_current: Whether this is the current version
        source_type: Type of source

    Returns:
        Number of chunks created
    """
    if not text or not text.strip():
        logger.warning(f"Empty text for source: {source_name}")
        return 0

    # Chunk the document
    chunks = _split_text_into_chunks(text)
    collection_name = category.value

    # Build chunk records
    ids = []
    documents = []
    metadatas = []

    base_id = re.sub(r'[^a-zA-Z0-9]', '_', source_name)[:50]

    for i, chunk_text in enumerate(chunks):
        chunk_id = f"{base_id}_chunk_{i}_{uuid.uuid4().hex[:8]}"

        metadata = SourceMetadata(
            source_name=source_name,
            source_type=source_type,
            category=category,
            jurisdiction=jurisdiction,
            effective_date=effective_date,
            citation=citation,
            url=url,
            section=section or (f"Chunk {i+1}" if len(chunks) > 1 else None),
            authority=authority,
            is_current=is_current,
            chunk_index=i,
            total_chunks=len(chunks),
            collection=collection_name,
        )

        ids.append(chunk_id)
        documents.append(chunk_text)
        metadatas.append(metadata.model_dump())

    # Add to store
    store = get_store()
    store.add_chunks(
        collection_name=collection_name,
        ids=ids,
        documents=documents,
        metadatas=metadatas,
    )

    logger.info(f"Ingested '{source_name}': {len(chunks)} chunks into {collection_name}")
    return len(chunks)


def bulk_ingest(sources: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Ingest multiple source documents at once.

    Args:
        sources: List of dicts, each matching ingest_source_document kwargs

    Returns:
        Dict with source_name -> chunk_count
    """
    results = {}
    for source in sources:
        try:
            count = ingest_source_document(**source)
            results[source.get("source_name", "unknown")] = count
        except Exception as e:
            logger.error(f"Failed to ingest {source.get('source_name', 'unknown')}: {e}")
            results[source.get("source_name", "unknown")] = 0
    return results


def get_collection_stats() -> Dict[str, Any]:
    """Get statistics for all collections in the knowledge base."""
    store = get_store()
    return store.get_all_stats()
