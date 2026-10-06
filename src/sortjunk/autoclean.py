"""Headless auto-clean, launched by the scheduled task (see scheduler.py).

Runs the same pipeline -> executor path as the GUI, with the user's saved
settings, for whichever of Downloads / Screenshots they opted in to. Every
run is saved to history, so "Undo Last <mode> Sort" in the GUI reverses it. Never
prompts: anything that would need a decision (a refused folder, an unusually
large scan) is skipped and logged instead.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from . import executor, history, pipeline, scanner, special_folders, target_guard
from .config import ScanConfig
from .models import ActionType
from .settings import Settings, app_data_dir, load

logger = logging.getLogger("sortjunk.autoclean")

_FOLDER_LOOKUP = {
    "downloads": special_folders.default_downloads_folder,
    "screenshots": special_folders.default_screenshots_folder,
}


def _enabled_modes(settings: Settings) -> list[str]:
    modes = []
    if settings.auto_clean_downloads:
        modes.append("downloads")
    if settings.auto_clean_screenshots:
        modes.append("screenshots")
    return modes


def run(settings: Settings | None = None) -> int:
    """Clean every opted-in folder once. Returns the number of folders that failed."""
    settings = settings or load()
    if not settings.auto_clean_enabled:
        logger.info("Auto-clean is turned off in settings; nothing to do.")
        return 0

    failures = 0
    for mode in _enabled_modes(settings):
        try:
            _clean_one(mode, settings)
        except Exception:  # noqa: BLE001 - one folder failing mustn't stop the other
            logger.exception("Auto-clean of %s failed", mode)
            failures += 1
    return failures


def _clean_one(mode: str, settings: Settings) -> None:
    folder = _FOLDER_LOOKUP[mode]()
    if folder is None:
        logger.warning("No %s folder found; skipping.", mode)
        return
    reason = target_guard.unsafe_target_reason(folder)
    if reason is not None:
        logger.warning("Skipping %s: %s", mode, reason)
        return

    config = ScanConfig(
        mode=mode,
        target_root=folder.resolve(),
        archive_after_days=settings.archive_after_days,
        move_existing_folders=settings.move_existing_folders,
        **pipeline.mode_defaults(mode),
    )
    estimated = scanner.estimate_file_count(
        config.target_root,
        recursive=mode != "downloads",
        skip_month_folders=mode == "screenshots",
    )
    if estimated > config.max_files:
        logger.warning(
            "Skipping %s: %d files is over the %d limit -- run SortJunk by hand for this one.",
            mode,
            estimated,
            config.max_files,
        )
        return

    plan = pipeline.build_plan(config)
    if not any(a.action != ActionType.SKIP for a in plan.actions):
        logger.info("%s: already tidy, nothing to do.", mode)
        return

    results = executor.apply(plan)
    history.save_run(plan, results, trigger="auto")
    failed = [r for r in results if not r.succeeded]
    logger.info(
        "%s: %d change(s) applied, %d failed.", mode, len(results) - len(failed), len(failed)
    )
    for r in failed:
        logger.warning("  failed %s: %s", r.source, r.error)


def _configure_logging() -> None:
    log_dir = app_data_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "auto-clean.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def main() -> None:
    _configure_logging()
    logger.info("Auto-clean started.")
    failures = run()
    logger.info("Auto-clean finished.")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
