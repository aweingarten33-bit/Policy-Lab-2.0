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


def test_build_script_can_be_made_strict():
    """--require-success turns an empty corpus into a hard build failure."""
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "scripts/build_knowledge_base.py", "--require-success"],
        capture_output=True, text=True, timeout=300,
        env={**os.environ, "KB_PERSIST_DIR": tempfile.mkdtemp()},
    )
    # This sandbox has no eCFR egress, so strict mode must fail here.
    assert result.returncode == 1
    assert "FAILED" in result.stderr
