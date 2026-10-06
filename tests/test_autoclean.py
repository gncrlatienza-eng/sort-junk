"""Tests for saved settings, the scheduled-task definition, and headless auto-clean."""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

from sortjunk import autoclean, history, scheduler, settings

_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


@pytest.fixture
def app_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    return tmp_path / "appdata" / "SortJunk"


def test_settings_round_trip(app_dir):
    s = settings.Settings(last_mode="custom", archive_after_days=30, auto_clean_enabled=True)
    settings.save(s)
    assert settings.load() == s


def test_settings_survive_a_corrupt_or_hand_edited_file(app_dir):
    app_dir.mkdir(parents=True)
    (app_dir / "settings.json").write_text(
        json.dumps({"archive_after_days": -5, "last_mode": "bogus", "skip_ocr": True, "x": 1})
    )
    loaded = settings.load()
    assert loaded.archive_after_days == 180
    assert loaded.last_mode == "downloads"

    (app_dir / "settings.json").write_text("{not json")
    assert settings.load() == settings.Settings()


@pytest.mark.parametrize("frequency", ["daily", "weekly"])
def test_task_xml_is_valid_and_runs_auto_clean(frequency):
    xml = scheduler.task_xml(frequency, r"C:\Apps\Sort & Junk\SortJunk.exe", "--auto-clean")
    root = ET.fromstring(xml.split("?>", 1)[1])  # noqa: S314 - our own generated XML

    assert root.find(".//t:Exec/t:Command", _NS).text == r"C:\Apps\Sort & Junk\SortJunk.exe"
    assert root.find(".//t:Exec/t:Arguments", _NS).text == "--auto-clean"
    assert root.find(".//t:StartWhenAvailable", _NS).text == "true"
    assert root.find(".//t:RunLevel", _NS).text == "LeastPrivilege"
    tag = "ScheduleByDay" if frequency == "daily" else "ScheduleByWeek"
    assert root.find(f".//t:{tag}", _NS) is not None


def test_exe_in_downloads_is_flagged_before_scheduling(tmp_path, monkeypatch):
    exe = tmp_path / "Downloads" / "SortJunk.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    monkeypatch.setenv("TEMP", str(tmp_path / "Temp"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(
        scheduler.special_folders, "default_downloads_folder", lambda: tmp_path / "Downloads"
    )
    assert scheduler.exe_location_problem() is not None

    safe = tmp_path / "Apps" / "SortJunk.exe"
    monkeypatch.setattr(sys, "executable", str(safe))
    assert scheduler.exe_location_problem() is None


def _fake_downloads(tmp_path, monkeypatch, make_file):
    make_file("Downloads/report.pdf", age_days=3)
    make_file("Downloads/setup.exe", age_days=3)
    monkeypatch.setattr(
        autoclean.special_folders, "default_downloads_folder", lambda: tmp_path / "Downloads"
    )
    monkeypatch.setitem(autoclean._FOLDER_LOOKUP, "downloads", lambda: tmp_path / "Downloads")
    # Test files can't have their creation time backdated, so lift the 24h hold-back.
    monkeypatch.setattr(
        autoclean.pipeline,
        "mode_defaults",
        lambda mode: {"min_age_hours": 0, "remove_empty_folders": False},
    )
    return tmp_path / "Downloads"


def test_auto_clean_sorts_and_logs_an_undoable_run(tmp_path, monkeypatch, make_file, app_dir):
    downloads = _fake_downloads(tmp_path, monkeypatch, make_file)

    failures = autoclean.run(settings.Settings(auto_clean_enabled=True))

    assert failures == 0
    assert (downloads / "PDF" / "report.pdf").exists()
    run = history.last_run(trigger="auto")
    assert run is not None and run.undoable_count == 2


def test_auto_clean_does_nothing_when_turned_off(tmp_path, monkeypatch, make_file, app_dir):
    downloads = _fake_downloads(tmp_path, monkeypatch, make_file)

    autoclean.run(settings.Settings(auto_clean_enabled=False))

    assert (downloads / "report.pdf").exists()
    assert history.last_run(trigger="auto") is None


def test_scheduled_program_is_read_back_from_the_task(monkeypatch):
    xml = scheduler.task_xml("weekly", r"C:\Old Place\Sort & Junk.exe", "--auto-clean")
    monkeypatch.setattr(
        scheduler,
        "_schtasks",
        lambda *args: SimpleNamespace(returncode=0, stdout=xml, stderr=""),
    )
    assert scheduler.scheduled_program() == r"C:\Old Place\Sort & Junk.exe"

    monkeypatch.setattr(
        scheduler, "_schtasks", lambda *args: SimpleNamespace(returncode=1, stdout="", stderr="")
    )
    assert scheduler.scheduled_program() is None
