"""
Every citation link must resolve to an authoritative government source.

Reported symptom: a conflict-of-interest policy came back with law.cornell.edu
links, and they did not work.

Two separate problems. Cornell's LII is a respected secondary source but it is
a private university site, not the government -- and this tool tells users its
citations resolve to .gov sources. Worse, the generic U.S. Code rule built a
Cornell *search* URL from the matched text rather than a link to the section,
so it frequently landed on nothing.

U.S. Code citations now point at uscode.house.gov, the Office of the Law
Revision Counsel: the official publisher, linked straight to the section.

This test reads the frontend link table directly, because that is the only
place these URLs are defined.

Run: python -m pytest tests/test_citation_links_are_official.py -v
"""

import re
from pathlib import Path

import pytest

LINKS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "regulation-links.tsx"

ALLOWED_HOSTS = {
    "www.ecfr.gov", "ecfr.gov",
    "www.hhs.gov", "hhs.gov",
    "www.cms.gov", "cms.gov",
    "oig.hhs.gov",
    "www.federalregister.gov", "federalregister.gov",
    "uscode.house.gov",
    "www.govinfo.gov", "govinfo.gov",
    "www.osha.gov", "osha.gov",
    "www.dol.gov", "dol.gov",
    "www.eeoc.gov", "eeoc.gov",
    "www.ftc.gov", "ftc.gov",
    "csrc.nist.gov", "www.nist.gov", "nist.gov",
    "www.deadiversion.usdoj.gov", "www.justice.gov",
}


def _hosts():
    source = LINKS.read_text()
    # Only real URLs, not prose in comments.
    return re.findall(r"https://([a-z0-9.\-]+)/", source)


@pytest.mark.skipif(not LINKS.exists(), reason="frontend not present")
class TestOnlyOfficialSources:
    def test_no_link_points_outside_the_allowlist(self):
        offenders = sorted({h for h in _hosts() if h not in ALLOWED_HOSTS})
        assert not offenders, (
            f"citation links point at non-official hosts: {offenders}. "
            f"The product tells users citations resolve to authoritative "
            f"government sources."
        )

    def test_cornell_specifically_is_gone(self):
        assert "law.cornell.edu" not in set(_hosts())

    def test_every_host_is_a_government_domain(self):
        for host in _hosts():
            assert host.endswith(".gov"), f"{host} is not a .gov domain"


@pytest.mark.skipif(not LINKS.exists(), reason="frontend not present")
class TestUsCodeLinksAreSectionLinks:
    def test_the_helper_builds_a_granule_deep_link(self):
        """A search URL built from matched text was why the links went
        nowhere. The official format addresses the section directly."""
        source = LINKS.read_text()
        # Built with a template literal, so "granuleid:" and the granule id
        # are not contiguous in the source text.
        assert "USC-prelim-title${title}-section${section}" in source
        assert "uscode.house.gov/view.xhtml?req=granuleid:" in source

    def test_the_generic_rule_captures_title_and_section(self):
        """It has to capture both numbers to build a section link; the old
        rule captured the whole match and searched for it."""
        source = LINKS.read_text()
        assert r"/\b(\d+)\s*U\.?S\.?C\.?\s*§?\s*(\d+[A-Za-z0-9\-]*)/gi" in source
        assert "uscodeUrl(m[1], m[2])" in source

    def test_no_rule_builds_a_search_query_from_matched_text(self):
        """Search URLs are how a citation link becomes a dead end."""
        source = LINKS.read_text()
        assert "uscode/search?q=" not in source
