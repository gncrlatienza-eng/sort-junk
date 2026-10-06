"""Tests for screenshots-mode categorization: month folders + duplicate detection."""

from __future__ import annotations

import re

from sortjunk import planner, scanner
from sortjunk.categorizer import screenshots as screenshots_categorizer
from sortjunk.config import ScanConfig


def test_screenshots_sort_straight_into_their_month_folder(tmp_path, make_file):
    shot = make_file("Screenshots/shot1.png", content=b"fake-image-bytes-1", age_days=3)
    target = tmp_path / "Screenshots"
    config = ScanConfig(mode="screenshots", target_root=target)

    records = scanner.scan(target)
    decisions = screenshots_categorizer.categorize(records, config)
    plan = planner.build_plan(records, decisions, config)

    month = planner.month_folder_name(records[0].modified_at)
    assert re.fullmatch(r"[A-Z][a-z]{2}-\d{4}", month)
    assert decisions[0].category == month
    assert plan.actions[0].destination == target / month / shot.name


def test_exact_duplicates_flag_all_but_the_oldest_copy(tmp_path, make_file):
    make_file("Screenshots/original.png", content=b"identical-bytes", age_days=10)
    make_file("Screenshots/copy.png", content=b"identical-bytes", age_days=1)
    target = tmp_path / "Screenshots"
    config = ScanConfig(mode="screenshots", target_root=target)

    records = scanner.scan(target)
    by_name = {d.record.path.name: d for d in screenshots_categorizer.categorize(records, config)}

    assert not by_name["original.png"].is_duplicate
    assert by_name["copy.png"].is_duplicate
    assert by_name["original.png"].dup_group_id == by_name["copy.png"].dup_group_id


def test_distinct_files_are_not_flagged_as_duplicates(tmp_path, make_file):
    make_file("Screenshots/shot1.png", content=b"aaaa")
    make_file("Screenshots/shot2.png", content=b"bbbb")
    target = tmp_path / "Screenshots"
    config = ScanConfig(mode="screenshots", target_root=target)

    records = scanner.scan(target)
    decisions = screenshots_categorizer.categorize(records, config)

    assert not any(d.is_duplicate for d in decisions)
