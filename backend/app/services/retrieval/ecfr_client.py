"""
eCFR Client — Pulls live regulatory text directly from the Electronic Code of Federal Regulations API.

Source: https://www.ecfr.gov/api/
No API key required. Content is the current, authoritative, in-force regulatory text.

ECFR_TARGETS (what gets fetched into the knowledge base) is derived from every
industry's "ecfr_targets" list in industry_config.py, deduplicated by (title,
part). This used to be a separate hardcoded healthcare-only list here, which
meant the KB never actually contained Home Health (42 CFR 484/424) or Other
(29 CFR 1630/1604/825) source material — those industries' gap analyses and
drafts were citing regulations from the model's own training data rather than
verified, dated source chunks, even though the product is positioned as
"source-grounded." Deriving from industry_config.py keeps the two in sync
automatically whenever an industry's regulation list changes.

Each pull is timestamped so outputs clearly show when the regulation was retrieved.
"""

import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any

import httpx

from app.services.retrieval.models import (
    SourceChunk, SourceMetadata, SourceType, SourceCategory, Jurisdiction,
)

logger = logging.getLogger(__name__)

ECFR_BASE = "https://www.ecfr.gov/api/versioner/v1"


def _build_ecfr_targets() -> List[tuple]:
    """Union of every industry's ecfr_targets, deduplicated by (title, part)."""
    from app.services.industry_config import INDUSTRIES

    seen = set()
    targets: List[tuple] = []
    for cfg in INDUSTRIES.values():
        for title, part, label, category in cfg.get("ecfr_targets", []):
            key = (title, part)
            if key in seen:
                continue
            seen.add(key)
            targets.append((title, part, label, category))
    return targets


# Regulations to pull — (title, part, label, category)
ECFR_TARGETS = _build_ecfr_targets()


class ECFRClient:
    """
    Fetches current regulatory text from eCFR.
    Uses the versioner API which returns XML/JSON with section-level text.
    """

    def __init__(self):
        self._client = None
        # title -> up_to_date_as_of, cached per run so titles.json is fetched once.
        self._title_dates: Dict[int, str] = {}

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
        # Prefer the date eCFR itself reports for this title over date.today().
        if as_of is None:
            reported = await self.get_title_as_of(title)
            if reported:
                try:
                    as_of = datetime.strptime(reported, "%Y-%m-%d").date()
                except ValueError:
                    logger.warning(f"eCFR returned an unparseable date for title {title}: {reported!r}")

        base_date = as_of or date.today()

        # Even with the reported date, step back through recent snapshots if a
        # given day has no published version. Regulation text rarely changes day
        # to day, so an older snapshot is far better than no grounding at all.
        for days_back in (0, 1, 3, 7, 30):
            attempt_date = (base_date - timedelta(days=days_back)).isoformat()
            result = await self._fetch_part_for_date(title, part, attempt_date)
            if result and result.get("sections"):
                if days_back:
                    logger.info(
                        f"eCFR: title-{title} part {part} unavailable for {base_date.isoformat()}, "
                        f"used snapshot from {attempt_date}"
                    )
                return result

        logger.warning(
            f"eCFR: no usable snapshot for title-{title} part {part} within 30 days of "
            f"{base_date.isoformat()}"
        )
        return None

    async def get_title_as_of(self, title: int) -> Optional[str]:
        """Ask eCFR what date it considers this title current as of.

        eCFR publishes each title's own `up_to_date_as_of` in titles.json.
        Using it beats guessing with date.today(), which can 404 whenever eCFR
        lags the calendar -- a request for a date eCFR hasn't published yet
        fails even though the regulation plainly exists.
        """
        if title in self._title_dates:
            return self._title_dates[title]

        try:
            response = await self.client.get(f"{ECFR_BASE}/titles.json")
            if response.status_code != 200:
                logger.warning(f"eCFR titles.json returned {response.status_code}")
                return None
            for entry in response.json().get("titles", []):
                if entry.get("number") == title:
                    as_of = entry.get("up_to_date_as_of") or entry.get("latest_issue_date")
                    if as_of:
                        self._title_dates[title] = as_of
                        logger.info(f"eCFR title {title} is current as of {as_of}")
                    return as_of
            logger.warning(f"eCFR titles.json has no entry for title {title}")
            return None
        except Exception as e:
            logger.warning(f"eCFR titles.json lookup failed: {e}")
            return None

    async def _fetch_part_for_date(
        self, title: int, part: int, today: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch one CFR part for one specific published date.

        XML first: it is eCFR's documented full-content format, and the JSON
        structure this client originally assumed does not reliably carry
        section text for part-level requests.
        """
        logger.info(f"Fetching eCFR: {title} CFR Part {part} as of {today}")

        result = await self._fetch_part_xml(title, part, today)
        if result and result.get("sections"):
            return result

        # JSON fallback — kept so a change to the XML endpoint doesn't take
        # grounding down entirely.
        url = f"{ECFR_BASE}/full/{today}/title-{title}.json"
        try:
            response = await self.client.get(url, params={"part": str(part)})
            if response.status_code != 200:
                logger.warning(
                    f"eCFR JSON fallback returned {response.status_code} for title-{title} part {part}"
                )
                return None
            return self._parse_ecfr_json(response.json(), title, part, today)
        except httpx.TimeoutException:
            logger.warning(f"eCFR timeout for title-{title} part {part}")
            return None
        except Exception as e:
            logger.warning(f"eCFR JSON fallback failed for title-{title} part {part}: {e}")
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
        """Parse eCFR part-level XML into sections.

        This previously regexed for literal <SECTION>...</SECTION> blocks, which
        do not exist in eCFR part-level XML. The real structure nests sections as
        DIV8 elements carrying a TYPE attribute:

            <DIV5 TYPE="PART"><DIV6 TYPE="SUBPART"><DIV8 TYPE="SECTION" N="164.530">

        So the regex matched nothing and every fetch produced sections=[] -- the
        seeder recorded 0 chunks, the knowledge base stayed empty, and the app
        silently fell back to model-only output while still looking grounded.
        That was the root cause of the empty production knowledge base.

        Parsed with ElementTree rather than regex so nested markup inside
        paragraphs (<I>, <E>, cross-references) is flattened correctly instead
        of being stripped by a blunt tag-removal pass.
        """
        sections: List[Dict[str, str]] = []

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.warning(f"eCFR XML parse error for title-{title} part {part}: {e}")
            return {"title": title, "part": part, "fetched_date": fetched_date, "sections": []}

        for node in root.iter():
            # DIV8 is the usual section container, but match on TYPE rather than
            # tag name so a structural change (DIV7/DIV9) doesn't silently
            # reintroduce the zero-sections failure.
            if (node.attrib.get("TYPE") or "").upper() != "SECTION":
                continue

            sectno = (node.findtext("SECTNO") or node.attrib.get("N") or "").strip()
            subject = (node.findtext("SUBJECT") or node.findtext("HEAD") or "").strip()

            paragraphs = []
            for p in node.findall(".//P"):
                text = " ".join("".join(p.itertext()).split())
                if text:
                    paragraphs.append(text)
            full_text = " ".join(paragraphs).strip()

            if not full_text or len(full_text) <= 50:
                continue

            clean_section = sectno.replace("§", "").strip()
            sections.append({
                "section": clean_section,
                "heading": subject,
                "text": full_text[:3000],
                "citation": f"{title} CFR § {clean_section}" if clean_section else f"{title} CFR Part {part}",
            })

        if not sections:
            logger.warning(
                f"eCFR XML for title-{title} part {part} parsed but yielded no sections — "
                f"the document structure may have changed."
            )

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
