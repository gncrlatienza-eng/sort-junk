"""Tests for scan progress reporting, cancellation, and hashing work avoidance."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sortjunk import hashing, scanner
from sortjunk.categorizer import screenshots as screenshots_categorizer
from sortjunk.config import ScanConfig
from sortjunk.exceptions import ScanCancelled
from sortjunk.models import FileRecord


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


def test_screenshots_scan_skips_already_sorted_month_folders(tmp_path, make_file):
    from sortjunk import pipeline

    make_file("S/new.png", age_days=3)
    make_file("S/Mar-2025/old.png", age_days=3)
    make_file("S/Edits/kept.png", age_days=3)
    config = ScanConfig(
        mode="screenshots", target_root=tmp_path / "S", **pipeline.mode_defaults("screenshots")
    )

    plan = pipeline.build_plan(config)

    names = sorted(a.source.name for a in plan.actions)
    assert names == ["kept.png", "new.png"]
    assert scanner.estimate_file_count(tmp_path / "S", skip_month_folders=True) == 2


def test_screenshots_flatten_category_folders_from_older_versions(tmp_path, make_file):
    from sortjunk import pipeline
    from sortjunk.models import ActionType

    make_file("S/Mar-2025/Chats/chat.png", content=b"chat", age_days=3)
    make_file("S/2025-03/Receipts/receipt.png", content=b"receipt", age_days=3)
    target = tmp_path / "S"
    config = ScanConfig(mode="screenshots", target_root=target, remove_empty_folders=True)

    plan = pipeline.build_plan(config)

    moves = {a.source.name: a.destination for a in plan.actions if a.action == ActionType.MOVE}
    for name, dest in moves.items():
        assert dest.parent.parent == target.resolve()
        assert re.fullmatch(r"[A-Z][a-z]{2}-\d{4}", dest.parent.name), name
    assert set(moves) == {"chat.png", "receipt.png"}
    removed = {a.source.name for a in plan.actions if a.action == ActionType.REMOVE_EMPTY_DIR}
    assert {"Chats", "Receipts"} <= removed


def test_screenshot_month_folder_name_is_month_and_year():
    from sortjunk.planner import month_folder_name

    assert month_folder_name(datetime(2026, 12, 3, tzinfo=UTC)) == "Dec-2026"
    assert month_folder_name(datetime(2026, 1, 31, tzinfo=UTC)) == "Jan-2026"


def test_custom_scan_still_includes_month_named_folders(tmp_path, make_file):
    make_file("C/2025-03/report.pdf")

    records = scanner.scan(tmp_path / "C")

    assert [r.path.name for r in records] == ["report.pdf"]


def _record(i: int) -> FileRecord:
    now = datetime.now(UTC)
    return FileRecord(Path(f"img{i}.png"), f"img{i}.png", 1, now, now)


def test_near_duplicate_bucketing_matches_brute_force():
    assert len(screenshots_categorizer._BANDS) > hashing.NEAR_DUP_THRESHOLD
    import random

    rng = random.Random(1)  # noqa: S311 - test data, not crypto
    hashes = []
    for _ in range(60):
        base = rng.getrandbits(64)
        hashes.append(base)
        # Neighbours at distances 1..7, bits spread across different bands.
        for dist in (1, 3, 5, 6, 7):
            flipped = base
            for bit in rng.sample(range(64), dist):
                flipped ^= 1 << bit
            hashes.append(flipped)
    entries = [(_record(i), h) for i, h in enumerate(hashes)]

    def brute(items):
        parent = list(range(len(items)))

        def find(i):
            while parent[i] != i:
                i = parent[i]
            return i

        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                d = hashing.hamming_distance(items[i][1], items[j][1])
                if d <= hashing.NEAR_DUP_THRESHOLD:
                    parent[find(i)] = find(j)
        groups = {}
        for i in range(len(items)):
            groups.setdefault(find(i), set()).add(items[i][0].path)
        return {frozenset(g) for g in groups.values() if len(g) > 1}

    result = screenshots_categorizer._find_near_duplicates(entries)
    clusters = {}
    for path, group in result.items():
        clusters.setdefault(group, set()).add(path)

    assert {frozenset(g) for g in clusters.values()} == brute(entries)


def test_cancel_during_near_duplicate_pass():
    entries = [(_record(i), i) for i in range(10)]

    def _cancel(stage, done, total):
        raise ScanCancelled

    with pytest.raises(ScanCancelled):
        screenshots_categorizer._find_near_duplicates(entries, report=_cancel)


def test_folders_receiving_files_are_never_planned_for_removal(tmp_path, make_file):
    from sortjunk import pipeline
    from sortjunk.models import ActionType
    from sortjunk.planner import month_folder_name

    shot = make_file("S/Mar-2025/Chats/chat.png", content=b"chat", age_days=3)
    month = month_folder_name(datetime.fromtimestamp(shot.stat().st_mtime, tz=UTC))
    shot.parent.parent.rename(tmp_path / "S" / month)  # its own month's folder
    target = tmp_path / "S"
    config = ScanConfig(mode="screenshots", target_root=target, remove_empty_folders=True)

    plan = pipeline.build_plan(config)

    removed = {a.source.name for a in plan.actions if a.action == ActionType.REMOVE_EMPTY_DIR}
    move = next(a for a in plan.actions if a.action == ActionType.MOVE)
    assert removed == {"Chats"}
    assert move.destination.parent.name not in removed
