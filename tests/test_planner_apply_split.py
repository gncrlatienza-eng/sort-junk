"""Enforcement tests for the plan/apply split.

`build_plan()` must never touch disk, and no disk-mutating call may exist
outside executor.py -- these tests are a regression guard against that
property quietly breaking as the codebase grows.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

from sortjunk import planner, scanner
from sortjunk.categorizer import custom as custom_categorizer
from sortjunk.categorizer import downloads as downloads_categorizer
from sortjunk.categorizer import screenshots as screenshots_categorizer
from sortjunk.config import ScanConfig
from sortjunk.models import ActionType, CategoryDecision, FileRecord


def _make_record(root, rel_path: str, *, age_hours: float = 1000.0, size: int = 10) -> FileRecord:
    """A synthetic FileRecord for planner-only tests -- doesn't need to exist on disk.

    Real file creation time (Windows ctime) can't be portably backdated the
    way `make_file(age_days=...)` backdates mtime, so freshness tests build
    records directly instead of going through the real scanner.
    """
    created = datetime.now(UTC) - timedelta(hours=age_hours)
    return FileRecord(
        path=root / rel_path,
        relative_path=rel_path,
        size_bytes=size,
        modified_at=created,
        created_at=created,
    )


_DISK_MUTATING_PATTERNS = (
    "shutil.move",
    "os.rename(",
    "os.remove(",
    "os.unlink(",
    "os.rmdir(",
    '"w")',
    "'w')",
)

_MODULES_THAT_MUST_NOT_MUTATE_DISK = [
    scanner,
    planner,
    downloads_categorizer,
    screenshots_categorizer,
    custom_categorizer,
]


def test_no_disk_mutating_calls_outside_executor():
    for module in _MODULES_THAT_MUST_NOT_MUTATE_DISK:
        source = inspect.getsource(module)
        for pattern in _DISK_MUTATING_PATTERNS:
            assert (
                pattern not in source
            ), f"{module.__name__} contains disk-mutating call: {pattern}"


def test_build_plan_does_not_touch_disk(tmp_path, make_file):
    make_file("Downloads/report.pdf")
    make_file("Downloads/movie.mp4")
    target = tmp_path / "Downloads"
    config = ScanConfig(mode="downloads", target_root=target)

    records = scanner.scan(target)
    before = sorted(p.name for p in target.rglob("*") if p.is_file())

    decisions = downloads_categorizer.categorize(records, config)
    plan = planner.build_plan(records, decisions, config)

    after = sorted(p.name for p in target.rglob("*") if p.is_file())
    assert before == after
    assert plan.total_files == len(records)


def test_fresh_file_is_skipped_when_min_age_set(tmp_path):
    target = tmp_path / "Downloads"
    target.mkdir()
    fresh = _make_record(target, "fresh.pdf", age_hours=1)
    old = _make_record(target, "old.pdf", age_hours=240)
    config = ScanConfig(mode="downloads", target_root=target, min_age_hours=24)
    decisions = [
        CategoryDecision(record=fresh, category="Documents"),
        CategoryDecision(record=old, category="Documents"),
    ]

    plan = planner.build_plan([fresh, old], decisions, config)

    by_name = {a.source.name: a for a in plan.actions}
    assert by_name["fresh.pdf"].action == ActionType.SKIP
    assert "last" in by_name["fresh.pdf"].reason
    assert by_name["old.pdf"].action == ActionType.MOVE


def test_min_age_hours_zero_disables_freshness_check(tmp_path):
    target = tmp_path / "Downloads"
    target.mkdir()
    fresh = _make_record(target, "fresh.pdf", age_hours=0.01)
    config = ScanConfig(mode="downloads", target_root=target, min_age_hours=0)
    decisions = [CategoryDecision(record=fresh, category="Documents")]

    plan = planner.build_plan([fresh], decisions, config)

    assert plan.actions[0].action == ActionType.MOVE


def test_empty_folder_candidates_computed_deepest_first(tmp_path):
    target = tmp_path / "Custom"
    target.mkdir()
    deep = _make_record(target, "Sub/Sub2/report.pdf")
    keep = _make_record(target, "Sub/keepme.txt")
    config = ScanConfig(mode="custom", target_root=target, remove_empty_folders=True)
    decisions = [
        CategoryDecision(record=deep, category="Documents"),
        CategoryDecision(record=keep, category="Documents", skip_reason="keep me"),
    ]

    plan = planner.build_plan([deep, keep], decisions, config)

    cleanup_dirs = {a.source for a in plan.actions if a.action == ActionType.REMOVE_EMPTY_DIR}
    assert target / "Sub" / "Sub2" in cleanup_dirs
    assert target / "Sub" not in cleanup_dirs  # keepme.txt is staying there
