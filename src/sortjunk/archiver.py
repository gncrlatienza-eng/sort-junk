"""Archive-zip writing: write, verify, and only then let the caller remove originals.

Only called by executor.py, the sole module permitted to mutate disk.
"""

from __future__ import annotations

import os
import shutil
import time
import zipfile
from pathlib import Path


def arcname_for(source: Path, root: Path) -> str:
    return source.relative_to(root).as_posix()


def write_archive(zip_path: Path, root: Path, sources: list[Path]) -> list[Path]:
    """Write `sources` into `zip_path` (arcnames relative to `root`) and verify integrity.

    Appends to an existing archive for the same month rather than
    overwriting it, so re-running SortJunk never destroys a prior archive.
    Never removes a source file -- the caller only does that after this
    returns successfully, so a failed or partial zip write can't lose data.

    Returns the subset of `sources` actually written to the zip. A source
    whose arcname already exists in the zip (e.g. a different file that
    happens to share a relative path with something archived in an earlier
    run) is left out of that list -- the caller must not delete it, since
    it was never stored anywhere.
    """
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if zip_path.exists() else "w"

    written: list[Path] = []
    with zipfile.ZipFile(zip_path, mode, zipfile.ZIP_DEFLATED) as zf:
        existing = set(zf.namelist())
        for source in sources:
            # as_posix: zip member names always use "/", so a backslash name
            # would never match `existing` for a file in a subfolder.
            arcname = arcname_for(source, root)
            if arcname in existing:
                continue
            zf.write(source, arcname=arcname)
            written.append(source)

    with zipfile.ZipFile(zip_path, "r") as zf:
        bad_file = zf.testzip()
        if bad_file is not None:
            raise OSError(f"Archive integrity check failed for member '{bad_file}' in {zip_path}")

    return written


def extract_member(zip_path: Path, arcname: str, destination: Path) -> None:
    """Restore one archived file to `destination` (used by undo).

    Opens the destination exclusively, so it fails rather than overwrite
    anything that appeared there since. Restores the original modified time.
    """
    with zipfile.ZipFile(zip_path, "r") as zf:
        info = zf.getinfo(arcname)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, destination.open("xb") as dst:
            shutil.copyfileobj(src, dst)
    mtime = time.mktime((*info.date_time, 0, 0, -1))
    os.utime(destination, (mtime, mtime))


def remove_members(zip_path: Path, arcnames: set[str]) -> None:
    """Drop `arcnames` from the archive (after undo restored them).

    Rewrites to a temp file, verifies it, then swaps it in -- the original
    archive is never left half-written. Deletes the archive if nothing is left.
    """
    with zipfile.ZipFile(zip_path, "r") as zf:
        keep = [info for info in zf.infolist() if info.filename not in arcnames]
        if not keep:
            remaining = None
        else:
            tmp_path = zip_path.with_suffix(".zip.tmp")
            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as out:
                for info in keep:
                    out.writestr(info, zf.read(info))
            remaining = tmp_path

    if remaining is None:
        zip_path.unlink()
        return
    with zipfile.ZipFile(remaining, "r") as check:
        if check.testzip() is not None:
            remaining.unlink()
            raise OSError(f"Rewritten archive failed its integrity check: {zip_path}")
    os.replace(remaining, zip_path)
