"""
eCFR Client — Pulls live regulatory text directly from the Electronic Code of Federal Regulations API.

Source: https://www.ecfr.gov/api/
No API key required. Content is the current, authoritative, in-force regulatory text.

Covers:
  - 45 CFR Part 164 (HIPAA Privacy, Security, Breach Notification)
  - 45 CFR Part 160 (General HIPAA Provisions)
  - 42 CFR Part 2 (Substance Abuse Confidentiality)
  - 42 CFR Part 482 (Conditions of Participation — Hospitals)

Each pull is timestamped so outputs clearly show when the regulation was retrieved.
"""

import logging
import re
from datetime import date, datetime
from typing import Optional, List, Dict, Any

import httpx

from app.services.retrieval.models import (
    SourceChunk, SourceMetadata, SourceType, SourceCategory, Jurisdiction,
)

logger = logging.getLogger(__name__)

ECFR_BASE = "https://www.ecfr.gov/api/versioner/v1"

# Regulations to pull — (title, part, label, category)
ECFR_TARGETS = [
    (45, 160, "45 CFR Part 160 — General HIPAA Provisions", SourceCategory.federal_regulation),
    (45, 164, "45 CFR Part 164 — HIPAA Privacy, Security & Breach", SourceCategory.federal_regulation),
    (42, 2,   "42 CFR Part 2 — Substance Abuse Confidentiality", SourceCategory.federal_regulation),
    (42, 482, "42 CFR Part 482 — Conditions of Participation (Hospitals)", SourceCategory.federal_regulation),
]


