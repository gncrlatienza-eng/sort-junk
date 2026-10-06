"""The only module in SortJunk permitted to mutate the user's files.

Every destination is re-validated for containment (and, for moves,
re-resolved for filename collisions) immediately before use -- defense in
depth against staleness between plan build and apply, and against bugs
upstream. Archives are written and integrity-checked before their source
files are ever removed.
"""

from __future__ import annotations

import logging
import os
import shutil
import zipfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from . import archiver, target_guard
from .exceptions import PathSafetyError
from .history import RunLog
from .models import ActionResult, ActionType, Plan, PlannedAction
from .pathsafety import is_unsafe_link, resolve_and_validate_containment, resolve_collision

logger = logging.getLogger(__name__)


def apply(plan: Plan) -> list[ActionResult]:
    """Execute every action in `plan`. This is the sole entry point that touches disk.

    Raises UnsafeTargetError, before touching anything, if the plan's root
    is a folder SortJunk refuses to sort (see target_guard).
    """
    target_guard.ensure_safe_target(plan.target_root)
    results: list[ActionResult] = []

    archive_actions = [a for a in plan.actions if a.action == ActionType.ARCHIVE]
    cleanup_actions = [a for a in plan.actions if a.action == ActionType.REMOVE_EMPTY_DIR]
    deferred = (ActionType.ARCHIVE, ActionType.REMOVE_EMPTY_DIR)
    other_actions = [a for a in plan.actions if a.action not in deferred]

    for action in other_actions:
        results.append(_apply_single(action, plan.target_root))

    results.extend(_apply_archive_batch(archive_actions, plan.target_root))

    # Last, so a folder emptied only by archiving is actually empty by now.
    for action in cleanup_actions:
        results.append(_apply_remove_empty_dir(action, plan.target_root))

    return results


def undo(run: RunLog) -> list[ActionResult]:
    """Reverse a logged run, newest action first. Never overwrites anything.

    A file is only moved back if it is still where SortJunk put it and its
    original spot is free; otherwise that one item is reported as failed and
    left where it is. Folders SortJunk created that end up empty are removed.
    """
    root = run.target_root
    target_guard.ensure_safe_target(root)

    results: list[ActionResult] = []
    restored_from_zip: dict[Path, set[str]] = defaultdict(set)
    touched_dirs: set[Path] = set()

    for done in reversed(run.results):
        if not done.succeeded or done.action == ActionType.SKIP:
            continue
        try:
            original = resolve_and_validate_containment(root, done.source)
            if done.action == ActionType.REMOVE_EMPTY_DIR:
                original.mkdir(parents=True, exist_ok=True)
            elif done.action == ActionType.ARCHIVE:
                zip_path = resolve_and_validate_containment(root, done.destination)
                if original.exists():
                    raise _UndoSkipped(f"something new is already at {original}")
                arcname = archiver.arcname_for(original, root.resolve())
                archiver.extract_member(zip_path, arcname, original)
                restored_from_zip[zip_path].add(arcname)
            else:
                current = resolve_and_validate_containment(root, done.destination)
                if not current.exists():
                    raise _UndoSkipped(f"no longer at {current} -- it was moved or deleted")
                if is_unsafe_link(current):
                    raise _UndoSkipped(f"{current} is now a symlink/junction")
                if original.exists():
                    raise _UndoSkipped(f"something new is already at {original}; left at {current}")
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(current), str(original))
                touched_dirs.add(current.parent)
            results.append(_undo_result(done, error=None))
        except (OSError, KeyError, PathSafetyError, zipfile.BadZipFile, _UndoSkipped) as exc:
            logger.error("Could not undo %s: %s", done.source, exc)
            results.append(_undo_result(done, error=str(exc)))

    for zip_path, arcnames in restored_from_zip.items():
        try:
            archiver.remove_members(zip_path, arcnames)
            touched_dirs.add(zip_path.parent)
        except (OSError, zipfile.BadZipFile) as exc:
            # The files are already restored; a leftover copy in the zip is harmless.
            logger.warning("Restored files but could not tidy %s: %s", zip_path, exc)

    for directory in sorted(touched_dirs, key=lambda p: len(p.parts), reverse=True):
        _prune_empty_dirs_up_to(directory, root)

    return results


class _UndoSkipped(Exception):
    """One item couldn't be safely undone; it's reported and left as-is."""


def _undo_result(done: ActionResult, error: str | None) -> ActionResult:
    return ActionResult(
        action=done.action,
        source=done.destination or done.source,
        destination=done.source,
        category=done.category,
        size_bytes=done.size_bytes,
        succeeded=error is None,
        error=error,
        executed_at=datetime.now(UTC),
    )


def _prune_empty_dirs_up_to(directory: Path, root: Path) -> None:
    """Remove `directory` and its empty parents, stopping at (never removing) `root`."""
    root_norm = os.path.normcase(str(root.resolve()))
    current = directory.resolve()
    while os.path.normcase(str(current)).startswith(root_norm + os.sep):
        try:
            os.rmdir(current)
        except OSError:
            return  # not empty (or gone) -- stop here
        current = current.parent


