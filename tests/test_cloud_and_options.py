"""OneDrive online-only files, the Downloads "My Folders" option, and self-exclusion."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sortjunk import hashing, pathsafety, pipeline, planner
from sortjunk.categorizer import custom as custom_categorizer
from sortjunk.categorizer import downloads as downloads_categorizer
from sortjunk.config import ScanConfig
from sortjunk.models import ActionType, FileRecord

_CLOUD_TAG = 0x9000601A  # IO_REPARSE_TAG_CLOUD_6 -- what OneDrive placeholders use
_REPARSE = 0x400
_RECALL_ON_DATA_ACCESS = 0x400000


def _record(root, name, *, cloud_only=False, age_days=1.0, size=10) -> FileRecord:
    when = datetime.now(UTC) - timedelta(days=age_days)
    return FileRecord(
        path=root / name,
        relative_path=name,
        size_bytes=size,
        modified_at=when,
        created_at=when,
        cloud_only=cloud_only,
    )


def test_online_only_attributes_are_detected():
    online = SimpleNamespace(st_file_attributes=_REPARSE | _RECALL_ON_DATA_ACCESS)
    local = SimpleNamespace(st_file_attributes=0x20)
    assert pathsafety.is_cloud_only(online)
    assert not pathsafety.is_cloud_only(local)


def test_onedrive_placeholder_is_not_treated_as_a_symlink(tmp_path, monkeypatch):
    target = tmp_path / "shot.png"
    target.write_bytes(b"x")
    fake = SimpleNamespace(
        st_file_attributes=_REPARSE | _RECALL_ON_DATA_ACCESS, st_reparse_tag=_CLOUD_TAG
    )
    monkeypatch.setattr(pathsafety.os, "stat", lambda *a, **k: fake)
    monkeypatch.setattr(pathsafety.Path, "is_symlink", lambda self: False)
    assert not pathsafety.is_unsafe_link(target)


def test_a_real_junction_is_still_unsafe(tmp_path, monkeypatch):
    target = tmp_path / "j"
    target.mkdir()
    fake = SimpleNamespace(st_file_attributes=_REPARSE, st_reparse_tag=0xA0000003)
    monkeypatch.setattr(pathsafety.os, "stat", lambda *a, **k: fake)
    monkeypatch.setattr(pathsafety.Path, "is_symlink", lambda self: False)
    assert pathsafety.is_unsafe_link(target)


def test_online_only_files_are_never_opened_for_duplicate_checks(tmp_path, monkeypatch):
    records = [_record(tmp_path, "a.png", cloud_only=True), _record(tmp_path, "b.png")]
    opened = []
    monkeypatch.setattr(hashing, "sha256_of", lambda p: opened.append(p.name) or "same")

    hashing.find_exact_duplicates(records)

    assert opened == []  # b.png's only same-size partner is online-only, so nothing to compare


def test_online_only_files_are_moved_but_never_zipped(tmp_path):
    record = _record(tmp_path, "old.pdf", cloud_only=True, age_days=400)
    config = ScanConfig(mode="custom", target_root=tmp_path, archive_after_days=180)

    plan = planner.build_plan([record], custom_categorizer.categorize([record], config), config)

    assert plan.actions[0].action == ActionType.MOVE


def test_downloads_leaves_existing_folders_alone_by_default(tmp_path, make_file):
    make_file("Downloads/report.pdf", age_days=3)
    make_file("Downloads/Work Stuff/notes.txt")
    config = ScanConfig(mode="downloads", target_root=tmp_path / "Downloads")

    plan = pipeline.build_plan(config)

    assert [a.source.name for a in plan.actions] == ["report.pdf"]


def test_downloads_moves_existing_folders_when_enabled(tmp_path, make_file):
    make_file("Downloads/Work Stuff/notes.txt")
    config = ScanConfig(
        mode="downloads", target_root=tmp_path / "Downloads", move_existing_folders=True
    )

    plan = pipeline.build_plan(config)

    assert [a.source.name for a in plan.actions] == ["Work Stuff"]
    assert plan.actions[0].destination.parent.name == "My Folders"


def test_sortjunk_never_moves_its_own_exe(tmp_path, make_file, monkeypatch):
    exe = make_file("Downloads/SortJunk.exe", age_days=3)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    config = ScanConfig(mode="downloads", target_root=tmp_path / "Downloads")

    decisions = downloads_categorizer.categorize([_record(exe.parent, exe.name)], config)

    assert decisions[0].skip_reason is not None
