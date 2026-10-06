"""Data types shared by the scan -> categorize -> plan -> apply pipeline.

Every type here is a plain, JSON-serializable dataclass. None of them carry
behavior that touches the filesystem -- that keeps the plan/apply split
enforceable (see planner.py and executor.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class ActionType(StrEnum):
    MOVE = "move"
    ARCHIVE = "archive"
    FLAG_DUPLICATE = "flag_duplicate"
    SKIP = "skip"
    REMOVE_EMPTY_DIR = "remove_empty_dir"


@dataclass(frozen=True)
class FileRecord:
    """A read-only snapshot of one file found by the scanner."""

    path: Path
    relative_path: str
    size_bytes: int
    modified_at: datetime
    created_at: datetime


@dataclass(frozen=True)
class CategoryDecision:
    """What a categorizer decided about one FileRecord, before any path is computed."""

    record: FileRecord
    category: str
    is_duplicate: bool = False
    dup_group_id: str | None = None
    ocr_used: bool = False
    skip_reason: str | None = None


@dataclass(frozen=True)
class PlannedAction:
    """One row of the dry-run plan: what would happen to one file."""

    action: ActionType
    source: Path
    destination: Path | None
    category: str
    size_bytes: int
    reason: str
    ocr_used: bool = False
    dup_group_id: str | None = None


@dataclass
class Plan:
    """The full, JSON-serializable output of planner.build_plan()."""

    mode: str
    target_root: Path
    generated_at: datetime
    actions: list[PlannedAction] = field(default_factory=list)

    @property
    def total_files(self) -> int:
        # REMOVE_EMPTY_DIR entries are folder cleanup, not scanned files.
        return sum(1 for a in self.actions if a.action != ActionType.REMOVE_EMPTY_DIR)

    @property
    def total_bytes_moved(self) -> int:
        # FLAG_DUPLICATE is included: it's a real shutil.move into Duplicates_Found,
        # not just a label -- see executor._apply_single.
        movable = (ActionType.MOVE, ActionType.ARCHIVE, ActionType.FLAG_DUPLICATE)
        return sum(a.size_bytes for a in self.actions if a.action in movable)


@dataclass(frozen=True)
class ActionResult:
    """What actually happened on disk for one PlannedAction, recorded by the executor."""

    action: ActionType
    source: Path
    destination: Path | None
    category: str
    size_bytes: int
    succeeded: bool
    error: str | None
    executed_at: datetime
