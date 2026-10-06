"""Downloads mode: pre-existing user folders are relocated whole into "My
Folders", never opened/re-sorted, while loose top-level files still get
categorized normally.
"""

from __future__ import annotations

from sortjunk import executor, planner, scanner
from sortjunk.categorizer import downloads as downloads_categorizer
from sortjunk.config import ScanConfig
from sortjunk.models import ActionType


def _plan_for(target, config):
    records = scanner.scan(target, recursive=False)
    existing_dirs = scanner.list_top_level_dirs(
        target,
        excluded_names=downloads_categorizer.OWNED_TOP_LEVEL_NAMES,
        excluded_prefixes=("_Archive_",),
    )
    decisions = downloads_categorizer.categorize(records, config)
    return planner.build_plan(records, decisions, config, existing_dirs=existing_dirs)


def test_nonrecursive_scan_ignores_subfolder_contents(tmp_path, make_file):
    make_file("Downloads/report.pdf")
    make_file("Downloads/Work Stuff/notes.txt")
    target = tmp_path / "Downloads"

    records = scanner.scan(target, recursive=False)

    assert [r.path.name for r in records] == ["report.pdf"]


def test_existing_user_folder_is_relocated_whole_and_untouched(tmp_path, make_file):
    make_file("Downloads/report.pdf")
    make_file("Downloads/Work Stuff/notes.txt", content=b"keep me exactly as-is")
    make_file("Downloads/Work Stuff/Sub/deep.txt")
    target = tmp_path / "Downloads"
    config = ScanConfig(mode="downloads", target_root=target)

    plan = _plan_for(target, config)
    move_actions = {a.source.name: a for a in plan.actions if a.action == ActionType.MOVE}

    assert "Work Stuff" in move_actions
    assert move_actions["Work Stuff"].destination == target / "My Folders" / "Work Stuff"
    assert move_actions["report.pdf"].destination == target / "PDF" / "report.pdf"

    results = executor.apply(plan)
    assert all(r.succeeded for r in results)

    relocated = target / "My Folders" / "Work Stuff"
    assert (relocated / "notes.txt").read_bytes() == b"keep me exactly as-is"
    assert (relocated / "Sub" / "deep.txt").exists()
    assert not (target / "Work Stuff").exists()


def test_already_sorted_folders_are_not_treated_as_user_folders(tmp_path, make_file):
    make_file("Downloads/Installers/setup.exe")
    make_file("Downloads/Storage/PDF/old.pdf")
    make_file("Downloads/My Folders/Vacation Photos/pic.jpg")
    target = tmp_path / "Downloads"

    existing_dirs = scanner.list_top_level_dirs(
        target,
        excluded_names=downloads_categorizer.OWNED_TOP_LEVEL_NAMES,
        excluded_prefixes=("_Archive_",),
    )

    assert existing_dirs == []


def test_rerun_does_not_re_move_an_already_relocated_folder(tmp_path, make_file):
    make_file("Downloads/My Folders/Vacation Photos/pic.jpg")
    target = tmp_path / "Downloads"
    config = ScanConfig(mode="downloads", target_root=target)

    plan = _plan_for(target, config)

    assert plan.actions == []
