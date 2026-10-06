"""Read-only recursive filesystem scan producing FileRecord snapshots.

This module never writes to disk -- it only calls os.scandir/os.stat.
A source-guard test (tests/test_planner_apply_split.py) asserts no
disk-mutating calls appear here.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from .config import ProgressCallback
from .models import FileRecord
from .pathsafety import is_cloud_only, is_unsafe_link
from .planner import DUPLICATES_SUBDIR, MONTH_ABBREVIATIONS
from .target_guard import is_project_dir

logger = logging.getLogger(__name__)

# Top-level folders SortJunk itself creates. Never re-ingested as clutter to
# sort -- without this, a rerun would try to re-categorize its own archive
# zips, and would re-hash/re-OCR everything already flagged into
# Duplicates_Found on every subsequent run.
_EXCLUDED_TOP_LEVEL_NAMES = {DUPLICATES_SUBDIR}
_EXCLUDED_TOP_LEVEL_PREFIXES = ("_Archive_",)
# Screenshots mode's own `Mon-YYYY/<Category>` output (`YYYY-MM` before
# v0.1.3). Skipped on reruns so already-sorted screenshots aren't re-hashed
# and re-OCR'd every scan.
_MONTH_FOLDER = re.compile(rf"\d{{4}}-\d{{2}}|(?:{'|'.join(MONTH_ABBREVIATIONS)})-\d{{4}}")


def _is_excluded_top_level(name: str, skip_month_folders: bool = False) -> bool:
    if skip_month_folders and _MONTH_FOLDER.fullmatch(name):
        return True
    return name in _EXCLUDED_TOP_LEVEL_NAMES or any(
        name.startswith(prefix) for prefix in _EXCLUDED_TOP_LEVEL_PREFIXES
    )


def estimate_file_count(
    root: Path, recursive: bool = True, skip_month_folders: bool = False
) -> int:
    """Cheap pre-scan count of files under `root`, for a soft-limit prompt before hashing/OCR.

    `recursive=False` counts only files sitting directly in `root`, matching
    what `scan(recursive=False)` will actually process -- used by Downloads
    mode, where subfolders are never descended into.
    """
    if not recursive:
        try:
            return sum(1 for entry in os.scandir(root) if entry.is_file(follow_symlinks=False))
        except OSError:
            return 0
    count = 0
    for current, dirs, files in os.walk(root):
        if skip_month_folders and Path(current) == Path(root):
            dirs[:] = [d for d in dirs if not _MONTH_FOLDER.fullmatch(d)]
        count += len(files)
    return count


def list_top_level_dirs(
    root: Path, excluded_names: frozenset[str] | set[str], excluded_prefixes: tuple[str, ...] = ()
) -> list[Path]:
    """Shallow, read-only listing of directories sitting directly inside `root`.

    Skips symlinks/junctions and anything in `excluded_names` or starting
    with `excluded_prefixes` -- SortJunk's own output folders -- so a rerun
    never re-ingests them as if they were a folder the user made.
    """
    root = root.resolve(strict=True)
    dirs: list[Path] = []
    try:
        entries = list(os.scandir(root))
    except OSError as exc:
        logger.warning("Skipping unreadable directory %s: %s", root, exc)
        return dirs

    for entry in entries:
        entry_path = Path(entry.path)
        if is_unsafe_link(entry_path):
            continue
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
        except OSError as exc:
            logger.warning("Skipping unreadable entry %s: %s", entry_path, exc)
            continue
        if entry.name in excluded_names or any(
            entry.name.startswith(prefix) for prefix in excluded_prefixes
        ):
            continue
        dirs.append(entry_path)

    return dirs


def scan(
    root: Path,
    max_files: int | None = None,
    recursive: bool = True,
    on_progress: ProgressCallback | None = None,
    skip_month_folders: bool = False,
) -> list[FileRecord]:
    """Walk `root` recursively and return a FileRecord for every regular file.

    Symlinks and Windows junctions/reparse points are skipped entirely --
    neither descended into (if a directory) nor recorded (if a file) -- so
    the scan never follows a link outside `root`.

    `recursive=False` scans only files sitting directly in `root` -- no
    subdirectory is ever descended into, whether it's SortJunk's own output
    or a folder the user made. Used by Downloads mode; see
    `list_top_level_dirs` for how pre-existing user folders are handled
    instead.

    `skip_month_folders=True` (Screenshots mode) also skips top-level
    month folders (`Mon-YYYY`, or `YYYY-MM` from older versions) -- that
    mode's own already-sorted output.
    """
    root = root.resolve(strict=True)
    records: list[FileRecord] = []
    stack = [root]

    while stack:
        current = stack.pop()
        is_root_level = current == root
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            logger.warning("Skipping unreadable directory %s: %s", current, exc)
            continue

        for entry in entries:
            entry_path = Path(entry.path)
            if is_unsafe_link(entry_path):
                logger.warning("Skipping symlink/junction: %s", entry_path)
                continue

            try:
                if entry.is_dir(follow_symlinks=False):
                    if is_root_level and (
                        not recursive or _is_excluded_top_level(entry.name, skip_month_folders)
                    ):
                        continue
                    if is_project_dir(entry_path):
                        # A repo / venv / node_modules: its layout matters, so it's
                        # left whole -- none of its files are ever recorded or moved.
                        logger.info("Skipping project folder: %s", entry_path)
                        continue
                    stack.append(entry_path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError as exc:
                logger.warning("Skipping unreadable entry %s: %s", entry_path, exc)
                continue

            try:
                st = entry.stat(follow_symlinks=False)
            except OSError as exc:
                logger.warning("Skipping unreadable file %s: %s", entry_path, exc)
                continue

            records.append(
                FileRecord(
                    path=entry_path,
                    relative_path=str(entry_path.relative_to(root)),
                    size_bytes=st.st_size,
                    modified_at=datetime.fromtimestamp(st.st_mtime, tz=UTC),
                    # st_ctime as creation time is deprecated on Windows (3.12+).
                    created_at=datetime.fromtimestamp(
                        getattr(st, "st_birthtime", st.st_ctime), tz=UTC
                    ),
                    cloud_only=is_cloud_only(st),
                )
            )

            if on_progress is not None and len(records) % 250 == 0:
                on_progress("Listing files", len(records), 0)

            if max_files is not None and len(records) >= max_files:
                logger.warning("Reached max_files cap (%d); stopping scan early.", max_files)
                return records

    return records
