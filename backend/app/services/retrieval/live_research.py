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

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict, Any

import httpx

from app.config import settings
from app.services.retrieval.models import (
    SourceChunk, SourceMetadata, SourceType, SourceCategory, Jurisdiction,
    RetrievalResult, RetrievalContext, SourceStatus, resolve_source_status,
)

from app.services.retrieval.sanitize import sanitize_source_text, wrap_untrusted_sources

logger = logging.getLogger(__name__)

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "").strip()
TAVILY_URL = "https://api.tavily.com/search"

# A web search either answers quickly or is not going to. The old 30s ceiling
# was long enough that a search engine quietly refusing to respond -- which is
# what DuckDuckGo does to cloud IPs -- looked exactly like the app being slow.
LIVE_RESEARCH_REQUEST_TIMEOUT = 10.0

# Ceiling for a whole round of live research, across every source. Live
# research improves an answer; it must never be the reason someone waits.
LIVE_RESEARCH_TOTAL_BUDGET = 20.0

# Per-result cap on live page text. Enough to hold the provision being cited
# plus surrounding context, without letting a handful of long pages crowd the
# verified CFR text out of the prompt.
LIVE_RESEARCH_MAX_PAGE_CHARS = 6000


def _is_gov_url(url: str) -> bool:
    """True for any .gov URL (state government sites don't share one domain,
    so this is the trust boundary for the unrestricted state_gov search)."""
    host = re.sub(r"^https?://", "", url, flags=re.IGNORECASE).split("/", 1)[0].lower()
    return host == "gov" or host.endswith(".gov")


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
    # Occupational safety. Absent until now, so a workplace-safety analysis had
    # no authoritative live source of its own and fell back to whatever the
    # selected vertical happened to configure -- which for a factory noise
    # policy meant HHS, OCR and CMS.
    "osha_standards": {
        "name": "OSHA Standards & Interpretations",
        "domain": "osha.gov",
        "search_prefix": "site:osha.gov standard interpretation letter requirement",
        "category": SourceCategory.federal_regulation,
        "authority": "OSHA (US Department of Labor)",
    },
    "dol_guidance": {
        "name": "US Department of Labor Guidance",
        "domain": "dol.gov",
        "search_prefix": "site:dol.gov regulation guidance employer requirement",
        "category": SourceCategory.federal_regulation,
        "authority": "US Department of Labor",
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
    # ── State law (unrestricted domain — filtered to .gov post-search, see
    # _search_tavily/_search_ddg) — the only source not scoped to one fixed
    # site, since there's no single domain for "all 50 states' law." ──
    "state_gov": {
        "name": "State Government Sources",
        "domain": None,
        "search_prefix": "official state government regulation statute law",
        "category": SourceCategory.state_law,
        "authority": "State Government",
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


# ── Source-status classification for live results ──
#
# A search engine returning a page is not evidence about that page's legal
# standing. Federal Register carries proposed rules and final rules on the same
# domain; agency sites keep archived and superseded guidance alongside current
# guidance. Marking every live result current -- which is what this code used to
# do -- meant a January NPRM and a codified regulation arrived at the verifier
# looking identical, and an "unverified" claim could be promoted by a document
# that is not law and may never become law.
#
# So status is read from the document itself, deterministically, and anything
# these patterns cannot place stays STATUS_UNKNOWN. Unknown is the safe answer,
# not a gap to be filled with an optimistic guess.

_PROPOSED_MARKERS = re.compile(
    r"\b(proposed\s+rule|notice\s+of\s+proposed\s+rulemaking|NPRM|advance\s+notice"
    r"|request\s+for\s+comment|comment\s+period|proposed\s+regulation|draft\s+guidance"
    r"|would\s+require|if\s+finalized|when\s+finalized)\b",
    re.IGNORECASE,
)
_SUPERSEDED_MARKERS = re.compile(
    r"\b(superseded|rescinded|withdrawn|revoked|no\s+longer\s+in\s+effect"
    r"|replaced\s+by|has\s+been\s+replaced)\b",
    re.IGNORECASE,
)
_HISTORICAL_MARKERS = re.compile(
    r"(/archive|/archives|archived\s+content|historical\s+document"
    r"|this\s+page\s+is\s+(?:no\s+longer|not)\s+(?:being\s+)?(?:updated|maintained)"
    r"|for\s+historical\s+(?:reference|purposes))",
    re.IGNORECASE,
)
# The one live source whose standing IS established by where it lives: eCFR's
# /current/ tree is the codified text in force.
_CURRENT_URL = re.compile(r"^https://(?:www\.)?ecfr\.gov/current/", re.IGNORECASE)


def classify_source_status(url: str, title: str, snippet: str) -> SourceStatus:
    """Decide a live result's standing from the document, or admit not knowing."""
    url = url or ""
    haystack = f"{title or ''}\n{(snippet or '')[:4000]}"

    if _HISTORICAL_MARKERS.search(url) or _HISTORICAL_MARKERS.search(haystack):
        return SourceStatus.historical
    if _SUPERSEDED_MARKERS.search(haystack):
        return SourceStatus.superseded
    if _PROPOSED_MARKERS.search(haystack):
        return SourceStatus.proposed
    if _CURRENT_URL.match(url):
        return SourceStatus.current_verified
    return SourceStatus.status_unknown


class LiveResearchResult:
    """A single result from live research."""
    def __init__(
        self,
        title: str,
        url: str,
        snippet: str,
        source_key: str,
        source_name: str,
        published_date: Optional[str] = None,
    ):
        self.title = title
        self.url = url
        self.snippet = snippet
        self.source_key = source_key
        self.source_name = source_name
        # Deliberately named for what it is. This was `date`, and it was passed
        # straight into SourceMetadata.effective_date -- so the day a page was
        # published became "the date this rule took effect", and for the
        # DuckDuckGo path it was a date scraped out of a snippet with no stated
        # meaning at all.
        self.published_date = published_date

    @property
    def status(self) -> SourceStatus:
        return classify_source_status(self.url, self.title, self.snippet)

    def to_retrieval_result(self, query: str) -> RetrievalResult:
        """Convert to a RetrievalResult for integration with the pipeline."""
        source_info = CURATED_SOURCES.get(self.source_key, {})
        category = source_info.get("category", SourceCategory.federal_regulation)
        authority = source_info.get("authority", "Unknown")
        status = self.status

        metadata = SourceMetadata(
            source_name=self.source_name,
            source_type=SourceType.live_research,
            category=category,
            jurisdiction=Jurisdiction.federal,
            # No effective date. A live page rarely states when the provision it
            # discusses took effect, and we must not manufacture one from the
            # dates we do have.
            effective_date=None,
            publication_date=self.published_date,
            retrieved_date=datetime.now().date().isoformat(),
            last_verified_date=None,
            source_status=status,
            citation=f"{authority} — {self.title}",
            url=self.url,
            authority=authority,
            # Derived from the document's own standing, never from the fact
            # that a search returned it.
            is_current=status is SourceStatus.current_verified,
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


# ── Routing: when a live search is actually justified ──


@dataclass(frozen=True)
class ResearchDecision:
    """Whether to search, and the reason — so the choice is auditable in logs."""
    should_search: bool
    reason: str


# How much current, authoritative, on-point material counts as the knowledge
# base covering the question. Below this, the corpus is thin enough that a
# search is worth its latency and cost.
MIN_AUTHORITATIVE_SOURCES = 3


def _is_authoritative(result: RetrievalResult) -> bool:
    """Primary law and agency guidance. Templates and examples are not authority."""
    return result.chunk.metadata.category in {
        SourceCategory.federal_regulation,
        SourceCategory.federal_guidance,
        SourceCategory.ocr_guidance,
        SourceCategory.state_law,
        SourceCategory.enforcement_action,
    }


def decide_live_research(
    context: RetrievalContext,
    needs_freshness: bool = False,
    jurisdiction: Optional[str] = None,
) -> ResearchDecision:
    """Decide whether this request needs a live web search.

    This used to be ``return True``: every generation step performed a live web
    search on top of knowledge-base retrieval, whether or not the corpus already
    answered the question. That cost a search per step and, worse, pulled
    undated web pages into the evidence pool for questions the codified CFR text
    already covered completely -- so a weaker source competed with a stronger one
    for no reason.

    Ordinary code, not a planning agent. Each condition below is a fact about
    the retrieved context that can be checked directly, and a decision a model
    would have to be trusted to make correctly on every request is instead the
    same on every request.

    A search runs when, and only when, one of these holds:
      1. The user asked for current or recent developments.
      2. The knowledge base returned too little authoritative material.
      3. A jurisdiction was requested and nothing for it came back.
      4. Every authoritative source retrieved is stale or of unknown standing.
      5. Two sources give conflicting versions of the same provision.
    """
    sources = context.get_all_sources() if context else []
    authoritative = [r for r in sources if _is_authoritative(r)]

    # 1 — the user explicitly wants current developments.
    if needs_freshness:
        return ResearchDecision(True, "current/recent developments were explicitly requested")

    # 2 — the corpus does not cover the question.
    if len(authoritative) < MIN_AUTHORITATIVE_SOURCES:
        return ResearchDecision(
            True,
            f"knowledge base returned only {len(authoritative)} authoritative source(s), "
            f"below the {MIN_AUTHORITATIVE_SOURCES} needed for coverage",
        )

    # 3 — a jurisdiction was named and nothing for it came back. State law is
    # not in the corpus at all, so this is the case live research exists for.
    if jurisdiction:
        state = str(jurisdiction).strip().upper()[:2]
        has_state = any(
            r.chunk.metadata.jurisdiction.value.upper() == state
            for r in sources
        )
        if not has_state:
            return ResearchDecision(
                True, f"jurisdiction {jurisdiction} was requested but no {jurisdiction} source was retrieved"
            )

    # 4 — nothing retrieved is established as currently in force. Verification
    # would fail everything closed; a search is the only way to do better.
    current = [
        r for r in authoritative
        if resolve_source_status(r.chunk.metadata) is SourceStatus.current_verified
    ]
    if not current:
        return ResearchDecision(
            True, "no retrieved authoritative source has an established current status"
        )

    # 5 — the same provision retrieved at two different versions. Which one
    # governs cannot be settled from inside the corpus.
    versions: Dict[str, set] = {}
    for r in current:
        meta = r.chunk.metadata
        if not meta.citation:
            continue
        stamp = meta.effective_date or meta.last_verified_date or meta.retrieved_date
        if stamp:
            versions.setdefault(meta.citation.strip().lower(), set()).add(stamp)
    conflicted = [c for c, stamps in versions.items() if len(stamps) > 1]
    if conflicted:
        return ResearchDecision(
            True, f"conflicting versions retrieved for {conflicted[0]} — resolving against the publisher"
        )

    return ResearchDecision(
        False,
        f"knowledge base covers this request ({len(current)} current authoritative source(s)); "
        f"no search needed",
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
                timeout=LIVE_RESEARCH_REQUEST_TIMEOUT,
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

        # Searched concurrently, and this matters a lot. Run one after another,
        # five curated sources against a 30-second timeout could spend 150
        # seconds before a single word was generated -- and that cost was paid
        # again at every generation step. Waiting on five independent HTTP
        # requests in sequence buys nothing: the whole batch now takes about as
        # long as its slowest member.
        async def _search_one(source_key: str) -> List[LiveResearchResult]:
            source_config = CURATED_SOURCES[source_key]
            # state_gov has no fixed domain to scope the search, so the state
            # itself has to be in the query text or every result is a coin flip.
            source_query = (
                f"{jurisdiction} {safe_query}"
                if source_key == "state_gov" and jurisdiction
                else safe_query
            )
            try:
                results = await self._search_source(source_key, source_config, source_query)
                return results[:max_results]
            except Exception as e:
                logger.warning(f"Live research failed for {source_key}: {e}")
                return []

        started = time.monotonic()
        try:
            # A hard ceiling on top of the per-request timeouts. Live research
            # is an enhancement; it must never be the reason a user waits.
            gathered = await asyncio.wait_for(
                asyncio.gather(*(_search_one(k) for k in target_sources)),
                timeout=LIVE_RESEARCH_TOTAL_BUDGET,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"Live research exceeded its {LIVE_RESEARCH_TOTAL_BUDGET}s budget — "
                f"continuing with knowledge-base grounding only."
            )
            gathered = []

        all_results: List[LiveResearchResult] = [r for batch in gathered for r in batch]
        logger.info(
            f"Live research: {len(target_sources)} sources searched concurrently in "
            f"{time.monotonic() - started:.1f}s"
        )

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
        should_research = self._should_use_live_research(context, needs_freshness, jurisdiction)

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
        jurisdiction: Optional[str] = None,
    ) -> bool:
        """Whether this request justifies a live search. See decide_live_research."""
        decision = decide_live_research(context, needs_freshness, jurisdiction)
        logger.info(
            "Live research %s — %s",
            "ON" if decision.should_search else "SKIPPED",
            decision.reason,
        )
        return decision.should_search

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

        # Healthcare and home health: add CMS for billing/medicare policy types.
        # Home health is Medicare-centric by default (CoPs, PDGM billing, OASIS)
        # even more than general healthcare, so it gets the same treatment.
        if industry in ("healthcare", "home_health") or not industry:
            if policy_type and any(kw in policy_type.lower() for kw in ["cms", "medicare", "medicaid", "billing"]):
                if "cms_guidance" not in sources:
                    sources.append("cms_guidance")
            if "oig_advisory" not in sources:
                sources.append("oig_advisory")

        # A state is selected — search state government sources for that
        # state's specific law, since the KB never has state-law content.
        if jurisdiction and "state_gov" not in sources:
            sources.append("state_gov")

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

        source_config["domain"] of None (state_gov only) means there's no
        single fixed domain to whitelist — every state has its own government
        domains — so the search runs unrestricted and results are filtered to
        .gov URLs afterward instead.
        """
        full_domain_path = source_config["domain"]
        bare_domain = full_domain_path.split("/")[0] if full_domain_path else None

        payload = {
            "api_key": TAVILY_API_KEY,
            "query": query,
            "max_results": 5,
            # Cost is driven by search_depth, not by raw content: "basic" is
            # 1 credit, "advanced" is 2. Staying on basic keeps every search
            # at a single credit.
            "search_depth": "basic",
            # Raw page text matters specifically for this product. Verification
            # can only confirm a claim against text it actually holds, and a
            # one-paragraph search summary rarely contains the provision being
            # cited -- so citations came back unverified even when the source
            # said exactly what the claim said. Full page text gives the
            # verifier something real to check against.
            "include_raw_content": settings.live_research_raw_content,
        }
        if bare_domain:
            payload["include_domains"] = [bare_domain]

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
            if full_domain_path:
                # Enforce path-level scoping (e.g. only OCR enforcement, not all of hhs.gov)
                if full_domain_path not in url:
                    continue
            elif not _is_gov_url(url):
                # Unrestricted search (state_gov) — only trust .gov results
                continue

            title = (item.get("title") or "").strip()
            # Prefer full page text when it came back, falling back to Tavily's
            # extracted summary. Trimmed rather than used whole: an entire
            # regulation page can be enormous, and several of them per source
            # would crowd out the retrieved CFR text in the prompt.
            summary = (item.get("content") or "").strip()
            raw = (item.get("raw_content") or "").strip()
            if raw and len(raw) > len(summary):
                snippet = raw[:LIVE_RESEARCH_MAX_PAGE_CHARS]
            else:
                snippet = summary
            # Tavily's published_date is a publication date and is carried as
            # one. It is not evidence of when anything took legal effect.
            published = (item.get("published_date") or "").strip() or None

            results.append(LiveResearchResult(
                title=title,
                url=url,
                snippet=snippet,
                source_key=source_key,
                source_name=source_config["name"],
                published_date=published,
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
        # state_gov has no fixed domain — restrict via the site: operator
        # instead, then _parse_ddg_results double-checks with _is_gov_url.
        prefix = source_config["search_prefix"] + (" site:.gov" if not source_config["domain"] else "")
        search_query = f"{prefix} {query}"
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

            # Only include results from the curated domain (or, for state_gov
            # which has no fixed domain, any .gov result)
            domain = source_config["domain"]
            if domain:
                if domain not in url:
                    continue
            elif not _is_gov_url(url):
                continue

            # No date is taken from the snippet.
            #
            # This used to regex the first date-shaped string out of the search
            # summary and store it as the source's effective date. That string
            # is as likely to be the date of a settlement being described, a
            # comment deadline, or the day the page was last touched as it is to
            # be anything about the provision -- and once stored it was
            # indistinguishable from a date the source actually stated. An
            # unknown date must stay unknown.
            results.append(LiveResearchResult(
                title=clean_title,
                url=url,
                snippet=clean_snippet,
                source_key=source_key,
                source_name=source_config["name"],
                published_date=None,
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
                "The following results were obtained from controlled live research on curated "
                "regulatory sources. Each one carries a Source Status. Only CURRENT_VERIFIED "
                "may be relied on for a statement about what the law requires today; PROPOSED, "
                "SUPERSEDED, HISTORICAL and STATUS_UNKNOWN may be mentioned as context but must "
                "never be written as a present legal requirement, and must never be given a "
                "compliance deadline. They are clearly distinct from the curated knowledge base "
                "results above."
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
                if meta.publication_date:
                    parts.append(f"Publication Date: {meta.publication_date}")
                if meta.effective_date:
                    parts.append(f"Effective Date (stated by the source): {meta.effective_date}")
                else:
                    parts.append("Effective Date: NOT STATED BY THIS SOURCE — do not infer one")
                status = resolve_source_status(meta)
                parts.append(f"Source Status: {status.value}")
                if status is not SourceStatus.current_verified:
                    parts.append(
                        "NOTE: this source is NOT established as current law. Anything taken "
                        "from it must be described as context, never as a present requirement."
                    )
                parts.append(f"Authority: {meta.authority}")
                parts.append("")
                # Fetched from a remote page -- the most plausibly
                # attacker-controlled text in the whole pipeline.
                parts.append(sanitize_source_text(chunk.text))
                parts.append("")

            parts.append("═══ END LIVE RESEARCH RESULTS ═══")

        if not parts:
            return "No relevant source material found in the knowledge base or live research."

        # The KB block already carries its own untrusted-data boundary from the
        # retriever. Wrap again only when live results were appended, so remote
        # content is never the last thing in context without a closing reminder.
        joined = "\n".join(parts)
        if context.live_research_results:
            joined = wrap_untrusted_sources(joined)
        return joined

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
