"""Screenshots-mode categorization: month folder per screenshot, hashing -> duplicates.

Every screenshot's category is its month folder (e.g. "Dec-2026").
Exact duplicates are found via sha256; near-duplicates via perceptual
(average) hashing clustered with a simple union-find over Hamming distance.
Neither pass ever removes or modifies a file -- duplicates are only flagged
(`is_duplicate` / `dup_group_id`) for the planner to route into
`Duplicates_Found/`.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .. import hashing
from ..config import ProgressCallback, ScanConfig
from ..models import CategoryDecision, FileRecord
from ..planner import month_folder_name

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


# The 64-bit hash split into 6 bands. Two hashes within NEAR_DUP_THRESHOLD (5)
# bits must agree exactly on at least one band (pigeonhole), so only pairs
# sharing a band bucket need comparing -- not all n^2 of them.
_BANDS = ((0, 11), (11, 11), (22, 11), (33, 11), (44, 10), (54, 10))


def _find_near_duplicates(
    entries: list[tuple[FileRecord, int]], report: ProgressCallback | None = None
) -> dict[Path, str]:
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

    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, (_record, phash) in enumerate(entries):
        for band, (shift, width) in enumerate(_BANDS):
            buckets[(band, (phash >> shift) & ((1 << width) - 1))].append(i)

    for i, (_record, phash) in enumerate(entries):
        if report is not None:
            report("Finding similar images", i, n)
        checked: set[int] = set()
        for band, (shift, width) in enumerate(_BANDS):
            for j in buckets[(band, (phash >> shift) & ((1 << width) - 1))]:
                if j <= i or j in checked:
                    continue
                checked.add(j)
                if hashing.hamming_distance(phash, entries[j][1]) <= hashing.NEAR_DUP_THRESHOLD:
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
    near_dups = _find_near_duplicates(phash_entries, report=config.on_progress)
    near_keepers = hashing.pick_keepers(records, near_dups)

    decisions: list[CategoryDecision] = []
    for record in records:
        if record.path in near_dups:
            dup_group_id: str | None = near_dups[record.path]
            is_duplicate = record.path not in near_keepers
        else:
            dup_group_id = exact_dups.get(record.path)
            is_duplicate = dup_group_id is not None and record.path not in exact_keepers

        decisions.append(
            CategoryDecision(
                record=record,
                category=month_folder_name(record.modified_at),
                is_duplicate=is_duplicate,
                dup_group_id=dup_group_id,
            )
        )

    return decisions
