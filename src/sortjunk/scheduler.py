"""Register / remove the opt-in auto-clean task in Windows Task Scheduler.

A per-user task (no admin rights needed) that runs at 12:00 daily or on
Sundays -- or as soon as the PC is on after a missed time -- only while the
user is signed in. It launches SortJunk with --auto-clean, which runs
headless using the saved settings (see autoclean.py).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape, unescape

from . import special_folders

TASK_NAME = "SortJunk Auto-Clean"
_CREATE_NO_WINDOW = 0x08000000


def auto_clean_command() -> tuple[str, str]:
    """(program, arguments) the scheduled task should run."""
    if getattr(sys, "frozen", False):
        return sys.executable, "--auto-clean"
    # From source: pythonw so no console window flashes up.
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    program = pythonw if pythonw.exists() else Path(sys.executable)
    return str(program), "-m sortjunk.autoclean"


def exe_location_problem() -> str | None:
    """Why the current SortJunk.exe is a bad thing to schedule, or None if it's fine.

    A task points at the exe's path, so an exe sitting in Downloads or a temp
    folder would break the task as soon as it's moved, sorted, or cleaned up.
    """
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    risky = [special_folders.default_downloads_folder(), os.environ.get("TEMP")]
    for folder in filter(None, risky):
        folder = Path(folder).resolve()
        if exe.is_relative_to(folder):
            return (
                f"SortJunk.exe is running from {folder}. Move it somewhere permanent "
                "first (for example a 'SortJunk' folder in Documents), then turn "
                "auto-clean on from there."
            )
    return None


def task_xml(frequency: str, program: str, arguments: str) -> str:
    if frequency == "daily":
        schedule = "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
    elif frequency == "weekly":
        schedule = (
            "<ScheduleByWeek><DaysOfWeek><Sunday /></DaysOfWeek>"
            "<WeeksInterval>1</WeeksInterval></ScheduleByWeek>"
        )
    else:
        raise ValueError(f"Unknown frequency: {frequency}")

    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Tidies Downloads/Screenshots. Turn off in SortJunk.</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-01-04T12:00:00</StartBoundary>
      <Enabled>true</Enabled>
      {schedule}
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(program)}</Command>
      <Arguments>{escape(arguments)}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed program, no shell, args we built
        ["schtasks", *args],  # noqa: S607 - schtasks.exe lives in System32
        capture_output=True,
        text=True,
        creationflags=_CREATE_NO_WINDOW,
        check=False,
    )


class SchedulerError(Exception):
    pass


def enable(frequency: str, *, task_name: str = TASK_NAME) -> None:
    program, arguments = auto_clean_command()
    xml = task_xml(frequency, program, arguments)
    fd, xml_path = tempfile.mkstemp(suffix=".xml")
    try:
        with os.fdopen(fd, "w", encoding="utf-16") as f:
            f.write(xml)
        result = _schtasks("/Create", "/F", "/TN", task_name, "/XML", xml_path)
    finally:
        os.unlink(xml_path)
    if result.returncode != 0:
        raise SchedulerError(result.stderr.strip() or result.stdout.strip())


def disable(*, task_name: str = TASK_NAME) -> None:
    if not is_enabled(task_name=task_name):
        return
    result = _schtasks("/Delete", "/F", "/TN", task_name)
    if result.returncode != 0:
        raise SchedulerError(result.stderr.strip() or result.stdout.strip())


def scheduled_program(*, task_name: str = TASK_NAME) -> str | None:
    """The program the existing task runs, or None if there's no task."""
    result = _schtasks("/Query", "/TN", task_name, "/XML")
    if result.returncode != 0:
        return None
    match = re.search(r"<Command>(.*?)</Command>", result.stdout, re.DOTALL)
    return unescape(match.group(1).strip()) if match else None


def is_stale() -> bool:
    """True if the task exists but points at a different SortJunk than this one
    (the exe was moved, renamed, or replaced by a newer download)."""
    program = scheduled_program()
    if program is None:
        return False
    current, _ = auto_clean_command()
    return os.path.normcase(program) != os.path.normcase(current)


def is_enabled(*, task_name: str = TASK_NAME) -> bool:
    return _schtasks("/Query", "/TN", task_name).returncode == 0
