"""Archive-zip writing: write, verify, and only then let the caller remove originals.

Only called by executor.py, the sole module permitted to mutate disk.
"""

from __future__ import annotations

import zipfile
from pathlib import Path


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
            arcname = str(source.relative_to(root))
            if arcname in existing:
                continue
            zf.write(source, arcname=arcname)
            written.append(source)

    with zipfile.ZipFile(zip_path, "r") as zf:
        bad_file = zf.testzip()
        if bad_file is not None:
            raise OSError(f"Archive integrity check failed for member '{bad_file}' in {zip_path}")

    return written
