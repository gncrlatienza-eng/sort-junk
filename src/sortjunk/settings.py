"""User settings, remembered between runs and used by scheduled auto-clean.

Stored as JSON in %LOCALAPPDATA%\\SortJunk\\settings.json. Loading is
forgiving: a missing, corrupt, or hand-edited file falls back to defaults
field by field rather than failing to start.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

logger = logging.getLogger(__name__)

FREQUENCIES = ("daily", "weekly")


def app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "SortJunk"
    return Path.home() / ".sortjunk"


@dataclass
class Settings:
    last_mode: str = "downloads"
    last_custom_folder: str = ""
    archive_after_days: int = 180
    skip_ocr: bool = False
    move_existing_folders: bool = False
    auto_clean_enabled: bool = False
    auto_clean_frequency: str = "weekly"
    auto_clean_downloads: bool = True
    auto_clean_screenshots: bool = False


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def _valid(name: str, value: object, default: object) -> bool:
    if type(value) is not type(default):
        return False
    if name == "last_mode":
        return value in ("downloads", "screenshots", "custom")
    if name == "auto_clean_frequency":
        return value in FREQUENCIES
    if name == "archive_after_days":
        return 1 <= value <= 3650  # type: ignore[operator]
    return True


def load(path: Path | None = None) -> Settings:
    path = path or settings_path()
    defaults = Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return defaults
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable settings file %s: %s", path, exc)
        return defaults
    if not isinstance(raw, dict):
        return defaults

    values = {}
    for f in fields(Settings):
        default = getattr(defaults, f.name)
        value = raw.get(f.name, default)
        values[f.name] = value if _valid(f.name, value, default) else default
    return Settings(**values)


def save(settings: Settings, path: Path | None = None) -> None:
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    os.replace(tmp, path)
