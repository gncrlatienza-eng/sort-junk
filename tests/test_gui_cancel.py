"""Cancel must free the window at once, even if the scan worker never reports progress again."""

from __future__ import annotations

import threading
import time

import pytest

tk = pytest.importorskip("tkinter")


@pytest.fixture
def app(tmp_path, monkeypatch):
    from sortjunk import gui

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(gui.target_guard, "unsafe_target_reason", lambda p: None)
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    instance = gui.SortJunkApp(root)
    (tmp_path / "T").mkdir()
    instance.mode_var.set("custom")
    instance.target_var.set(str(tmp_path / "T"))
    yield instance
    root.destroy()


def _pump(app, seconds: float = 0.5) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.root.update()
        time.sleep(0.02)


def test_cancel_frees_window_while_worker_is_stuck(app, monkeypatch):
    from sortjunk import gui

    release = threading.Event()

    def stuck_build_plan(config):
        release.wait(5)  # a stage that never calls the progress callback
        return "stale plan"

    monkeypatch.setattr(gui.pipeline, "build_plan", stuck_build_plan)

    app._start_scan()
    _pump(app, 0.3)
    assert app._busy_kind == "scan"

    app._cancel_scan()
    _pump(app, 0.3)
    assert app._busy_kind is None
    assert "cancelled" in app.status_var.get().lower()
    assert str(app.scan_button.cget("state")) == "normal"

    # The abandoned worker finishing later must not overwrite the UI.
    release.set()
    _pump(app, 0.5)
    assert app.plan is None
    assert "cancelled" in app.status_var.get().lower()


def test_scan_button_label_has_no_preview_suffix(app):
    assert app.scan_button.cget("text") == "Scan"
