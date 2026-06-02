"""
Live Research Service — Controlled web research from curated regulatory sources.

This is NOT a general-purpose web scraper. It searches only from pre-approved
regulatory and compliance sources, and only when the internal knowledge base
is insufficient.

Design principles:
  - Never browse the entire web freely
  - Only search curated, authoritative sources (HHS.gov, Federal Register, OCR, etc.)
  - Search results are extracted, normalized, and tagged before being sent to the LLM
  - Each retrieved fact carries source, date, and provenance metadata
  - Live research is clearly distinguished from curated retrieval in all outputs
  - Live research is OPTIONAL and only used when:
    a) The internal KB is missing needed source material, OR
    b) The user explicitly asks for current updates

Curated search sources:
  - HHS.gov regulatory guidance
  - Federal Register (federalregister.gov)
  - OCR guidance and enforcement (hhs.gov/hipaa/for-professionals)
  - CMS regulations and guidance (cms.gov)
  - OIG advisory opinions and work plans (oig.hhs.gov)
"""

import logging
import os
import re
from datetime import datetime
from typing import Optional, List, Dict, Any

import httpx

from app.config import settings
from app.services.retrieval.models import (
    SourceChunk, SourceMetadata, SourceType, SourceCategory, Jurisdiction,
    RetrievalResult, RetrievalContext,
)

logger = logging.getLogger(__name__)

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "").strip()
TAVILY_URL = "https://api.tavily.com/search"


# ── Curated Source Registry ──
# Only these domains are allowed for live research.
# Each entry has a display name, the domain, and the type of content expected.

CURATED_SOURCES = {
    # ── Healthcare ──
    "hhs_regulations": {
        "name": "HHS Regulations & Guidance",
        "domain": "hhs.gov",
        "search_prefix": "site:hhs.gov HIPAA regulation guidance",
        "category": SourceCategory.ocr_guidance,
        "authority": "HHS",
    },
    "federal_register": {
        "name": "Federal Register",
        "domain": "federalregister.gov",
        "search_prefix": "site:federalregister.gov compliance regulation",
        "category": SourceCategory.federal_regulation,
        "authority": "Office of the Federal Register",
    },
    "ocr_enforcement": {
        "name": "OCR Enforcement Actions",
        "domain": "hhs.gov/hipaa/for-professionals/compliance-enforcement",
        "search_prefix": "site:hhs.gov/hipaa/for-professionals/compliance-enforcement OCR enforcement",
        "category": SourceCategory.enforcement_action,
        "authority": "HHS OCR",
    },
    "cms_guidance": {
        "name": "CMS Regulations & Guidance",
        "domain": "cms.gov",
        "search_prefix": "site:cms.gov regulation guidance hospital compliance",
        "category": SourceCategory.federal_regulation,
        "authority": "CMS",
    },
    "oig_advisory": {
        "name": "OIG Advisory Opinions & Work Plans",
        "domain": "oig.hhs.gov",
        "search_prefix": "site:oig.hhs.gov advisory opinion work plan",
        "category": SourceCategory.ocr_guidance,
        "authority": "HHS OIG",
    },
    # ── Education / Childcare ──
    "education_dept": {
        "name": "U.S. Department of Education",
        "domain": "ed.gov",
        "search_prefix": "site:ed.gov FERPA regulation guidance compliance",
        "category": SourceCategory.federal_regulation,
        "authority": "U.S. Department of Education",
    },
    "education_ocr": {
        "name": "ED Office for Civil Rights",
        "domain": "ed.gov/about/offices/list/ocr",
        "search_prefix": "site:ed.gov/about/offices/list/ocr Title IX Section 504 enforcement",
        "category": SourceCategory.enforcement_action,
        "authority": "ED OCR",
    },
    # ── HOA / 55+ / Fair Housing ──
    "hud_guidance": {
        "name": "HUD Fair Housing Guidance",
        "domain": "hud.gov",
        "search_prefix": "site:hud.gov Fair Housing Act 55+ HOPA age restriction guidance",
        "category": SourceCategory.federal_regulation,
        "authority": "HUD",
    },
    "hud_enforcement": {
        "name": "HUD Fair Housing Enforcement",
        "domain": "hud.gov/program_offices/fair_housing_equal_opp",
        "search_prefix": "site:hud.gov/program_offices/fair_housing_equal_opp enforcement complaint",
        "category": SourceCategory.enforcement_action,
        "authority": "HUD FHEO",
    },
}


