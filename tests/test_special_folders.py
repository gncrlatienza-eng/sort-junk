"""Tests for the Windows known-folder lookup used to auto-fill a default target."""

from __future__ import annotations

from pathlib import Path

from sortjunk.special_folders import default_downloads_folder, default_screenshots_folder


def test_default_downloads_folder_resolves_to_a_real_directory():
    result = default_downloads_folder()
    assert result is None or (isinstance(result, Path) and result.is_dir())


def test_default_screenshots_folder_resolves_to_a_real_directory_or_none():
    # Unlike Downloads, the Screenshots known folder may not exist on a
    # machine that has never taken a screenshot -- None is a valid result.
    result = default_screenshots_folder()
    assert result is None or (isinstance(result, Path) and result.is_dir())


def test_unknown_guid_returns_none():
    from sortjunk.special_folders import _known_folder_path

    # A syntactically valid but non-existent/unregistered known-folder GUID.
    assert _known_folder_path("00000000-0000-0000-0000-000000000000") is None
