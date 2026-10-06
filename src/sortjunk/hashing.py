"""Duplicate detection: exact hash (any file type) + perceptual near-duplicate (images).

Read-only: every function here only opens files for reading.
"""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from pathlib import Path

from .config import ProgressCallback
from .models import FileRecord

logger = logging.getLogger(__name__)

_HASH_CHUNK_SIZE = 1024 * 1024
# Max Hamming distance between average-hashes to call two images "near-duplicate".
NEAR_DUP_THRESHOLD = 5


def sha256_of(path: Path) -> str:
    """Exact-duplicate fingerprint: stream the file in chunks, never load it whole."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_exact_duplicates(
    records: list[FileRecord], on_progress: ProgressCallback | None = None
) -> dict[Path, str]:
    """sha256-based exact duplicates, across any file type. Returns path -> group_id
    for groups with more than one member; unpaired files are simply absent.

    Only files that share their exact size with another file are hashed --
    a file with a unique size can't have a duplicate, and skipping it avoids
    reading every large video or installer end to end.
    """
    by_size: dict[int, list[FileRecord]] = defaultdict(list)
    for record in records:
        if record.cloud_only:
            continue  # hashing would download it from OneDrive
        by_size[record.size_bytes].append(record)
    candidates = [r for group in by_size.values() if len(group) > 1 for r in group]

    groups: dict[str, list[FileRecord]] = defaultdict(list)
    for i, record in enumerate(candidates):
        if on_progress is not None:
            on_progress("Checking for duplicates", i, len(candidates))
        try:
            groups[sha256_of(record.path)].append(record)
        except OSError as exc:
            logger.warning("Could not hash %s: %s", record.path, exc)

    result: dict[Path, str] = {}
    for digest, members in groups.items():
        if len(members) > 1:
            group_id = f"sha256:{digest[:12]}"
            for record in members:
                result[record.path] = group_id
    return result


def pick_keepers(records: list[FileRecord], groups: dict[Path, str]) -> set[Path]:
    """One file per duplicate group that stays where it is: the oldest copy.

    Ties break on the shorter name (the original, not "name (1).png"), then
    on path, so the choice is deterministic across runs.
    """
    best: dict[str, FileRecord] = {}
    for record in records:
        group_id = groups.get(record.path)
        if group_id is None:
            continue
        current = best.get(group_id)
        if current is None or _keeper_key(record) < _keeper_key(current):
            best[group_id] = record
    return {record.path for record in best.values()}


def _keeper_key(record: FileRecord) -> tuple:
    return (record.modified_at, len(record.path.name), str(record.path))


def perceptual_hash(path: Path) -> int | None:
    """Near-duplicate fingerprint: a 64-bit average hash. None if not a readable image.

    Same algorithm (and bit-for-bit the same result) as imagehash.average_hash:
    shrink to 8x8 grayscale, then one bit per pixel brighter than the mean.
    Done directly in Pillow so the app doesn't ship numpy/scipy/PyWavelets.
    """
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow not available; skipping perceptual hashing.")
        return None

    try:
        with Image.open(path) as img:
            small = img.convert("L").resize((8, 8), Image.Resampling.LANCZOS)
            pixels = small.tobytes()
    except Exception as exc:  # noqa: BLE001 - any decode failure just means "can't hash this one"
        logger.warning("Could not compute perceptual hash for %s: %s", path, exc)
        return None

    mean = sum(pixels) / len(pixels)
    return sum(1 << i for i, value in enumerate(pixels) if value > mean)


def hamming_distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()
