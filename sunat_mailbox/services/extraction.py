"""Text extraction from downloaded attachments."""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass

from pypdf import PdfReader
from pypdf.errors import PyPdfError

logger = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF"


@dataclass(frozen=True)
class ExtractionResult:
    """Outcome of extracting text from a single file."""

    status: str
    text: str = ""
    page_count: int | None = None
    error: str = ""
    checksum: str = ""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_text(data: bytes, *, content_type: str = "") -> ExtractionResult:
    """Extract text from an attachment.

    Only PDFs are supported. A PDF with no text layer (a scan) is reported as
    ``empty`` rather than an error, so those rows can be found later and routed
    through OCR.
    """
    from ..models import ExtractionStatus

    checksum = sha256(data)

    if not data:
        return ExtractionResult(ExtractionStatus.FAILED, error="Empty response body.")

    if not data.startswith(PDF_MAGIC):
        return ExtractionResult(
            ExtractionStatus.UNSUPPORTED,
            error=f"Not a PDF (content type {content_type or 'unknown'}).",
            checksum=checksum,
        )

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except (PyPdfError, ValueError, OSError) as exc:
        return ExtractionResult(
            ExtractionStatus.FAILED,
            error=f"{type(exc).__name__}: {exc}"[:500],
            checksum=checksum,
        )

    text = "\n".join(pages).strip()
    status = ExtractionStatus.EXTRACTED if text else ExtractionStatus.EMPTY
    return ExtractionResult(
        status=status, text=text, page_count=len(pages), checksum=checksum
    )
