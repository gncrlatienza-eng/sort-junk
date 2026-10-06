"""Tests for screenshots-mode categorization: OCR classification + duplicate detection.

OCR is mocked here rather than requiring a real Tesseract install (not
present in CI or on the dev machine this was built on) -- ocr.py's own
detect/fallback behavior is exercised separately in test_executor.py.
"""

from __future__ import annotations

import pytest

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


@pytest.mark.parametrize(
    "text",
    [
        "SyntaxError: invalid syntax",
        "PS C:\\> npm install\nnpm ERR! code ENOENT\n$ git push",
        "TypeError: unsupported operand\n  File main.py, line 3",
    ],
)
def test_code_errors_are_not_mistaken_for_receipts(text):
    assert screenshots_categorizer._classify_text(text) == "Errors_Code"


@pytest.mark.parametrize(
    "text",
    ["Subtotal 12.00\nTax 1.20\nTotal 13.20", "Order number 5581\nPaid $13.20", "Amount ₱1,250.00"],
)
def test_real_receipts_still_classify_as_receipts(text):
    assert screenshots_categorizer._classify_text(text) == "Receipts"


def test_keywords_match_whole_words_only():
    # "thread", "syntax", "totally" contain chat/receipt keywords as substrings only.
    assert screenshots_categorizer._classify_text("A thread about syntax, totally") == (
        "Uncategorized"
    )


def test_exact_duplicates_flag_all_but_the_oldest_copy(tmp_path, make_file):
    make_file("Screenshots/original.png", content=b"identical-bytes", age_days=10)
    make_file("Screenshots/copy.png", content=b"identical-bytes", age_days=1)
    target = tmp_path / "Screenshots"
    config = ScanConfig(mode="screenshots", target_root=target, use_ocr=False)

    records = scanner.scan(target)
    by_name = {d.record.path.name: d for d in screenshots_categorizer.categorize(records, config)}

    assert not by_name["original.png"].is_duplicate
    assert by_name["copy.png"].is_duplicate
    assert by_name["original.png"].dup_group_id == by_name["copy.png"].dup_group_id


def test_distinct_files_are_not_flagged_as_duplicates(tmp_path, make_file):
    make_file("Screenshots/shot1.png", content=b"aaaa")
    make_file("Screenshots/shot2.png", content=b"bbbb")
    target = tmp_path / "Screenshots"
    config = ScanConfig(mode="screenshots", target_root=target, use_ocr=False)

    records = scanner.scan(target)
    decisions = screenshots_categorizer.categorize(records, config)

    assert not any(d.is_duplicate for d in decisions)
