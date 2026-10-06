r"""Windows known-folder lookup for sensible default target paths.

Uses SHGetKnownFolderPath rather than guessing fixed paths like
`~/Downloads` or `~/Pictures/Screenshots`: both can be silently redirected
(OneDrive Known Folder Move, a user-relocated folder, a non-English
Windows install) -- the API is the only way to get the folder Windows
currently considers authoritative. Confirmed necessary in practice: on
this project's own dev machine, the registered Screenshots folder is
`OneDrive\Pictures\Screenshots 1`, not `Pictures\Screenshots`.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path

_FOLDERID_DOWNLOADS = "374DE290-123F-4565-9164-39C4925E467B"
_FOLDERID_SCREENSHOTS = "B7BEDE81-DF94-4682-A7D8-57A52620B86F"


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _known_folder_path(guid_str: str) -> Path | None:
    """Resolve a FOLDERID to a path, or None if unavailable (not Windows,
    folder not configured, or the lookup fails for any reason)."""
    try:
        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
        ole32 = ctypes.windll.ole32  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return None

    from uuid import UUID

    try:
        time_low, time_mid, time_hi, clock_hi, clock_lo, node = UUID(guid_str).fields
        data4 = bytes([clock_hi, clock_lo]) + node.to_bytes(6, "big")
        guid = _GUID(time_low, time_mid, time_hi, (ctypes.c_ubyte * 8)(*data4))

        fn = shell32.SHGetKnownFolderPath
        fn.argtypes = [
            ctypes.POINTER(_GUID),
            wintypes.DWORD,
            wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_wchar_p),
        ]
        fn.restype = ctypes.c_long

        path_ptr = ctypes.c_wchar_p()
        result = fn(ctypes.byref(guid), 0, None, ctypes.byref(path_ptr))
        if result != 0 or not path_ptr.value:
            return None

        try:
            path = Path(path_ptr.value)
        finally:
            ole32.CoTaskMemFree(path_ptr)
    except (OSError, ValueError):
        return None

    return path if path.is_dir() else None


def default_downloads_folder() -> Path | None:
    return _known_folder_path(_FOLDERID_DOWNLOADS)


def default_screenshots_folder() -> Path | None:
    return _known_folder_path(_FOLDERID_SCREENSHOTS)
