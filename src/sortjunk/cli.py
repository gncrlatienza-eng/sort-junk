"""SortJunk command-line entry point.

Pipeline: scan -> categorize -> build_plan -> print summary -> (confirm) -> apply.
There is no code path from the default (dry-run) invocation to executor.apply --
reaching it requires --apply plus either --yes or an interactive "y" confirmation.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import executor, ocr, planner, scanner, special_folders
from .categorizer import custom as custom_categorizer
from .categorizer import downloads as downloads_categorizer
from .categorizer import screenshots as screenshots_categorizer
from .config import ScanConfig
from .models import Plan

logger = logging.getLogger("sortjunk")

_DEFAULT_FOLDER_LOOKUP = {
    "downloads": special_folders.default_downloads_folder,
    "screenshots": special_folders.default_screenshots_folder,
}


def _bounded_int(min_value: int, max_value: int):
    """argparse type= factory: an int constrained to [min_value, max_value].

    Matches the GUI's Spinbox range for the same setting, so a value that's
    silently clamped in one interface is rejected with a clear error in the
    other instead of producing surprising behavior (e.g. a negative
    archive-after-days making every file archive-eligible immediately).
    """

    def _parse(value: str) -> int:
        parsed = int(value)
        if not (min_value <= parsed <= max_value):
            raise argparse.ArgumentTypeError(
                f"must be between {min_value} and {max_value}, got {parsed}"
            )
        return parsed

    return _parse


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sortjunk",
        description="Scan and sort a Screenshots or Downloads folder. Dry-run by default.",
    )
    parser.add_argument("--mode", choices=["screenshots", "downloads", "custom"], required=True)
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help="Folder to scan. Defaults to the system's Downloads/Screenshots folder for "
        "--mode downloads/screenshots. Required for --mode custom.",
    )
    parser.add_argument("--apply", action="store_true", help="Actually move/archive files.")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation.")
    parser.add_argument(
        "--skip-ocr", action="store_true", help="Never run OCR, even if Tesseract is available."
    )
    parser.add_argument("--fast", action="store_true", help="Alias for --skip-ocr.")
    parser.add_argument("--archive-after-days", type=_bounded_int(1, 3650), default=180)
    parser.add_argument("--max-ocr-size-mb", type=int, default=20)
    parser.add_argument("--max-files", type=int, default=50_000)
    parser.add_argument(
        "--tesseract-cmd", default=None, help="Path to tesseract.exe if not on PATH."
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def _config_from_args(args: argparse.Namespace) -> ScanConfig:
    return ScanConfig(
        mode=args.mode,
        target_root=args.target.resolve(),
        apply=args.apply,
        assume_yes=args.yes,
        use_ocr=True,  # refined below once Tesseract availability is known
        archive_after_days=args.archive_after_days,
        max_ocr_size_mb=args.max_ocr_size_mb,
        max_files=args.max_files,
        tesseract_cmd=args.tesseract_cmd,
        verbose=args.verbose,
        # Custom is a deliberate one-off cleanup: no freshness hold-back,
        # and it's the only mode that prunes folders left empty by sorting.
        min_age_hours=0 if args.mode == "custom" else 24,
        remove_empty_folders=(args.mode == "custom"),
    )


def _print_summary(plan: Plan) -> None:
    counts: dict[str, int] = {}
    for action in plan.actions:
        counts[action.action.value] = counts.get(action.action.value, 0) + 1

    print(f"\nSortJunk plan for {plan.target_root} ({plan.mode} mode)")
    print(f"  Files scanned:         {plan.total_files}")
    for action_type, count in sorted(counts.items()):
        print(f"  {action_type:>16}: {count}")
    print(f"  Bytes to move/archive: {plan.total_bytes_moved:,}")


def _confirm(config: ScanConfig, plan: Plan) -> bool:
    if config.assume_yes:
        return True
    _print_summary(plan)
    answer = input("\nApply these changes now? [y/N] ").strip().lower()
    return answer == "y"


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.target is None:
        lookup = _DEFAULT_FOLDER_LOOKUP.get(args.mode)
        default_target = lookup() if lookup else None
        if default_target is None:
            print(
                f"Could not auto-detect a {args.mode} folder -- pass --target explicitly.",
                file=sys.stderr,
            )
            return 1
        args.target = default_target
        print(f"No --target given; using detected {args.mode} folder: {default_target}")

    config = _config_from_args(args)

    if not config.target_root.is_dir():
        print(
            f"Target folder does not exist or is not a directory: {config.target_root}",
            file=sys.stderr,
        )
        return 1

    if config.mode == "screenshots":
        tesseract_status = ocr.detect_tesseract(config.tesseract_cmd)
        config.use_ocr = tesseract_status.available and not args.skip_ocr and not args.fast
        if not tesseract_status.available:
            print(
                "Tesseract OCR not found -- falling back to date-only categorization "
                "for screenshots (use --tesseract-cmd to point at a binary, or install "
                "Tesseract). See README for details."
            )
        elif not config.use_ocr:
            print("OCR skipped by request (--skip-ocr/--fast); using date-only categorization.")
    else:
        config.use_ocr = False

    estimated = scanner.estimate_file_count(
        config.target_root, recursive=config.mode != "downloads"
    )
    if estimated > config.max_files and not config.assume_yes:
        answer = (
            input(
                f"\n{estimated} files found, above the {config.max_files} soft limit. "
                f"Continue anyway? [y/N] "
            )
            .strip()
            .lower()
        )
        if answer != "y":
            print("Aborted.")
            return 1

    print(f"Scanning {config.target_root} ...")
    existing_dirs: list[Path] = []
    if config.mode == "downloads":
        records = scanner.scan(config.target_root, max_files=config.max_files, recursive=False)
        existing_dirs = scanner.list_top_level_dirs(
            config.target_root,
            excluded_names=downloads_categorizer.OWNED_TOP_LEVEL_NAMES,
            excluded_prefixes=("_Archive_",),
        )
    else:
        records = scanner.scan(config.target_root, max_files=config.max_files)

    if config.mode == "screenshots":
        decisions = screenshots_categorizer.categorize(records, config)
    elif config.mode == "custom":
        decisions = custom_categorizer.categorize(records, config)
    else:
        decisions = downloads_categorizer.categorize(records, config)

    plan = planner.build_plan(records, decisions, config, existing_dirs=existing_dirs)

    if not config.apply:
        _print_summary(plan)
        print("\nNo files were changed (dry run). Re-run with --apply to make these changes.")
        return 0

    if not _confirm(config, plan):
        print("Aborted -- no files were changed.")
        return 1

    results = executor.apply(plan)
    succeeded = sum(1 for r in results if r.succeeded)
    failed = len(results) - succeeded
    print(f"\nDone. {succeeded} action(s) succeeded, {failed} failed.")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
