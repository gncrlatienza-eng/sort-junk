"""Filesystem path-safety primitives.

Downloads-folder filenames originate from the internet and are effectively
adversarial input. Every function here is small, pure (aside from the
existence/stat checks needed to resolve symlinks and collisions), and
independently testable -- `planner.py` uses these at plan time and
`executor.py` re-validates with the same functions immediately before it
touches disk (defense in depth).
"""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path

from .exceptions import PathSafetyError

_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_INVALID_CHARS = '<>:"|?*'
_MAX_NAME_LENGTH = 200
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def sanitize_filename(name: str) -> str:
    """Return a name that is safe to create as a file on Windows.

    Strips path separators and control characters, replaces other
    Windows-invalid characters, trims trailing dots/spaces (which Windows
    silently strips and which can otherwise cause two distinct requested
    names to collide), renames reserved device names, and truncates
    overlong names while preserving the extension.

    Also strips Unicode "format" characters (category Cf) -- these include
    bidi overrides like U+202E RIGHT-TO-LEFT OVERRIDE, which a downloaded
    file can use to make its *displayed* name misleading (e.g. an .exe
    whose name renders as if it were a harmless .png) in the GUI's plan
    preview and confirm dialog, without changing which category it
    actually sorts into.
    """
    if not name:
        return "_unnamed"

    cleaned = "".join(
        "_" if ch in "/\\" or ord(ch) < 32 or unicodedata.category(ch) == "Cf" else ch
        for ch in name
    )
    cleaned = "".join("_" if ch in _INVALID_CHARS else ch for ch in cleaned)
    cleaned = cleaned.rstrip(" .")
    if not cleaned:
        cleaned = "_unnamed"

    stem = cleaned.split(".", 1)[0]
    if stem.upper() in _RESERVED_NAMES:
        cleaned = f"_{cleaned}"

    if len(cleaned) > _MAX_NAME_LENGTH:
        stem, dot, ext = cleaned.rpartition(".")
        if dot and len(ext) <= 20:
            keep = max(1, _MAX_NAME_LENGTH - len(ext) - 1)
            cleaned = f"{stem[:keep]}.{ext}"
        else:
            cleaned = cleaned[:_MAX_NAME_LENGTH]

    return cleaned


def resolve_and_validate_containment(root: Path, candidate: Path) -> Path:
    """Resolve `candidate` and raise PathSafetyError unless it stays inside `root`.

    Rejects raw '..' segments outright, then resolves symlinks and
    case-normalizes both sides -- required on Windows, where the filesystem
    is case-insensitive and a naive case-sensitive prefix check could be
    bypassed by a case-variant path.
    """
    if ".." in candidate.parts:
        raise PathSafetyError(f"Path contains '..' segment: {candidate}")

    root_resolved = root.resolve(strict=False)
    candidate_resolved = candidate.resolve(strict=False)

    root_norm = os.path.normcase(str(root_resolved))
    candidate_norm = os.path.normcase(str(candidate_resolved))

    if candidate_norm != root_norm and not candidate_norm.startswith(root_norm + os.sep):
        raise PathSafetyError(f"Destination '{candidate}' resolves outside target root '{root}'")

    return candidate_resolved


def is_unsafe_link(path: Path) -> bool:
    """True if `path` is a symlink or a Windows NTFS junction/reparse point.

    `Path.is_symlink()` alone does not detect junctions on Windows, so this
    also inspects the file's reparse-point attribute directly.
    """
    if path.is_symlink():
        return True
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & _FILE_ATTRIBUTE_REPARSE_POINT)


def resolve_collision(dest: Path, max_attempts: int = 10_000) -> Path:
    """Return `dest`, or the first `name (N).ext` variant that doesn't exist.

    Never overwrites an existing file. Bounds worst-case work with
    `max_attempts` rather than looping forever.
    """
    if not dest.exists():
        return dest

    stem, ext = dest.stem, dest.suffix
    parent = dest.parent
    for i in range(1, max_attempts + 1):
        candidate = parent / f"{stem} ({i}){ext}"
        if not candidate.exists():
            return candidate

    raise PathSafetyError(
        f"Could not find a free filename for '{dest}' after {max_attempts} attempts"
    )


def paths_equal(a: Path, b: Path) -> bool:
    """Case-normalized path equality (Windows filesystems are case-insensitive)."""
    return os.path.normcase(str(a.resolve(strict=False))) == os.path.normcase(
        str(b.resolve(strict=False))
    )


def safe_destination_for(
    root: Path, category_subpath: str | Path, filename: str, *, source: Path | None = None
) -> Path:
    """The single composed call site: sanitize -> validate containment -> dedupe name.

    If `source` already sits exactly at the computed destination (a file
    that's already correctly sorted, e.g. on a second run), that spot is
    returned as-is rather than treated as a collision with itself -- which
    would otherwise rename the file out from under itself on every rerun.
    """
    safe_name = sanitize_filename(filename)
    candidate = root / category_subpath / safe_name
    validated = resolve_and_validate_containment(root, candidate)
    if source is not None and paths_equal(source, validated):
        return validated
    return resolve_collision(validated)
