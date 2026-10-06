"""Custom-mode categorization: general-purpose cleanup for any folder.

Reuses Downloads mode's extension-based sort (it was never actually
Downloads-specific) and layers on exact-duplicate detection across every
file type, not just images. Deliberately never imports Pillow/imagehash/
pytesseract -- no OCR or perceptual hashing here, same dependency-light
philosophy as Downloads mode. Empty-folder cleanup is handled by
planner.py/executor.py, not here.
"""

from __future__ import annotations

from .. import hashing
from ..config import ScanConfig
from ..models import CategoryDecision, FileRecord
from . import downloads as downloads_categorizer


def categorize(records: list[FileRecord], config: ScanConfig) -> list[CategoryDecision]:
    base_decisions = downloads_categorizer.categorize(records, config)
    dup_groups = hashing.find_exact_duplicates(records)

    decisions: list[CategoryDecision] = []
    for decision in base_decisions:
        dup_group_id = dup_groups.get(decision.record.path)
        decisions.append(
            CategoryDecision(
                record=decision.record,
                category=decision.category,
                is_duplicate=dup_group_id is not None,
                dup_group_id=dup_group_id,
                skip_reason=decision.skip_reason,
            )
        )
    return decisions
