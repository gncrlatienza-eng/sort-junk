"""Tests for scan progress reporting, cancellation, and hashing work avoidance."""

from __future__ import annotations

import pytest

from sortjunk import hashing, scanner
from sortjunk.categorizer import screenshots as screenshots_categorizer
from sortjunk.config import ScanConfig
from sortjunk.exceptions import ScanCancelled


def test_only_files_sharing_a_size_are_hashed(tmp_path, make_file, monkeypatch):
    make_file("T/a.bin", content=b"1")
    make_file("T/b.bin", content=b"22")
    make_file("T/c.bin", content=b"333")
    make_file("T/d.bin", content=b"444")
    hashed = []
    real = hashing.sha256_of
    monkeypatch.setattr(hashing, "sha256_of", lambda p: hashed.append(p.name) or real(p))

    hashing.find_exact_duplicates(scanner.scan(tmp_path / "T"))

    assert sorted(hashed) == ["c.bin", "d.bin"]


def test_progress_callback_receives_stage_counts(tmp_path, make_file):
    make_file("S/a.png", content=b"same")
    make_file("S/b.png", content=b"same")
    calls = []
    config = ScanConfig(
        mode="screenshots",
        target_root=tmp_path / "S",
        use_ocr=False,
        on_progress=lambda stage, done, total: calls.append((stage, done, total)),
    )

    screenshots_categorizer.categorize(scanner.scan(tmp_path / "S"), config)

    assert calls
    assert all(done <= total for _stage, done, total in calls if total)


def test_progress_callback_can_cancel_a_scan(tmp_path, make_file):
    for i in range(600):
        make_file(f"S/f{i}.txt")

    def _cancel(stage, done, total):
        raise ScanCancelled

    with pytest.raises(ScanCancelled):
        scanner.scan(tmp_path / "S", on_progress=_cancel)


def test_find_tesseract_checks_standard_install_folder(tmp_path, monkeypatch):
    from sortjunk import ocr

    exe = tmp_path / "PF" / "Tesseract-OCR" / "tesseract.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(ocr.shutil, "which", lambda name: None)

    assert ocr.find_tesseract(env={"ProgramFiles": str(tmp_path / "PF")}) == str(exe)


def test_perceptual_hash_finds_near_duplicates_but_not_different_images(tmp_path):
    from PIL import Image, ImageDraw

    def _draw(path, shift=0, invert=False):
        img = Image.new("RGB", (200, 120), "black" if invert else "white")
        ImageDraw.Draw(img).rectangle(
            [20 + shift, 20, 120 + shift, 90], fill="white" if invert else "navy"
        )
        img.save(path)

    _draw(tmp_path / "a.png")
    _draw(tmp_path / "a_resaved.jpg", shift=1)
    _draw(tmp_path / "other.png", invert=True)

    a, near, other = (
        hashing.perceptual_hash(tmp_path / n) for n in ("a.png", "a_resaved.jpg", "other.png")
    )

    assert hashing.hamming_distance(a, near) <= hashing.NEAR_DUP_THRESHOLD
    assert hashing.hamming_distance(a, other) > hashing.NEAR_DUP_THRESHOLD
    assert hashing.perceptual_hash(tmp_path / "missing.png") is None
