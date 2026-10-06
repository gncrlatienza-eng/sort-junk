"""Screenshots-mode categorization: OCR text -> category bucket, hashing -> duplicates.

Exact duplicates are found via sha256; near-duplicates via perceptual
(average) hashing clustered with a simple union-find over pairwise Hamming
distance. Neither pass ever removes or modifies a file -- duplicates are
only flagged (`is_duplicate` / `dup_group_id`) for the planner to route into
`Duplicates_Found/`.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from .. import hashing, ocr
from ..config import ScanConfig
from ..models import CategoryDecision, FileRecord

logger = logging.getLogger(__name__)

UNCATEGORIZED = "Uncategorized"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

_RECEIPT_KEYWORDS = (
    "total",
    "subtotal",
    "receipt",
    "order #",
    "order number",
    "tax",
    "$",
    "payment method",
)
_ERROR_KEYWORDS = (
    "error",
    "exception",
    "traceback",
    "stack trace",
    "failed to",
    "warning:",
    "at line",
)
_CHAT_HINTS = ("delivered", "read ", "typing...", "yesterday", "today at")


def _classify_text(text: str) -> str:
    lowered = text.lower()
    if any(kw in lowered for kw in _RECEIPT_KEYWORDS):
        return "Receipts"
    if any(kw in lowered for kw in _ERROR_KEYWORDS):
        return "Errors_Code"
    if any(kw in lowered for kw in _CHAT_HINTS):
        return "Chats"

    # Fallback heuristic: chat screenshots tend to be many short lines.
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) >= 6 and sum(len(ln) < 40 for ln in lines) / len(lines) > 0.7:
        return "Chats"

    return UNCATEGORIZED


def _find_near_duplicates(entries: list[tuple[FileRecord, Any]]) -> dict[Path, str]:
    """Cluster `entries` (record, phash) pairs by Hamming distance via union-find."""
    n = len(entries)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(n):
        for j in range(i + 1, n):
            if hashing.hamming_distance(entries[i][1], entries[j][1]) <= hashing.NEAR_DUP_THRESHOLD:
                union(i, j)

    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(i)

    result: dict[Path, str] = {}
    for members in clusters.values():
        if len(members) < 2:
            continue
        group_id = f"phash:{entries[members[0]][0].path.name}"
        for idx in members:
            result[entries[idx][0].path] = group_id
    return result


def categorize(records: list[FileRecord], config: ScanConfig) -> list[CategoryDecision]:
    exact_dups = hashing.find_exact_duplicates(records)

    # Near-duplicate pass only over images not already flagged as exact duplicates
    # (an exact duplicate's fate is already decided; no need to hash it twice).
    phash_entries: list[tuple[FileRecord, Any]] = []
    for record in records:
        if record.path in exact_dups or record.path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        phash = hashing.perceptual_hash(record.path)
        if phash is not None:
            phash_entries.append((record, phash))
    near_dups = _find_near_duplicates(phash_entries)

    decisions: list[CategoryDecision] = []
    for record in records:
        dup_group_id = exact_dups.get(record.path) or near_dups.get(record.path)
        is_duplicate = dup_group_id is not None

        ocr_used = False
        category = UNCATEGORIZED
        max_ocr_bytes = config.max_ocr_size_mb * 1024 * 1024
        if record.path.suffix.lower() in IMAGE_SUFFIXES:
            if config.use_ocr and record.size_bytes <= max_ocr_bytes:
                text = ocr.ocr_image(record.path)
                if text:
                    category = _classify_text(text)
                    ocr_used = True
            elif config.use_ocr:
                logger.info(
                    "Skipping OCR for %s: %d bytes exceeds max-ocr-size-mb cap",
                    record.path,
                    record.size_bytes,
                )

        decisions.append(
            CategoryDecision(
                record=record,
                category=category,
                is_duplicate=is_duplicate,
                dup_group_id=dup_group_id,
                ocr_used=ocr_used,
            )
        )

    return decisions
