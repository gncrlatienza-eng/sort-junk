"""Tkinter GUI for SortJunk: folder picker -> dry-run preview -> confirm -> apply.

Wraps the exact same engine the CLI uses (pipeline.build_plan ->
executor.apply / executor.undo) -- this file adds no file-mutating logic of
its own. Scanning, applying, and undoing run on a background thread so the
window never freezes; results are marshaled back to the main thread through
a thread-safe queue polled with `root.after`, since Tkinter widgets may only
be touched from the main thread.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import (
    executor,
    history,
    ocr,
    pipeline,
    scanner,
    scheduler,
    settings,
    special_folders,
    target_guard,
)
from .config import ScanConfig
from .exceptions import ScanCancelled
from .models import ActionResult, ActionType, Plan

_DEFAULT_FOLDER_LOOKUP = {
    "downloads": special_folders.default_downloads_folder,
    "screenshots": special_folders.default_screenshots_folder,
}

_MODE_HELP = {
    "downloads": (
        "Sorts loose files in your Downloads folder into PDF, Docs, Images, Installers, Zip, "
        "Media and Others. Folders you already had stay where they are (unless you tick the "
        "option below). Files older than the archive age go into 'Storage'. Nothing is deleted."
    ),
    "screenshots": (
        "Sorts screenshots into <month>/Receipts, Errors_Code, Chats or Uncategorized "
        "(using OCR text when Tesseract is installed). Duplicates go to 'Duplicates_Found' "
        "for you to review. The oldest copy stays put. Nothing is deleted."
    ),
    "custom": (
        "Tidies any folder you pick: files from all subfolders are gathered into type "
        "folders, duplicates are set aside, files older than the archive age are zipped, "
        "and emptied folders are removed. Project folders (git repos, venvs, node_modules) "
        "are left untouched."
    ),
}

_MODE_NAMES = {"downloads": "Downloads", "screenshots": "Screenshots", "custom": "Custom folder"}

_ARCHIVE_DAYS_RANGE = (1, 3650)
_ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)

_TREE_COLUMNS = (
    ("action", "Action", 110),
    ("file", "File", 260),
    ("destination", "Moves to", 260),
    ("category", "Category", 100),
    ("size", "Size", 80),
    ("reason", "Why", 220),
)

_ACTION_LABELS = {
    ActionType.MOVE: "Move",
    ActionType.ARCHIVE: "Zip (archive)",
    ActionType.FLAG_DUPLICATE: "Duplicate",
    ActionType.SKIP: "Leave as is",
    ActionType.REMOVE_EMPTY_DIR: "Remove empty folder",
}

_TAG_COLORS = {
    "move": None,
    "archive": "#1f5fbf",
    "flag_duplicate": "#a15c00",
    "skip": "#808080",
    "remove_empty_dir": "#808080",
    "failed": "#c42b1c",
    "undo": "#6b2fa3",
}

_UNDO_LABELS = {
    ActionType.MOVE: "Move back",
    ActionType.FLAG_DUPLICATE: "Move back",
    ActionType.ARCHIVE: "Unzip back",
    ActionType.REMOVE_EMPTY_DIR: "Recreate folder",
}


def _assets_dir() -> Path:
    """Locate the bundled icon PNGs whether running from source or frozen.

    PyInstaller extracts bundled `datas` under `sys._MEIPASS` at runtime;
    running from source, they sit alongside this file at src/sortjunk/assets.
    """
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "sortjunk" / "assets"
    return Path(__file__).resolve().parent / "assets"


def _load_icon_photos() -> list[tk.PhotoImage]:
    """Load every bundled icon size as a PhotoImage for `wm iconphoto`.

    Deliberately not `iconbitmap` with a multi-size .ico: Tk's legacy Windows
    ICO reader only reliably picks one fixed-size frame and lets Windows
    stretch it for the titlebar/taskbar, which looks blurry at non-matching
    DPI scales. Handing Tk several exact-size PNGs directly lets Windows pick
    the closest match itself, the same way it does for the packaged .exe's
    own file icon.
    """
    photos = []
    for size in _ICON_SIZES:
        path = _assets_dir() / f"icon_{size}.png"
        try:
            photos.append(tk.PhotoImage(file=str(path)))
        except tk.TclError:
            continue
    return photos


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _relative(path: Path | None, root: Path) -> str:
    if path is None:
        return ""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _count_by_action(actions) -> str:
    counts: dict[str, int] = {}
    for item in actions:
        label = _ACTION_LABELS[item.action]
        counts[label] = counts.get(label, 0) + 1
    return ", ".join(f"{label}: {n:,}" for label, n in sorted(counts.items()))


class SortJunkApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("SortJunk")
        self.root.geometry("1080x660")
        self.root.minsize(760, 440)
        icon_photos = _load_icon_photos()
        if icon_photos:
            self.root.iconphoto(True, *icon_photos)
            self._icon_photos = icon_photos  # keep a reference -- Tk drops GC'd images

        self.plan: Plan | None = None
        self.work_queue: queue.Queue = queue.Queue()
        self._busy_kind: str | None = None  # "scan" | "apply" | "undo"
        self._busy_message = ""
        self._busy_start: float | None = None
        self._elapsed_job: str | None = None
        self._cancel_event = threading.Event()
        self._large_scan_event = threading.Event()
        self._large_scan_confirmed = False
        self._rows: list[tuple[tuple, str]] = []
        self.settings = settings.load()

        self._build_widgets()
        self.mode_var.set(self.settings.last_mode)
        self.skip_ocr_var.set(self.settings.skip_ocr)
        self.archive_days_var.set(str(self.settings.archive_after_days))
        self.move_folders_var.set(self.settings.move_existing_folders)
        self._on_mode_change()
        self.target_var.trace_add("write", lambda *_: self._refresh_undo_button())
        self._refresh_undo_button()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._poll_queue)

    # -- widget construction -------------------------------------------------

    def _build_widgets(self) -> None:
        # Its own frame, packed left-to-right -- deliberately NOT part of the
        # weighted grid below, so the mode choices stay grouped together
        # regardless of how wide the folder Entry stretches.
        mode_row = ttk.Frame(self.root, padding=(10, 10, 10, 0))
        mode_row.pack(fill="x")

        self.mode_var = tk.StringVar(value="downloads")
        ttk.Label(mode_row, text="Mode:").pack(side="left")
        for value, text in (
            ("downloads", "Downloads"),
            ("screenshots", "Screenshots"),
            ("custom", "Custom folder"),
        ):
            ttk.Radiobutton(
                mode_row,
                text=text,
                variable=self.mode_var,
                value=value,
                command=self._on_mode_change,
            ).pack(side="left", padx=(6, 0))

        self.mode_help_var = tk.StringVar()
        self.mode_help = ttk.Label(
            self.root,
            textvariable=self.mode_help_var,
            padding=(10, 4, 10, 0),
            foreground="#555555",
            wraplength=1000,
            justify="left",
        )
        self.mode_help.pack(fill="x")
        self.root.bind(
            "<Configure>",
            lambda e: (
                self.mode_help.configure(wraplength=max(400, self.root.winfo_width() - 30))
                if e.widget is self.root
                else None
            ),
        )

        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Folder:").grid(row=0, column=0, sticky="w")
        self.target_var = tk.StringVar()
        self.folder_entry = ttk.Entry(top, textvariable=self.target_var, width=70)
        self.folder_entry.grid(row=0, column=1, columnspan=2, sticky="we")
        self.browse_button = ttk.Button(top, text="Browse...", command=self._browse)
        self.browse_button.grid(row=0, column=3, padx=(6, 0))

        self.skip_ocr_var = tk.BooleanVar(value=False)
        self.ocr_check = ttk.Checkbutton(
            top, text="Skip OCR (faster; screenshots only)", variable=self.skip_ocr_var
        )
        self.ocr_check.grid(row=1, column=1, sticky="w", pady=(8, 0))

        ttk.Label(top, text="Archive after (days):").grid(row=1, column=2, sticky="e", pady=(8, 0))
        self.archive_days_var = tk.StringVar(value="180")
        self.archive_days_spinbox = ttk.Spinbox(
            top,
            from_=_ARCHIVE_DAYS_RANGE[0],
            to=_ARCHIVE_DAYS_RANGE[1],
            textvariable=self.archive_days_var,
            width=8,
        )
        self.archive_days_spinbox.grid(row=1, column=3, sticky="w", pady=(8, 0))

        self.move_folders_var = tk.BooleanVar(value=False)
        self.move_folders_check = ttk.Checkbutton(
            top,
            text="Also move folders I already have into 'My Folders' (Downloads only)",
            variable=self.move_folders_var,
        )
        self.move_folders_check.grid(row=2, column=1, columnspan=3, sticky="w", pady=(4, 0))

        top.columnconfigure(1, weight=1)

        button_row = ttk.Frame(self.root, padding=(10, 0))
        button_row.pack(fill="x")
        self.scan_button = ttk.Button(
            button_row, text="Scan (preview only)", command=self._start_scan
        )
        self.scan_button.pack(side="left")
        self.cancel_button = ttk.Button(button_row, text="Cancel", command=self._cancel_scan)
        self.apply_button = ttk.Button(
            button_row,
            text="Confirm and Apply Changes",
            command=self._confirm_and_apply,
            state="disabled",
        )
        self.apply_button.pack(side="left", padx=(8, 0))
        self.progress = ttk.Progressbar(button_row, mode="indeterminate", length=180)

        self.open_button = ttk.Button(button_row, text="Open Folder", command=self._open_folder)
        self.open_button.pack(side="right")
        self.undo_button = ttk.Button(
            button_row, text="Undo Last Sort...", command=self._confirm_and_undo
        )
        self.undo_button.pack(side="right", padx=(0, 8))
        self.auto_button = ttk.Button(
            button_row, text="Auto-Clean...", command=self._open_auto_clean_dialog
        )
        self.auto_button.pack(side="right", padx=(0, 8))

        status_row = ttk.Frame(self.root, padding=(10, 6, 10, 2))
        status_row.pack(fill="x")
        self.status_var = tk.StringVar(value="Pick a folder and click Scan.")
        ttk.Label(status_row, textvariable=self.status_var).pack(side="left")
        self.hide_skipped_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            status_row,
            text="Hide files left as is",
            variable=self.hide_skipped_var,
            command=self._render_rows,
        ).pack(side="right")

        tree_frame = ttk.Frame(self.root)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        columns = [c[0] for c in _TREE_COLUMNS]
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings")
        for col_id, heading, width in _TREE_COLUMNS:
            self.tree.heading(col_id, text=heading)
            self.tree.column(col_id, width=width, anchor="w")
        for tag, color in _TAG_COLORS.items():
            if color:
                self.tree.tag_configure(tag, foreground=color)
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.summary_var = tk.StringVar(value="")
        ttk.Label(self.root, textvariable=self.summary_var, padding=(10, 0, 10, 8)).pack(fill="x")

    # -- event handlers --------------------------------------------------------

    def _on_mode_change(self) -> None:
        mode = self.mode_var.get()
        if hasattr(self, "undo_button"):
            self._refresh_undo_button()
        self.mode_help_var.set(_MODE_HELP[mode])
        self.ocr_check.configure(state="normal" if mode == "screenshots" else "disabled")
        # Screenshots mode always sorts by month regardless of age -- this
        # setting has no effect there.
        self.archive_days_spinbox.configure(state="disabled" if mode == "screenshots" else "normal")
        self.move_folders_check.configure(state="normal" if mode == "downloads" else "disabled")

        self.plan = None
        self.apply_button.configure(state="disabled")
        self._set_rows([])
        self.summary_var.set("")

        if mode == "custom":
            # Force an explicit choice (or the last custom folder) rather than
            # silently reusing whatever Downloads/Screenshots had auto-filled.
            self.target_var.set(self.settings.last_custom_folder)
            self.folder_entry.configure(state="normal")
            self.browse_button.configure(state="normal")
        else:
            self._apply_default_folder(mode)
            self.folder_entry.configure(state="readonly")
            self.browse_button.configure(state="disabled")

    def _apply_default_folder(self, mode: str) -> None:
        """Fill the (read-only, for this mode) folder field with the detected folder.

        Setting the bound StringVar works regardless of the Entry's state --
        `readonly` only blocks direct keyboard editing by the user.
        """
        lookup = _DEFAULT_FOLDER_LOOKUP.get(mode)
        default_path = lookup() if lookup else None
        if default_path is not None:
            self.target_var.set(str(default_path))
        else:
            self.target_var.set(f"Could not auto-detect a {mode} folder -- use Custom mode instead")

    def _browse(self) -> None:
        chosen = filedialog.askdirectory()
        if chosen:
            self.target_var.set(chosen)

    def _open_folder(self) -> None:
        target = self.target_var.get().strip()
        if target and Path(target).is_dir():
            os.startfile(target)  # noqa: S606 - opens a folder the user chose in Explorer

    def _read_archive_days(self) -> int | None:
        low, high = _ARCHIVE_DAYS_RANGE
        try:
            days = int(self.archive_days_var.get().strip())
        except ValueError:
            days = None
        if days is None or not low <= days <= high:
            messagebox.showerror(
                "SortJunk", f"'Archive after (days)' must be a whole number from {low} to {high}."
            )
            return None
        return days

    def _start_scan(self) -> None:
        target = self.target_var.get().strip()
        if not target:
            messagebox.showerror("SortJunk", "Pick a folder first.")
            return
        target_path = Path(target)
        if not target_path.is_dir():
            messagebox.showerror("SortJunk", f"Not a folder: {target}")
            return
        unsafe_reason = target_guard.unsafe_target_reason(target_path)
        if unsafe_reason is not None:
            messagebox.showerror("SortJunk", f"SortJunk won't sort this folder.\n\n{unsafe_reason}")
            return
        archive_days = self._read_archive_days()
        if archive_days is None:
            return

        self.plan = None
        self._set_rows([])
        self.summary_var.set("")
        self._cancel_event.clear()

        mode = self.mode_var.get()
        config = ScanConfig(
            mode=mode,
            target_root=target_path.resolve(),
            archive_after_days=archive_days,
            move_existing_folders=self.move_folders_var.get(),
            on_progress=self._make_progress_callback(),
            **pipeline.mode_defaults(mode),
        )
        self._save_settings()
        self._start_busy("scan", "Scanning...")
        threading.Thread(
            target=self._scan_worker, args=(config, self.skip_ocr_var.get()), daemon=True
        ).start()

    def _cancel_scan(self) -> None:
        self._cancel_event.set()
        self._busy_message = "Cancelling..."

    def _confirm_and_apply(self) -> None:
        if self.plan is None or not self.plan.actions:
            return
        plan = self.plan
        changing = [a for a in plan.actions if a.action != ActionType.SKIP]
        if not changing:
            messagebox.showinfo("SortJunk", "Nothing to change -- everything is already sorted.")
            return

        notes = [
            "Nothing is overwritten; name clashes get a (1), (2)... suffix.",
            "Duplicates are only set aside in Duplicates_Found, never deleted.",
        ]
        if plan.mode == "custom":
            notes.append(
                "Files from subfolders are gathered into type folders at the top level, "
                "and folders left empty are removed."
            )
            if any(a.action == ActionType.ARCHIVE for a in changing):
                notes.append(
                    "Old files are zipped into _Archive_<month> folders; each original is "
                    "removed only after its zip is written and verified."
                )
        notes.append("You can reverse this sort afterwards with the Undo button.")

        confirmed = messagebox.askyesno(
            "Confirm changes",
            f"Apply these changes to:\n{plan.target_root}\n\n"
            f"{_count_by_action(changing)}\n"
            f"Data moved: {_human_size(plan.total_bytes_moved)}\n\n"
            + "\n".join(f"- {n}" for n in notes)
            + "\n\nProceed?",
            icon="warning",
        )
        if not confirmed:
            return

        self._start_busy("apply", "Applying changes -- please keep SortJunk open...")
        threading.Thread(target=self._apply_worker, args=(plan,), daemon=True).start()

    def _latest_undo_path(self) -> Path | None:
        """Newest not-yet-undone run for the current mode AND folder.

        Scoped on purpose: undo in Screenshots must never reverse a newer
        Downloads sort or auto-clean, and Custom only undoes the folder shown.
        """
        target = self.target_var.get().strip()
        if not target or not Path(target).is_dir():
            return None
        return history.latest_run(mode=self.mode_var.get(), target_root=Path(target))

    def _confirm_and_undo(self) -> None:
        name = _MODE_NAMES[self.mode_var.get()]
        log_path = self._latest_undo_path()
        if log_path is None:
            messagebox.showinfo("SortJunk", f"There's no {name} sort to undo for this folder.")
            self._refresh_undo_button()
            return
        try:
            run = history.load_run(log_path)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror("SortJunk", f"Could not read the run log:\n{exc}")
            return

        when = run.applied_at.astimezone().strftime("%Y-%m-%d %H:%M")
        kind = "automatic clean-up" if run.trigger == "auto" else "sort"
        self._show_undo_preview(run, f"Last {name} {kind} from {when}")
        self.root.update_idletasks()

        confirmed = messagebox.askyesno(
            "Undo last sort",
            f"Are you sure you want to undo the last {name} {kind}?\n\n"
            f"Folder: {run.target_root}\nWhen: {when}\n"
            f"Changes: {run.undoable_count:,} (listed in the main window)\n\n"
            "Each file goes back only if it's still where SortJunk put it and its original "
            "spot is free -- nothing is overwritten.",
            icon="warning",
        )
        if not confirmed:
            self._set_rows([])
            self.summary_var.set("")
            self.status_var.set("Undo cancelled. Nothing was changed.")
            return

        self._start_busy("undo", f"Undoing the last {name} {kind} -- please keep SortJunk open...")
        threading.Thread(target=self._undo_worker, args=(run,), daemon=True).start()

    def _on_close(self) -> None:
        if self._busy_kind in ("apply", "undo"):
            # Killing the worker mid-way would leave a half-applied run.
            messagebox.showwarning(
                "SortJunk",
                "SortJunk is still moving files. Please wait for it to finish before closing.",
            )
            return
        self._cancel_event.set()
        self._save_settings()
        self.root.destroy()

    def _save_settings(self) -> None:
        s = self.settings
        s.last_mode = self.mode_var.get()
        if s.last_mode == "custom":
            s.last_custom_folder = self.target_var.get().strip()
        s.skip_ocr = self.skip_ocr_var.get()
        s.move_existing_folders = self.move_folders_var.get()
        try:
            days = int(self.archive_days_var.get().strip())
            if _ARCHIVE_DAYS_RANGE[0] <= days <= _ARCHIVE_DAYS_RANGE[1]:
                s.archive_after_days = days
        except ValueError:
            pass
        try:
            settings.save(s)
        except OSError:
            pass  # remembering settings is a convenience, never worth an error box

    def _open_auto_clean_dialog(self) -> None:
        AutoCleanDialog(self.root, self.settings, on_saved=self._save_settings)

    def _refresh_undo_button(self) -> None:
        name = _MODE_NAMES[self.mode_var.get()]
        available = self._busy_kind is None and self._latest_undo_path() is not None
        self.undo_button.configure(
            text=f"Undo Last {name} Sort...", state="normal" if available else "disabled"
        )

    # -- busy/progress indicator ------------------------------------------------

    def _start_busy(self, kind: str, message: str) -> None:
        self._busy_kind = kind
        self._busy_message = message
        self._busy_start = time.monotonic()
        for button in (self.scan_button, self.apply_button, self.undo_button):
            button.configure(state="disabled")
        if kind == "scan":
            self.cancel_button.pack(side="left", padx=(8, 0), after=self.scan_button)
        self.progress.configure(mode="indeterminate")
        self.progress.pack(side="left", padx=(8, 0))
        self.progress.start(12)
        self._tick_elapsed()

    def _tick_elapsed(self) -> None:
        if self._busy_start is None:
            return
        elapsed = int(time.monotonic() - self._busy_start)
        self.status_var.set(f"{self._busy_message} ({elapsed}s)")
        self._elapsed_job = self.root.after(500, self._tick_elapsed)

    def _stop_busy(self) -> None:
        self._busy_kind = None
        self._busy_start = None
        if self._elapsed_job is not None:
            self.root.after_cancel(self._elapsed_job)
            self._elapsed_job = None
        self.progress.stop()
        self.progress.pack_forget()
        self.cancel_button.pack_forget()
        self.scan_button.configure(state="normal")
        self._refresh_undo_button()

    def _on_progress(self, stage: str, done: int, total: int) -> None:
        if self._cancel_event.is_set():
            return
        if total:
            self.progress.stop()
            self.progress.configure(mode="determinate", maximum=total, value=done)
            self._busy_message = f"{stage}... {done:,} of {total:,}"
        else:
            if str(self.progress.cget("mode")) == "determinate":
                self.progress.configure(mode="indeterminate")
                self.progress.start(12)
            self._busy_message = f"{stage}... {done:,} found"

    # -- background workers (must never touch Tkinter widgets directly) --------

    def _make_progress_callback(self):
        last_sent = [0.0]

        def _progress(stage: str, done: int, total: int) -> None:
            if self._cancel_event.is_set():
                raise ScanCancelled
            now = time.monotonic()
            if now - last_sent[0] >= 0.15:
                last_sent[0] = now
                self.work_queue.put(("progress", stage, done, total))

        return _progress

    def _scan_worker(self, config: ScanConfig, skip_ocr: bool) -> None:
        try:
            estimated = scanner.estimate_file_count(
                config.target_root, recursive=config.mode != "downloads"
            )
            if estimated > config.max_files:
                self._large_scan_event.clear()
                self.work_queue.put(("confirm_large_scan", estimated, config.max_files))
                self._large_scan_event.wait()
                if not self._large_scan_confirmed:
                    self.work_queue.put(("scan_aborted",))
                    return

            ocr_note = ""
            if config.mode == "screenshots":
                status = ocr.detect_tesseract()
                config.use_ocr = status.available and not skip_ocr
                if not status.available:
                    ocr_note = "Tesseract OCR not found, so screenshots are sorted by month only."
                elif not config.use_ocr:
                    ocr_note = "OCR skipped, so screenshots are sorted by month only."
            else:
                config.use_ocr = False

            plan = pipeline.build_plan(config)
            self.work_queue.put(("scan_done", plan, ocr_note))
        except ScanCancelled:
            self.work_queue.put(("scan_aborted",))
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI thread
            self.work_queue.put(("error", str(exc)))

    def _apply_worker(self, plan: Plan) -> None:
        try:
            results = executor.apply(plan)
            log_path = None
            try:
                log_path = history.save_run(plan, results)
            except OSError as exc:
                self.work_queue.put(("warning", f"Changes applied, but the undo log failed: {exc}"))
            self.work_queue.put(("apply_done", plan, results, log_path))
        except Exception as exc:  # noqa: BLE001
            self.work_queue.put(("error", str(exc)))

    def _undo_worker(self, run: history.RunLog) -> None:
        try:
            results = executor.undo(run)
            history.mark_undone(run.path)
            self.work_queue.put(("undo_done", run, results))
        except Exception as exc:  # noqa: BLE001
            self.work_queue.put(("error", str(exc)))

    # -- main-thread message handling -------------------------------------------

    def _poll_queue(self) -> None:
        try:
            while True:
                self._handle_message(self.work_queue.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _handle_message(self, message: tuple) -> None:
        kind = message[0]
        if kind == "progress":
            self._on_progress(*message[1:])
        elif kind == "confirm_large_scan":
            _, estimated, max_files = message
            self._large_scan_confirmed = messagebox.askyesno(
                "SortJunk",
                f"{estimated:,} files found, above the {max_files:,} soft limit.\n\n"
                "Scanning this many files (duplicate checks, and OCR in Screenshots mode) "
                "can take a long time. Continue anyway?",
            )
            self._large_scan_event.set()
        elif kind == "warning":
            messagebox.showwarning("SortJunk", message[1])
        elif kind == "scan_done":
            _, plan, ocr_note = message
            self._stop_busy()
            self._show_plan(plan, ocr_note)
        elif kind == "scan_aborted":
            self._stop_busy()
            self.status_var.set("Scan cancelled. Nothing was changed.")
        elif kind == "apply_done":
            _, plan, results, log_path = message
            self._stop_busy()
            self.plan = None
            self.apply_button.configure(state="disabled")
            self._show_results(plan.target_root, results, "Done", log_path is not None)
        elif kind == "undo_done":
            _, run, results = message
            self._stop_busy()
            self.plan = None
            self.apply_button.configure(state="disabled")
            self._show_results(run.target_root, results, "Undo finished", False)
        elif kind == "error":
            _, error_text = message
            self._stop_busy()
            self.status_var.set("Something went wrong -- see the message box.")
            self.apply_button.configure(state="disabled" if self.plan is None else "normal")
            messagebox.showerror("SortJunk", error_text)

    # -- result display ----------------------------------------------------------

    def _show_plan(self, plan: Plan, ocr_note: str) -> None:
        self.plan = plan
        root = plan.target_root
        rows = []
        for a in plan.actions:
            values = (
                _ACTION_LABELS[a.action],
                _relative(a.source, root),
                _relative(a.destination, root),
                a.category,
                _human_size(a.size_bytes) if a.action != ActionType.REMOVE_EMPTY_DIR else "",
                a.reason,
            )
            rows.append((values, a.action.value))
        self._set_rows(rows)

        changing = [a for a in plan.actions if a.action != ActionType.SKIP]
        note = f" {ocr_note}" if ocr_note else ""
        if plan.cloud_only_files:
            note += (
                f" {plan.cloud_only_files:,} OneDrive online-only file(s) were sorted by name "
                "and date only, so they aren't downloaded."
            )
        if changing:
            self.status_var.set(f"Preview ready -- nothing has been changed yet.{note}")
        else:
            self.status_var.set(f"Everything here is already sorted.{note}")
        self.summary_var.set(
            f"{plan.total_files:,} files scanned. {_count_by_action(plan.actions)}. "
            f"Data to move: {_human_size(plan.total_bytes_moved)}."
        )
        self.apply_button.configure(state="normal" if changing else "disabled")

    def _show_results(
        self, root: Path, results: list[ActionResult], title: str, undo_available: bool
    ) -> None:
        failed = [r for r in results if not r.succeeded]
        succeeded = len(results) - len(failed)
        rows = [
            (
                (
                    "Failed",
                    _relative(r.source, root),
                    _relative(r.destination, root),
                    r.category,
                    _human_size(r.size_bytes),
                    r.error or "",
                ),
                "failed",
            )
            for r in failed
        ]
        self._set_rows(rows)
        self.summary_var.set("")

        if failed:
            self.status_var.set(
                f"{title}. {succeeded:,} succeeded, {len(failed):,} failed -- listed below."
            )
        else:
            self.status_var.set(f"{title}. All {succeeded:,} change(s) succeeded.")
        undo_hint = "\n\nChanged your mind? Use the Undo button." if undo_available else ""
        messagebox.showinfo(
            "SortJunk",
            f"{title}: {succeeded:,} succeeded, {len(failed):,} failed.{undo_hint}",
        )

    def _show_undo_preview(self, run: history.RunLog, title: str) -> None:
        """List what undo would reverse: where each item is now -> where it goes back."""
        self.plan = None
        self.apply_button.configure(state="disabled")
        root = run.target_root
        rows = []
        for r in reversed(run.results):
            if not r.succeeded or r.action == ActionType.SKIP:
                continue
            is_dir = r.action == ActionType.REMOVE_EMPTY_DIR
            values = (
                _UNDO_LABELS[r.action],
                "" if is_dir else _relative(r.destination, root),
                _relative(r.source, root),
                r.category,
                "" if is_dir else _human_size(r.size_bytes),
                f"was: {_ACTION_LABELS[r.action].lower()}",
            )
            rows.append((values, "undo"))
        self._set_rows(rows)
        self.status_var.set(f"{title}: {len(rows):,} change(s) to reverse, listed below.")
        self.summary_var.set(
            "Columns: 'File' is where it is now, 'Moves to' is where it goes back."
        )

    def _set_rows(self, rows: list[tuple[tuple, str]]) -> None:
        self._rows = rows
        self._render_rows()

    def _render_rows(self) -> None:
        self.tree.delete(*self.tree.get_children())
        hide_skipped = self.hide_skipped_var.get()
        for values, tag in self._rows:
            if hide_skipped and tag == "skip":
                continue
            self.tree.insert("", "end", values=values, tags=(tag,))


class AutoCleanDialog:
    """Opt-in background clean-up: registers or removes the scheduled task."""

    def __init__(self, parent: tk.Tk, current: settings.Settings, on_saved) -> None:
        self.settings = current
        self.on_saved = on_saved
        self.parent = parent
        self.win = tk.Toplevel(parent)
        self.win.withdraw()  # hidden until centered, so it never flashes top-left
        self.win.title("Auto-Clean")
        self.win.resizable(False, False)
        self.win.transient(parent)

        frame = ttk.Frame(self.win, padding=16)
        frame.pack(fill="both", expand=True)

        actually_scheduled = scheduler.is_enabled()
        self.enabled_var = tk.BooleanVar(value=current.auto_clean_enabled and actually_scheduled)
        ttk.Checkbutton(
            frame,
            text="Clean up automatically in the background",
            variable=self.enabled_var,
            command=self._sync_state,
        ).pack(anchor="w")

        self.options = ttk.Frame(frame, padding=(20, 8, 0, 0))
        self.options.pack(fill="x")
        ttk.Label(self.options, text="How often:").grid(row=0, column=0, sticky="w")
        self.frequency_var = tk.StringVar(value=current.auto_clean_frequency)
        self._option_widgets = [
            ttk.Radiobutton(self.options, text="Daily", variable=self.frequency_var, value="daily"),
            ttk.Radiobutton(
                self.options, text="Weekly (Sundays)", variable=self.frequency_var, value="weekly"
            ),
        ]
        self._option_widgets[0].grid(row=0, column=1, sticky="w", padx=(8, 0))
        self._option_widgets[1].grid(row=0, column=2, sticky="w", padx=(8, 0))

        ttk.Label(self.options, text="Folders:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.downloads_var = tk.BooleanVar(value=current.auto_clean_downloads)
        self.screenshots_var = tk.BooleanVar(value=current.auto_clean_screenshots)
        for col, (text, var) in enumerate(
            (("Downloads", self.downloads_var), ("Screenshots", self.screenshots_var)), start=1
        ):
            widget = ttk.Checkbutton(self.options, text=text, variable=var)
            widget.grid(row=1, column=col, sticky="w", padx=(8, 0), pady=(6, 0))
            self._option_widgets.append(widget)

        ttk.Label(
            frame,
            text=(
                "Runs at 12:00 (or as soon as your PC is on after that) while you're signed "
                "in, using your current settings: archive age, OCR, and the 'My Folders' "
                "option. Files newer than 24 hours are never touched, and every automatic "
                "clean-up can be reversed with the Undo button in that folder's mode."
            ),
            wraplength=420,
            justify="left",
            foreground="#555555",
            padding=(0, 12, 0, 0),
        ).pack(anchor="w")

        last = history.last_run(trigger="auto")
        if last is not None:
            when = last.applied_at.astimezone().strftime("%Y-%m-%d %H:%M")
            undone = " (undone)" if last.undone_at else ""
            last_text = (
                f"Last automatic clean-up: {when}, {last.mode}, "
                f"{last.undoable_count:,} change(s){undone}."
            )
        else:
            last_text = "No automatic clean-up has run yet."
        ttk.Label(frame, text=last_text, padding=(0, 8, 0, 0)).pack(anchor="w")

        if actually_scheduled and scheduler.is_stale():
            ttk.Label(
                frame,
                text=(
                    "Auto-clean is set up for a different copy of SortJunk (it was moved "
                    "or replaced). Click Save to point it at this one."
                ),
                foreground=_TAG_COLORS["failed"],
                wraplength=420,
                justify="left",
                padding=(0, 8, 0, 0),
            ).pack(anchor="w")

        buttons = ttk.Frame(frame, padding=(0, 14, 0, 0))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Cancel", command=self.win.destroy).pack(side="right")
        ttk.Button(buttons, text="Save", command=self._save).pack(side="right", padx=(0, 8))

        self._sync_state()
        _center_over(self.win, parent)
        self.win.deiconify()
        self.win.grab_set()

    def _sync_state(self) -> None:
        state = "normal" if self.enabled_var.get() else "disabled"
        for widget in self._option_widgets:
            widget.configure(state=state)

    def _save(self) -> None:
        enabled = self.enabled_var.get()
        if enabled and not (self.downloads_var.get() or self.screenshots_var.get()):
            messagebox.showerror("Auto-Clean", "Pick at least one folder.", parent=self.win)
            return
        if enabled:
            problem = scheduler.exe_location_problem()
            if problem is not None:
                messagebox.showerror("Auto-Clean", problem, parent=self.win)
                return

        try:
            if enabled:
                scheduler.enable(self.frequency_var.get())
            else:
                scheduler.disable()
        except (scheduler.SchedulerError, OSError) as exc:
            messagebox.showerror(
                "Auto-Clean", f"Windows Task Scheduler refused the change:\n{exc}", parent=self.win
            )
            return

        s = self.settings
        s.auto_clean_enabled = enabled
        s.auto_clean_frequency = self.frequency_var.get()
        s.auto_clean_downloads = self.downloads_var.get()
        s.auto_clean_screenshots = self.screenshots_var.get()
        self.on_saved()
        self.win.destroy()
        if enabled:
            how_often = "every day" if s.auto_clean_frequency == "daily" else "every Sunday"
            messagebox.showinfo(
                "Auto-Clean",
                f"Auto-clean is on. It will run {how_often} at 12:00.",
                parent=self.parent,
            )
        else:
            messagebox.showinfo("Auto-Clean", "Auto-clean is off.", parent=self.parent)


def _center_over(win: tk.Toplevel, parent: tk.Misc) -> None:
    """Place `win` in the middle of `parent`, kept fully on the parent's screen."""
    win.update_idletasks()
    width, height = win.winfo_reqwidth(), win.winfo_reqheight()
    x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
    x = max(0, min(x, parent.winfo_screenwidth() - width))
    y = max(0, min(y, parent.winfo_screenheight() - height))
    win.geometry(f"+{x}+{y}")


def main() -> None:
    root = tk.Tk()
    SortJunkApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
