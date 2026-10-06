"""Runtime configuration for a single SortJunk scan."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# (stage, done, total) -- total is 0 when unknown. May raise ScanCancelled.
ProgressCallback = Callable[[str, int, int], None]


@dataclass
class ScanConfig:
    mode: str  # "screenshots" | "downloads" | "custom"
    target_root: Path
    apply: bool = False
    assume_yes: bool = False
    use_ocr: bool = True
    archive_after_days: int = 180
    max_ocr_size_mb: int = 20
    max_files: int = 50_000
    tesseract_cmd: str | None = None
    verbose: bool = False
    # 0 disables the check. Files created more recently than this are left
    # untouched so sorting never disrupts something just downloaded/captured.
    min_age_hours: int = 0
    # Remove a folder once every file inside it has been sorted out of it.
    remove_empty_folders: bool = False
    # Downloads mode: relocate folders the user already had into "My Folders".
    # Off by default -- an app may rely on a folder staying where it is.
    move_existing_folders: bool = False
    on_progress: ProgressCallback | None = field(default=None, repr=False, compare=False)

    def report(self, stage: str, done: int, total: int = 0) -> None:
        if self.on_progress is not None:
            self.on_progress(stage, done, total)
