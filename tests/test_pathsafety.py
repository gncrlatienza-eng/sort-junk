"""Tests for the path-safety choke point pathsafety.py.

These are the highest-priority tests in the suite: pathsafety.py is what
stands between a filename SortJunk didn't choose (Downloads-folder content
is internet-influenced) and an actual move/archive on disk.
"""

from __future__ import annotations

import os

import pytest

from sortjunk.exceptions import PathSafetyError
from sortjunk.pathsafety import (
    is_unsafe_link,
    resolve_and_validate_containment,
    resolve_collision,
    sanitize_filename,
)


def test_destination_inside_root_passes(tmp_path):
    root = tmp_path
    candidate = root / "sub" / "file.txt"
    result = resolve_and_validate_containment(root, candidate)
    assert result == candidate.resolve()


def test_dotdot_segment_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    candidate = root / ".." / "escape.txt"
    with pytest.raises(PathSafetyError):
        resolve_and_validate_containment(root, candidate)


def test_resolved_symlink_escape_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    link = root / "link"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("symlink creation not permitted in this environment")

    with pytest.raises(PathSafetyError):
        resolve_and_validate_containment(root, link)


def test_case_variant_within_root_is_still_contained(tmp_path):
    root = tmp_path / "Root"
    root.mkdir()
    candidate = tmp_path / "ROOT" / "file.txt"
    result = resolve_and_validate_containment(root, candidate)
    assert result is not None


@pytest.mark.parametrize("name", ["CON", "con.txt", "NUL", "COM1", "LPT1.log"])
def test_reserved_device_names_are_renamed(name):
    sanitized = sanitize_filename(name)
    stem = sanitized.split(".", 1)[0]
    assert stem.upper() not in {"CON", "NUL", "COM1", "LPT1"}


def test_trailing_dots_and_spaces_trimmed():
    assert sanitize_filename("evil. . ") == "evil"


def test_path_separators_and_control_chars_stripped():
    sanitized = sanitize_filename("a/b\\c\x00d")
    assert "/" not in sanitized
    assert "\\" not in sanitized
    assert "\x00" not in sanitized


def test_bidi_override_character_stripped():
    """A RIGHT-TO-LEFT OVERRIDE (U+202E) can make an .exe *display* as if
    it were a .png (e.g. "invoice_<RTLO>gnp.exe" renders reversed as
    "invoice_exe.png") -- a real spoofing technique, not a theoretical
    one. sanitize_filename must not let it survive into a name shown in
    the GUI's plan preview.
    """
    rtlo = "‮"
    sanitized = sanitize_filename(f"invoice_{rtlo}gnp.exe")
    assert rtlo not in sanitized


def test_zero_width_and_format_characters_stripped():
    zero_width_space = "​"
    bom = "﻿"
    sanitized = sanitize_filename(f"file{zero_width_space}name{bom}.txt")
    assert zero_width_space not in sanitized
    assert bom not in sanitized


def test_overlong_name_truncated_preserving_extension():
    name = "a" * 300 + ".png"
    sanitized = sanitize_filename(name)
    assert sanitized.endswith(".png")
    assert len(sanitized) <= 200


def test_resolve_collision_returns_original_when_free(tmp_path):
    dest = tmp_path / "file.txt"
    assert resolve_collision(dest) == dest


def test_resolve_collision_increments_suffix(tmp_path):
    dest = tmp_path / "file.txt"
    dest.write_text("x")
    (tmp_path / "file (1).txt").write_text("x")
    result = resolve_collision(dest)
    assert result == tmp_path / "file (2).txt"


def test_resolve_collision_never_overwrites(tmp_path):
    dest = tmp_path / "file.txt"
    dest.write_bytes(b"original")
    result = resolve_collision(dest)
    assert result != dest


def test_resolve_collision_raises_past_max_attempts(tmp_path):
    dest = tmp_path / "file.txt"
    dest.write_text("x")
    with pytest.raises(PathSafetyError):
        resolve_collision(dest, max_attempts=0)


def test_is_unsafe_link_detects_symlink(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("x")
    link = tmp_path / "link.txt"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlink creation not permitted in this environment")

    assert is_unsafe_link(link) is True


def test_is_unsafe_link_false_for_regular_file(tmp_path):
    regular = tmp_path / "regular.txt"
    regular.write_text("x")
    assert is_unsafe_link(regular) is False
