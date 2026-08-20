"""
Guidance Client — loads federal compliance-program guidance that is NOT in the CFR.

Why this exists
---------------
The eCFR pipeline can only fetch codified regulation. But the documents that
actually define what an "effective compliance program" looks like are agency
guidance, not regulation:

  * OIG's General Compliance Program Guidance (GCPG) states the seven elements.
    No CFR part states them. The app has always *named* the seven elements from
    the model's own memory while claiming to be source-grounded.
  * The OIG/HCCA "Measuring Compliance Program Effectiveness" resource guide is
    the reference compliance officers actually use to audit a program, and it
    was co-authored by HCCA. HCCA's own manuals are copyrighted and paywalled;
    this one is free and public precisely because OIG co-published it.
  * OIG's industry segment-specific guidance (ICPGs) carry the current,
    named risk areas for a given provider type.

All of these are published by HHS-OIG as free public PDFs. They are downloaded
at image build time alongside the CFR corpus.

Robustness note
---------------
OIG's direct PDF paths are inconsistent between documents (compare
`/documents/compliance-guidance/1135/` with `/documents/compliance/10038/`) and
have moved before. So each document lists any known direct URLs *and* a landing
page; if the direct URLs fail, the landing page HTML is scanned for a PDF link.
A document that cannot be fetched is skipped with a warning -- guidance is
additive, and losing it must never take down regulation seeding.
"""

import asyncio
import io
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urljoin

import httpx

logger = logging.getLogger(__name__)

# Guidance PDFs are multi-megabyte and served slowly; the 20s used for eCFR
# JSON is not enough. Still bounded so a hung download can't stall the build.
_DOWNLOAD_TIMEOUT = 90.0

# Refuse anything implausibly large rather than trying to parse it.
_MAX_PDF_BYTES = 40 * 1024 * 1024


# Guidance PDFs bundled with the repo. Downloading them at build time makes the
# corpus depend on oig.hhs.gov being reachable from the build host at that exact
# moment; a bundled copy makes the build deterministic and offline-safe. The
# network path below still exists for documents that are not bundled.
BUNDLED_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "guidance_sources",
)


@dataclass(frozen=True)
class GuidanceDocument:
    """One public guidance document to load into the knowledge base."""
    key: str
    label: str
    authority: str
    citation: str
    published: str                       # ISO date the document itself carries
    landing_url: str                     # scanned for a PDF link if direct URLs fail
    bundled_filename: str = ""           # file in guidance_sources/, tried first
    pdf_urls: tuple = ()                 # known direct URLs, tried in order
    industries: tuple = ()               # informational: who this is aimed at


# Current as of August 2026. OIG's "Modernization of Compliance Program Guidance
# Documents" initiative (announced April 2023) replaced the 1998-2008 guidance
# with one general document plus a rolling series of industry-specific ones, so
# this list is expected to grow -- the Medicare Advantage ICPG below was
# published February 3, 2026 and is the most recent addition.
GUIDANCE_DOCUMENTS: List[GuidanceDocument] = [
    GuidanceDocument(
        key="oig_gcpg_2023",
        label="OIG General Compliance Program Guidance (GCPG)",
        authority="HHS Office of Inspector General",
        citation="OIG General Compliance Program Guidance (November 2023)",
        published="2023-11-06",
        landing_url="https://oig.hhs.gov/compliance/general-compliance-program-guidance/",
        bundled_filename="oig-gcpg-2023.pdf",
        pdf_urls=(
            "https://oig.hhs.gov/documents/compliance-guidance/1135/HHS-OIG-GCPG-2023.pdf",
        ),
        industries=("healthcare", "home_health", "pharmacy"),
    ),
    GuidanceDocument(
        key="oig_hcca_effectiveness",
        label="Measuring Compliance Program Effectiveness: A Resource Guide (OIG & HCCA)",
        authority="HHS Office of Inspector General and the Health Care Compliance Association",
        citation="OIG-HCCA Compliance Program Effectiveness Resource Guide (March 2017)",
        published="2017-03-27",
        landing_url="https://oig.hhs.gov/compliance/101/",
        bundled_filename="oig-hcca-effectiveness-2017.pdf",
        pdf_urls=(
            "https://oig.hhs.gov/documents/toolkits/928/HCCA-OIG-Resource-Guide.pdf",
        ),
        industries=("healthcare", "home_health", "pharmacy"),
    ),
    GuidanceDocument(
        key="oig_icpg_nursing_facility",
        label="OIG Industry Segment-Specific Compliance Program Guidance — Nursing Facilities",
        authority="HHS Office of Inspector General",
        citation="OIG Nursing Facility ICPG (November 2024)",
        published="2024-11-20",
        landing_url="https://oig.hhs.gov/compliance/nursing-facility-icpg",
        pdf_urls=(
            "https://oig.hhs.gov/documents/compliance/10038/nursing-facility-icpg.pdf",
        ),
        industries=("healthcare", "home_health"),
    ),
    GuidanceDocument(
        key="oig_icpg_medicare_advantage",
        label="OIG Industry Segment-Specific Compliance Program Guidance — Medicare Advantage",
        authority="HHS Office of Inspector General",
        citation="OIG Medicare Advantage ICPG (February 2026)",
        published="2026-02-03",
        # No direct PDF path is listed here on purpose: this document is recent
        # enough that its published path could not be confirmed, so it is
        # resolved from the landing page rather than guessed. Discovery is the
        # reliable route for it.
        landing_url="https://oig.hhs.gov/compliance/medicare-advantage-icpg/",
        pdf_urls=(),
        industries=("healthcare",),
    ),
]


