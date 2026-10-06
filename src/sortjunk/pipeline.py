"""The read-only scan -> categorize -> plan pipeline, shared by the CLI and GUI.

Never mutates disk: the result is a Plan for the caller to show, and only
executor.apply() acts on it.
"""

from __future__ import annotations

from pathlib import Path

from . import planner, scanner
from .categorizer import custom as custom_categorizer
from .categorizer import downloads as downloads_categorizer
from .categorizer import screenshots as screenshots_categorizer
from .config import ScanConfig
from .models import Plan

_CATEGORIZERS = {
    "downloads": downloads_categorizer,
    "screenshots": screenshots_categorizer,
    "custom": custom_categorizer,
}


def mode_defaults(mode: str) -> dict:
    """Per-mode settings that aren't user options.

    Custom is a deliberate one-off cleanup: no freshness hold-back, and it's
    the only mode that prunes folders left empty by sorting. The others hold
    back anything created in the last 24h so an in-use file isn't yanked away.
    """
    return {
        "min_age_hours": 0 if mode == "custom" else 24,
        "remove_empty_folders": mode == "custom",
    }


def build_plan(config: ScanConfig) -> Plan:
    existing_dirs: list[Path] = []
    if config.mode == "downloads":
        # Downloads: only loose top-level files are sorted. Folders the user
        # already had are left alone, or (if enabled) relocated whole, never opened.
        records = scanner.scan(
            config.target_root,
            max_files=config.max_files,
            recursive=False,
            on_progress=config.on_progress,
        )
        if config.move_existing_folders:
            existing_dirs = scanner.list_top_level_dirs(
                config.target_root,
                excluded_names=downloads_categorizer.OWNED_TOP_LEVEL_NAMES,
                excluded_prefixes=("_Archive_",),
            )
    else:
        records = scanner.scan(
            config.target_root, max_files=config.max_files, on_progress=config.on_progress
        )

    decisions = _CATEGORIZERS[config.mode].categorize(records, config)
    plan = planner.build_plan(records, decisions, config, existing_dirs=existing_dirs)
    plan.cloud_only_files = sum(1 for r in records if r.cloud_only)
    return plan
