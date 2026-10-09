"""
Three small changes, one branch.

A. An expanded finding opens with what the user's policy says (and a section
   label when the analysis names one), and the finding text is not repeated.
B. A draft written with no regulation sources is labelled -- never blocked:
   `grounded` on the response, a banner in the UI, and the first line of the DOCX.
C. Marketing copy says only what the tool does.

Run: python -m pytest tests/test_anchor_grounded_copy.py -v
"""

import io
import pathlib

import docx
import pytest

from app.models.schemas import DraftedPolicy
from app.services.export_service import generate_draft_policy_docx

ROOT = pathlib.Path(__file__).resolve().parents[2]
NOTICE = ("Drafted without federal regulation sources - treat every requirement as unverified "
          "and confirm what applies to your organization.")


def _src(rel):
    path = ROOT / "frontend" / "src" / rel
    if not path.exists():
        pytest.skip("frontend not present")
    return path.read_text()


# ── B ──

class _Ctx:
    def __init__(self, sources):
        self._sources = sources
        self.formatted_context = ""
        self.live_research_used = False
        self.live_research_results = []

    def get_all_sources(self):
        return self._sources

    def get_source_names(self):
        return []

    def get_source_url_map(self):
        return {}

    def get_source_snippets(self):
        return []


@pytest.mark.parametrize("sources, expected", [([object()], True), ([], False)])
def test_finalize_marks_whether_any_source_was_used(monkeypatch, sources, expected):
    import app.services.draft_policy_service as svc
    monkeypatch.setattr(svc, "attach_attribution", lambda data, ctx: data)
    monkeypatch.setattr(svc, "state_coverage", lambda ctx, j: None)
    data = svc.finalize_draft({"full_text": "Policy text."}, _Ctx(sources), "a policy", "healthcare", None)
    assert data["grounded"] is expected


def test_no_context_at_all_is_ungrounded(monkeypatch):
    import app.services.draft_policy_service as svc
    monkeypatch.setattr(svc, "state_coverage", lambda ctx, j: None)
    assert svc.finalize_draft({"full_text": "x"}, None, "a policy")["grounded"] is False


def test_the_response_model_carries_it():
    assert DraftedPolicy(policy_title="t", full_text="x", grounded=False).grounded is False
    assert DraftedPolicy(policy_title="t", full_text="x").grounded is True


def _first_lines(policy):
    d = docx.Document(io.BytesIO(generate_draft_policy_docx(policy)))
    return [p.text for p in d.paragraphs if p.text.strip()]


def test_ungrounded_docx_opens_with_the_notice():
    lines = _first_lines({"policy_title": "Breach Policy", "full_text": "x", "sections": [], "grounded": False})
    assert lines[0] == NOTICE


def test_grounded_docx_has_no_notice():
    lines = _first_lines({"policy_title": "Breach Policy", "full_text": "x", "sections": [], "grounded": True})
    assert NOTICE not in "\n".join(lines)


def test_generation_is_never_blocked():
    from app.config import settings
    assert settings.require_grounding is False


def test_the_ui_banner_shows_only_when_ungrounded():
    notices = _src("components/ResultNotices.tsx")
    assert NOTICE in notices
    assert "if (grounded !== false) return null;" in notices
    assert "<UngroundedDraftBanner grounded={draftResult.grounded} />" in _src("pages/Index.tsx")


# ── A ──

def test_the_policy_excerpt_comes_first_and_the_finding_is_not_repeated():
    index = _src("pages/Index.tsx")
    expanded = index[index.index("{open && ("):]
    expanded = expanded[:expanded.index("{row.evidence") if "{row.evidence" in expanded else 6000]
    assert "Your policy says" in expanded
    assert expanded.index("Your policy says") < expanded.index("Applicable Regulations")
    assert "linkifyRegulations(stripCiteTags(row.finding)" not in index


@pytest.mark.parametrize("text, label", [
    ("Section 4.2 says staff notify the office manager.", "Policy section: 4.2"),
    ("See Section IV of the policy.", "Policy section: IV"),
    ("On page 3 the policy says nothing.", "Policy page: 3"),
])
def test_section_label_patterns(text, label):
    import re
    findings = _src("lib/findings.ts")
    section = re.search(r"const SECTION_RE = (/.*/);", findings).group(1)
    assert "Section" in section and "(?![Pp]olicy\\b)" in section
    # Python mirror of the TS patterns, to check the examples.
    sec = re.search(r"\bSection\s+((?:\d{1,2}(?:\.\d{1,3})*[A-Za-z]?)|(?:[IVX]{1,6}))\b(?!\s+of\s+(?:the\s+)?(?![Pp]olicy\b)[A-Z])", text)
    page = re.search(r"\bpage\s+(\d{1,3})\b", text, re.I)
    got = f"Policy section: {sec.group(1)}" if sec else (f"Policy page: {page.group(1)}" if page else None)
    assert got == label


def test_statutes_are_not_policy_sections():
    import re
    assert not re.search(r"\bSection\s+((?:\d{1,2}(?:\.\d{1,3})*[A-Za-z]?)|(?:[IVX]{1,6}))\b(?!\s+of\s+(?:the\s+)?(?![Pp]olicy\b)[A-Z])",
                         "Section 1557 of the ACA prohibits discrimination.")


# ── C ──

GUIDE_NEW = [
    "It shows its sources on every result - and when it couldn't find one,",
    "regulation cite it, and you can click through to the actual source text.",
    "best-practice recommendations are labeled as recommendations, not legal requirements.",
    "draw on a stored database of federal",
    "regulation text, and every result shows what sources were used.",
    "Pharmacy maps to DEA and Medicare Part D rules.",
    "It's built to stay on your policy and compliance topics.",
]
GUIDE_GONE = ["answering from memory", "cited to a specific regulation", "are grounded in a stored database",
              "DEA, FDA", "won't answer questions unrelated"]


def test_guide_copy():
    guide = _src("pages/Guide.tsx")
    for text in GUIDE_NEW:
        assert text in guide, text
    for text in GUIDE_GONE:
        assert text not in guide, text


def test_draft_description_copy():
    index = _src("pages/Index.tsx")
    assert ("We draft it from federal regulation text and real policy templates. Who does what, "
            "deadlines, and start dates are left blank for you to fill in.") in index
    assert "We draft it from the federal regulation text." not in index