class GuidanceClient:
    """Downloads public agency guidance PDFs and extracts their text."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._client_loop = None

    @property
    def client(self) -> httpx.AsyncClient:
        # Same event-loop binding rule as the eCFR client: an AsyncClient
        # belongs to the loop that created it, and seeding may run in a
        # different loop than the one alive later. Rebuild on loop change
        # instead of dying with "Event loop is closed".
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._client is not None and self._client_loop is not current_loop:
            logger.info("Guidance client was bound to a different event loop — rebuilding it.")
            self._client = None

        if self._client is None or self._client.is_closed:
            self._client_loop = current_loop
            self._client = httpx.AsyncClient(
                timeout=_DOWNLOAD_TIMEOUT,
                follow_redirects=True,
                headers={
                    "Accept": "application/pdf,text/html",
                    "User-Agent": "CompliancePolicyAnalyzer/3.0 (healthcare-compliance-research)",
                },
            )
        return self._client

    async def fetch_document(self, doc: GuidanceDocument) -> Optional[Dict]:
        """Download and extract one guidance document.

        Returns {'sections': [{'heading', 'text'}], 'url': str} or None.
        """
        pdf_bytes, resolved_url = await self._download_pdf(doc)
        if not pdf_bytes:
            logger.warning(f"Guidance: could not download {doc.label}")
            return None

        text = extract_pdf_text(pdf_bytes)
        if not text or len(text) < 500:
            logger.warning(
                f"Guidance: {doc.label} downloaded ({len(pdf_bytes)} bytes) but yielded "
                f"only {len(text)} characters of text — treating as a failed extraction."
            )
            return None

        sections = split_into_sections(text)
        logger.info(f"Guidance: {doc.label} — {len(text)} chars, {len(sections)} sections")
        return {"sections": sections, "url": resolved_url, "char_count": len(text)}

    async def _download_pdf(self, doc: GuidanceDocument) -> tuple:
        """Bundled copy first, then known PDF URLs, then discovery."""
        bundled = self._read_bundled(doc)
        if bundled:
            # Cite the canonical public URL even though the bytes came from
            # disk -- the reader needs the authoritative source, not a path
            # inside a container.
            return bundled, (doc.pdf_urls[0] if doc.pdf_urls else doc.landing_url)

        for url in doc.pdf_urls:
            data = await self._get_pdf(url)
            if data:
                return data, url

        discovered = await self._discover_pdf_url(doc.landing_url)
        if discovered:
            logger.info(f"Guidance: resolved {doc.label} PDF from landing page -> {discovered}")
            data = await self._get_pdf(discovered)
            if data:
                return data, discovered

        return None, doc.landing_url

    def _read_bundled(self, doc: GuidanceDocument) -> Optional[bytes]:
        """Read a guidance PDF shipped with the repo, if there is one."""
        if not doc.bundled_filename:
            return None
        path = os.path.join(BUNDLED_DIR, doc.bundled_filename)
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except FileNotFoundError:
            logger.info(f"Guidance: no bundled copy at {path} — will try the network.")
            return None
        except OSError as e:
            logger.warning(f"Guidance: could not read bundled {path}: {e}")
            return None

        if not data.startswith(b"%PDF"):
            logger.warning(f"Guidance: bundled file {path} is not a PDF — ignoring it.")
            return None

        logger.info(f"Guidance: using bundled copy of {doc.label} ({len(data)} bytes)")
        return data

    async def _get_pdf(self, url: str) -> Optional[bytes]:
        try:
            response = await self.client.get(url)
        except Exception as e:
            logger.warning(f"Guidance: request failed for {url}: {type(e).__name__}: {e}")
            return None

        if response.status_code != 200:
            logger.warning(f"Guidance: {url} returned HTTP {response.status_code}")
            return None

        data = response.content
        if len(data) > _MAX_PDF_BYTES:
            logger.warning(f"Guidance: {url} is {len(data)} bytes — over the size cap, skipping.")
            return None
        if not data.startswith(b"%PDF"):
            # A 200 that is actually an HTML error page. Reported explicitly,
            # because silently feeding HTML to the PDF parser produces an empty
            # extraction that looks like a parsing bug instead of a bad URL.
            logger.warning(f"Guidance: {url} returned 200 but the body is not a PDF.")
            return None
        return data

    async def _discover_pdf_url(self, landing_url: str) -> Optional[str]:
        """Scan a landing page for the guidance PDF link.

        OIG's direct paths differ per document and have changed, so pinning
        URLs alone is fragile. The landing pages are stable.
        """
        try:
            response = await self.client.get(landing_url)
        except Exception as e:
            logger.warning(f"Guidance: landing page {landing_url} failed: {type(e).__name__}: {e}")
            return None

        if response.status_code != 200:
            logger.warning(f"Guidance: landing page {landing_url} returned HTTP {response.status_code}")
            return None

        return find_pdf_link(response.text, landing_url)

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()


def find_pdf_link(html: str, base_url: str) -> Optional[str]:
    """Return the most likely guidance PDF href on a landing page.

    Prefers links whose path hints at compliance guidance over incidental PDFs
    (newsletters, unrelated reports) that also appear on OIG pages.
    """
    hrefs = re.findall(r'href=["\']([^"\']+\.pdf[^"\']*)["\']', html, flags=re.IGNORECASE)
    if not hrefs:
        return None

    preferred = ("icpg", "gcpg", "compliance-guidance", "compliance", "resource-guide")

    def rank(href: str) -> int:
        lowered = href.lower()
        for i, token in enumerate(preferred):
            if token in lowered:
                return i
        return len(preferred)

    best = min(hrefs, key=rank)
    return urljoin(base_url, best)


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract plain text from a PDF.

    pdfplumber is already a dependency (used for uploaded policy documents),
    so guidance loading adds no new package.
    """
    try:
        import pdfplumber
    except ImportError:  # pragma: no cover - pdfplumber is a pinned dependency
        logger.error("pdfplumber is not installed — cannot load guidance PDFs.")
        return ""

    pages: List[str] = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                try:
                    page_text = page.extract_text() or ""
                except Exception as e:
                    logger.warning(f"Guidance: a page failed to extract: {e}")
                    continue
                finally:
                    # pdfplumber keeps every character object it built for a
                    # page. Across a long document that reached ~390 MB for a
                    # single file, which is most of a small container's whole
                    # budget. Dropping each page's cache as we go keeps peak
                    # usage flat instead of proportional to page count.
                    page.flush_cache()
                    page.get_textmap.cache_clear()
                if page_text.strip():
                    pages.append(page_text)
    except Exception as e:
        logger.warning(f"Guidance: PDF could not be opened: {type(e).__name__}: {e}")
        return ""

    return "\n\n".join(pages)


