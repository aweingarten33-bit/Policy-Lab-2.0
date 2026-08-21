"""
Tests for the image-baked knowledge base.

Context: seeding at container start meant redoing minutes of downloading and
embedding on every deploy (ephemeral filesystem), and blocking the app past the
platform health-check window so it never finished. Production retrieval systems
build the corpus once, ahead of time, and ship it. scripts/build_knowledge_base.py
is that build step; restore_baked_knowledge_base() makes the result usable even
when KB_PERSIST_DIR points at a mounted disk instead.

Run: python -m pytest tests/test_baked_knowledge_base.py -v
"""

import os
import re
import pathlib
import tempfile
from unittest.mock import patch

import pytest

import app.services.retrieval.seed_data as sd
from app.config import settings


@pytest.fixture
def baked_dir():
    d = tempfile.mkdtemp()
    pathlib.Path(d, "chroma.sqlite3").write_text("baked corpus")
    os.makedirs(os.path.join(d, "collection"), exist_ok=True)
    pathlib.Path(d, "collection", "data.bin").write_text("vectors")
    return d


@pytest.fixture
def target_dir():
    return os.path.join(tempfile.mkdtemp(), "kb")


@pytest.fixture(autouse=True)
def _restore_setting():
    original = settings.kb_persist_dir
    yield
    settings.kb_persist_dir = original


def test_baked_corpus_is_restored_into_an_empty_target(baked_dir, target_dir):
    """Attaching a persistent disk must not discard the prebuilt corpus."""
    settings.kb_persist_dir = target_dir
    with patch.object(sd, "BAKED_KB_DIR", baked_dir):
        copied = sd.restore_baked_knowledge_base()

    assert copied == 2
    assert pathlib.Path(target_dir, "chroma.sqlite3").read_text() == "baked corpus"
    assert pathlib.Path(target_dir, "collection", "data.bin").exists()


def test_existing_data_is_never_overwritten(baked_dir, target_dir):
    """A populated store is live data — the image copy must not clobber it."""
    os.makedirs(target_dir, exist_ok=True)
    pathlib.Path(target_dir, "chroma.sqlite3").write_text("LIVE DATA")

    settings.kb_persist_dir = target_dir
    with patch.object(sd, "BAKED_KB_DIR", baked_dir):
        copied = sd.restore_baked_knowledge_base()

    assert copied == 0
    assert pathlib.Path(target_dir, "chroma.sqlite3").read_text() == "LIVE DATA"


def test_no_baked_corpus_is_a_safe_noop(target_dir):
    settings.kb_persist_dir = target_dir
    with patch.object(sd, "BAKED_KB_DIR", tempfile.mkdtemp()):
        assert sd.restore_baked_knowledge_base() == 0


def test_same_path_is_a_noop(baked_dir):
    """When already pointed at the baked location there is nothing to copy."""
    settings.kb_persist_dir = baked_dir
    with patch.object(sd, "BAKED_KB_DIR", baked_dir):
        assert sd.restore_baked_knowledge_base() == 0


def test_unwritable_target_degrades_without_raising(baked_dir):
    """A restore failure must not take down startup."""
    settings.kb_persist_dir = "/proc/cannot/write/here"
    with patch.object(sd, "BAKED_KB_DIR", baked_dir):
        assert sd.restore_baked_knowledge_base() == 0


# The build downloads every configured CFR part and embeds the result. On a
# machine with network access that is minutes of real work, and this file used
# to do it three separate times -- which is most of why the CI job ran for
# eighteen minutes, and why one test tripped a five-minute subprocess timeout
# that had never been hit locally, where there is no eCFR egress and the build
# finishes in seconds.
#
# Each mode is built once per session and shared. The assertions below are
# about the build's *outcome*, so they do not each need their own build.
def _run_build(*flags):
    import subprocess, sys
    return subprocess.run(
        [sys.executable, "scripts/build_knowledge_base.py", *flags],
        capture_output=True, text=True, timeout=1800,
        env={**os.environ, "KB_PERSIST_DIR": tempfile.mkdtemp()},
    )


@pytest.fixture(scope="session")
def default_build():
    return _run_build()


@pytest.fixture(scope="session")
def strict_build():
    return _run_build("--require-success")


def _regulatory_chunks(stdout):
    """How many chunks came from eCFR, per the build's own report."""
    match = re.search(r"\((\d+) of them regulatory text from eCFR\)", stdout)
    return int(match.group(1)) if match else 0


def test_a_normal_build_never_fails_and_reports_what_it_got(default_build):
    """A default build ships whatever it could load and says which.

    A transient eCFR outage must not block shipping unrelated fixes, and the
    output must state the regulatory count rather than implying a complete
    corpus.

    Written to hold with or without network access. It previously asserted the
    offline outcome specifically, which passed on a machine with no eCFR egress
    and failed in CI, where eCFR is reachable and the regulations really do
    download -- the test encoding its author's environment rather than the
    behaviour.
    """
    assert default_build.returncode == 0, default_build.stderr
    assert "SUCCESS" in default_build.stdout
    assert "regulatory" in default_build.stdout
    if _regulatory_chunks(default_build.stdout) == 0:
        assert "guidance-only grounding" in default_build.stderr


def test_strict_mode_requires_the_regulations_not_merely_content(strict_build):
    """The gate has to check the thing it exists to protect.

    Strict mode used to fail only when the total chunk count was zero. The
    bundled guidance always loads from disk, so the total is never zero -- and
    a build in which eCFR was unreachable and not one regulation downloaded
    would have passed. The switch protected against nothing.
    """
    regulatory = _regulatory_chunks(strict_build.stdout)

    if regulatory > 0:
        # eCFR was reachable: a strict build is exactly what should succeed.
        assert strict_build.returncode == 0, strict_build.stderr
    else:
        assert strict_build.returncode == 1, strict_build.stdout
        assert "FAILED" in strict_build.stderr
        assert "no regulatory text was downloaded" in strict_build.stderr
        # The failure must still report the partial success, or it is harder
        # to act on.
        assert "chunks of bundled guidance loaded" in strict_build.stderr


class TestReleaseBuildIsStrict:
    """The build must refuse to ship an image whose citations cannot be
    checked against current CFR text.

    This flag is a Docker build argument, set in the Dockerfile rather than in
    the hosting dashboard. A platform's environment-variable panel does not
    reach a build arg, so configuring it there would look enabled and do
    nothing -- the exact silent no-op the flag exists to prevent.
    """

    def _dockerfile(self):
        import pathlib
        return (pathlib.Path(__file__).resolve().parents[2] / "Dockerfile").read_text()

    def test_the_gate_is_on(self):
        assert "ARG KB_SEED_REQUIRED=true" in self._dockerfile()

    def test_the_flag_actually_reaches_the_build_step(self):
        """Declaring the arg is not enough; the RUN line has to consume it."""
        text = self._dockerfile()
        assert "--require-success" in text
        assert '[ "$KB_SEED_REQUIRED" = "true" ]' in text

    def test_the_tradeoff_is_written_down(self):
        """Whoever hits a blocked deploy during an eCFR outage needs to find
        the reason and the way out at the point of the setting."""
        # Collapsed first: the comment wraps across lines, so the phrases are
        # not contiguous in the raw file.
        text = " ".join(self._dockerfile().replace("#", " ").split())
        assert "an eCFR outage blocks deploys entirely" in text
        assert "Set it back to false to ship during one" in text
