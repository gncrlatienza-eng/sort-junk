# SortJunk

A local, safety-first cleanup tool for cluttered Windows Screenshots and Downloads folders.

> **Status: preview.** The scan/sort engine, a Tkinter GUI, and a packaged
> standalone `.exe` all work today.

## Features

- **Screenshots mode** — finds exact and near-duplicate screenshots (perceptual
  hashing) and, when Tesseract OCR is available, sorts them into
  `Receipts` / `Errors_Code` / `Chats` / `Uncategorized` by month, regardless
  of how old they are. Without Tesseract, it falls back to date-only
  categorization automatically.
- **Downloads mode** — sorts loose top-level files by type into `PDF`,
  `Docs`, `Images`, `Installers`, `Zip`, `Media`, `Others`, and leaves
  in-progress downloads (`.crdownload`, `.part`, `.tmp`) alone. Any folder
  you already had in Downloads is left completely alone — moved as a whole,
  sealed unit into `My Folders`, never opened or reorganized. Files that
  have sat untouched past the archive threshold (default 180 days) are
  moved into `Storage/<category>` — plain files, not zipped.
- **Finds your folders automatically** — the GUI pre-fills the target folder
  for whichever mode you pick, and the CLI's `--target` is optional, both
  via the real Windows known-folder API (so it's correct even when OneDrive
  or a custom setup has moved/renamed Downloads or Screenshots). You can
  always override it.
- **Dry-run by default** — every run prints a full preview of what would
  happen. Nothing on disk changes unless you pass `--apply` and confirm.
- **Never deletes** — duplicates are flagged into `Duplicates_Found/` for you
  to review, and in Downloads mode old files are moved (never deleted) into
  `Storage/<category>` rather than sorted with the rest.

## Download

Grab `SortJunk.exe` (or the zip with README + LICENSE) from the
[latest release](../../releases/latest) — no Python needed. Checksums are in
`SHA256SUMS.txt` on the same page.

## Requirements (running from source)

- Python 3.13+
- Tesseract OCR (optional) — only needed for text-based categorization in
  Screenshots mode. Without it, SortJunk still runs fine and sorts
  screenshots by month instead.
  [Install Tesseract for Windows](https://github.com/UB-Mannheim/tesseract/wiki)
  and either add it to your `PATH` or pass `--tesseract-cmd`.

## Installation

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
```

## Running the GUI

```powershell
.venv\Scripts\sortjunk-gui.exe
```

Pick a mode, browse to a folder, click **Scan (Dry Run)** to preview, then
**Confirm and Apply Changes** — which asks you to confirm a summary before
touching anything. This is the same engine the CLI uses; nothing is
duplicated or reimplemented for the GUI.

## Building the standalone .exe

```powershell
.venv\Scripts\pip install -e ".[build]"
.venv\Scripts\python -m PyInstaller SortJunk.spec --noconfirm
```

The result is `dist\SortJunk.exe` — a single file with no Python
installation required to run it, carrying the SortJunk icon and version
metadata (product name, version, description) baked in. `build/` and `dist/`
are gitignored, disposable output; `SortJunk.spec`, `SortJunk.ico`, and
`version_info.txt` are checked in and hand-customized, so build from the
spec rather than regenerating one from scratch.

## Usage (command line)

Dry run (default — nothing is changed):

```powershell
.venv\Scripts\python -m sortjunk.cli --mode downloads --target "C:\Users\you\Downloads"
.venv\Scripts\python -m sortjunk.cli --mode screenshots --target "C:\Users\you\Pictures\Screenshots"
```

Apply the plan (moves/archives files, after an interactive confirmation):

```powershell
.venv\Scripts\python -m sortjunk.cli --mode downloads --target "C:\Users\you\Downloads" --apply
```

Common flags:

| Flag | Purpose |
|---|---|
| `--apply` | Actually move/archive files (default is dry-run only) |
| `--yes` | Skip the interactive confirmation (for scripting) |
| `--skip-ocr` / `--fast` | Never run OCR, even if Tesseract is available |
| `--archive-after-days N` | Age threshold for archiving in Downloads mode (default 180); has no effect in Screenshots mode |
| `--tesseract-cmd PATH` | Point at `tesseract.exe` if it's not on `PATH` |
| `--max-files N` | Soft cap before SortJunk asks you to confirm a large scan |
| `-v` / `--verbose` | Debug-level logging |

Run `python -m sortjunk.cli --help` for the full list.

## Safety model

SortJunk is built so that a dry run structurally *cannot* touch your files:
scanning, categorizing, and planning are pure, read-only operations that
produce a `Plan`; only a separate, explicit apply step is allowed to move,
archive, or remove anything, and it only runs after `--apply` plus your
confirmation. Every destination path is validated to stay inside your
target folder before any move happens, filenames are sanitized, and
SortJunk never overwrites an existing file — it appends a `(1)`, `(2)`, ...
suffix instead. See [`src/sortjunk/pathsafety.py`](src/sortjunk/pathsafety.py)
and [`src/sortjunk/executor.py`](src/sortjunk/executor.py) for the details.

## Privacy & security

SortJunk is fully local: it never makes a network call, never phones home,
and stores nothing outside the folder you point it at (plus the audit
report next to it). There's nothing to configure and no account needed —
the source is here to inspect if you want to verify that yourself.

## Development

```powershell
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m black src tests
```

## Project structure

```
src/sortjunk/
    cli.py            # CLI entry point: scan -> categorize -> plan -> (confirm) -> apply
    gui.py             # Tkinter GUI, wraps the same engine as cli.py
    scanner.py          # read-only folder walk
    categorizer/          # screenshots.py (OCR + phash) and downloads.py (extension-based)
    planner.py              # builds the dry-run Plan; never touches disk
    executor.py               # the only module allowed to move/archive/delete files
    pathsafety.py               # containment checks, filename sanitization, collision handling
    archiver.py, ocr.py, hashing.py, models.py, config.py
run_gui.py           # PyInstaller entry point (see Building the standalone .exe)
tests/
```

## Roadmap

- Code-sign the release `.exe` to reduce SmartScreen warnings

## Releasing

Bump `version` in `pyproject.toml` and `version_info.txt`, commit, then push
a matching tag (`git tag v0.1.0 && git push origin v0.1.0`). GitHub Actions
runs the tests, builds `SortJunk.exe`, and publishes a GitHub Release.

Not planned: cloud sync, a backend of any kind, or Mac/Linux support (this
is a Windows-first tool — Task Scheduler and SmartScreen references are
Windows-specific).

## A note on SmartScreen

Once this ships as a packaged `.exe`, Windows SmartScreen and some antivirus
tools may flag it simply because it's unsigned and not yet widely
downloaded — that's expected for a small open-source tool and not a sign of
a problem. The source is public here so you can verify what it does.

## License

MIT — see [LICENSE](LICENSE).