# Headings in OIG guidance look like "II. Compliance Program Infrastructure",
# "A. Written Policies and Procedures", or a bare title-case line. Matching a
# few shapes is more reliable than trying to infer structure from font size,
# which pdfplumber's plain text extraction discards anyway.
_HEADING_PATTERNS = (
    re.compile(r"^(?:[IVXLC]+)\.\s+(.{3,90})$"),          # II. Section Title
    re.compile(r"^(?:[A-Z])\.\s+(.{3,90})$"),             # A. Subsection Title
    re.compile(r"^(?:\d{1,2})\.\s+([A-Z].{3,90})$"),      # 3. Training and Education
)


def _is_heading(line: str) -> Optional[str]:
    stripped = line.strip()
    if not (3 < len(stripped) <= 95):
        return None
    if stripped.endswith((".", ",", ";", ":")) and not stripped.endswith("..."):
        # Sentences end in punctuation; headings generally do not. A trailing
        # colon is allowed through below only for the all-caps case.
        if not stripped.isupper():
            return None
    for pattern in _HEADING_PATTERNS:
        match = pattern.match(stripped)
        if match:
            return stripped
    # All-caps lines that are not page furniture.
    letters = [c for c in stripped if c.isalpha()]
    if letters and all(c.isupper() for c in letters) and len(letters) >= 6:
        return stripped
    return None


