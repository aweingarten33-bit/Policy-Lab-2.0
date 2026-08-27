"""An OpenContracts authority corpus, built by OpenContracts' own code.

Loading a section runs, in order:

  1. ``CFRAuthoritySourceProvider.locate()`` -- theirs. Derives the eCFR
     Versioner URL, the fetch params and the citation from a canonical key.
  2. ``CFRAuthoritySourceProvider.fetch()`` -- theirs. Parses the eCFR title
     XML, finds the SECTION element, flattens inline tags and returns an
     ``AuthoritySection``.
  3. ``AuthoritySourceRecord.as_document_metadata()`` -- theirs. Projects
     provenance into the metadata dict an OpenContracts Document carries,
     including the ``effective_date_review_status`` fail-closed marker it
     derives itself.

Policy Lab supplies none of that. It supplies the provenance facts a real
publisher feed would supply (status, effective dates, version relationships)
and then reads the result.

On the eCFR fetch
-----------------
Step 2's HTTP call is intercepted, because ``www.ecfr.gov`` is unreachable from
this sandbox -- the outbound proxy refuses the CONNECT with 403. The bytes
handed to their parser are eCFR Versioner XML in the exact shape their parser
expects, and every line of parsing, extraction and record construction runs
unmodified. What is NOT proven here is the live fetch itself; what IS proven is
everything from the XML onwards.

The section text in ``fixtures.py`` is a fixture standing in for the live
response, and is labelled as such there.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class AuthorityFixture:
    """One authority to load, as provenance facts plus the publisher's bytes."""

    canonical_key: str
    title: str
    xml: bytes
    status: str = "CURRENT"
    instrument_type: str = "REGULATION"
    authority_type: str = "regulation"
    effective_from: Optional[date] = None
    effective_until: Optional[date] = None
    current_version: Optional[bool] = True
    version_label: Optional[str] = None
    superseded_by_key: Optional[str] = None
    source_url_override: Optional[str] = None
    metadata: dict = field(default_factory=dict)


class InProcessAuthorityCorpus:
    """Holds loaded authorities and answers the provider's one question.

    Implements ``AuthorityDocumentClient``. Against a running OpenContracts the
    same method would be a GraphQL or REST call for the Document filed under a
    canonical key; the provider above it cannot tell the difference, which is
    what makes this a compatibility proof rather than a mock.
    """

    def __init__(self):
        self._docs: dict[str, dict] = {}

    # ── AuthorityDocumentClient ──

    def get_authority_document(self, canonical_key: str) -> Optional[dict]:
        return self._docs.get(canonical_key)

    # ── loading ──

    def load(self, fixture: AuthorityFixture) -> dict:
        """Run the fixture through OpenContracts and file the result."""
        from opencontractserver.enrichment.authority_sources import (
            AuthoritySourceRecord,
            RelationshipType,
            RightsStatus,
            SourceRelationship,
        )
        from opencontractserver.pipeline.authority_source_providers import cfr_provider

        provider = cfr_provider.CFRAuthoritySourceProvider()
        request = provider.locate(fixture.canonical_key)

        with _ecfr_response(cfr_provider, fixture.xml):
            sections = provider.fetch(request)

        if not sections:
            raise RuntimeError(
                f"OpenContracts' CFR provider found no section for {fixture.canonical_key}"
            )
        section = sections[0]

        relationships = ()
        if fixture.superseded_by_key:
            relationships = (
                SourceRelationship(
                    target_key=fixture.superseded_by_key,
                    relationship_type=RelationshipType.SUPERSEDED_BY,
                    verified=True,
                ),
            )

        record = AuthoritySourceRecord(
            canonical_key=fixture.canonical_key,
            title=fixture.title,
            source_url=fixture.source_url_override or (section.source_url or request.url),
            source_identifier=request.citation,
            publisher="Office of the Federal Register",
            jurisdiction="us-federal",
            authority_type=fixture.authority_type,
            instrument_type=fixture.instrument_type,
            issued_date=None,
            effective_from=fixture.effective_from,
            effective_until=fixture.effective_until,
            status=fixture.status,
            authority_weight="CONTROLLING",
            parent_key=None,
            version_label=fixture.version_label,
            content=section.text.encode("utf-8"),
            mime_type="text/plain",
            corpus_slug="us-federal-regulations",
            metadata={"citation": request.citation, **fixture.metadata},
            relationships=relationships,
            current_version=fixture.current_version,
            rights_status=RightsStatus.PUBLIC_DOMAIN,
            extracted_text=section.text,
        )

        doc = {
            "text": section.text,
            # Their projection, verbatim. The only additions are the two fields
            # a Document carries outside its metadata blob -- its title and the
            # citation the provider derived -- so that the Policy Lab adapter
            # reads exactly what a real Document would expose.
            "metadata": {
                **record.as_document_metadata(),
                "title": section.heading or fixture.title,
                "citation": request.citation,
            },
        }
        self._docs[record.canonical_key] = doc
        return doc

    def replace_text(self, canonical_key: str, new_xml: bytes) -> dict:
        """Reload one authority from amended publisher bytes.

        The realistic shape of a regulation being amended in place: same key,
        same standing, different words.
        """
        existing = self._docs.get(canonical_key)
        if existing is None:
            raise KeyError(canonical_key)
        meta = existing["metadata"]
        return self.load(
            AuthorityFixture(
                canonical_key=canonical_key,
                title=str(meta.get("title") or canonical_key),
                xml=new_xml,
                status=str(meta.get("status") or "CURRENT"),
                effective_from=_parse_iso(meta.get("effective_from")),
                current_version=meta.get("current_version"),
            )
        )


@contextmanager
def _ecfr_response(cfr_provider_module, payload: bytes):
    """Hand OpenContracts' parser the bytes eCFR would have returned."""
    original = cfr_provider_module.safe_fetch_bytes
    cfr_provider_module.safe_fetch_bytes = lambda *a, **kw: (payload, {})
    try:
        yield
    finally:
        cfr_provider_module.safe_fetch_bytes = original


def _parse_iso(value) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc).date()
    except ValueError:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None