def _apply_single(action: PlannedAction, root: Path) -> ActionResult:
    if action.action == ActionType.SKIP:
        return ActionResult(
            action=action.action,
            source=action.source,
            destination=None,
            category=action.category,
            size_bytes=action.size_bytes,
            succeeded=True,
            error=None,
            executed_at=datetime.now(UTC),
        )

    if action.action == ActionType.REMOVE_EMPTY_DIR:
        return _apply_remove_empty_dir(action, root)

    if is_unsafe_link(action.source):
        # The scanner already excludes symlinks/junctions, but the plan may
        # be minutes old by the time this runs (a dry-run preview sits in
        # front of the confirmation) -- re-check immediately before moving
        # anything, same defense-in-depth principle already applied to
        # every destination path.
        logger.error("Refusing to move symlink/junction: %s", action.source)
        return ActionResult(
            action=action.action,
            source=action.source,
            destination=action.destination,
            category=action.category,
            size_bytes=action.size_bytes,
            succeeded=False,
            error="source is a symlink/junction -- refusing to move for safety",
            executed_at=datetime.now(UTC),
        )

    try:
        destination = resolve_and_validate_containment(root, action.destination)
        destination = resolve_collision(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(action.source), str(destination))
        return ActionResult(
            action=action.action,
            source=action.source,
            destination=destination,
            category=action.category,
            size_bytes=action.size_bytes,
            succeeded=True,
            error=None,
            executed_at=datetime.now(UTC),
        )
    except (OSError, PathSafetyError) as exc:
        logger.error("Failed to apply action for %s: %s", action.source, exc)
        return ActionResult(
            action=action.action,
            source=action.source,
            destination=action.destination,
            category=action.category,
            size_bytes=action.size_bytes,
            succeeded=False,
            error=str(exc),
            executed_at=datetime.now(UTC),
        )


def _apply_remove_empty_dir(action: PlannedAction, root: Path) -> ActionResult:
    """Remove a folder the plan predicted would be empty.

    Not a failure if it turns out not to be empty (something unexpected
    landed there) -- the attempt just silently no-ops; nothing is ever
    force-removed. A real containment violation is a genuine failure.
    """
    try:
        resolved = resolve_and_validate_containment(root, action.source)
    except PathSafetyError as exc:
        logger.error("Refusing to remove %s: %s", action.source, exc)
        return ActionResult(
            action=action.action,
            source=action.source,
            destination=None,
            category=action.category,
            size_bytes=action.size_bytes,
            succeeded=False,
            error=str(exc),
            executed_at=datetime.now(UTC),
        )

    try:
        os.rmdir(resolved)
    except OSError:
        pass  # not actually empty, or already gone -- fine, leave it

    return ActionResult(
        action=action.action,
        source=action.source,
        destination=None,
        category=action.category,
        size_bytes=action.size_bytes,
        succeeded=True,
        error=None,
        executed_at=datetime.now(UTC),
    )


def _apply_archive_batch(actions: list[PlannedAction], root: Path) -> list[ActionResult]:
    results: list[ActionResult] = []
    by_zip: dict[Path, list[PlannedAction]] = defaultdict(list)
    for action in actions:
        by_zip[action.destination].append(action)

    for raw_zip_path, batch in by_zip.items():
        safe_batch = []
        for action in batch:
            if action.source.exists() and is_unsafe_link(action.source):
                logger.error("Refusing to archive symlink/junction: %s", action.source)
                result = ActionResult(
                    action=action.action,
                    source=action.source,
                    destination=raw_zip_path,
                    category=action.category,
                    size_bytes=action.size_bytes,
                    succeeded=False,
                    error="source is a symlink/junction -- refusing to archive for safety",
                    executed_at=datetime.now(UTC),
                )
                results.append(result)
                continue
            safe_batch.append(action)

        try:
            zip_path = resolve_and_validate_containment(root, raw_zip_path)
            sources = [a.source for a in safe_batch if a.source.exists()]
            written = set(archiver.write_archive(zip_path, root, sources))
        except (OSError, PathSafetyError, zipfile.BadZipFile) as exc:
            logger.error("Failed to write archive %s: %s", raw_zip_path, exc)
            for action in safe_batch:
                result = ActionResult(
                    action=action.action,
                    source=action.source,
                    destination=raw_zip_path,
                    category=action.category,
                    size_bytes=action.size_bytes,
                    succeeded=False,
                    error=str(exc),
                    executed_at=datetime.now(UTC),
                )
                results.append(result)
            continue

        # Zip write + integrity check succeeded -- now, and only now, remove originals
        # that actually made it into the zip. A source left out of `written` (its
        # arcname collided with an unrelated entry already in the zip) must survive
        # on disk -- deleting it would destroy the only copy of that file.
        for action in safe_batch:
            error: str | None = None
            succeeded = True
            if action.source.exists() and action.source not in written:
                succeeded = False
                error = (
                    f"skipped: another archived file already occupies this name in "
                    f"{zip_path.name}; original left in place"
                )
                logger.error(
                    "Not removing %s: arcname collision in %s left it unarchived",
                    action.source,
                    zip_path,
                )
            else:
                try:
                    if action.source.exists():
                        os.remove(action.source)
                except OSError as exc:
                    succeeded = False
                    error = str(exc)
                    logger.error(
                        "Archived but could not remove original %s: %s", action.source, exc
                    )

            result = ActionResult(
                action=action.action,
                source=action.source,
                destination=zip_path,
                category=action.category,
                size_bytes=action.size_bytes,
                succeeded=succeeded,
                error=error,
                executed_at=datetime.now(UTC),
            )
            results.append(result)

    return results