# Table-of-contents rows ("III. Compliance Program Infrastructure ...... 12")
# match the heading patterns and would otherwise be ingested as content,
# producing sections whose entire body is page numbers.
_TOC_LINE = re.compile(r"\.{4,}\s*\d+\s*$")

# The OIG/HCCA effectiveness guide is laid out as wide tables, which flatten
# into long unbroken runs with no detectable headings -- one section came out
# at over 100,000 characters. Sections are capped so every chunk keeps a
# meaningful heading instead of inheriting one label for half the document.
_MAX_SECTION_CHARS = 4000


def _cap_section(heading: str, body: str) -> List[Dict[str, str]]:
    """Break an oversized section into labelled parts on word boundaries."""
    if len(body) <= _MAX_SECTION_CHARS:
        return [{"heading": heading, "text": body}]

    parts: List[Dict[str, str]] = []
    words = body.split(" ")
    current: List[str] = []
    length = 0
    for word in words:
        if length + len(word) + 1 > _MAX_SECTION_CHARS and current:
            parts.append(" ".join(current))
            current, length = [], 0
        current.append(word)
        length += len(word) + 1
    if current:
        parts.append(" ".join(current))

    return [
        {"heading": heading if i == 0 else f"{heading} (cont. {i + 1})", "text": part}
        for i, part in enumerate(parts)
    ]


def split_into_sections(text: str, min_section_chars: int = 200) -> List[Dict[str, str]]:
    """Split extracted guidance text into heading-labelled sections.

    Falls back to a single whole-document section when too little structure is
    found, so a layout change degrades retrieval quality rather than dropping
    the document entirely.
    """
    # Contents rows are dropped up front so the whole-document fallback below
    # cannot reintroduce them: it must rebuild from the filtered lines, not
    # from the original text.
    lines = [line for line in text.splitlines() if not _TOC_LINE.search(line)]

    sections: List[Dict[str, str]] = []
    current_heading = "Introduction"
    buffer: List[str] = []

    def flush():
        body = " ".join(" ".join(buffer).split()).strip()
        if len(body) >= min_section_chars:
            sections.extend(_cap_section(current_heading, body))

    for line in lines:
        heading = _is_heading(line)
        if heading:
            flush()
            current_heading = heading
            buffer = []
        else:
            buffer.append(line)
    flush()

    if len(sections) < 3:
        body = " ".join(" ".join(lines).split()).strip()
        if not body:
            return []
        logger.info(
            "Guidance: found little heading structure — ingesting the document as one section."
        )
        return _cap_section("Full document", body)

    return sections


_guidance_client: Optional[GuidanceClient] = None


def get_guidance_client() -> GuidanceClient:
    global _guidance_client
    if _guidance_client is None:
        _guidance_client = GuidanceClient()
    return _guidance_client
