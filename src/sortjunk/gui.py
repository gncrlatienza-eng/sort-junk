"""Tkinter GUI for SortJunk: folder picker -> dry-run preview -> confirm -> apply.

Wraps the exact same scan -> categorize -> plan -> apply engine the CLI
uses -- this file adds no new file-mutating logic, it only calls into
scanner/categorizer/planner/executor. Scanning and applying run on a
background thread so the window never freezes; results are marshaled back
to the main thread through a thread-safe queue polled with `root.after`,
since Tkinter widgets may only be touched from the main thread.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import executor, ocr, planner, scanner, special_folders
from .categorizer import custom as custom_categorizer
from .categorizer import downloads as downloads_categorizer
from .categorizer import screenshots as screenshots_categorizer
from .config import ScanConfig
from .models import Plan

_DEFAULT_FOLDER_LOOKUP = {
    "downloads": special_folders.default_downloads_folder,
    "screenshots": special_folders.default_screenshots_folder,
}


_ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


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


_TREE_COLUMNS = (
    ("action", "Action", 100),
    ("source", "Source", 300),
    ("destination", "Destination", 300),
    ("category", "Category", 110),
    ("size", "Size (bytes)", 100),
)


class SortJunkApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("SortJunk")
        self.root.geometry("980x620")
        self.root.minsize(700, 400)
        icon_photos = _load_icon_photos()
        if icon_photos:
            self.root.iconphoto(True, *icon_photos)
            self._icon_photos = icon_photos  # keep a reference -- Tk drops GC'd images

        self.plan: Plan | None = None
        self.work_queue: queue.Queue = queue.Queue()
        self._busy_start: float | None = None
        self._elapsed_job: str | None = None
        self._large_scan_event = threading.Event()
        self._large_scan_confirmed = False

        self._build_widgets()
        self._on_mode_change()
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
        ttk.Radiobutton(
            mode_row,
            text="Downloads",
            variable=self.mode_var,
            value="downloads",
            command=self._on_mode_change,
        ).pack(side="left", padx=(6, 0))
        ttk.Radiobutton(
            mode_row,
            text="Screenshots",
            variable=self.mode_var,
            value="screenshots",
            command=self._on_mode_change,
        ).pack(side="left", padx=(6, 0))
        ttk.Radiobutton(
            mode_row,
            text="Custom",
            variable=self.mode_var,
            value="custom",
            command=self._on_mode_change,
        ).pack(side="left", padx=(6, 0))

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
        self.archive_days_var = tk.IntVar(value=180)
        self.archive_days_spinbox = ttk.Spinbox(
            top, from_=1, to=3650, textvariable=self.archive_days_var, width=8
        )
        self.archive_days_spinbox.grid(row=1, column=3, sticky="w", pady=(8, 0))

        top.columnconfigure(1, weight=1)

        button_row = ttk.Frame(self.root, padding=(10, 0))
        button_row.pack(fill="x")
        self.scan_button = ttk.Button(button_row, text="Scan", command=self._start_scan)
        self.scan_button.pack(side="left")
        self.apply_button = ttk.Button(
            button_row,
            text="Confirm and Apply Changes",
            command=self._confirm_and_apply,
            state="disabled",
        )
        self.apply_button.pack(side="left", padx=(8, 0))

        self.progress = ttk.Progressbar(button_row, mode="indeterminate", length=160)

        self.status_var = tk.StringVar(value="Pick a folder and click Scan.")
        ttk.Label(self.root, textvariable=self.status_var, padding=(10, 6)).pack(fill="x")

        tree_frame = ttk.Frame(self.root)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        columns = [c[0] for c in _TREE_COLUMNS]
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings")
        for col_id, heading, width in _TREE_COLUMNS:
            self.tree.heading(col_id, text=heading)
            self.tree.column(col_id, width=width, anchor="w")
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.summary_var = tk.StringVar(value="")
        ttk.Label(self.root, textvariable=self.summary_var, padding=(10, 0)).pack(fill="x")

    # -- event handlers --------------------------------------------------------

    def _on_mode_change(self) -> None:
        mode = self.mode_var.get()
        self.ocr_check.configure(state="normal" if mode == "screenshots" else "disabled")
        # Screenshots mode always sorts by month regardless of age -- this
        # setting has no effect there.
        self.archive_days_spinbox.configure(state="disabled" if mode == "screenshots" else "normal")

        if mode == "custom":
            # Force an explicit choice rather than silently reusing whatever
            # Downloads/Screenshots had auto-filled.
            self.target_var.set("")
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

    def _start_scan(self) -> None:
        target = self.target_var.get().strip()
        if not target:
            messagebox.showerror("SortJunk", "Pick a folder first.")
            return
        target_path = Path(target)
        if not target_path.is_dir():
            messagebox.showerror("SortJunk", f"Not a folder: {target}")
            return

        self.plan = None
        self.scan_button.configure(state="disabled")
        self.apply_button.configure(state="disabled")
        self.summary_var.set("")
        self.tree.delete(*self.tree.get_children())
        self._start_busy(
            "Scanning... this can take a few minutes for large or cloud-synced folders"
            " (files are hashed to find duplicates)."
        )

        mode = self.mode_var.get()
        config = ScanConfig(
            mode=mode,
            target_root=target_path.resolve(),
            archive_after_days=self.archive_days_var.get(),
            # Custom is a deliberate one-off cleanup: no freshness hold-back,
            # and it's the only mode that prunes folders left empty by sorting.
            min_age_hours=0 if mode == "custom" else 24,
            remove_empty_folders=(mode == "custom"),
        )
        threading.Thread(
            target=self._scan_worker, args=(config, self.skip_ocr_var.get()), daemon=True
        ).start()

    def _confirm_and_apply(self) -> None:
        if self.plan is None or not self.plan.actions:
            return

        counts: dict[str, int] = {}
        for action in self.plan.actions:
            counts[action.action.value] = counts.get(action.action.value, 0) + 1
        summary = "\n".join(f"  {k}: {v}" for k, v in sorted(counts.items()))

        confirmed = messagebox.askyesno(
            "Confirm changes",
            f"This will apply the following to:\n{self.plan.target_root}\n\n{summary}\n\n"
            f"Total bytes moved/archived: {self.plan.total_bytes_moved:,}\n\n"
            "Duplicates are only flagged into Duplicates_Found, never deleted. "
            "In Downloads mode, old files are moved into Storage, never deleted."
            "\n\nProceed?",
        )
        if not confirmed:
            return

        self.apply_button.configure(state="disabled")
        self.scan_button.configure(state="disabled")
        self._start_busy("Applying changes...")
        threading.Thread(target=self._apply_worker, args=(self.plan,), daemon=True).start()

    # -- busy/progress indicator ------------------------------------------------

    def _start_busy(self, message: str) -> None:
        self._busy_start = time.monotonic()
        self.progress.pack(side="left", padx=(8, 0))
        self.progress.start(12)
        self._tick_elapsed(message)

    def _tick_elapsed(self, message: str) -> None:
        if self._busy_start is None:
            return
        elapsed = int(time.monotonic() - self._busy_start)
        self.status_var.set(f"{message} ({elapsed}s elapsed)")
        self._elapsed_job = self.root.after(1000, self._tick_elapsed, message)

    def _stop_busy(self) -> None:
        self._busy_start = None
        if self._elapsed_job is not None:
            self.root.after_cancel(self._elapsed_job)
            self._elapsed_job = None
        self.progress.stop()
        self.progress.pack_forget()

    # -- background workers (must never touch Tkinter widgets directly) --------

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
                    ocr_note = "Tesseract not found -- using date-only categorization."
                elif not config.use_ocr:
                    ocr_note = "OCR skipped by request -- using date-only categorization."
            else:
                config.use_ocr = False

            existing_dirs: list[Path] = []
            if config.mode == "downloads":
                records = scanner.scan(
                    config.target_root, max_files=config.max_files, recursive=False
                )
                existing_dirs = scanner.list_top_level_dirs(
                    config.target_root,
                    excluded_names=downloads_categorizer.OWNED_TOP_LEVEL_NAMES,
                    excluded_prefixes=("_Archive_",),
                )
            else:
                records = scanner.scan(config.target_root, max_files=config.max_files)

            if config.mode == "screenshots":
                decisions = screenshots_categorizer.categorize(records, config)
            elif config.mode == "custom":
                decisions = custom_categorizer.categorize(records, config)
            else:
                decisions = downloads_categorizer.categorize(records, config)
            plan = planner.build_plan(records, decisions, config, existing_dirs=existing_dirs)
            self.work_queue.put(("scan_done", plan, ocr_note))
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI thread
            self.work_queue.put(("error", str(exc)))

    def _apply_worker(self, plan: Plan) -> None:
        try:
            results = executor.apply(plan)
            self.work_queue.put(("apply_done", results))
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
        self._stop_busy()
        if kind == "scan_done":
            _, plan, ocr_note = message
            self.plan = plan
            self._populate_tree(plan)
            note = f" {ocr_note}" if ocr_note else ""
            self.status_var.set(f"Dry run complete.{note}")
            self.summary_var.set(self._summary_text(plan))
            self.scan_button.configure(state="normal")
            self.apply_button.configure(state="normal" if plan.actions else "disabled")
        elif kind == "apply_done":
            _, results = message
            succeeded = sum(1 for r in results if r.succeeded)
            failed = len(results) - succeeded
            self.status_var.set(f"Done. {succeeded} succeeded, {failed} failed.")
            self.scan_button.configure(state="normal")
            self.apply_button.configure(state="disabled")
            self.plan = None
            messagebox.showinfo(
                "SortJunk",
                f"Applied {succeeded} action(s), {failed} failed.",
            )
        elif kind == "error":
            _, error_text = message
            self.status_var.set("Error -- see message box.")
            self.scan_button.configure(state="normal")
            self.apply_button.configure(state="disabled" if self.plan is None else "normal")
            messagebox.showerror("SortJunk", error_text)
        elif kind == "confirm_large_scan":
            _, estimated, max_files = message
            self._large_scan_confirmed = messagebox.askyesno(
                "SortJunk",
                f"{estimated} files found, above the {max_files} soft limit.\n\n"
                "Scanning this many files (hashing, and OCR in Screenshots mode) can "
                "take a long time. Continue anyway?",
            )
            if self._large_scan_confirmed:
                self._start_busy(
                    "Scanning... this can take a few minutes for large or "
                    "cloud-synced folders (files are hashed to find duplicates)."
                )
            self._large_scan_event.set()
        elif kind == "scan_aborted":
            self.status_var.set("Scan cancelled.")
            self.scan_button.configure(state="normal")

    def _populate_tree(self, plan: Plan) -> None:
        self.tree.delete(*self.tree.get_children())
        for action in plan.actions:
            destination = str(action.destination) if action.destination else ""
            self.tree.insert(
                "",
                "end",
                values=(
                    action.action.value,
                    str(action.source),
                    destination,
                    action.category,
                    action.size_bytes,
                ),
            )

    @staticmethod
    def _summary_text(plan: Plan) -> str:
        counts: dict[str, int] = {}
        for action in plan.actions:
            counts[action.action.value] = counts.get(action.action.value, 0) + 1
        parts = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
        return (
            f"{plan.total_files} files scanned -- {parts} -- "
            f"{plan.total_bytes_moved:,} bytes to move/archive"
        )


def main() -> None:
    root = tk.Tk()
    SortJunkApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
