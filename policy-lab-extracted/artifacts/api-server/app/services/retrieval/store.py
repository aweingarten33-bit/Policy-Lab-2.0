"""
ChromaDB Vector Store — Persistent local vector store for compliance source material.

Privacy-first: All embeddings run locally using ChromaDB's default embedding function
(all-MiniLM-L6-v2). No text is sent to external embedding APIs.

The store persists to disk at a configurable path (default: ./knowledge_base/).
Collections map to source categories (federal_regulation, ocr_guidance, etc.).
"""

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

        # Add in batches of 100 to avoid API limits
        batch_size = 100
        for i in range(0, len(ids), batch_size):
            batch_ids = ids[i:i + batch_size]
            batch_docs = documents[i:i + batch_size]
            batch_metas = clean_metas[i:i + batch_size]

            collection.upsert(
                ids=batch_ids,
                documents=batch_docs,
                metadatas=batch_metas,
            )

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
        """Get chunk counts for all collections."""
        stats = {}
        for name in self.COLLECTION_NAMES:
            try:
                stats[name] = self.get_collection_count(name)
            except Exception:
                stats[name] = 0
        return stats

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
