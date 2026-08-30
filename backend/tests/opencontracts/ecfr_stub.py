"""eCFR responses, so the production authority client can be driven in tests.

The only thing stubbed is the network. Everything downstream of the bytes runs
for real: OpenContracts' provider, their XML parser, their AuthoritySourceRecord
and its validation, the local cache, the Policy Lab status mapping, and every
verification gate.

Two seams, because the client makes two calls and they belong to different
owners. The full-text call is OpenContracts' own, so it is intercepted inside
their provider module. The versions call is the one they do not make, so it is
intercepted where Policy Lab makes it.

FIXTURE TEXT. www.ecfr.gov is unreachable from the build sandbox (the outbound
proxy refuses CONNECT with 403), so these payloads stand in for live responses.
They are written in the eCFR Versioner shapes the real client consumes and
their wording follows 45 CFR Part 164 closely, but they are not a verified copy
of the regulation and must not be read as one. What the tests assert is which
verdict the pipeline reaches, never what the regulation says.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Optional


def section_xml(number: str, heading: str, paragraphs: list[str]) -> bytes:
    """One CFR section in eCFR Versioner full-text XML shape."""
    body = "".join(f"<P>{p}</P>" for p in paragraphs)
    part = number.split(".")[0]
    return (
        f'<DIV5 TYPE="PART" N="{part}">'
        f'<DIV8 TYPE="SECTION" N="{number}">'
        f"<HEAD>§ {number} {heading}</HEAD>{body}"
        f"</DIV8></DIV5>"
    ).encode("utf-8")


def versions_json(
    section: str, amendment_date: Optional[str] = "2013-01-25", removed: bool = False
) -> bytes:
    """eCFR's versions response for one section."""
    entry = {
        "identifier": section,
        "name": f"§ {section}",
        "part": section.split(".")[0],
        "title": "45",
        "type": "section",
        "removed": removed,
    }
    if amendment_date:
        entry["amendment_date"] = amendment_date
        entry["issue_date"] = amendment_date
    return json.dumps({"content_versions": [entry]}).encode("utf-8")


# ── the fixtures ──

SECTION_164_404 = section_xml(
    "164.404",
    "Notification to individuals.",
    [
        "(a) Standard—(1) General rule. A covered entity shall, following the discovery "
        "of a breach of unsecured protected health information, notify each individual "
        "whose unsecured protected health information has been, or is reasonably believed "
        "by the covered entity to have been, accessed, acquired, used, or disclosed as a "
        "result of such breach.",
        "(b) Implementation specification: Timeliness of notification. Except as provided "
        "in § 164.412, a covered entity shall provide the notification required by "
        "paragraph (a) of this section without unreasonable delay and in no case later "
        "than 60 calendar days after discovery of a breach.",
        "(c) Implementation specifications: Content of notification. The notification "
        "shall include, to the extent possible, a brief description of what happened, a "
        "description of the types of unsecured protected health information involved, and "
        "contact procedures for individuals to ask questions.",
    ],
)

# The same section after an amendment: paragraph (a) is reworded and the
# deadline moves from 60 days to 30. Drives the Obligation Memory case.
SECTION_164_404_AMENDED = section_xml(
    "164.404",
    "Notification to individuals.",
    [
        "(a) Standard—(1) General rule. A covered entity shall, following the discovery "
        "of a breach of unsecured protected health information, notify each affected "
        "individual and the individual's personal representative.",
        "(b) Implementation specification: Timeliness of notification. A covered entity "
        "shall provide the notification required by paragraph (a) of this section without "
        "unreasonable delay and in no case later than 30 calendar days after discovery of "
        "a breach.",
    ],
)

SECTION_164_316 = section_xml(
    "164.316",
    "Policies and procedures and documentation requirements.",
    [
        "(b) Documentation. (1) Standard: Documentation. Maintain the policies and "
        "procedures implemented to comply with this subpart in written form.",
        "(2) Implementation specifications: (i) Time limit. Retain the documentation "
        "required by paragraph (b)(1) of this section for 6 years from the date of its "
        "creation or the date when it last was in effect, whichever is later.",
    ],
)


class FakeECFR:
    """Serves the two eCFR endpoints the production client calls."""

    def __init__(self):
        self.full_text: dict[str, bytes] = {}
        self.versions: dict[str, bytes] = {}
        self.full_text_calls = 0
        self.versions_calls = 0
        self.offline = False

    def serve(
        self,
        section: str,
        xml: bytes,
        *,
        amendment_date: Optional[str] = "2013-01-25",
        removed: bool = False,
    ) -> "FakeECFR":
        self.full_text[section] = xml
        self.versions[section] = versions_json(section, amendment_date, removed)
        return self

    # ── the two seams ──

    def _full_text_response(self, url, params=None, headers=None, **kw):
        self.full_text_calls += 1
        if self.offline:
            raise ConnectionError("eCFR unreachable")
        section = str((params or {}).get("section") or "")
        payload = self.full_text.get(section)
        if payload is None:
            # eCFR answers with the title XML regardless; a section that does
            # not exist simply is not in it. Their parser then finds nothing,
            # which is the real behaviour for a fabricated citation.
            return b'<DIV5 TYPE="PART" N="164"></DIV5>', "www.ecfr.gov"
        return payload, "www.ecfr.gov"

    def _versions_response(self, url, params=None, headers=None, **kw):
        self.versions_calls += 1
        if self.offline:
            raise ConnectionError("eCFR unreachable")
        section = str((params or {}).get("section") or "")
        payload = self.versions.get(section)
        if payload is None:
            return json.dumps({"content_versions": []}).encode("utf-8"), "www.ecfr.gov"
        return payload, "www.ecfr.gov"


@contextmanager
def serving(fake: FakeECFR):
    """Install the stub on both seams for the duration of the block."""
    from app.services.retrieval import opencontracts_client, opencontracts_runtime as ocr

    # Boot before reaching for their module: the runtime is what puts the
    # checkout on sys.path, and it is lazy by design.
    assert ocr.available(), f"OpenContracts runtime unavailable: {ocr.unavailable_reason()}"

    cfr_module = ocr.cfr_provider_module()
    original_provider_fetch = cfr_module.safe_fetch_bytes
    original_client_fetch = opencontracts_client.ocr.safe_fetch_bytes

    cfr_module.safe_fetch_bytes = fake._full_text_response
    opencontracts_client.ocr.safe_fetch_bytes = fake._versions_response
    try:
        yield fake
    finally:
        cfr_module.safe_fetch_bytes = original_provider_fetch
        opencontracts_client.ocr.safe_fetch_bytes = original_client_fetch
