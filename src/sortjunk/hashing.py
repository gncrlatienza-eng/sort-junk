"""Duplicate detection: exact hash (any file type) + perceptual near-duplicate (images).

Read-only: every function here only opens files for reading.
"""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

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


def find_exact_duplicates(records: list[FileRecord]) -> dict[Path, str]:
    """sha256-based exact duplicates, across any file type. Returns path -> group_id
    for groups with more than one member; unpaired files are simply absent.
    """
    groups: dict[str, list[FileRecord]] = defaultdict(list)
    for record in records:
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


def perceptual_hash(path: Path) -> Any | None:
    """Near-duplicate fingerprint via average hash. None if the file isn't a readable image."""
    try:
        import imagehash
        from PIL import Image
    except ImportError:
        logger.warning("imagehash/Pillow not available; skipping perceptual hashing.")
        return None

    try:
        with Image.open(path) as img:
            return imagehash.average_hash(img)
    except Exception as exc:  # noqa: BLE001 - any decode failure just means "can't hash this one"
        logger.warning("Could not compute perceptual hash for %s: %s", path, exc)
        return None


def hamming_distance(a: Any, b: Any) -> int:
    """imagehash hash objects support subtraction as Hamming distance."""
    return a - b
