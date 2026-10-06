"""The interface both mode-specific categorizers implement.

planner.py consumes `list[CategoryDecision]` and never needs to know which
mode produced them.
"""

from __future__ import annotations

from typing import Protocol

from ..config import ScanConfig
from ..models import CategoryDecision, FileRecord


class Categorizer(Protocol):
    def categorize(
        self, records: list[FileRecord], config: ScanConfig
    ) -> list[CategoryDecision]: ...
