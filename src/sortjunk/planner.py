"""Pure plan construction: turns FileRecords + CategoryDecisions into a Plan.

Zero filesystem mutation. The only I/O this module performs is via
pathsafety's existence/resolve checks (reads), required to compute a
collision-free destination. This module never moves, renames, deletes,
or archives a file -- see executor.py, which is the sole module allowed
to do that.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import ScanConfig
from .models import ActionType, CategoryDecision, FileRecord, Plan, PlannedAction
from .pathsafety import paths_equal, resolve_and_validate_containment, safe_destination_for

DUPLICATES_SUBDIR = "Duplicates_Found"
# Downloads mode only: where files aged past archive_after_days land, as
# plain moved files (no zip) organized by the same categories as fresh sorting.
STORAGE_SUBDIR = "Storage"
# Downloads mode only: where a folder the user already had in Downloads gets
# relocated whole -- its contents are never opened or re-sorted.
EXISTING_FOLDERS_SUBDIR = "My Folders"


def _is_archive_eligible(record: FileRecord, config: ScanConfig) -> bool:
    if record.cloud_only and config.mode == "custom":
        return False  # zipping reads the file, which would download it from OneDrive
    age = datetime.now(UTC) - record.modified_at
    return age >= timedelta(days=config.archive_after_days)


def _is_too_fresh(record: FileRecord, config: ScanConfig) -> bool:
    """True if `record` was created too recently to sort yet.

    Uses `created_at`, not `modified_at`: a downloaded file's modified time
    often reflects the *source* file's original timestamp (a server's
    Last-Modified header, or embedded metadata), not when it landed here --
    `created_at` is the actual "how long has this been sitting in this
    folder" signal. `min_age_hours == 0` disables the check entirely.
    """
    if config.min_age_hours <= 0:
        return False
    age = datetime.now(UTC) - record.created_at
    return age < timedelta(hours=config.min_age_hours)


def _empty_dir_candidates(actions: list[PlannedAction], root: Path) -> list[Path]:
    """Directories (relative to `root`) that end up with nothing left in them.

    A directory qualifies unless at least one action inside it is a SKIP
    (in-progress download, already-sorted, already-flagged duplicate, or
    too-fresh) -- any of those means something real is still there. Every
    ancestor directory of every action's source is considered, so a
    directory left empty only because a now-also-empty subdirectory was
    removed still qualifies. Sorted deepest-first so the executor's
    sequential removal naturally cascades in one pass.
    """
    all_dirs: set[Path] = set()
    blocked_dirs: set[Path] = set()
    for action in actions:
        try:
            rel_parts = action.source.relative_to(root).parts[:-1]
        except ValueError:
            continue
        for i in range(1, len(rel_parts) + 1):
            directory = Path(*rel_parts[:i])
            all_dirs.add(directory)
            if action.action == ActionType.SKIP:
                blocked_dirs.add(directory)

    candidates = all_dirs - blocked_dirs
    return sorted(candidates, key=lambda p: len(p.parts), reverse=True)


def _archive_zip_path(config: ScanConfig, record: FileRecord) -> Path:
    """All files aged into the same month share one archive zip."""
    bucket = record.modified_at.strftime("%Y-%m")
    candidate = config.target_root / f"_Archive_{bucket}" / f"_Archive_{bucket}.zip"
    return resolve_and_validate_containment(config.target_root, candidate)


def _category_subpath(decision: CategoryDecision, config: ScanConfig) -> str:
    if config.mode == "screenshots":
        month = decision.record.modified_at.strftime("%Y-%m")
        return f"{month}/{decision.category}"
    return decision.category


def build_plan(
    records: list[FileRecord],
    decisions: list[CategoryDecision],
    config: ScanConfig,
    existing_dirs: list[Path] | None = None,
) -> Plan:
    """Decide, for every file, whether to skip / flag-as-duplicate / archive / move.

    Precedence: a file too fresh to sort yet (min_age_hours) wins over
    everything; then an explicit skip (e.g. in-progress download); a
    flagged duplicate is routed for review rather than silently archived;
    only then does age-based archiving apply (Downloads: moved into Storage;
    Custom: zipped) -- anything left gets a normal categorized move. In
    Screenshots mode, age never triggers archiving at all: every screenshot,
    however old, sorts into `<month>/<category>` like a fresh one -- age-based
    archiving is skipped, not merely redirected. If `remove_empty_folders` is
    set, a final pass adds cleanup actions for any directory left with
    nothing in it.

    `existing_dirs` (Downloads mode only): top-level folders the user already
    had in the target root, found by `scanner.list_top_level_dirs`. Each is
    planned as a single whole-folder move into `EXISTING_FOLDERS_SUBDIR` --
    its contents are never scanned, categorized, or otherwise touched.
    """
    plan = Plan(
        mode=config.mode,
        target_root=config.target_root,
        generated_at=datetime.now(UTC),
    )

    decisions_by_path = {d.record.path: d for d in decisions}
    # The copy each duplicate group keeps in place, to name it in the reason.
    keeper_names = {
        d.dup_group_id: d.record.relative_path
        for d in decisions
        if d.dup_group_id is not None and not d.is_duplicate
    }

    for record in records:
        decision = decisions_by_path[record.path]

        if _is_too_fresh(record, config):
            fresh_reason = (
                f"created within the last {config.min_age_hours}h -- left for a later run"
            )
            plan.actions.append(
                PlannedAction(
                    action=ActionType.SKIP,
                    source=record.path,
                    destination=None,
                    category=decision.category,
                    size_bytes=record.size_bytes,
                    reason=fresh_reason,
                )
            )
            continue

        if decision.skip_reason:
            plan.actions.append(
                PlannedAction(
                    action=ActionType.SKIP,
                    source=record.path,
                    destination=None,
                    category=decision.category,
                    size_bytes=record.size_bytes,
                    reason=decision.skip_reason,
                )
            )
            continue

        if decision.is_duplicate:
            destination = safe_destination_for(
                config.target_root, DUPLICATES_SUBDIR, record.path.name, source=record.path
            )
            if paths_equal(destination, record.path):
                plan.actions.append(
                    PlannedAction(
                        action=ActionType.SKIP,
                        source=record.path,
                        destination=None,
                        category=decision.category,
                        size_bytes=record.size_bytes,
                        reason="already flagged as a duplicate",
                    )
                )
                continue
            plan.actions.append(
                PlannedAction(
                    action=ActionType.FLAG_DUPLICATE,
                    source=record.path,
                    destination=destination,
                    category=decision.category,
                    size_bytes=record.size_bytes,
                    reason=(
                        f"duplicate of {keeper_names[decision.dup_group_id]} (that copy is kept)"
                        if decision.dup_group_id in keeper_names
                        else "duplicate"
                    ),
                    ocr_used=decision.ocr_used,
                    dup_group_id=decision.dup_group_id,
                )
            )
            continue

        if _is_archive_eligible(record, config) and config.mode != "screenshots":
            if config.mode == "downloads":
                destination = safe_destination_for(
                    config.target_root,
                    f"{STORAGE_SUBDIR}/{decision.category}",
                    record.path.name,
                    source=record.path,
                )
                if paths_equal(destination, record.path):
                    plan.actions.append(
                        PlannedAction(
                            action=ActionType.SKIP,
                            source=record.path,
                            destination=None,
                            category=decision.category,
                            size_bytes=record.size_bytes,
                            reason="already in Storage",
                        )
                    )
                    continue
                plan.actions.append(
                    PlannedAction(
                        action=ActionType.MOVE,
                        source=record.path,
                        destination=destination,
                        category=decision.category,
                        size_bytes=record.size_bytes,
                        reason=(
                            f"untouched for {config.archive_after_days}+ days -- "
                            "moved to Storage"
                        ),
                        ocr_used=decision.ocr_used,
                    )
                )
                continue
            plan.actions.append(
                PlannedAction(
                    action=ActionType.ARCHIVE,
                    source=record.path,
                    destination=_archive_zip_path(config, record),
                    category=decision.category,
                    size_bytes=record.size_bytes,
                    reason=f"untouched for {config.archive_after_days}+ days",
                    ocr_used=decision.ocr_used,
                )
            )
            continue

        destination = safe_destination_for(
            config.target_root,
            _category_subpath(decision, config),
            record.path.name,
            source=record.path,
        )
        if paths_equal(destination, record.path):
            plan.actions.append(
                PlannedAction(
                    action=ActionType.SKIP,
                    source=record.path,
                    destination=None,
                    category=decision.category,
                    size_bytes=record.size_bytes,
                    reason="already sorted",
                )
            )
            continue
        plan.actions.append(
            PlannedAction(
                action=ActionType.MOVE,
                source=record.path,
                destination=destination,
                category=decision.category,
                size_bytes=record.size_bytes,
                reason="categorized",
                ocr_used=decision.ocr_used,
            )
        )

    for directory in existing_dirs or []:
        destination = safe_destination_for(
            config.target_root, EXISTING_FOLDERS_SUBDIR, directory.name, source=directory
        )
        if paths_equal(destination, directory):
            continue
        plan.actions.append(
            PlannedAction(
                action=ActionType.MOVE,
                source=directory,
                destination=destination,
                category=EXISTING_FOLDERS_SUBDIR,
                size_bytes=0,
                reason="existing folder -- moved as-is, contents untouched",
            )
        )

    if config.remove_empty_folders:
        for rel_dir in _empty_dir_candidates(plan.actions, config.target_root):
            plan.actions.append(
                PlannedAction(
                    action=ActionType.REMOVE_EMPTY_DIR,
                    source=config.target_root / rel_dir,
                    destination=None,
                    category="Cleanup",
                    size_bytes=0,
                    reason="folder emptied by sort",
                )
            )

    return plan
