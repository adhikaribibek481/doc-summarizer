# Extract plain text from PDF, DOCX, and TXT files

import fitz  # PyMuPDF
import logging
from pathlib import Path
from docx import Document

logger = logging.getLogger(__name__)


def parse_pdf(path: str | Path) -> str:
    """Extract text from a PDF using PyMuPDF (fitz)."""
    doc = fitz.open(str(path))
    pages_text = []
    for page in doc:
        pages_text.append(page.get_text())
    doc.close()
    full_text = "\n".join(pages_text).strip()
    logger.info(f"Parsed PDF: {path} ({len(full_text)} chars)")
    return full_text


def parse_docx(path: str | Path) -> str:
    """Extract text from a DOCX using python-docx."""
    doc = Document(str(path))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    full_text = "\n".join(paragraphs).strip()
    logger.info(f"Parsed DOCX: {path} ({len(full_text)} chars)")
    return full_text


def parse_txt(path: str | Path) -> str:
    """Read plain text file."""
    with open(str(path), "r", encoding="utf-8", errors="replace") as f:
        full_text = f.read().strip()
    logger.info(f"Parsed TXT: {path} ({len(full_text)} chars)")
    return full_text


def parse_document(path: str | Path, mime_type: str) -> str:
    """
    Dispatch to the correct parser based on MIME type or file extension.
    Returns extracted plain text.
    """
    path = Path(path)
    ext = path.suffix.lower()

    # MIME-type based dispatch
    if mime_type == "application/pdf" or ext == ".pdf":
        return parse_pdf(path)
    elif mime_type in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.google-apps.document",
    ) or ext == ".docx":
        return parse_docx(path)
    elif mime_type == "text/plain" or ext == ".txt":
        return parse_txt(path)
    else:
        # Last resort: try reading as text
        logger.warning(f"Unknown MIME type '{mime_type}', attempting plain text read.")
        return parse_txt(path)
