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
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from . import archiver
from .exceptions import PathSafetyError
from .models import ActionResult, ActionType, Plan, PlannedAction
from .pathsafety import is_unsafe_link, resolve_and_validate_containment, resolve_collision

logger = logging.getLogger(__name__)


def apply(plan: Plan) -> list[ActionResult]:
    """Execute every action in `plan`. This is the sole entry point that touches disk."""
    results: list[ActionResult] = []

    archive_actions = [a for a in plan.actions if a.action == ActionType.ARCHIVE]
    other_actions = [a for a in plan.actions if a.action != ActionType.ARCHIVE]

    for action in other_actions:
        results.append(_apply_single(action, plan.target_root))

    results.extend(_apply_archive_batch(archive_actions, plan.target_root))

    return results


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
        except (OSError, PathSafetyError) as exc:
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