class ECFRClient:
    """
    Fetches current regulatory text from eCFR.
    Uses the versioner API which returns XML/JSON with section-level text.
    """

    def __init__(self):
        self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=60.0,
                follow_redirects=True,
                headers={
                    "Accept": "application/json, text/xml",
                    "User-Agent": "CompliancePolicyAnalyzer/3.0 (healthcare-compliance-research)",
                },
            )
        return self._client

    async def fetch_part(
        self,
        title: int,
        part: int,
        as_of: Optional[date] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch the full text of a CFR title/part.

        Returns a dict with 'sections': list of {section, heading, text} dicts.
        """
        today = (as_of or date.today()).isoformat()
        url = f"{ECFR_BASE}/full/{today}/title-{title}.json"
        params = {"part": str(part)}

        try:
            logger.info(f"Fetching eCFR: 45 CFR Part {part} as of {today}")
            response = await self.client.get(url, params=params)

            if response.status_code == 404:
                logger.warning(f"eCFR returned 404 for title-{title} part {part} — trying XML")
                return await self._fetch_part_xml(title, part, today)

            if response.status_code != 200:
                logger.warning(f"eCFR returned {response.status_code} for title-{title} part {part}")
                return None

            data = response.json()
            return self._parse_ecfr_json(data, title, part, today)

        except httpx.TimeoutException:
            logger.warning(f"eCFR timeout for title-{title} part {part}")
            return None
        except Exception as e:
            logger.warning(f"eCFR fetch failed for title-{title} part {part}: {e}")
            return None

    async def _fetch_part_xml(self, title: int, part: int, today: str) -> Optional[Dict[str, Any]]:
        """Fallback: fetch eCFR in XML format and extract section text."""
        url = f"{ECFR_BASE}/full/{today}/title-{title}.xml"
        params = {"part": str(part)}

        try:
            response = await self.client.get(url, params=params)
            if response.status_code != 200:
                return None

            text = response.text
            return self._parse_ecfr_xml(text, title, part, today)

        except Exception as e:
            logger.warning(f"eCFR XML fallback failed: {e}")
            return None

    def _parse_ecfr_json(self, data: Dict, title: int, part: int, fetched_date: str) -> Dict:
        """Parse eCFR JSON response into sections."""
        sections = []

        def walk(node, parent_heading=""):
            if not isinstance(node, dict):
                return

            heading = node.get("heading", "") or node.get("title", "") or ""
            node_type = node.get("type", "")
            identifier = node.get("identifier", "")

            # Extract section text
            if node_type in ("section", "paragraph") or identifier.startswith(f"{part}."):
                text_parts = []
                self._extract_text(node, text_parts)
                full_text = " ".join(text_parts).strip()

                if full_text and len(full_text) > 50:
                    sections.append({
                        "section": identifier or f"{title} CFR {part}",
                        "heading": heading or parent_heading,
                        "text": full_text[:3000],  # cap per section
                        "citation": f"{title} CFR § {identifier}" if identifier else f"{title} CFR Part {part}",
                    })

            # Recurse into children
            for child in node.get("children", []):
                walk(child, heading or parent_heading)

        walk(data)

        return {
            "title": title,
            "part": part,
            "fetched_date": fetched_date,
            "sections": sections,
        }

    def _extract_text(self, node: Any, accumulator: List[str]):
        """Recursively extract plain text from eCFR JSON nodes."""
        if isinstance(node, str):
            cleaned = re.sub(r'\s+', ' ', node).strip()
            if cleaned:
                accumulator.append(cleaned)
        elif isinstance(node, dict):
            # Skip metadata fields
            for key in ("text", "content", "p", "E"):
                if key in node and isinstance(node[key], str):
                    cleaned = re.sub(r'\s+', ' ', node[key]).strip()
                    if cleaned:
                        accumulator.append(cleaned)
            for child in node.get("children", []):
                self._extract_text(child, accumulator)
        elif isinstance(node, list):
            for item in node:
                self._extract_text(item, accumulator)

    def _parse_ecfr_xml(self, xml_text: str, title: int, part: int, fetched_date: str) -> Dict:
        """Parse eCFR XML into sections using regex (no lxml dependency)."""
        sections = []

        # Extract section blocks: <SECTION>...<SECTNO>164.XXX</SECTNO>...<SUBJECT>...</SUBJECT>...<P>...</P></SECTION>
        section_pattern = re.compile(
            r'<SECTION>(.*?)</SECTION>',
            re.DOTALL,
        )

        for match in section_pattern.finditer(xml_text):
            block = match.group(1)

            sectno_m = re.search(r'<SECTNO[^>]*>(.*?)</SECTNO>', block, re.DOTALL)
            subject_m = re.search(r'<SUBJECT[^>]*>(.*?)</SUBJECT>', block, re.DOTALL)
            paras = re.findall(r'<P>(.*?)</P>', block, re.DOTALL)

            sectno = re.sub(r'<[^>]+>', '', sectno_m.group(1)).strip() if sectno_m else ""
            subject = re.sub(r'<[^>]+>', '', subject_m.group(1)).strip() if subject_m else ""
            text = " ".join(
                re.sub(r'<[^>]+>', '', p).strip()
                for p in paras
            ).strip()

            if text and len(text) > 50:
                sections.append({
                    "section": sectno,
                    "heading": subject,
                    "text": text[:3000],
                    "citation": f"{title} CFR § {sectno}" if sectno else f"{title} CFR Part {part}",
                })

        return {
            "title": title,
            "part": part,
            "fetched_date": fetched_date,
            "sections": sections,
        }

    async def fetch_all_targets(self) -> List[Dict]:
        """Fetch all configured CFR targets. Returns list of part results."""
        results = []
        for title, part, label, category in ECFR_TARGETS:
            data = await self.fetch_part(title, part)
            if data and data.get("sections"):
                data["label"] = label
                data["category"] = category
                results.append(data)
                logger.info(f"eCFR fetched {len(data['sections'])} sections for {label}")
            else:
                logger.warning(f"eCFR returned no sections for {label}")
        return results

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()


def parts_to_source_chunks(part_data: Dict, category: SourceCategory) -> List[SourceChunk]:
    """Convert fetched eCFR part data into SourceChunk objects for the KB."""
    chunks = []
    title = part_data["title"]
    part = part_data["part"]
    label = part_data.get("label", f"{title} CFR Part {part}")
    fetched_date = part_data.get("fetched_date", date.today().isoformat())
    sections = part_data.get("sections", [])

    for i, section in enumerate(sections):
        text = section.get("text", "").strip()
        if not text or len(text) < 30:
            continue

        heading = section.get("heading", "")
        citation = section.get("citation", f"{title} CFR Part {part}")
        section_id = section.get("section", "")

        metadata = SourceMetadata(
            source_name=f"{label} — {heading}" if heading else label,
            source_type=SourceType.curated_source,
            category=category,
            jurisdiction=Jurisdiction.federal,
            effective_date=fetched_date,
            citation=citation,
            url=f"https://www.ecfr.gov/current/title-{title}/part-{part}" + (
                f"#p-{section_id}" if section_id else ""
            ),
            section=section_id or None,
            authority="eCFR — Electronic Code of Federal Regulations (current, in-force text)",
            is_current=True,
            chunk_index=i,
            total_chunks=len(sections),
            collection=category.value,
        )

        chunks.append(SourceChunk(
            id=f"ecfr_{title}_{part}_{i}_{fetched_date}",
            text=f"[LIVE eCFR — {fetched_date}]\n{citation}\n{heading}\n\n{text}",
            metadata=metadata,
        ))

    return chunks


_ecfr_client: Optional[ECFRClient] = None


def get_ecfr_client() -> ECFRClient:
    global _ecfr_client
    if _ecfr_client is None:
        _ecfr_client = ECFRClient()
    return _ecfr_client
