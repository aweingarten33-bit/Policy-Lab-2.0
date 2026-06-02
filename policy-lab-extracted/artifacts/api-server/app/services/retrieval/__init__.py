"""
Retrieval Package — Source-grounded compliance intelligence.

Provides curated internal retrieval (RAG), controlled live research,
verification, and source attribution for the compliance action package pipeline.

Architecture:
  1. Curated Retrieval — ChromaDB vector store with metadata-rich source chunks
  2. Live Research — Controlled web search from curated regulatory sources
  3. Verification — Post-generation claim checking against source material
  4. Source Attribution — Every output labeled with source provenance
"""

from app.services.retrieval.models import (
    SourceChunk,
    SourceMetadata,
    SourceType,
    RetrievalResult,
    SourceAttribution,
    VerificationStatus,
)
from app.services.retrieval.retriever import get_retriever, ComplianceRetriever
from app.services.retrieval.store import get_store, ChromaStore

__all__ = [
    "SourceChunk",
    "SourceMetadata",
    "SourceType",
    "RetrievalResult",
    "SourceAttribution",
    "VerificationStatus",
    "ComplianceRetriever",
    "get_retriever",
    "ChromaStore",
    "get_store",
]
