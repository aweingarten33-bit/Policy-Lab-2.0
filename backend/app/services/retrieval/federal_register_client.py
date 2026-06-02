"""
Federal Register Client — Real-time access to the Federal Register API.

Source: https://www.federalregister.gov/api/v1/
No API key required. Returns current and historical federal regulatory documents.

Used on EVERY analysis (not cached) to pull the most recent:
  - Final rules affecting healthcare
  - HHS/OCR enforcement notices
  - CMS guidance documents
  - Proposed rules (so gaps include pending changes)
  - OIG advisory opinions

Every result is timestamped and labeled [LIVE — Federal Register].
"""

import logging
from datetime import datetime, timedelta, date
from typing import Optional, List, Dict, Any

import httpx

from app.services.retrieval.models import (
    SourceChunk, SourceMetadata, SourceType, SourceCategory, Jurisdiction,
    RetrievalResult,
)

logger = logging.getLogger(__name__)

FR_BASE = "https://www.federalregister.gov/api/v1"

# Agencies whose documents are always relevant for healthcare compliance
HEALTHCARE_AGENCIES = [
    "health-and-human-services-department",
    "centers-for-medicare-medicaid-services",
    "office-for-civil-rights-hhs",
    "office-of-inspector-general-hhs",
]

# Document types to pull
DOCUMENT_TYPES = ["RULE", "PRORULE", "NOTICE"]


class FederalRegisterClient:
    """
    Real-time Federal Register search.
    Called on every analysis — results are NEVER stale.
    """

    def __init__(self):
        self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "CompliancePolicyAnalyzer/3.0 (healthcare-compliance-research)",
                },
            )
        return self._client

    async def search(
        self,
        query: str,
        days_back: int = 365,
        max_results: int = 8,
        agencies: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search the Federal Register for documents matching the query.

        Args:
            query: Search terms (compliance topic, regulation type, etc.)
            days_back: How far back to look (default 1 year)
            max_results: Max documents to return
            agencies: Agency slugs to filter by (default: all healthcare agencies)

        Returns:
            List of document dicts with title, abstract, date, url, citation
        """
        since = (date.today() - timedelta(days=days_back)).isoformat()
        target_agencies = agencies or HEALTHCARE_AGENCIES

        params = {
            "conditions[term]": query,
            "conditions[publication_date][gte]": since,
            "conditions[type][]": DOCUMENT_TYPES,
            "fields[]": [
                "title", "abstract", "document_number", "publication_date",
                "html_url", "agencies", "docket_ids", "action", "citation",
                "effective_on", "regulation_id_numbers",
            ],
            "per_page": max_results,
            "order": "relevance",
        }

        # Add agency filters
        for agency in target_agencies:
            params.setdefault("conditions[agencies][]", [])
            if isinstance(params["conditions[agencies][]"], list):
                params["conditions[agencies][]"].append(agency)
            else:
                params["conditions[agencies][]"] = [params["conditions[agencies][]"], agency]

        try:
            response = await self.client.get(f"{FR_BASE}/documents.json", params=params)

            if response.status_code != 200:
                logger.warning(f"Federal Register returned {response.status_code} for query: {query[:60]}")
                return []

            data = response.json()
            results = data.get("results", [])
            logger.info(f"Federal Register: {len(results)} results for '{query[:60]}'")
            return results

        except httpx.TimeoutException:
            logger.warning(f"Federal Register timeout for query: {query[:60]}")
            return []
        except Exception as e:
            logger.warning(f"Federal Register search failed: {e}")
            return []

    async def get_recent_enforcement(self, days_back: int = 180) -> List[Dict[str, Any]]:
        """Pull recent OCR/OIG enforcement notices and final rules."""
        return await self.search(
            query="HIPAA enforcement penalty civil monetary compliance",
            days_back=days_back,
            agencies=["office-for-civil-rights-hhs", "office-of-inspector-general-hhs"],
        )

    async def get_recent_guidance(self, topic: str = "", days_back: int = 365) -> List[Dict[str, Any]]:
        """Pull recent HHS/CMS guidance relevant to a compliance topic."""
        base_query = "healthcare compliance guidance regulation"
        query = f"{topic} {base_query}".strip() if topic else base_query
        return await self.search(query=query, days_back=days_back)

    def to_retrieval_results(
        self,
        documents: List[Dict[str, Any]],
        query: str,
    ) -> List[RetrievalResult]:
        """Convert Federal Register documents to RetrievalResult objects."""
        results = []
        fetched_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        for i, doc in enumerate(documents):
            title = doc.get("title", "Untitled")
            abstract = doc.get("abstract") or doc.get("action") or ""
            pub_date = doc.get("publication_date", "")
            effective_date = doc.get("effective_on") or pub_date
            url = doc.get("html_url", "")
            citation = doc.get("citation", doc.get("document_number", ""))
            doc_number = doc.get("document_number", "")

            if not abstract or len(abstract) < 20:
                continue

            text = (
                f"[LIVE — Federal Register — Retrieved {fetched_at}]\n"
                f"Document: {title}\n"
                f"Published: {pub_date}\n"
                f"Effective: {effective_date}\n"
                f"Citation: {citation}\n"
                f"Document Number: {doc_number}\n\n"
                f"{abstract}"
            )

            metadata = SourceMetadata(
                source_name=f"Federal Register — {title}",
                source_type=SourceType.live_research,
                category=SourceCategory.federal_regulation,
                jurisdiction=Jurisdiction.federal,
                effective_date=effective_date or pub_date,
                citation=citation,
                url=url,
                authority="Federal Register (Office of the Federal Register)",
                is_current=True,
                chunk_index=i,
                total_chunks=len(documents),
                collection=SourceCategory.federal_regulation.value,
            )

            chunk = SourceChunk(
                id=f"fr_{doc_number}_{i}",
                text=text,
                metadata=metadata,
            )

            results.append(RetrievalResult(
                chunk=chunk,
                score=0.75,  # Live FR results are high-confidence
                query=query,
            ))

        return results

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()


_fr_client: Optional[FederalRegisterClient] = None


def get_fr_client() -> FederalRegisterClient:
    global _fr_client
    if _fr_client is None:
        _fr_client = FederalRegisterClient()
    return _fr_client
