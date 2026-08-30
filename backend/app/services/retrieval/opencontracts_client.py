"""The production authority client: OpenContracts fetches, Policy Lab reads.

One method, ``get_authority_document(canonical_key)``, answering the only
question the verification seam asks of a substrate. Cache first; on a miss, the
authority is fetched by OpenContracts' own CFR provider and stored.

Division of labour inside a fetch
---------------------------------
OpenContracts does all of it except one call:

  ``CFRAuthoritySourceProvider.locate()``  derives the eCFR URL, params and
                                           citation from the canonical key
  ``CFRAuthoritySourceProvider.fetch()``   fetches, parses the title XML,
                                           extracts the section, flattens
                                           inline tags, returns AuthoritySection
  ``AuthoritySourceRecord``                normalises and validates the record,
                                           hashes the content, derives the
                                           effective-date review state
  ``as_document_metadata()``               the metadata projection stored

The one call OpenContracts does not make is the eCFR *versions* endpoint, which
is where a CFR section's amendment date lives. Without it every fetched section
would arrive with no effective date, OpenContracts would mark it
UNKNOWN_NEEDS_REVIEW (correctly), and Policy Lab would fail every federal claim
closed. So this module asks eCFR for the section's amendment date and hands it
to their record as ``effective_from``. It uses their SSRF-hardened
``safe_fetch_bytes`` to do it, so the one call outside their provider still
goes through their HTTP hardening.

That date is a publisher fact, obtained and passed through unjudged. What it
*means* -- whether a present legal duty may rest on this section -- is decided
downstream by ``derive_source_status``, and that decision stays Policy Lab's.

Fail-closed
-----------
Every failure path returns None: runtime unavailable, network error, section
not found in the returned XML, removed section, malformed response. None means
the citation did not resolve, which verification already treats as unverifiable.
Nothing in this module can produce a document it did not receive from eCFR.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from typing import Optional

from app.config import settings
from app.services.retrieval import opencontracts_runtime as ocr
from app.services.retrieval.opencontracts_store import (
    OpenContractsStore,
    get_opencontracts_store,
)

logger = logging.getLogger(__name__)

_ECFR_VERSIONS_URL = "https://www.ecfr.gov/api/versioner/v1/versions/title-{title}.json"

# "cfr-45:164.404" -> title 45, section 164.404. Subsection-suffixed keys never
# reach here: candidate_keys rolls them up to the section root first, which is
# the granularity eCFR serves.
_CFR_KEY_RE = re.compile(r"^cfr-(?P<title>\d+):(?P<section>\d+\.[0-9A-Za-z-]+)$", re.IGNORECASE)


class CFRAuthorityClient:
    """Serves federal CFR authorities, fetched and modelled by OpenContracts."""

    def __init__(self, store: Optional[OpenContractsStore] = None):
        self._store = store or get_opencontracts_store()

    # ── AuthorityDocumentClient ──

    def get_authority_document(self, canonical_key: str) -> Optional[dict]:
        if not canonical_key:
            return None

        cached = self._store.get(canonical_key)
        if cached is not None:
            return cached

        if not _CFR_KEY_RE.match(canonical_key):
            # Not a federal CFR key. This client only speaks CFR; another
            # provider owning another corpus is a later concern, and answering
            # anyway would be answering about a body of law we did not fetch.
            return None

        if not settings.authority_fetch_enabled:
            logger.info("Authority fetch disabled; %s not in cache", canonical_key)
            return None

        if not ocr.available():
            logger.error(
                "Cannot fetch %s: OpenContracts runtime unavailable (%s)",
                canonical_key,
                ocr.unavailable_reason(),
            )
            return None

        document = self._fetch(canonical_key)
        if document is not None:
            self._store.put(canonical_key, document["text"], document["metadata"])
        return document

    # ── fetch ──

    def _fetch(self, canonical_key: str) -> Optional[dict]:
        match = _CFR_KEY_RE.match(canonical_key)
        title, section = match.group("title"), match.group("section")
        snapshot = self._snapshot_date()

        try:
            provider = ocr.cfr_provider()
            request = provider.locate(canonical_key, snapshot_date=snapshot)
            sections = provider.fetch(request)
        except Exception as e:  # noqa: BLE001
            # Network failure, refused host, unparseable XML -- all the same
            # answer. An authority we could not read cannot support a claim.
            logger.warning("OpenContracts CFR fetch failed for %s: %s", canonical_key, e)
            return None

        if not sections:
            logger.info("eCFR returned no section %s for %s", section, canonical_key)
            return None
        oc_section = sections[0]
        if not (oc_section.text or "").strip():
            logger.info("eCFR returned empty text for %s", canonical_key)
            return None

        effective_from, removed = self._section_version(title, section, snapshot)
        if removed:
            # eCFR says this section no longer exists. Not current law, and not
            # something to quietly serve as if it were.
            logger.info("eCFR reports %s as removed", canonical_key)
            return None

        record = self._record(canonical_key, request, oc_section, effective_from, snapshot)
        if record is None:
            return None

        return {
            "text": oc_section.text,
            "metadata": {
                **record.as_document_metadata(),
                # The two fields a Document carries outside its metadata blob.
                "title": oc_section.heading or request.citation,
                "citation": request.citation,
            },
        }

    def _record(self, canonical_key, request, oc_section, effective_from, snapshot):
        """Build OpenContracts' canonical record. Their validation, not ours."""
        modules = ocr.authority_sources()
        try:
            return modules.AuthoritySourceRecord(
                canonical_key=canonical_key,
                title=oc_section.heading or request.citation,
                source_url=oc_section.source_url or request.url,
                source_identifier=request.citation,
                publisher="Office of the Federal Register",
                jurisdiction="us-federal",
                authority_type="regulation",
                instrument_type=modules.InstrumentType.REGULATION,
                issued_date=None,
                # The publisher's own amendment date, or nothing. Never the
                # snapshot date and never today: a date we fetched on is not a
                # date a rule took effect, and conflating them is exactly how a
                # newly indexed page starts looking like newly effective law.
                effective_from=effective_from,
                effective_until=None,
                status=modules.SourceStatus.CURRENT,
                authority_weight=modules.AuthorityWeight.CONTROLLING,
                parent_key=None,
                version_label=None,
                content=oc_section.text.encode("utf-8"),
                mime_type="text/plain",
                corpus_slug="us-federal-regulations",
                metadata={
                    "citation": request.citation,
                    # Which day's eCFR snapshot this text was taken from --
                    # provenance for the fetch, kept distinct from any date
                    # about the rule itself.
                    "ecfr_snapshot_date": snapshot,
                },
                current_version=True,
                rights_status=modules.RightsStatus.PUBLIC_DOMAIN,
                extracted_text=oc_section.text,
            )
        except ValueError as e:
            # Their record refused the input. Trust that over our own eagerness.
            logger.warning("OpenContracts rejected the authority record for %s: %s", canonical_key, e)
            return None

    def _section_version(
        self, title: str, section: str, snapshot: str
    ) -> tuple[Optional[dt.date], bool]:
        """The section's amendment date and whether eCFR reports it removed.

        Returns ``(None, False)`` whenever the answer is not clearly available.
        An absent effective date is not a problem to route around: it makes
        OpenContracts mark the record UNKNOWN_NEEDS_REVIEW, which makes Policy
        Lab treat the section as of unestablished standing. That is the correct
        outcome and it happens without any special-casing here.
        """
        url = _ECFR_VERSIONS_URL.format(title=title)
        part = section.split(".")[0]
        try:
            body, _ = ocr.safe_fetch_bytes(
                url,
                params={"part": part, "section": section, "on": snapshot},
                headers={"User-Agent": ocr.authority_user_agent()},
            )
            payload = json.loads(body)
        except Exception as e:  # noqa: BLE001
            logger.info("eCFR version lookup failed for %s CFR %s: %s", title, section, e)
            return None, False

        best: Optional[dt.date] = None
        removed = False
        for version in payload.get("content_versions") or []:
            if str(version.get("identifier") or "").strip() != section:
                continue
            if version.get("removed"):
                removed = True
            parsed = _as_date(version.get("amendment_date") or version.get("issue_date"))
            if parsed is not None and (best is None or parsed > best):
                best = parsed

        return best, removed

    @staticmethod
    def _snapshot_date() -> str:
        """Which day's eCFR text to fetch.

        Today by default, because the product's question is what the law
        requires now. Pinnable for a reproducible corpus.
        """
        pinned = (settings.ecfr_snapshot_date or "").strip()
        if pinned:
            return pinned
        return dt.date.today().isoformat()


def _as_date(value) -> Optional[dt.date]:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
