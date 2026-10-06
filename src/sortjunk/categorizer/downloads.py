"""Downloads-mode categorization: extension-based, with in-progress-download skip.

Deliberately has zero import dependency on Pillow/imagehash/pytesseract, so
Downloads mode keeps working even if the image-processing stack fails to
import.
"""

from __future__ import annotations

from ..config import ScanConfig
from ..models import CategoryDecision, FileRecord
from ..planner import DUPLICATES_SUBDIR, EXISTING_FOLDERS_SUBDIR, STORAGE_SUBDIR

IN_PROGRESS_SUFFIXES = {".crdownload", ".part", ".tmp"}
OTHERS_CATEGORY = "Others"

_EXTENSION_CATEGORIES: dict[str, str] = {}


def _register(category: str, extensions: list[str]) -> None:
    for ext in extensions:
        _EXTENSION_CATEGORIES[ext] = category


_register("Installers", [".exe", ".msi", ".dmg", ".apk"])
_register("PDF", [".pdf"])
_register(
    "Docs",
    [".docx", ".doc", ".txt", ".xlsx", ".xls", ".pptx", ".ppt", ".csv", ".rtf"],
)
_register(
    "Images",
    [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".tiff", ".heic", ".heif"],
)
_register("Zip", [".zip", ".rar", ".7z", ".tar", ".gz"])
_register("Media", [".mp4", ".mp3", ".mov", ".avi", ".wav", ".mkv", ".flac"])

CATEGORIES = ("PDF", "Docs", "Images", "Installers", "Zip", "Media", OTHERS_CATEGORY)

# Top-level names SortJunk itself creates in Downloads mode. Anything else
# sitting at the Downloads root is a folder the user already made, and gets
# relocated whole into EXISTING_FOLDERS_SUBDIR -- see scanner.list_top_level_dirs.
OWNED_TOP_LEVEL_NAMES = frozenset(
    {*CATEGORIES, STORAGE_SUBDIR, EXISTING_FOLDERS_SUBDIR, DUPLICATES_SUBDIR}
)


def categorize(records: list[FileRecord], config: ScanConfig) -> list[CategoryDecision]:
    """Sort by extension; in-progress downloads are flagged to be skipped entirely."""
    decisions = []
    for record in records:
        suffix = record.path.suffix.lower()

        if suffix in IN_PROGRESS_SUFFIXES:
            decisions.append(
                CategoryDecision(
                    record=record,
                    category=OTHERS_CATEGORY,
                    skip_reason="in-progress download",
                )
            )
            continue

        category = _EXTENSION_CATEGORIES.get(suffix, OTHERS_CATEGORY)
        decisions.append(CategoryDecision(record=record, category=category))

    return decisions
