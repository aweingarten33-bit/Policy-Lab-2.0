"""
Text Extraction Service — Extracts text from uploaded policy files.
All processing happens in-memory. No files are written to disk.
"""

import io
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


async def extract_text_from_file(contents: bytes, filename: str, file_ext: str) -> str:
    """
    Extract text from an uploaded file. Returns plain text string.
    Supports: .txt, .md, .pdf, .docx, .doc, .rtf
    """
    if file_ext in (".txt", ".md"):
        return contents.decode("utf-8", errors="replace")

    if file_ext in (".docx", ".doc"):
        return await _extract_docx(contents)

    if file_ext == ".pdf":
        return await _extract_pdf(contents)

    if file_ext == ".rtf":
        return _extract_rtf(contents.decode("utf-8", errors="replace"))

    # Fallback: try to read as text
    return contents.decode("utf-8", errors="replace")


async def _extract_docx(contents: bytes) -> str:
    """Extract text from a .docx file using python-docx."""
    try:
        from docx import Document
        buffer = io.BytesIO(contents)
        doc = Document(buffer)
        paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
        return "\n\n".join(paragraphs)
    except Exception as e:
        logger.error(f"DOCX extraction error: {e}")
        raise ValueError(f"Could not extract text from DOCX: {str(e)}")


async def _extract_pdf(contents: bytes) -> str:
    """Extract text from a PDF file using pdfplumber (preferred) or PyPDF2 (fallback)."""
    try:
        import pdfplumber
        buffer = io.BytesIO(contents)
        pages = []
        with pdfplumber.open(buffer) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages.append(text)
        result = "\n\n".join(pages)
        if result.strip():
            return result
    except ImportError:
        logger.warning("pdfplumber not available, falling back to PyPDF2")
    except Exception as e:
        logger.warning(f"pdfplumber extraction failed: {e}, trying PyPDF2")

    # Fallback to PyPDF2
    try:
        from PyPDF2 import PdfReader
        buffer = io.BytesIO(contents)
        reader = PdfReader(buffer)
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n\n".join(pages)
    except Exception as e:
        logger.error(f"PDF extraction error: {e}")
        raise ValueError(f"Could not extract text from PDF: {str(e)}")


def _extract_rtf(text: str) -> str:
    """Basic RTF text stripping."""
    stripped = text
    stripped = re.sub(r'\{\\[^{}]*\}', '', stripped)
    stripped = re.sub(r'\\[a-z]+\d* ?', '', stripped, flags=re.IGNORECASE)
    stripped = re.sub(r'[{}\\]', '', stripped)
    return stripped.strip()
