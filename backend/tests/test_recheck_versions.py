"""
Re-check must not be a dead end, and must not overwrite the original report.

Seen live: after Revision -> Re-check the screen said "6 remaining issues, 2
must-fix" with no way to see them, and Gap Analysis still showed the original
cards and counts, so the two versions were indistinguishable. The re-check
result is now kept per revision beside the original, with an
"Original analysis | Revision analysis" switch. The browser flow is exercised
end to end separately; these source checks guard the rules the suite can see.

Run: python -m pytest tests/test_recheck_versions.py -v
"""

import pathlib
import re

import pytest

INDEX = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "Index.tsx"


@pytest.fixture(scope="module")
def src():
    if not INDEX.exists():
        pytest.skip("frontend not present")
    return INDEX.read_text()


def _handler(src, name):
    start = src.index(f"const {name} = async")
    return src[start:src.index("\n  };\n", start)]


def test_recheck_never_replaces_the_original(src):
    body = _handler(src, "handleRecheck")
    assert "setPkg(" not in body
    assert "setVersions(" in body


def test_each_revision_is_stored_under_its_own_number(src):
    assert "results: Record<number, RevisionAnalysis>" in src
    assert re.search(r"results: \{ \.\.\.base\.results, \[version\]:", src)


def test_a_new_revision_starts_unchecked(src):
    body = _handler(src, "handleFixAllGaps")
    assert "version: prev.version + 1" in body


def test_the_summary_line_and_the_way_in(src):
    assert "must-fix → Revision:" in src
    assert "VIEW REMAINING FINDINGS" in src
    assert '"Original analysis" : "Revision analysis"' in src


def test_counts_use_the_same_rows_as_the_cards(src):
    # The summary counts with the helper the Must-fix filter uses.
    assert "mustFixCount(pkg.gap_analysis?.gap_table)" in src
    assert "mustFixCount(revisionAnalysis.pkg.gap_analysis?.gap_table)" in src


def test_remaining_findings_are_labelled_with_their_revision_section(src):
    assert "revisionSectionFor(row, revisionSections)" in src
    assert "Revision section:" in src
