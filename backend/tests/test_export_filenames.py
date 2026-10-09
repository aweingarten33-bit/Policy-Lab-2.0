"""
Downloads must work whatever characters a document's name contains.

Seen live: "Proposed revision (.docx)" failed every time with "Download failed".
The server raised UnicodeEncodeError building the Content-Disposition header:
HTTP headers are Latin-1, and the AI-written revision title contained an em
dash. Every export built its filename the same way, so any title or uploaded
file name with "—", curly quotes or an accented letter broke that download.

Run: python -m pytest tests/test_export_filenames.py -v
"""

from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app.main import app

NAMES = [
    "HIPAA Privacy Policy — Proposed Revision",
    "Clinic’s “Breach” Policy",
    "Política de Privacidad",
    'Policy "A"',
]


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def _check(response, expected_fragment):
    assert response.status_code == 200, response.text[:300]
    header = response.headers["content-disposition"]
    header.encode("latin-1")  # the header itself must be encodable
    assert 'filename="' in header and "filename*=UTF-8''" in header
    real = unquote(header.split("filename*=UTF-8''", 1)[1])
    assert expected_fragment in real
    assert response.content[:2] == b"PK"  # a real .docx (zip)


@pytest.mark.parametrize("title", NAMES)
def test_proposed_revision(client, title):
    body = {"rewritten_policy": {"policy_title": title, "effective_date": "", "version_note": "v",
                                 "sections": [], "full_text": "Body.", "change_summary": "c",
                                 "live_research_used": False}}
    _check(client.post("/api/export-updated-policy", json=body), title.replace(" ", "_")[:20])


@pytest.mark.parametrize("title", NAMES)
def test_drafted_policy(client, title):
    body = {"policy": {"policy_title": title, "full_text": "Body.", "sections": []}}
    r = client.post("/api/export-draft", json=body)
    assert r.status_code == 200, r.text[:300]
    r.headers["content-disposition"].encode("latin-1")


@pytest.mark.parametrize("name", NAMES)
def test_gap_report(client, name):
    body = {"result": {"policy_type": "P", "audit_ready_summary": "S."}, "file_name": f"{name}.docx",
            "export_format": "docx"}
    r = client.post("/api/export", json=body)
    assert r.status_code == 200, r.text[:300]
    r.headers["content-disposition"].encode("latin-1")


def test_the_old_header_is_what_failed():
    """The exact failure: a raw em dash in a header cannot be encoded."""
    with pytest.raises(UnicodeEncodeError):
        'attachment; filename="HIPAA_Policy_—_Proposed.docx"'.encode("latin-1")
