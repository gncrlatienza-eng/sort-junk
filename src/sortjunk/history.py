"""Run history: a JSON log of every applied run, so it can be reviewed or undone.

Logs live in %LOCALAPPDATA%\\SortJunk\\history -- never inside the folder
being sorted, so they can't be swept up by a later scan. This module only
ever writes its own log files; it never touches the user's files (undo is
done by executor.undo, the sole module allowed to do that).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .models import ActionResult, ActionType, Plan
from .settings import app_data_dir

_FORMAT_VERSION = 1
# Undo only ever needs the latest run per mode/folder; ~a year of weekly
# auto-cleans of two folders, plus manual runs, fits comfortably.
_MAX_RUNS_KEPT = 100


@dataclass(frozen=True)
class RunLog:
    path: Path
    mode: str
    target_root: Path
    applied_at: datetime
    results: list[ActionResult]
    undone_at: datetime | None = None
    trigger: str = "manual"  # "manual" | "auto"

    @property
    def undoable_count(self) -> int:
        return sum(1 for r in self.results if r.succeeded and r.action != ActionType.SKIP)


def history_dir() -> Path:
    return app_data_dir() / "history"


def _result_to_json(r: ActionResult) -> dict:
    return {
        "action": r.action.value,
        "source": str(r.source),
        "destination": str(r.destination) if r.destination else None,
        "category": r.category,
        "size_bytes": r.size_bytes,
        "succeeded": r.succeeded,
        "error": r.error,
        "executed_at": r.executed_at.isoformat(),
    }


def _result_from_json(d: dict) -> ActionResult:
    return ActionResult(
        action=ActionType(d["action"]),
        source=Path(d["source"]),
        destination=Path(d["destination"]) if d["destination"] else None,
        category=d["category"],
        size_bytes=d["size_bytes"],
        succeeded=d["succeeded"],
        error=d["error"],
        executed_at=datetime.fromisoformat(d["executed_at"]),
    )


def save_run(
    plan: Plan,
    results: list[ActionResult],
    *,
    directory: Path | None = None,
    trigger: str = "manual",
) -> Path | None:
    """Write the log for one applied run. Returns None if nothing actually changed."""
    if not any(r.succeeded and r.action != ActionType.SKIP for r in results):
        return None
    directory = directory or history_dir()
    directory.mkdir(parents=True, exist_ok=True)
    applied_at = datetime.now(UTC)
    path = directory / f"{applied_at:%Y%m%d-%H%M%S-%f}_{plan.mode}.json"
    payload = {
        "format": _FORMAT_VERSION,
        "mode": plan.mode,
        "target_root": str(plan.target_root),
        "applied_at": applied_at.isoformat(),
        "undone_at": None,
        "trigger": trigger,
        "results": [_result_to_json(r) for r in results],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _prune(directory)
    return path


def _prune(directory: Path) -> None:
    """Keep only the newest runs, so history (and the Undo button's lookups) stay small."""
    for old in sorted(directory.glob("*.json"), reverse=True)[_MAX_RUNS_KEPT:]:
        try:
            old.unlink()
        except OSError:
            pass


def load_run(path: Path) -> RunLog:
    data = json.loads(path.read_text(encoding="utf-8"))
    undone = data.get("undone_at")
    return RunLog(
        path=path,
        mode=data["mode"],
        target_root=Path(data["target_root"]),
        applied_at=datetime.fromisoformat(data["applied_at"]),
        results=[_result_from_json(r) for r in data["results"]],
        undone_at=datetime.fromisoformat(undone) if undone else None,
        trigger=data.get("trigger", "manual"),
    )


def latest_run(
    *,
    mode: str | None = None,
    target_root: Path | None = None,
    directory: Path | None = None,
) -> Path | None:
    """The most recent run that hasn't been undone yet, or None.

    `mode` / `target_root` narrow it to one folder's runs, so undoing in
    Screenshots never reverses a newer Downloads (or auto-clean) run.
    """
    directory = directory or history_dir()
    if not directory.is_dir():
        return None
    wanted_root = _norm(target_root) if target_root is not None else None
    for path in sorted(directory.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("undone_at") is not None:
            continue
        if mode is not None and data.get("mode") != mode:
            continue
        if wanted_root is not None and _norm(Path(data.get("target_root", ""))) != wanted_root:
            continue
        return path
    return None


def _norm(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def last_run(*, trigger: str, directory: Path | None = None) -> RunLog | None:
    """The most recent run with this trigger, undone or not (for "last auto-clean" info)."""
    directory = directory or history_dir()
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob("*.json"), reverse=True):
        try:
            run = load_run(path)
        except (OSError, ValueError, KeyError):
            continue
        if run.trigger == trigger:
            return run
    return None


def mark_undone(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["undone_at"] = datetime.now(UTC).isoformat()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
