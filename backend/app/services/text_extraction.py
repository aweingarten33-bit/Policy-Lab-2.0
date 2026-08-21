"""Text extraction for uploaded policy files.

Extraction is intentionally bounded. A small compressed DOCX or PDF can expand
into a very large amount of text, so the raw upload-size limit alone is not a
sufficient control before content reaches an LLM-backed endpoint.

All processing happens in memory; files are not written to disk.
"""

import io
import logging
import re
import zipfile

from app.models.schemas import MAX_INPUT_CHARS

logger = logging.getLogger(__name__)

MAX_EXTRACTED_CHARS = MAX_INPUT_CHARS
MAX_PDF_PAGES = 250
MAX_DOCX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_DOCX_ENTRIES = 5_000


def _validate_text_size(text: str) -> str:
    """Reject extracted text that exceeds the same limit as normal API input."""
    if len(text) > MAX_EXTRACTED_CHARS:
        raise ValueError(
            f"Extracted document text is too large ({len(text):,} characters). "
            f"Maximum is {MAX_EXTRACTED_CHARS:,} characters."
        )
    return text


def _append_limited(parts: list[str], text: str, current_chars: int) -> int:
    """Append one extracted chunk without first constructing an oversized result."""
    if not text:
        return current_chars
    added = len(text) + (2 if parts else 0)
    new_total = current_chars + added
    if new_total > MAX_EXTRACTED_CHARS:
        raise ValueError(
            f"Extracted document text exceeds the {MAX_EXTRACTED_CHARS:,}-character limit."
        )
    parts.append(text)
    return new_total


def _validate_docx_archive(contents: bytes) -> None:
    """Reject malformed or expansion-heavy DOCX archives before python-docx opens them."""
    try:
        with zipfile.ZipFile(io.BytesIO(contents)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_DOCX_ENTRIES:
                raise ValueError(
                    f"DOCX contains too many archive entries ({len(entries):,}); "
                    f"maximum is {MAX_DOCX_ENTRIES:,}."
                )
            expanded_bytes = sum(item.file_size for item in entries)
            if expanded_bytes > MAX_DOCX_UNCOMPRESSED_BYTES:
                raise ValueError(
                    f"DOCX expands to {expanded_bytes / 1024 / 1024:.1f} MB; "
                    f"maximum expanded size is {MAX_DOCX_UNCOMPRESSED_BYTES / 1024 / 1024:.0f} MB."
                )
    except ValueError:
        raise
    except zipfile.BadZipFile as exc:
        raise ValueError("The DOCX file is not a valid Office Open XML document.") from exc


async def extract_text_from_file(contents: bytes, filename: str, file_ext: str) -> str:
    """Extract plain text from a supported policy file.

    Supports: .txt, .md, .pdf, .docx, .rtf.
    Legacy binary .doc is deliberately not supported; callers should convert it
    to .docx first rather than attempting to parse it as a ZIP-based DOCX file.
    """
    file_ext = (file_ext or "").lower()

    if file_ext in (".txt", ".md"):
        return _validate_text_size(contents.decode("utf-8", errors="replace"))

    if file_ext == ".docx":
        return await _extract_docx(contents)

    if file_ext == ".pdf":
        return await _extract_pdf(contents)

    if file_ext == ".rtf":
        return _validate_text_size(
            _extract_rtf(contents.decode("utf-8", errors="replace"))
        )

    raise ValueError(f"Unsupported file type: {file_ext or 'unknown'}")


async def _extract_docx(contents: bytes) -> str:
    """Extract bounded text from a DOCX file using python-docx."""
    _validate_docx_archive(contents)
    try:
        from docx import Document

        doc = Document(io.BytesIO(contents))
        parts: list[str] = []
        current_chars = 0
        for paragraph in doc.paragraphs:
            text = paragraph.text.strip()
            if text:
                current_chars = _append_limited(parts, text, current_chars)
        return "\n\n".join(parts)
    except ValueError:
        raise
    except Exception as exc:
        logger.error("DOCX extraction error: %s", exc)
        raise ValueError("Could not extract readable text from this DOCX file.") from exc


async def _extract_pdf(contents: bytes) -> str:
    """Extract bounded text from a PDF using pdfplumber, then PyPDF2 fallback."""
    try:
        import pdfplumber

        parts: list[str] = []
        current_chars = 0
        with pdfplumber.open(io.BytesIO(contents)) as pdf:
            if len(pdf.pages) > MAX_PDF_PAGES:
                raise ValueError(
                    f"PDF has {len(pdf.pages):,} pages; maximum is {MAX_PDF_PAGES:,}."
                )
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    current_chars = _append_limited(parts, text, current_chars)
        result = "\n\n".join(parts)
        if result.strip():
            return result
    except ImportError:
        logger.warning("pdfplumber not available, falling back to PyPDF2")
    except ValueError:
        # Limits and validation failures are policy decisions, not parser
        # failures. Never bypass them by retrying through another library.
        raise
    except Exception as exc:
        logger.warning("pdfplumber extraction failed: %s; trying PyPDF2", exc)

    try:
        from PyPDF2 import PdfReader

        reader = PdfReader(io.BytesIO(contents))
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError(
                f"PDF has {len(reader.pages):,} pages; maximum is {MAX_PDF_PAGES:,}."
            )

        parts: list[str] = []
        current_chars = 0
        for page in reader.pages:
            text = page.extract_text()
            if text:
                current_chars = _append_limited(parts, text, current_chars)
        return "\n\n".join(parts)
    except ValueError:
        raise
    except Exception as exc:
        logger.error("PDF extraction error: %s", exc)
        raise ValueError("Could not extract readable text from this PDF file.") from exc


def _extract_rtf(text: str) -> str:
    """Basic RTF text stripping."""
    stripped = re.sub(r'\{\\[^{}]*\}', '', text)
    stripped = re.sub(r'\\[a-z]+\d* ?', '', stripped, flags=re.IGNORECASE)
    stripped = re.sub(r'[{}\\]', '', stripped)
    return stripped.strip()
