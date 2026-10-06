"""Tests for executor.apply -- the only disk-mutating entry point."""

from __future__ import annotations

import os
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sortjunk import executor, planner, scanner
from sortjunk.categorizer import custom as custom_categorizer
from sortjunk.categorizer import downloads as downloads_categorizer
from sortjunk.categorizer import screenshots as screenshots_categorizer
from sortjunk.config import ScanConfig
from sortjunk.models import ActionType, Plan, PlannedAction


def _plan_for(target: Path, config: ScanConfig):
    records = scanner.scan(target)
    decisions = downloads_categorizer.categorize(records, config)
    return planner.build_plan(records, decisions, config)


def _screenshots_plan_for(target: Path, config: ScanConfig):
    records = scanner.scan(target)
    decisions = screenshots_categorizer.categorize(records, config)
    return planner.build_plan(records, decisions, config)


def _custom_plan_for(target: Path, config: ScanConfig):
    records = scanner.scan(target)
    decisions = custom_categorizer.categorize(records, config)
    return planner.build_plan(records, decisions, config)


def test_apply_moves_files_to_expected_paths(tmp_path, make_file):
    make_file("Downloads/setup.exe")
    make_file("Downloads/notes.pdf")
    target = tmp_path / "Downloads"
    config = ScanConfig(mode="downloads", target_root=target)

    plan = _plan_for(target, config)
    results = executor.apply(plan)

    assert all(r.succeeded for r in results)
    assert (target / "Installers" / "setup.exe").exists()
    assert (target / "PDF" / "notes.pdf").exists()
    assert not (target / "setup.exe").exists()


def test_apply_never_overwrites_on_collision(tmp_path, make_file):
    make_file("Downloads/setup.exe")
    target = tmp_path / "Downloads"
    config = ScanConfig(mode="downloads", target_root=target)
    plan = _plan_for(target, config)

    # Simulate a collision that appeared after the plan was built.
    (target / "Installers").mkdir(parents=True)
    (target / "Installers" / "setup.exe").write_bytes(b"already here")

    results = executor.apply(plan)
    assert results[0].succeeded
    assert results[0].destination.name == "setup (1).exe"
    assert (target / "Installers" / "setup.exe").read_bytes() == b"already here"


def test_downloads_old_files_move_to_storage_no_zip(tmp_path, make_file):
    """Downloads mode no longer zips aged files -- they're moved as plain
    files into Storage/<category>, organized the same way as fresh sorting."""
    make_file("Downloads/old.pdf", age_days=200)
    target = tmp_path / "Downloads"
    config = ScanConfig(mode="downloads", target_root=target, archive_after_days=180)
    plan = _plan_for(target, config)

    assert plan.actions[0].action == ActionType.MOVE

    results = executor.apply(plan)
    assert results[0].succeeded
    assert not (target / "old.pdf").exists()
    assert (target / "Storage" / "PDF" / "old.pdf").exists()
    assert not any(target.rglob("*.zip"))


def test_screenshots_old_files_sort_per_month_not_zip(tmp_path, make_file):
    """Screenshots mode never archives by age -- an old screenshot sorts into
    <month>/<category> exactly like a fresh one, no zip involved."""
    make_file("Screenshots/old.png", content=b"not a real image", age_days=200)
    target = tmp_path / "Screenshots"
    config = ScanConfig(
        mode="screenshots", target_root=target, archive_after_days=180, use_ocr=False
    )
    plan = _screenshots_plan_for(target, config)

    assert plan.actions[0].action == ActionType.MOVE

    results = executor.apply(plan)
    assert results[0].succeeded
    assert not (target / "old.png").exists()

    month = results[0].destination.parent.parent.name
    assert re.fullmatch(r"\d{4}-\d{2}", month)
    assert (target / month / "Uncategorized" / "old.png").exists()
    assert not any(target.rglob("*.zip"))


