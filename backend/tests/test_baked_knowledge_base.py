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


def test_build_script_does_not_fail_the_build_by_default():
    """A transient eCFR outage must not block shipping unrelated fixes."""
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "scripts/build_knowledge_base.py"],
        capture_output=True, text=True, timeout=300,
        env={**os.environ, "KB_PERSIST_DIR": tempfile.mkdtemp()},
    )
    assert result.returncode == 0


def test_a_normal_build_ships_guidance_even_with_no_network():
    """An offline build must still produce a usable corpus.

    The bundled OIG/HCCA guidance loads from disk, so an eCFR outage costs the
    regulatory corpus but leaves the compliance-program guidance -- partial
    grounding rather than none. A default build should not be blocked by that.
    """
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "scripts/build_knowledge_base.py"],
        capture_output=True, text=True, timeout=600,
        env={**os.environ, "KB_PERSIST_DIR": tempfile.mkdtemp()},
    )
    assert result.returncode == 0, result.stderr
    assert "SUCCESS" in result.stdout
    # It must say plainly that the regulations are missing, not imply a
    # complete corpus.
    assert "0 regulatory" in result.stdout
    assert "guidance-only grounding" in result.stderr


def test_strict_mode_requires_the_regulations_not_merely_content():
    """The gate has to check the thing it exists to protect.

    Strict mode used to fail only when the total chunk count was zero. The
    bundled guidance always loads from disk, so the total is never zero -- and
    a build in which eCFR was unreachable and not one regulation downloaded
    would have passed. The switch protected against nothing.
    """
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "scripts/build_knowledge_base.py", "--require-success"],
        capture_output=True, text=True, timeout=600,
        env={**os.environ, "KB_PERSIST_DIR": tempfile.mkdtemp()},
    )
    # No eCFR egress in this environment, so a strict build must refuse.
    assert result.returncode == 1, result.stdout
    assert "FAILED" in result.stderr
    assert "no regulatory text was downloaded" in result.stderr


def test_strict_mode_reports_what_did_load():
    """A failure that hides the partial success is harder to act on."""
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "scripts/build_knowledge_base.py", "--require-success"],
        capture_output=True, text=True, timeout=600,
        env={**os.environ, "KB_PERSIST_DIR": tempfile.mkdtemp()},
    )
    assert "chunks of bundled guidance loaded" in result.stderr


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
