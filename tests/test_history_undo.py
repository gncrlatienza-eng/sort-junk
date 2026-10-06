"""Tests for the run history log and executor.undo()."""

from __future__ import annotations

from pathlib import Path

from sortjunk import executor, history, planner, scanner
from sortjunk.categorizer import custom as custom_categorizer
from sortjunk.categorizer import downloads as downloads_categorizer
from sortjunk.config import ScanConfig


def _apply_and_log(target: Path, config: ScanConfig, history_dir: Path, categorizer):
    records = scanner.scan(target, recursive=config.mode != "downloads")
    existing_dirs = (
        scanner.list_top_level_dirs(target, downloads_categorizer.OWNED_TOP_LEVEL_NAMES)
        if config.mode == "downloads"
        else []
    )
    plan = planner.build_plan(
        records, categorizer.categorize(records, config), config, existing_dirs=existing_dirs
    )
    results = executor.apply(plan)
    return history.save_run(plan, results, directory=history_dir)


def test_undo_restores_downloads_sort_exactly(tmp_path, make_file):
    make_file("Downloads/setup.exe", content=b"exe")
    make_file("Downloads/notes.pdf", content=b"pdf")
    make_file("Downloads/Project X/plan.docx", content=b"docx")
    target = tmp_path / "Downloads"
    config = ScanConfig(mode="downloads", target_root=target)
    log = _apply_and_log(target, config, tmp_path / "hist", downloads_categorizer)
    assert (target / "Installers" / "setup.exe").exists()

    results = executor.undo(history.load_run(log))

    assert all(r.succeeded for r in results)
    assert (target / "setup.exe").read_bytes() == b"exe"
    assert (target / "notes.pdf").read_bytes() == b"pdf"
    assert (target / "Project X" / "plan.docx").read_bytes() == b"docx"
    # Folders SortJunk created and that are now empty are cleaned up.
    assert sorted(p.name for p in target.iterdir()) == ["Project X", "notes.pdf", "setup.exe"]


def test_undo_never_overwrites_a_file_now_at_the_original_spot(tmp_path, make_file):
    make_file("Downloads/notes.pdf", content=b"original")
    target = tmp_path / "Downloads"
    config = ScanConfig(mode="downloads", target_root=target)
    log = _apply_and_log(target, config, tmp_path / "hist", downloads_categorizer)
    (target / "notes.pdf").write_bytes(b"new download")

    results = executor.undo(history.load_run(log))

    assert not results[0].succeeded
    assert (target / "notes.pdf").read_bytes() == b"new download"
    assert (target / "PDF" / "notes.pdf").read_bytes() == b"original"


def test_undo_reports_a_file_that_was_moved_away_since(tmp_path, make_file):
    make_file("Downloads/notes.pdf")
    target = tmp_path / "Downloads"
    config = ScanConfig(mode="downloads", target_root=target)
    log = _apply_and_log(target, config, tmp_path / "hist", downloads_categorizer)
    (target / "PDF" / "notes.pdf").unlink()

    results = executor.undo(history.load_run(log))

    assert not results[0].succeeded
    assert "no longer" in results[0].error


def test_undo_restores_archived_files_and_removed_folders(tmp_path, make_file):
    make_file("Custom/sub/old.pdf", content=b"old", age_days=200)
    target = tmp_path / "Custom"
    config = ScanConfig(
        mode="custom", target_root=target, archive_after_days=180, remove_empty_folders=True
    )
    log = _apply_and_log(target, config, tmp_path / "hist", custom_categorizer)
    assert not (target / "sub").exists()

    results = executor.undo(history.load_run(log))

    assert all(r.succeeded for r in results)
    assert (target / "sub" / "old.pdf").read_bytes() == b"old"


def test_latest_run_skips_runs_already_undone(tmp_path, make_file):
    make_file("Downloads/a.pdf")
    target = tmp_path / "Downloads"
    config = ScanConfig(mode="downloads", target_root=target)
    hist = tmp_path / "hist"
    first = _apply_and_log(target, config, hist, downloads_categorizer)
    make_file("Downloads/b.pdf")
    second = _apply_and_log(target, config, hist, downloads_categorizer)

    assert history.latest_run(directory=hist) == second
    history.mark_undone(second)
    assert history.latest_run(directory=hist) == first


def test_latest_run_can_be_limited_to_one_mode_and_folder(tmp_path, make_file):
    hist = tmp_path / "hist"
    make_file("Downloads/a.pdf")
    make_file("Shots/s.png", content=b"img")
    make_file("Other/o.txt")
    downloads = _apply_and_log(
        tmp_path / "Downloads",
        ScanConfig(mode="downloads", target_root=tmp_path / "Downloads"),
        hist,
        downloads_categorizer,
    )
    custom = _apply_and_log(
        tmp_path / "Other",
        ScanConfig(mode="custom", target_root=tmp_path / "Other"),
        hist,
        custom_categorizer,
    )

    assert history.latest_run(directory=hist) == custom
    assert history.latest_run(mode="downloads", directory=hist) == downloads
    assert history.latest_run(mode="screenshots", directory=hist) is None
    assert (
        history.latest_run(
            mode="custom", target_root=tmp_path / "OTHER", directory=hist
        )  # Windows paths are case-insensitive
        == custom
    )
    assert history.latest_run(mode="custom", target_root=tmp_path / "Shots", directory=hist) is None


def test_history_keeps_only_the_newest_runs(tmp_path, make_file, monkeypatch):
    monkeypatch.setattr(history, "_MAX_RUNS_KEPT", 3)
    hist = tmp_path / "hist"
    target = tmp_path / "Downloads"
    config = ScanConfig(mode="downloads", target_root=target)
    logs = []
    for i in range(5):
        make_file(f"Downloads/f{i}.pdf")
        logs.append(_apply_and_log(target, config, hist, downloads_categorizer))

    assert sorted(hist.glob("*.json")) == logs[-3:]
