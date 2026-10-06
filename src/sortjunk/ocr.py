"""Tesseract OCR detection and per-file OCR with a safe, always-non-fatal fallback.

Screenshots mode uses this; Downloads mode never imports it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_OCR_TIMEOUT_S = 10


@dataclass
class TesseractStatus:
    available: bool
    version: str | None = None
    error: str | None = None


def detect_tesseract(tesseract_cmd: str | None = None) -> TesseractStatus:
    """Check once whether a working Tesseract binary is available.

    If `tesseract_cmd` is given, point pytesseract at it before checking --
    handles a binary that's installed but not on PATH. Called once at CLI
    startup; the result is cached in ScanConfig.use_ocr.
    """
    try:
        import pytesseract
    except ImportError as exc:
        return TesseractStatus(available=False, error=f"pytesseract not installed: {exc}")

    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    try:
        version = str(pytesseract.get_tesseract_version())
        return TesseractStatus(available=True, version=version)
    except Exception as exc:  # noqa: BLE001 - any failure here just means "not available"
        return TesseractStatus(available=False, error=str(exc))


def ocr_image(path: Path, timeout_s: int = DEFAULT_OCR_TIMEOUT_S) -> str | None:
    """Extract text from one image. Never raises -- a failure just returns None.

    `timeout_s` bounds worst-case per-file OCR time so one corrupt or huge
    image can't hang an entire run; pytesseract enforces this itself.
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return None

    try:
        with Image.open(path) as img:
            return pytesseract.image_to_string(img, timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001 - OCR failures are expected and non-fatal
        logger.warning("OCR failed for %s: %s", path, exc)
        return None
