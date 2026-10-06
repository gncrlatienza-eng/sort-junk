"""Tests for target_guard: refusing folders where a sort would cause real damage."""

from __future__ import annotations

from pathlib import Path

import pytest

from sortjunk import executor, scanner, target_guard
from sortjunk.models import Plan


@pytest.fixture
def fake_env(tmp_path: Path) -> dict[str, str]:
    """A fake Windows layout rooted in tmp_path, so tests never depend on the real machine."""
    layout = {
        "SystemRoot": "Windows",
        "ProgramFiles": "Program Files",
        "ProgramFiles(x86)": "Program Files (x86)",
        "ProgramData": "ProgramData",
        "APPDATA": "Users/me/AppData/Roaming",
        "LOCALAPPDATA": "Users/me/AppData/Local",
        "TEMP": "Users/me/AppData/Local/Temp",
    }
    env = {}
    for key, rel in layout.items():
        path = tmp_path / rel
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    (tmp_path / "Users/me/Downloads").mkdir(parents=True)
    return env


def _reason(target: Path, env: dict[str, str], tmp_path: Path) -> str | None:
    return target_guard.unsafe_target_reason(target, env=env, home=tmp_path / "Users/me")


def test_normal_user_folder_is_allowed(tmp_path, fake_env):
    assert _reason(tmp_path / "Users/me/Downloads", fake_env, tmp_path) is None


def test_drive_root_is_refused(fake_env, tmp_path):
    anchor = Path(tmp_path.anchor)
    assert _reason(anchor, fake_env, tmp_path) is not None


def test_home_folder_itself_is_refused(tmp_path, fake_env):
    assert _reason(tmp_path / "Users/me", fake_env, tmp_path) is not None


def test_folder_containing_home_is_refused(tmp_path, fake_env):
    assert _reason(tmp_path / "Users", fake_env, tmp_path) is not None


@pytest.mark.parametrize(
    "rel",
    [
        "Windows",
        "Windows/System32",
        "Program Files/SomeApp",
        "Program Files (x86)",
        "ProgramData/x",
    ],
)
def test_system_folders_and_their_contents_are_refused(tmp_path, fake_env, rel):
    target = tmp_path / rel
    target.mkdir(parents=True, exist_ok=True)
    assert _reason(target, fake_env, tmp_path) is not None


def test_app_data_is_refused(tmp_path, fake_env):
    target = tmp_path / "Users/me/AppData/Roaming/SomeApp"
    target.mkdir(parents=True)
    assert _reason(target, fake_env, tmp_path) is not None


def test_temp_inside_local_app_data_is_allowed(tmp_path, fake_env):
    target = tmp_path / "Users/me/AppData/Local/Temp/scratch"
    target.mkdir(parents=True)
    assert _reason(target, fake_env, tmp_path) is None


def test_git_repository_is_refused(tmp_path, fake_env):
    repo = tmp_path / "Users/me/code/project"
    (repo / ".git").mkdir(parents=True)
    assert _reason(repo, fake_env, tmp_path) is not None


def test_subfolder_of_git_repository_is_refused(tmp_path, fake_env):
    repo = tmp_path / "Users/me/code/project"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    assert _reason(repo / "src", fake_env, tmp_path) is not None


def test_recursive_scan_never_descends_into_a_project_folder(tmp_path, make_file):
    make_file("Target/loose.pdf")
    make_file("Target/myrepo/.git/HEAD")
    make_file("Target/myrepo/main.py")
    make_file("Target/venv/pyvenv.cfg")
    make_file("Target/venv/Lib/site.py")
    make_file("Target/web/node_modules/pkg/index.js")
    make_file("Target/notes/todo.txt")

    names = {r.path.name for r in scanner.scan(tmp_path / "Target")}

    assert names == {"loose.pdf", "todo.txt"}


def test_executor_refuses_an_unsafe_plan_root(tmp_path, monkeypatch):
    monkeypatch.setattr(target_guard, "unsafe_target_reason", lambda *a, **k: "nope")
    plan = Plan(mode="custom", target_root=tmp_path, generated_at=None)  # type: ignore[arg-type]

    with pytest.raises(target_guard.UnsafeTargetError):
        executor.apply(plan)


def test_onedrive_root_is_refused(tmp_path, fake_env):
    onedrive = tmp_path / "Users/me/OneDrive"
    (onedrive / "Pictures").mkdir(parents=True)
    env = {**fake_env, "OneDrive": str(onedrive)}

    assert _reason(onedrive, env, tmp_path) is not None
    assert _reason(onedrive / "Pictures", env, tmp_path) is None
