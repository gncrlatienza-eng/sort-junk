"""Refuse target folders where a sort would do real damage.

Read-only. Called by the CLI and GUI before scanning, and again by
executor.apply() immediately before anything moves (defense in depth --
a plan is only as safe as the root it was built for).

Refused: a drive root, the user profile or any folder containing it,
Windows / Program Files / ProgramData and anything inside them, AppData
(except Temp), and any folder that is or sits inside a version-controlled
project. Sorting any of these by file type would break installed apps,
the OS, or a codebase's structure.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

_SYSTEM_TREE_VARS = (
    "SystemRoot",
    "ProgramFiles",
    "ProgramFiles(x86)",
    "ProgramW6432",
    "ProgramData",
)
_APP_DATA_VARS = ("APPDATA", "LOCALAPPDATA")
_TEMP_VARS = ("TEMP", "TMP")
_ONEDRIVE_VARS = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")

# A directory containing any of these is a project/environment whose layout
# matters -- never sorted, never descended into by a recursive scan.
PROJECT_MARKERS = (".git", ".hg", ".svn", "pyvenv.cfg")
# Directory names that are always someone else's managed tree.
SEALED_DIR_NAMES = frozenset({"node_modules"})


class UnsafeTargetError(Exception):
    """Raised when asked to apply a plan whose target folder is refused."""


def _norm(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def _is_within(path: Path, ancestor: Path) -> bool:
    p, a = _norm(path), _norm(ancestor)
    return p == a or p.startswith(a.rstrip(os.sep) + os.sep)


def _env_paths(env: Mapping[str, str], names: tuple[str, ...]) -> list[Path]:
    return [Path(env[n]) for n in names if env.get(n)]


def is_project_dir(path: Path) -> bool:
    """True if `path` is a project/environment folder that must be left whole."""
    if path.name in SEALED_DIR_NAMES:
        return True
    return any((path / marker).exists() for marker in PROJECT_MARKERS)


def unsafe_target_reason(
    target: Path, *, env: Mapping[str, str] | None = None, home: Path | None = None
) -> str | None:
    """Return a human-readable reason `target` must not be sorted, or None if it's fine."""
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    resolved = target.resolve(strict=False)

    if resolved.parent == resolved:
        return f"{target} is a drive root. Pick a specific folder instead."

    if _is_within(home, resolved):
        return (
            f"{target} contains your whole user profile. "
            "Pick a specific folder such as Downloads or Desktop instead."
        )

    for onedrive in _env_paths(env, _ONEDRIVE_VARS):
        if _is_within(onedrive, resolved):
            return (
                f"{target} contains your whole OneDrive ({onedrive}). "
                "Pick a specific folder inside it instead."
            )

    for system_dir in _env_paths(env, _SYSTEM_TREE_VARS):
        if _is_within(resolved, system_dir):
            return f"{target} is a Windows system or program folder ({system_dir})."

    temp_dirs = _env_paths(env, _TEMP_VARS)
    for app_data in _env_paths(env, _APP_DATA_VARS):
        if _is_within(resolved, app_data) and not any(_is_within(resolved, t) for t in temp_dirs):
            return f"{target} is application data ({app_data}); sorting it would break apps."

    for folder in (resolved, *resolved.parents):
        if is_project_dir(folder):
            return (
                f"{target} is inside a project folder ({folder}); "
                "sorting it would break the project's layout."
            )

    return None


def ensure_safe_target(target: Path) -> None:
    """Raise UnsafeTargetError if `target` is refused."""
    reason = unsafe_target_reason(target)
    if reason is not None:
        raise UnsafeTargetError(reason)
