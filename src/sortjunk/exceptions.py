"""Exception types used across SortJunk."""


class SortJunkError(Exception):
    """Base class for all SortJunk errors."""


class PathSafetyError(SortJunkError):
    """Raised when a computed destination path fails a safety check."""


class ScanCancelled(SortJunkError):
    """Raised from a progress callback to stop a scan early (nothing is changed)."""
