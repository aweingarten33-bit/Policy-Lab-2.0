"""
ChromaDB Vector Store — Persistent local vector store for compliance source material.

Privacy-first: All embeddings run locally using ChromaDB's default embedding function
(all-MiniLM-L6-v2). No text is sent to external embedding APIs.

The store persists to disk at a configurable path (default: ./knowledge_base/).
Collections map to source categories (federal_regulation, ocr_guidance, etc.).
"""

import gc
import logging
import os
from typing import Optional, List, Dict, Any

from app.config import settings

logger = logging.getLogger(__name__)

# Lazy imports — ChromaDB and sentence-transformers are heavy
_chromadb = None
_embedding_function = None


def _get_chromadb():
    """Lazy-load chromadb."""
    global _chromadb
    if _chromadb is None:
        import chromadb
        _chromadb = chromadb
    return _chromadb


def _get_embedding_function():
    """Get the default embedding function (local, no API calls)."""
    global _embedding_function
    if _embedding_function is None:
        chromadb = _get_chromadb()
        _embedding_function = chromadb.utils.embedding_functions.DefaultEmbeddingFunction()
    return _embedding_function


class ChromaStore:
    """
    Persistent local vector store for compliance source material.

    Uses ChromaDB with the default local embedding function (all-MiniLM-L6-v2).
    No text is ever sent to external APIs for embedding.

    Collections:
        - federal_regulation: Federal regulatory text
        - ocr_guidance: OCR guidance and enforcement
        - state_law: State-specific law packs
        - policy_clause_library: Approved clause templates
        - policy_template: Complete policy templates
        - example_policy: Example policies
        - enforcement_action: OCR enforcement actions
        - requirement_pack: Bundled requirement sets
    """

    COLLECTION_NAMES = [
        "federal_regulation",
        "federal_guidance",
        "ocr_guidance",
        "state_law",
        "policy_clause_library",
        "policy_template",
        "example_policy",
        "enforcement_action",
        "requirement_pack",
    ]

    def __init__(self, persist_dir: Optional[str] = None):
        self._persist_dir = persist_dir or settings.kb_persist_dir
        self._client = None
        self._collections: Dict[str, Any] = {}

    def _ensure_client(self):
        """Initialize the ChromaDB client if not already done."""
        if self._client is not None:
            return

        chromadb = _get_chromadb()
        self._persist_dir = os.path.abspath(self._persist_dir)
        os.makedirs(self._persist_dir, exist_ok=True)

        logger.info(f"Initializing ChromaDB store at {self._persist_dir}")
        self._client = chromadb.PersistentClient(path=self._persist_dir)

        # Pre-initialize all collections
        for name in self.COLLECTION_NAMES:
            self._get_or_create_collection(name)

    def _get_or_create_collection(self, name: str):
        """Get or create a collection."""
        if name in self._collections:
            return self._collections[name]

        self._ensure_client()
        ef = _get_embedding_function()

        try:
            collection = self._client.get_collection(
                name=name,
                embedding_function=ef,
            )
            logger.info(f"Loaded existing collection: {name} ({collection.count()} chunks)")
        except Exception:
            collection = self._client.create_collection(
                name=name,
                embedding_function=ef,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(f"Created new collection: {name}")

        self._collections[name] = collection
        return collection

    def get_collection(self, name: str):
        """Get a collection by name."""
        if name not in self.COLLECTION_NAMES:
            raise ValueError(f"Unknown collection: {name}. Must be one of {self.COLLECTION_NAMES}")
        return self._get_or_create_collection(name)

    def add_chunks(
        self,
        collection_name: str,
        ids: List[str],
        documents: List[str],
        metadatas: List[Dict[str, Any]],
    ):
        """
        Add chunks to a collection.

        Args:
            collection_name: Which collection to add to
            ids: Unique IDs for each chunk
            documents: Text content of each chunk
            metadatas: Metadata dict for each chunk (must match schema)
        """
        collection = self.get_collection(collection_name)

        # Sanitize metadatas for ChromaDB (must be str, int, float, or bool)
        clean_metas = []
        for meta in metadatas:
            clean = {}
            for k, v in meta.items():
                if v is None:
                    clean[k] = ""
                elif isinstance(v, bool):
                    clean[k] = v
                elif isinstance(v, (int, float, str)):
                    clean[k] = v
                else:
                    clean[k] = str(v)
            clean_metas.append(clean)

        # Embedding is the memory hot spot: every document in a batch is held
        # as a tensor at once. At 100 per batch, seeding peaked over 1 GB --
        # more than a small container has, so the process was killed mid-seed
        # and restarted, forever. A smaller batch trades a little throughput
        # for a peak that fits alongside the ~420 MB the app already needs.
        batch_size = settings.kb_embed_batch_size
        for i in range(0, len(ids), batch_size):
            collection.upsert(
                ids=ids[i:i + batch_size],
                documents=documents[i:i + batch_size],
                metadatas=clean_metas[i:i + batch_size],
            )
            # Release each batch's tensors before building the next one.
            gc.collect()

        logger.info(f"Added {len(ids)} chunks to {collection_name}")

    def query(
        self,
        collection_name: str,
        query_text: str,
        n_results: int = 5,
        where: Optional[Dict] = None,
        where_document: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Query a collection for relevant chunks.

        Args:
            collection_name: Which collection to search
            query_text: The search query
            n_results: Number of results to return
            where: Metadata filter (e.g., {"jurisdiction": "federal"})
            where_document: Document content filter

        Returns:
            ChromaDB query results dict with ids, documents, metadatas, distances
        """
        collection = self.get_collection(collection_name)

        kwargs = {
            "query_texts": [query_text],
            "n_results": n_results,
        }
        if where:
            kwargs["where"] = where
        if where_document:
            kwargs["where_document"] = where_document

        results = collection.query(**kwargs)
        return results

    def query_all_collections(
        self,
        query_text: str,
        n_results_per_collection: int = 3,
        where: Optional[Dict] = None,
        collections: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Query multiple collections and return combined results.

        Args:
            query_text: The search query
            n_results_per_collection: Results per collection
            where: Metadata filter
            collections: Specific collections to search (None = all)

        Returns:
            List of result dicts, each with collection name, documents, metadatas, distances
        """
        target_collections = collections or self.COLLECTION_NAMES
        all_results = []

        for col_name in target_collections:
            try:
                col = self.get_collection(col_name)
                if col.count() == 0:
                    continue

                results = self.query(
                    collection_name=col_name,
                    query_text=query_text,
                    n_results=n_results_per_collection,
                    where=where,
                )

                if results["ids"] and results["ids"][0]:
                    all_results.append({
                        "collection": col_name,
                        "results": results,
                    })
            except Exception as e:
                logger.warning(f"Query failed for collection {col_name}: {e}")

        return all_results

    def get_collection_count(self, collection_name: str) -> int:
        """Get the number of chunks in a collection."""
        collection = self.get_collection(collection_name)
        return collection.count()

    def get_all_stats(self) -> Dict[str, int]:
        """Get chunk counts for all collections.

        An unreadable collection reports -1, not 0. Collapsing errors to zero
        made a corrupted or inaccessible collection indistinguishable from a
        genuinely empty one -- which matters because the grounding checks and
        the health endpoint both key off these counts, and "broken" needs a
        different response than "not seeded yet".
        """
        stats = {}
        for name in self.COLLECTION_NAMES:
            try:
                stats[name] = self.get_collection_count(name)
            except Exception as e:
                logger.error(f"Collection {name} is unreadable: {e}")
                stats[name] = -1
        return stats

    def has_unreadable_collections(self) -> bool:
        """True if any collection failed to report a count."""
        return any(v < 0 for v in self.get_all_stats().values())

    def delete_by_prefix(self, collection_name: str, id_prefix: str) -> int:
        """Delete every chunk whose id starts with `id_prefix`. Returns the count.

        The nightly refresh calls this to clear a CFR part's previous version
        before ingesting the new one. It was never implemented -- the caller
        swallowed the AttributeError -- so refreshed content accumulated
        alongside the old instead of replacing it. Chunk ids embed the fetch
        date, so a re-fetch on a different date never overwrites by id, meaning
        superseded regulatory text would stay retrievable indefinitely. That is
        a correctness problem for a tool whose whole claim is current, verified
        source material.
        """
        collection = self.get_collection(collection_name)

        try:
            existing = collection.get(include=[])
        except Exception as e:
            logger.warning(f"delete_by_prefix could not enumerate {collection_name}: {e}")
            return 0

        ids = [cid for cid in (existing.get("ids") or []) if cid.startswith(id_prefix)]
        if not ids:
            return 0

        collection.delete(ids=ids)
        logger.info(f"Deleted {len(ids)} stale chunks matching '{id_prefix}' from {collection_name}")
        return len(ids)

    def delete_chunks(self, collection_name: str, ids: List[str]):
        """Delete specific chunks from a collection."""
        collection = self.get_collection(collection_name)
        collection.delete(ids=ids)
        logger.info(f"Deleted {len(ids)} chunks from {collection_name}")

    def reset_collection(self, collection_name: str):
        """Delete and recreate a collection (for re-ingestion)."""
        if collection_name not in self.COLLECTION_NAMES:
            raise ValueError(f"Unknown collection: {collection_name}")

        self._ensure_client()
        try:
            self._client.delete_collection(name=collection_name)
            logger.info(f"Deleted collection: {collection_name}")
        except Exception:
            pass  # Collection may not exist

        self._collections.pop(collection_name, None)
        self._get_or_create_collection(collection_name)


# Singleton
_store: Optional[ChromaStore] = None


def get_store() -> ChromaStore:
    """Get the singleton ChromaStore instance."""
    global _store
    if _store is None:
        _store = ChromaStore()
    return _store