def test_archive_batch_writes_zip_then_removes_originals(tmp_path, make_file):
    make_file("Custom/old.pdf", age_days=200)
    target = tmp_path / "Custom"
    config = ScanConfig(mode="custom", target_root=target, archive_after_days=180)
    plan = _custom_plan_for(target, config)

    assert plan.actions[0].action == ActionType.ARCHIVE

    results = executor.apply(plan)
    assert results[0].succeeded
    assert not (target / "old.pdf").exists()

    zip_path = results[0].destination
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as zf:
        assert "old.pdf" in zf.namelist()


def test_archive_failure_does_not_remove_original(tmp_path, make_file, monkeypatch):
    make_file("Custom/old.pdf", age_days=200)
    target = tmp_path / "Custom"
    config = ScanConfig(mode="custom", target_root=target, archive_after_days=180)
    plan = _custom_plan_for(target, config)

    def _boom(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr("sortjunk.executor.archiver.write_archive", _boom)

    results = executor.apply(plan)
    assert not results[0].succeeded
    assert (target / "old.pdf").exists()


def test_rerun_is_idempotent_and_appends_to_existing_archive(tmp_path, make_file):
    make_file("Custom/old1.pdf", age_days=200)
    target = tmp_path / "Custom"
    config = ScanConfig(mode="custom", target_root=target, archive_after_days=180)

    executor.apply(_custom_plan_for(target, config))

    make_file("Custom/old2.pdf", age_days=200)
    results = executor.apply(_custom_plan_for(target, config))

    assert all(r.succeeded for r in results)
    zip_path = next(p for p in (target).rglob("_Archive_*.zip"))
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        assert "old1.pdf" in names
        assert "old2.pdf" in names


def test_archive_arcname_collision_does_not_delete_unarchived_original(tmp_path, make_file):
    """A source whose arcname collides with an existing zip entry must survive on disk.

    Regression test: the executor used to remove every source in an archive
    batch once write_archive() returned, even one skipped from the zip
    because its arcname already existed (e.g. a different file that reused
    the name of something archived in an earlier run). That deleted the
    only copy of the file without ever storing it anywhere.
    """
    make_file("Custom/dup.pdf", content=b"first version", age_days=200)
    target = tmp_path / "Custom"
    config = ScanConfig(mode="custom", target_root=target, archive_after_days=180)

    first_results = executor.apply(_custom_plan_for(target, config))
    assert first_results[0].succeeded
    zip_path = first_results[0].destination
    assert not (target / "dup.pdf").exists()

    # A distinct file reappears at the same relative path, aged into the same
    # month bucket, so it targets the exact same zip entry name.
    make_file("Custom/dup.pdf", content=b"second, unrelated file", age_days=200)
    second_results = executor.apply(_custom_plan_for(target, config))

    assert not second_results[0].succeeded
    assert "already occupies this name" in second_results[0].error
    assert (target / "dup.pdf").exists()
    assert (target / "dup.pdf").read_bytes() == b"second, unrelated file"

    with zipfile.ZipFile(zip_path) as zf:
        assert zf.read("dup.pdf") == b"first version"


def test_move_refuses_a_source_that_became_a_symlink(tmp_path):
    """Regression guard: the scanner rejects symlinks at scan time, but a
    dry-run plan can sit in front of the confirmation dialog for a while --
    the executor must re-check immediately before moving anything, the same
    defense-in-depth already applied to every destination path.
    """
    target = tmp_path / "Downloads"
    target.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside content")
    link_source = target / "setup.exe"
    try:
        os.symlink(outside, link_source)
    except OSError:
        pytest.skip("symlink creation not permitted in this environment")

    plan = Plan(mode="downloads", target_root=target, generated_at=datetime.now(UTC))
    plan.actions.append(
        PlannedAction(
            action=ActionType.MOVE,
            source=link_source,
            destination=target / "Installers" / "setup.exe",
            category="Installers",
            size_bytes=0,
            reason="categorized",
        )
    )

    results = executor.apply(plan)

    assert not results[0].succeeded
    assert "symlink" in results[0].error
    assert link_source.is_symlink()
    assert not (target / "Installers" / "setup.exe").exists()


def test_move_refuses_when_source_is_reported_unsafe(tmp_path, monkeypatch):
    """Same guarantee as test_move_refuses_a_source_that_became_a_symlink,
    but independent of the OS actually permitting symlink creation (many CI
    runners, like this sandbox, don't grant that without elevation).
    """
    target = tmp_path / "Downloads"
    target.mkdir()
    source = target / "setup.exe"
    source.write_bytes(b"a perfectly ordinary file")

    monkeypatch.setattr("sortjunk.executor.is_unsafe_link", lambda path: True)

    plan = Plan(mode="downloads", target_root=target, generated_at=datetime.now(UTC))
    plan.actions.append(
        PlannedAction(
            action=ActionType.MOVE,
            source=source,
            destination=target / "Installers" / "setup.exe",
            category="Installers",
            size_bytes=0,
            reason="categorized",
        )
    )

    results = executor.apply(plan)

    assert not results[0].succeeded
    assert "symlink" in results[0].error
    assert source.exists()
    assert not (target / "Installers" / "setup.exe").exists()


def test_archive_refuses_a_source_that_became_a_symlink(tmp_path):
    target = tmp_path / "Downloads"
    target.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside content")
    link_source = target / "old.pdf"
    try:
        os.symlink(outside, link_source)
    except OSError:
        pytest.skip("symlink creation not permitted in this environment")

    zip_path = target / "_Archive_2024-01" / "_Archive_2024-01.zip"
    plan = Plan(mode="downloads", target_root=target, generated_at=datetime.now(UTC))
    plan.actions.append(
        PlannedAction(
            action=ActionType.ARCHIVE,
            source=link_source,
            destination=zip_path,
            category="Documents",
            size_bytes=0,
            reason="untouched for 180+ days",
        )
    )

    results = executor.apply(plan)

    assert not results[0].succeeded
    assert "symlink" in results[0].error
    assert link_source.is_symlink()
    assert not zip_path.exists()


def _remove_empty_dir_plan(target: Path, source: Path) -> Plan:
    plan = Plan(mode="custom", target_root=target, generated_at=datetime.now(UTC))
    plan.actions.append(
        PlannedAction(
            action=ActionType.REMOVE_EMPTY_DIR,
            source=source,
            destination=None,
            category="Cleanup",
            size_bytes=0,
            reason="folder emptied by sort",
        )
    )
    return plan


def test_remove_empty_dir_removes_an_actually_empty_folder(tmp_path):
    target = tmp_path / "Custom"
    empty_dir = target / "Sub"
    empty_dir.mkdir(parents=True)

    results = executor.apply(_remove_empty_dir_plan(target, empty_dir))

    assert results[0].succeeded
    assert not empty_dir.exists()


def test_remove_empty_dir_leaves_a_non_empty_folder_in_place(tmp_path):
    target = tmp_path / "Custom"
    occupied_dir = target / "Sub"
    occupied_dir.mkdir(parents=True)
    (occupied_dir / "surprise.txt").write_text("still here")

    results = executor.apply(_remove_empty_dir_plan(target, occupied_dir))

    # Not a failure -- a folder that turned out not to be empty is correctly
    # left alone, never force-removed.
    assert results[0].succeeded
    assert occupied_dir.exists()
    assert (occupied_dir / "surprise.txt").exists()


def test_ocr_unavailable_path_end_to_end(tmp_path, make_file):
    make_file("Screenshots/shot.png", content=b"not a real image")
    target = tmp_path / "Screenshots"
    config = ScanConfig(mode="screenshots", target_root=target, use_ocr=False)

    records = scanner.scan(target)
    decisions = screenshots_categorizer.categorize(records, config)
    plan = planner.build_plan(records, decisions, config)

    results = executor.apply(plan)
    assert all(r.succeeded for r in results)

    action = plan.actions[0]
    assert action.ocr_used is False
    assert action.category == "Uncategorized"
