"""Shared pytest fixtures for SortJunk tests."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest


@pytest.fixture
def make_file(tmp_path: Path):
    """Create a file under tmp_path, optionally backdating its mtime by `age_days`."""

    def _make_file(
        relative_path: str, content: bytes = b"hello", age_days: int | None = None
    ) -> Path:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        if age_days is not None:
            old_time = time.time() - age_days * 86400
            os.utime(path, (old_time, old_time))
        return path

    return _make_file
