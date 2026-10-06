"""Unit tests for archiver.write_archive's return value and integrity check."""

from __future__ import annotations

import zipfile

from sortjunk import archiver


def test_write_archive_returns_all_sources_on_first_write(tmp_path, make_file):
    root = tmp_path
    a = make_file("a.txt", content=b"a")
    b = make_file("sub/b.txt", content=b"b")
    zip_path = tmp_path / "_Archive_2024-01" / "_Archive_2024-01.zip"

    written = archiver.write_archive(zip_path, root, [a, b])

    assert set(written) == {a, b}
    with zipfile.ZipFile(zip_path) as zf:
        assert set(zf.namelist()) == {"a.txt", "sub/b.txt"}


def test_write_archive_excludes_arcname_already_present(tmp_path, make_file):
    root = tmp_path
    original = make_file("dup.txt", content=b"original")
    zip_path = tmp_path / "_Archive_2024-01" / "_Archive_2024-01.zip"
    archiver.write_archive(zip_path, root, [original])
    original.unlink()

    replacement = make_file("dup.txt", content=b"a different file, same relative path")
    written = archiver.write_archive(zip_path, root, [replacement])

    assert written == []
    with zipfile.ZipFile(zip_path) as zf:
        assert zf.read("dup.txt") == b"original"


def test_nothing_to_archive_creates_no_zip(tmp_path):
    zip_path = tmp_path / "_Archive_2024-01" / "_Archive_2024-01.zip"

    assert archiver.write_archive(zip_path, tmp_path, []) == []
    assert not zip_path.exists()
    assert not zip_path.parent.exists()
