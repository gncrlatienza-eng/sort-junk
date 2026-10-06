"""Tests for screenshots-mode categorization: OCR classification + duplicate detection.

OCR is mocked here rather than requiring a real Tesseract install (not
present in CI or on the dev machine this was built on) -- ocr.py's own
detect/fallback behavior is exercised separately in test_executor.py.
"""

from __future__ import annotations

from sortjunk import scanner
from sortjunk.categorizer import screenshots as screenshots_categorizer
from sortjunk.config import ScanConfig


def test_ocr_text_maps_to_expected_category(tmp_path, make_file, monkeypatch):
    make_file("Screenshots/shot1.png", content=b"fake-image-bytes-1")

    def fake_ocr_image(path, timeout_s=10):
        return "Total: $42.00 Subtotal Tax Order #123"

    monkeypatch.setattr(screenshots_categorizer.ocr, "ocr_image", fake_ocr_image)

    target = tmp_path / "Screenshots"
    config = ScanConfig(mode="screenshots", target_root=target, use_ocr=True)
    records = scanner.scan(target)
    decisions = screenshots_categorizer.categorize(records, config)

    assert decisions[0].category == "Receipts"
    assert decisions[0].ocr_used is True


def test_error_keywords_map_to_errors_code(tmp_path, make_file, monkeypatch):
    make_file("Screenshots/shot1.png", content=b"fake-image-bytes-1")

    monkeypatch.setattr(
        screenshots_categorizer.ocr,
        "ocr_image",
        lambda path, timeout_s=10: "Traceback (most recent call last):\nValueError: bad input",
    )

    target = tmp_path / "Screenshots"
    config = ScanConfig(mode="screenshots", target_root=target, use_ocr=True)
    records = scanner.scan(target)
    decisions = screenshots_categorizer.categorize(records, config)

    assert decisions[0].category == "Errors_Code"


def test_exact_duplicates_are_flagged(tmp_path, make_file):
    make_file("Screenshots/shot1.png", content=b"identical-bytes")
    make_file("Screenshots/shot2.png", content=b"identical-bytes")
    target = tmp_path / "Screenshots"
    config = ScanConfig(mode="screenshots", target_root=target, use_ocr=False)

    records = scanner.scan(target)
    decisions = screenshots_categorizer.categorize(records, config)

    assert all(d.is_duplicate for d in decisions)
    assert decisions[0].dup_group_id == decisions[1].dup_group_id


def test_distinct_files_are_not_flagged_as_duplicates(tmp_path, make_file):
    make_file("Screenshots/shot1.png", content=b"aaaa")
    make_file("Screenshots/shot2.png", content=b"bbbb")
    target = tmp_path / "Screenshots"
    config = ScanConfig(mode="screenshots", target_root=target, use_ocr=False)

    records = scanner.scan(target)
    decisions = screenshots_categorizer.categorize(records, config)

    assert not any(d.is_duplicate for d in decisions)
