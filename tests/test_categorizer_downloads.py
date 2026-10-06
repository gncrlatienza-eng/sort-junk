"""Tests for downloads-mode extension-based categorization."""

from __future__ import annotations

from sortjunk import scanner
from sortjunk.categorizer import downloads as downloads_categorizer
from sortjunk.config import ScanConfig


def test_extension_table_coverage(tmp_path, make_file):
    make_file("Downloads/setup.exe")
    make_file("Downloads/report.pdf")
    make_file("Downloads/notes.docx")
    make_file("Downloads/photo.png")
    make_file("Downloads/photo.heic")
    make_file("Downloads/archive.zip")
    make_file("Downloads/clip.mp4")
    make_file("Downloads/mystery.xyz")
    target = tmp_path / "Downloads"
    config = ScanConfig(mode="downloads", target_root=target)

    records = scanner.scan(target)
    decisions = downloads_categorizer.categorize(records, config)
    by_name = {d.record.path.name: d.category for d in decisions}

    assert by_name["setup.exe"] == "Installers"
    assert by_name["report.pdf"] == "PDF"
    assert by_name["notes.docx"] == "Docs"
    assert by_name["photo.png"] == "Images"
    assert by_name["photo.heic"] == "Images"
    assert by_name["archive.zip"] == "Zip"
    assert by_name["clip.mp4"] == "Media"
    assert by_name["mystery.xyz"] == "Others"


def test_in_progress_downloads_are_skipped(tmp_path, make_file):
    make_file("Downloads/partial.crdownload")
    make_file("Downloads/partial.part")
    make_file("Downloads/partial.tmp")
    target = tmp_path / "Downloads"
    config = ScanConfig(mode="downloads", target_root=target)

    records = scanner.scan(target)
    decisions = downloads_categorizer.categorize(records, config)

    assert len(decisions) == 3
    assert all(d.skip_reason == "in-progress download" for d in decisions)
