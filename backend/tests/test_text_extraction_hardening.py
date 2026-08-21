"""Resource-limit tests for uploaded document extraction."""

import io
import zipfile

import pytest

import app.services.text_extraction as extraction


@pytest.mark.asyncio
async def test_plain_text_cannot_bypass_llm_input_limit():
    payload = b"x" * (extraction.MAX_EXTRACTED_CHARS + 1)

    with pytest.raises(ValueError, match="too large"):
        await extraction.extract_text_from_file(payload, "policy.txt", ".txt")


@pytest.mark.asyncio
async def test_legacy_doc_is_rejected_instead_of_parsed_as_docx():
    with pytest.raises(ValueError, match="Unsupported file type"):
        await extraction.extract_text_from_file(b"legacy-doc", "policy.doc", ".doc")


def test_docx_expansion_is_checked_before_parser_runs(monkeypatch):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "x" * 128)

    monkeypatch.setattr(extraction, "MAX_DOCX_UNCOMPRESSED_BYTES", 64)

    with pytest.raises(ValueError, match="expands to"):
        extraction._validate_docx_archive(buffer.getvalue())


def test_docx_archive_entry_count_is_bounded(monkeypatch):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("a", "1")
        archive.writestr("b", "2")

    monkeypatch.setattr(extraction, "MAX_DOCX_ENTRIES", 1)

    with pytest.raises(ValueError, match="too many archive entries"):
        extraction._validate_docx_archive(buffer.getvalue())


def test_chunk_accumulator_rejects_before_building_oversized_string(monkeypatch):
    monkeypatch.setattr(extraction, "MAX_EXTRACTED_CHARS", 10)
    parts = ["12345"]

    with pytest.raises(ValueError, match="character limit"):
        extraction._append_limited(parts, "67890", current_chars=5)

    assert parts == ["12345"]
