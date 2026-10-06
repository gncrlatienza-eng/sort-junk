"""Tesseract OCR detection and per-file OCR with a safe, always-non-fatal fallback.

Screenshots mode uses this; Downloads mode never imports it.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_OCR_TIMEOUT_S = 10
_TESSERACT_NATIVE_SUFFIXES = {".png", ".jpg", ".jpeg"}


@dataclass
class TesseractStatus:
    available: bool
    version: str | None = None
    error: str | None = None


def find_tesseract(env: Mapping[str, str] | None = None) -> str | None:
    """Locate tesseract.exe: on PATH, else the standard Windows install folders.

    The official Windows installer doesn't add itself to PATH by default, so
    without this most users who did install Tesseract would never get OCR.
    """
    on_path = shutil.which("tesseract")
    if on_path:
        return on_path
    env = os.environ if env is None else env
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = env.get(var)
        if not base:
            continue
        for sub in ("Tesseract-OCR", "Programs/Tesseract-OCR"):
            candidate = Path(base) / sub / "tesseract.exe"
            if candidate.is_file():
                return str(candidate)
    return None


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

    tesseract_cmd = tesseract_cmd or find_tesseract()
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
        if path.suffix.lower() in _TESSERACT_NATIVE_SUFFIXES:
            # Tesseract reads these itself; handing it a PIL image would make
            # pytesseract re-encode every screenshot to a temp file first.
            return pytesseract.image_to_string(str(path), timeout=timeout_s)
        with Image.open(path) as img:
            return pytesseract.image_to_string(img, timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001 - OCR failures are expected and non-fatal
        logger.warning("OCR failed for %s: %s", path, exc)
        return None
