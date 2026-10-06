"""Real NTFS junctions (no admin rights needed, unlike symlinks).

A junction inside a target folder pointing elsewhere must never be followed
or moved -- otherwise sorting one folder could reach into another.
"""

from __future__ import annotations

import sys

import pytest

from sortjunk import pathsafety, pipeline, scanner
from sortjunk.config import ScanConfig

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions are Windows-only")


def _junction(link, target):
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


def test_junction_is_detected_as_unsafe(tmp_path):
    (tmp_path / "elsewhere").mkdir()
    _junction(tmp_path / "link", tmp_path / "elsewhere")

    assert pathsafety.is_unsafe_link(tmp_path / "link")


def test_scan_never_follows_a_junction(tmp_path, make_file):
    make_file("Elsewhere/private.docx")
    make_file("Target/mine.pdf")
    _junction(tmp_path / "Target" / "shortcut", tmp_path / "Elsewhere")

    names = {r.path.name for r in scanner.scan(tmp_path / "Target")}

    assert names == {"mine.pdf"}


def test_junction_in_downloads_is_never_moved_as_a_folder(tmp_path, make_file):
    make_file("Elsewhere/private.docx")
    (tmp_path / "Downloads").mkdir()
    _junction(tmp_path / "Downloads" / "shortcut", tmp_path / "Elsewhere")
    config = ScanConfig(
        mode="downloads", target_root=tmp_path / "Downloads", move_existing_folders=True
    )

    plan = pipeline.build_plan(config)

    assert plan.actions == []
    assert (tmp_path / "Elsewhere" / "private.docx").exists()
