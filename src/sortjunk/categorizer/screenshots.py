"""Screenshots-mode categorization: OCR text -> category bucket, hashing -> duplicates.

Exact duplicates are found via sha256; near-duplicates via perceptual
(average) hashing clustered with a simple union-find over pairwise Hamming
distance. Neither pass ever removes or modifies a file -- duplicates are
only flagged (`is_duplicate` / `dup_group_id`) for the planner to route into
`Duplicates_Found/`.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from pathlib import Path

from .. import hashing, ocr
from ..config import ScanConfig
from ..models import CategoryDecision, FileRecord

logger = logging.getLogger(__name__)

UNCATEGORIZED = "Uncategorized"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}

# Whole-word patterns, checked errors-first: a plain substring match let
# "SyntaxError" hit "tax" and any shell prompt hit "$", filing code
# screenshots under Receipts.
_ERROR_PATTERN = re.compile(
    r"\w*(?:error|exception)\b|\btraceback\b|\bstack trace\b|\bfailed to\b"
    r"|\bwarning:|\bat line \d|\bnpm err!",
    re.IGNORECASE,
)
_RECEIPT_PATTERN = re.compile(
    r"\b(?:sub)?total\b|\breceipt\b|\border (?:#|no\.?|number)|\btax\b|\bpayment method\b"
    r"|\bamount (?:due|paid)\b|[$€£₱]\s?\d[\d,]*\.\d{2}\b",
    re.IGNORECASE,
)
_CHAT_PATTERN = re.compile(
    r"\bdelivered\b|\bread \d|\bseen\b|\btyping\.\.\.|\byesterday\b|\btoday at\b",
    re.IGNORECASE,
)


def _classify_text(text: str) -> str:
    if _ERROR_PATTERN.search(text):
        return "Errors_Code"
    if _RECEIPT_PATTERN.search(text):
        return "Receipts"
    if _CHAT_PATTERN.search(text):
        return "Chats"

    # Fallback heuristic: chat screenshots tend to be many short lines.
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) >= 6 and sum(len(ln) < 40 for ln in lines) / len(lines) > 0.7:
        return "Chats"

    return UNCATEGORIZED


def _find_near_duplicates(entries: list[tuple[FileRecord, int]]) -> dict[Path, str]:
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
    exact_dups = hashing.find_exact_duplicates(records, on_progress=config.on_progress)
    exact_keepers = hashing.pick_keepers(records, exact_dups)

    # Near-duplicate pass skips exact copies (their fate is already decided)
    # but still includes each exact group's keeper, which may itself be a
    # near-duplicate of something else.
    phash_entries: list[tuple[FileRecord, int]] = []
    for i, record in enumerate(records):
        config.report("Comparing images", i, len(records))
        if record.path in exact_dups and record.path not in exact_keepers:
            continue
        if record.cloud_only or record.path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        phash = hashing.perceptual_hash(record.path)
        if phash is not None:
            phash_entries.append((record, phash))
    near_dups = _find_near_duplicates(phash_entries)
    near_keepers = hashing.pick_keepers(records, near_dups)

    decisions: list[CategoryDecision] = []
    for i, record in enumerate(records):
        if config.use_ocr:
            config.report("Reading text (OCR)", i, len(records))
        if record.path in near_dups:
            dup_group_id: str | None = near_dups[record.path]
            is_duplicate = record.path not in near_keepers
        else:
            dup_group_id = exact_dups.get(record.path)
            is_duplicate = dup_group_id is not None and record.path not in exact_keepers

        ocr_used = False
        category = UNCATEGORIZED
        max_ocr_bytes = config.max_ocr_size_mb * 1024 * 1024
        if record.path.suffix.lower() in IMAGE_SUFFIXES and not record.cloud_only:
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