class LiveResearchResult:
    """A single result from live research."""
    def __init__(
        self,
        title: str,
        url: str,
        snippet: str,
        source_key: str,
        source_name: str,
        date: Optional[str] = None,
    ):
        self.title = title
        self.url = url
        self.snippet = snippet
        self.source_key = source_key
        self.source_name = source_name
        self.date = date

    def to_retrieval_result(self, query: str) -> RetrievalResult:
        """Convert to a RetrievalResult for integration with the pipeline."""
        source_info = CURATED_SOURCES.get(self.source_key, {})
        category = source_info.get("category", SourceCategory.federal_regulation)
        authority = source_info.get("authority", "Unknown")

        metadata = SourceMetadata(
            source_name=self.source_name,
            source_type=SourceType.live_research,
            category=category,
            jurisdiction=Jurisdiction.federal,
            effective_date=self.date,
            citation=f"{authority} — {self.title}",
            url=self.url,
            authority=authority,
            is_current=True,
            chunk_index=0,
            total_chunks=1,
            collection=category.value,
        )

        chunk = SourceChunk(
            id=f"live_{self.source_key}_{hash(self.url) % 1000000}",
            text=self.snippet,
            metadata=metadata,
        )

        return RetrievalResult(
            chunk=chunk,
            score=0.6,  # Live research starts at a moderate confidence
            query=query,
        )


