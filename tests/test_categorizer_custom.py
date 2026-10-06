"""Tests for custom-mode categorization: extension sort + generic duplicate detection."""

from __future__ import annotations

from sortjunk import scanner
from sortjunk.categorizer import custom as custom_categorizer
from sortjunk.config import ScanConfig


def test_extension_sort_matches_downloads_mode(tmp_path, make_file):
    make_file("Custom/setup.exe")
    make_file("Custom/report.pdf")
    target = tmp_path / "Custom"
    config = ScanConfig(mode="custom", target_root=target)

    records = scanner.scan(target)
    decisions = custom_categorizer.categorize(records, config)
    by_name = {d.record.path.name: d.category for d in decisions}

    assert by_name["setup.exe"] == "Installers"
    assert by_name["report.pdf"] == "PDF"


def test_duplicate_detection_works_for_any_file_type(tmp_path, make_file):
    make_file("Custom/report.pdf", content=b"identical bytes")
    make_file("Custom/copy_of_report.pdf", content=b"identical bytes")
    make_file("Custom/unrelated.txt", content=b"something else entirely")
    target = tmp_path / "Custom"
    config = ScanConfig(mode="custom", target_root=target)

    records = scanner.scan(target)
    decisions = custom_categorizer.categorize(records, config)
    by_name = {d.record.path.name: d for d in decisions}

    assert by_name["report.pdf"].is_duplicate
    assert by_name["copy_of_report.pdf"].is_duplicate
    assert by_name["report.pdf"].dup_group_id == by_name["copy_of_report.pdf"].dup_group_id
    assert not by_name["unrelated.txt"].is_duplicate


def test_in_progress_downloads_still_skipped(tmp_path, make_file):
    make_file("Custom/partial.crdownload")
    target = tmp_path / "Custom"
    config = ScanConfig(mode="custom", target_root=target)

    records = scanner.scan(target)
    decisions = custom_categorizer.categorize(records, config)

    assert decisions[0].skip_reason == "in-progress download"


def test_never_imports_ocr_or_image_hashing_stack():
    import ast
    import inspect

    from sortjunk.categorizer import custom

    tree = ast.parse(inspect.getsource(custom))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module.split(".")[0])

    assert imported_names.isdisjoint({"pytesseract", "imagehash", "PIL"})