class LiveResearchService:
    """
    Controlled live research from curated regulatory sources.

    This service is designed to be:
      - Purposeful: Only searches when the KB is insufficient
      - Controlled: Only searches pre-approved sources
      - Transparent: All live research is clearly tagged
      - Safe: No PHI is ever sent in search queries
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
                    "User-Agent": "CompliancePolicyAnalyzer/3.0 (Healthcare compliance research tool)",
                    "Accept": "text/html,application/json",
                },
            )
        return self._client

    async def research(
        self,
        query: str,
        policy_type: str = "",
        industry: Optional[str] = None,
        jurisdiction: Optional[str] = None,
        needs_freshness: bool = False,
        max_results: int = 5,
    ) -> List[LiveResearchResult]:
        """
        Perform controlled live research from curated sources.

        Args:
            query: What to search for (sanitized — no PHI)
            policy_type: The policy type for context
            jurisdiction: State/jurisdiction for targeted research
            needs_freshness: Whether current/recent information is needed
            max_results: Maximum results to return

        Returns:
            List of LiveResearchResult objects from curated sources
        """
        # Sanitize the query (strip any potential PHI patterns)
        safe_query = self._sanitize_query(query)

        if not safe_query:
            logger.warning("Live research query was empty after sanitization")
            return []

        # Determine which curated sources to search
        target_sources = self._select_sources(policy_type, jurisdiction, needs_freshness, industry)

        all_results: List[LiveResearchResult] = []

        for source_key in target_sources:
            source_config = CURATED_SOURCES[source_key]
            try:
                results = await self._search_source(source_key, source_config, safe_query)
                all_results.extend(results[:max_results])
            except Exception as e:
                logger.warning(f"Live research failed for {source_key}: {e}")
                continue

        # Deduplicate by URL
        seen_urls = set()
        unique_results = []
        for result in all_results:
            if result.url not in seen_urls:
                seen_urls.add(result.url)
                unique_results.append(result)

        logger.info(f"Live research returned {len(unique_results)} results for query: {safe_query[:100]}")
        return unique_results[:max_results]

    async def augment_retrieval_context(
        self,
        context: RetrievalContext,
        policy_type: str = "",
        industry: Optional[str] = None,
        jurisdiction: Optional[str] = None,
        needs_freshness: bool = False,
    ) -> RetrievalContext:
        """
        Augment a RetrievalContext with live research results if the KB is insufficient.

        This is the main integration point — called by the orchestrator after
        curated retrieval if the results are sparse or the user needs current info.

        Args:
            context: The existing retrieval context from curated KB
            policy_type: The policy type
            jurisdiction: State/jurisdiction
            needs_freshness: Whether current info is needed

        Returns:
            Updated RetrievalContext with live research results appended
        """
        # Decide if live research is needed
        should_research = self._should_use_live_research(context, needs_freshness)

        if not should_research:
            return context

        logger.info(f"Augmenting retrieval with live research for: {context.query[:80]}")

        live_results = await self.research(
            query=context.query,
            policy_type=policy_type,
            industry=industry,
            jurisdiction=jurisdiction,
            needs_freshness=needs_freshness,
        )

        # Convert to RetrievalResult objects
        live_retrieval_results = [
            r.to_retrieval_result(context.query) for r in live_results
        ]

        # Update the context
        context.live_research_results = live_retrieval_results
        context.live_research_used = True
        context.total_sources_found += len(live_retrieval_results)

        # Re-format the context with live research appended
        context.formatted_context = self._format_augmented_context(context)

        return context

    def _should_use_live_research(
        self,
        context: RetrievalContext,
        needs_freshness: bool,
    ) -> bool:
        """
        Decide whether live research is needed.

        Live research is used when:
          1. The user explicitly asks for current updates (needs_freshness=True)
          2. The curated KB returned very few or no results
          3. The KB results are all low-relevance
        """
        # User explicitly asked for current info
        if needs_freshness:
            return True

        # KB returned nothing
        if context.total_sources_found == 0:
            return True

        # KB returned very few results and they're all low relevance
        if len(context.retrieved_chunks) <= 1:
            avg_score = (
                sum(r.score for r in context.retrieved_chunks) / len(context.retrieved_chunks)
                if context.retrieved_chunks
                else 0
            )
            if avg_score < 0.3:
                return True

        return False

    def _select_sources(
        self,
        policy_type: str,
        jurisdiction: Optional[str],
        needs_freshness: bool,
        industry: Optional[str] = None,
    ) -> List[str]:
        """Select which curated sources to search based on industry and context."""
        from app.services.industry_config import get_industry
        cfg = get_industry(industry or "healthcare")
        sources = list(cfg.get("live_research_sources", ["hhs_regulations", "ocr_enforcement"]))

        # Always include Federal Register for freshness
        if needs_freshness and "federal_register" not in sources:
            sources.append("federal_register")

        # Healthcare: add CMS for billing/medicare policy types
        if industry == "healthcare" or not industry:
            if policy_type and any(kw in policy_type.lower() for kw in ["cms", "medicare", "medicaid", "billing"]):
                if "cms_guidance" not in sources:
                    sources.append("cms_guidance")
            if "oig_advisory" not in sources:
                sources.append("oig_advisory")

        return sources

    async def _search_source(
        self,
        source_key: str,
        source_config: Dict[str, Any],
        query: str,
    ) -> List[LiveResearchResult]:
        """
        Search a single curated source.

        Backend priority:
          1. Tavily (if TAVILY_API_KEY is set) — returns extracted page content,
             not just snippets. Native include_domains support, no scraping.
          2. DuckDuckGo HTML scrape — fallback if Tavily fails or isn't configured.

        Always degrades gracefully — a failed search returns [] instead of raising.
        """
        # Try Tavily first — better quality (full content extraction) and reliability.
        if TAVILY_API_KEY:
            try:
                tavily_results = await self._search_tavily(source_key, source_config, query)
                if tavily_results:
                    logger.info(f"Tavily returned {len(tavily_results)} results for {source_key}")
                    return tavily_results
                # Empty Tavily result → fall through to DDG rather than returning empty
                logger.info(f"Tavily returned no results for {source_key}, falling back to DDG")
            except Exception as e:
                logger.warning(f"Tavily search failed for {source_key} ({type(e).__name__}: {e}), falling back to DDG")

        # DuckDuckGo HTML fallback (legacy path, brittle but free)
        return await self._search_ddg(source_key, source_config, query)

    async def _search_tavily(
        self,
        source_key: str,
        source_config: Dict[str, Any],
        query: str,
    ) -> List[LiveResearchResult]:
        """
        Search via Tavily API. Constrained to the source's whitelisted domain
        via Tavily's native include_domains parameter — no site: prefix needed.
        Returns extracted page content (not just snippets) when available.
        """
        # Extract the bare domain (Tavily wants the host, not a path).
        # source_config["domain"] may be e.g. "hhs.gov/hipaa/for-professionals/compliance-enforcement"
        # — Tavily expects "hhs.gov" and we filter by URL prefix in post-processing.
        full_domain_path = source_config["domain"]
        bare_domain = full_domain_path.split("/")[0]

        payload = {
            "api_key": TAVILY_API_KEY,
            "query": query,
            "include_domains": [bare_domain],
            "max_results": 5,
            "search_depth": "basic",  # "basic" = 1 credit; "advanced" = 2 credits
            "include_raw_content": False,  # use snippet-extracted content; full HTML not needed
        }

        response = await self.client.post(TAVILY_URL, json=payload, timeout=15.0)
        if response.status_code != 200:
            logger.warning(f"Tavily returned status {response.status_code} for {source_key}: {response.text[:200]}")
            return []

        data = response.json()
        items = data.get("results", []) or []

        results: List[LiveResearchResult] = []
        for item in items:
            url = (item.get("url") or "").strip()
            if not url:
                continue
            # Enforce path-level scoping (e.g. only OCR enforcement, not all of hhs.gov)
            if full_domain_path not in url:
                continue

            title = (item.get("title") or "").strip()
            # Prefer 'content' (Tavily's extracted snippet) over the raw URL
            snippet = (item.get("content") or "").strip()
            published = (item.get("published_date") or "").strip() or None

            results.append(LiveResearchResult(
                title=title,
                url=url,
                snippet=snippet,
                source_key=source_key,
                source_name=source_config["name"],
                date=published,
            ))

        return results

    async def _search_ddg(
        self,
        source_key: str,
        source_config: Dict[str, Any],
        query: str,
    ) -> List[LiveResearchResult]:
        """Legacy DuckDuckGo HTML scrape — fallback only."""
        results: List[LiveResearchResult] = []
        search_query = f"{source_config['search_prefix']} {query}"
        try:
            search_url = "https://html.duckduckgo.com/html/"
            params = {"q": search_query, "kl": "us-en"}
            response = await self.client.post(search_url, data=params)
            if response.status_code != 200:
                logger.warning(f"DDG returned status {response.status_code} for {source_key}")
                return results
            results = self._parse_ddg_results(response.text, source_key, source_config)
        except httpx.TimeoutException:
            logger.warning(f"DDG timeout for {source_key}")
        except Exception as e:
            logger.warning(f"DDG error for {source_key}: {e}")
        return results

    def _parse_ddg_results(
        self,
        html: str,
        source_key: str,
        source_config: Dict[str, Any],
    ) -> List[LiveResearchResult]:
        """Parse DuckDuckGo HTML search results."""
        results = []

        # Extract result blocks from DDG HTML
        # DDG HTML uses class="result" divs with data attributes
        result_pattern = re.compile(
            r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
            r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
            re.DOTALL,
        )

        matches = result_pattern.findall(html)

        for url, title, snippet in matches[:5]:
            # Clean HTML tags from title and snippet
            clean_title = re.sub(r'<[^>]+>', '', title).strip()
            clean_snippet = re.sub(r'<[^>]+>', '', snippet).strip()

            # Only include results from the curated domain
            domain = source_config["domain"]
            if domain not in url:
                continue

            # Try to extract date from snippet
            date_match = re.search(r'(\w+ \d{1,2},? \d{4})', clean_snippet)
            date = date_match.group(1) if date_match else None

            results.append(LiveResearchResult(
                title=clean_title,
                url=url,
                snippet=clean_snippet,
                source_key=source_key,
                source_name=source_config["name"],
                date=date,
            ))

        return results

    def _sanitize_query(self, query: str) -> str:
        """
        Remove potential PHI from a search query.
        Never send PHI to external search engines.
        """
        # Remove patterns that look like names, SSNs, MRNs, dates of birth
        sanitized = query

        # Remove SSN patterns
        sanitized = re.sub(r'\d{3}-\d{2}-\d{4}', '[REDACTED_SSN]', sanitized)

        # Remove MRN patterns
        sanitized = re.sub(r'\bMRN[:\s]*\d+\b', '[REDACTED_MRN]', sanitized)

        # Remove date of birth patterns
        sanitized = re.sub(r'\bDOB[:\s]*\d{1,2}/\d{1,2}/\d{2,4}\b', '[REDACTED_DOB]', sanitized)

        # Remove email addresses
        sanitized = re.sub(r'\b[\w.-]+@[\w.-]+\.\w+\b', '[REDACTED_EMAIL]', sanitized)

        # Remove phone numbers
        sanitized = re.sub(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', '[REDACTED_PHONE]', sanitized)

        # Remove patient names (Last, First pattern)
        sanitized = re.sub(r'\b[A-Z][a-z]+,\s+[A-Z][a-z]+\b', '[REDACTED_NAME]', sanitized)

        # Truncate to reasonable length
        if len(sanitized) > 500:
            sanitized = sanitized[:500]

        return sanitized.strip()

    def _format_augmented_context(self, context: RetrievalContext) -> str:
        """Format the full context including both curated and live research results."""
        parts = []

        # Curated KB results
        if context.retrieved_chunks:
            parts.append(context.formatted_context)

        # Live research results (clearly labeled)
        if context.live_research_results:
            parts.append("")
            parts.append("═══ LIVE RESEARCH RESULTS ═══")
            parts.append(
                "The following results were obtained from controlled live research on curated regulatory sources. "
                "These results are current but should be verified against primary source documents. "
                "They are clearly distinct from the curated knowledge base results above."
            )
            parts.append("")

            for i, result in enumerate(context.live_research_results, 1):
                chunk = result.chunk
                meta = chunk.metadata

                parts.append(f"─── Live Source {i} ───")
                parts.append(f"[LIVE RESEARCH]")
                parts.append(f"Source Name: {meta.source_name}")
                if meta.citation:
                    parts.append(f"Citation: {meta.citation}")
                if meta.url:
                    parts.append(f"URL: {meta.url}")
                if meta.effective_date:
                    parts.append(f"Date: {meta.effective_date}")
                parts.append(f"Authority: {meta.authority}")
                parts.append("")
                parts.append(chunk.text)
                parts.append("")

            parts.append("═══ END LIVE RESEARCH RESULTS ═══")

        if not parts:
            return "No relevant source material found in the knowledge base or live research."

        return "\n".join(parts)

    async def close(self):
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# Singleton
_live_research: Optional[LiveResearchService] = None


def get_live_research_service() -> LiveResearchService:
    """Get the singleton LiveResearchService instance."""
    global _live_research
    if _live_research is None:
        _live_research = LiveResearchService()
    return _live_research
