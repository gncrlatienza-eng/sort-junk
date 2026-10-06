# SortJunk

**A safe, one-click tidy-up for messy Windows folders.**

> **Status: preview (v0.1).** Works today; feedback welcome via Issues.

## What is SortJunk?

If your Downloads folder is hundreds of installers, PDFs, and zips, and your
Screenshots folder is thousands of `Screenshot (1234).png` files, SortJunk
sorts them for you:

- **Downloads** → neat folders by type: `PDF`, `Docs`, `Images`,
  `Installers`, `Zip`, `Media`, `Others`, with anything untouched for months
  moved into `Storage`.
- **Screenshots** → one folder per month, split into `Receipts`,
  `Errors_Code`, `Chats`, and `Uncategorized` by reading the text in each
  image, with duplicates set aside for you to review.
- **Any other folder** you choose → the same tidy type folders.

It's built to be trusted with your files:

- **You see everything first.** Every sort starts as a preview listing
  each file, where it will go, and why. Nothing moves until you confirm.
- **One-click undo.** Changed your mind? Undo puts every file back.
- **Nothing is deleted or overwritten.** Duplicates are set aside, not
  removed, and name clashes get a `(1)` suffix.
- **It won't touch what it shouldn't.** System folders, your whole user
  profile, and code projects are refused, and files from the last 24 hours
  are left alone.
- **Fully offline.** No account, no internet, no tracking.

Optionally, it can keep things tidy for you with a weekly or daily
**Auto-Clean** that you switch on yourself.

## Setup (about 2 minutes)

1. **Download** `SortJunk.exe` from the
   [latest release](../../releases/latest). No installer and no Python
   needed. (Optional: compare its checksum with `SHA256SUMS.txt` on the same
   page.)
2. **Give it a permanent home.** Make a folder such as
   `Documents\SortJunk` and move `SortJunk.exe` into it. Don't leave it in
   Downloads, or SortJunk will be sorting the folder it lives in.
3. **Open it.** Double-click `SortJunk.exe`. If Windows shows "Windows
   protected your PC", click **More info → Run anyway**. That appears because
   the app is new and unsigned (see [SmartScreen](#a-note-on-smartscreen)).
4. **Try it safely first.** Choose **Custom folder**, click **Browse...**,
   and pick a *copy* of a messy folder. Click **Scan**, look
   through the list, then **Confirm and Apply Changes**. Try **Undo** to see
   everything go back.
5. **Use it for real.** Choose **Downloads** or **Screenshots**. SortJunk
   finds those folders by itself, even if OneDrive has moved them. Scan,
   check the preview, and apply.
6. **Optional: read text in screenshots.** Install
   [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) with its
   default settings so Screenshots mode can sort receipts, errors, and chats.
   SortJunk finds it automatically. Without it, screenshots are sorted by
   month only.
7. **Optional: turn on Auto-Clean.** Click **Auto-Clean...**, tick
   **Clean up automatically**, choose daily or weekly and which folders, then
   click **Save**. To stop it, untick the box and click Save again.

**To uninstall,** turn off Auto-Clean (if you turned it on), then delete
`SortJunk.exe` and the `%LOCALAPPDATA%\SortJunk` folder. Your sorted files
stay where they are.

## Features

- **Downloads mode** — sorts loose files by type into `PDF`, `Docs`,
  `Images`, `Installers`, `Zip`, `Media`, and `Others`, and leaves in-progress
  downloads (`.crdownload`, `.part`, `.tmp`) alone. Folders you already had in
  Downloads are never opened or reorganized: by default they stay exactly
  where they are, or — if you tick the option (`--move-folders`) — they're
  moved whole into `My Folders`. Files untouched past the archive age
  (default 180 days) go into `Storage/<category>` as plain files.
- **Screenshots mode** — sorts screenshots into
  `<month>/Receipts`, `Errors_Code`, `Chats`, or `Uncategorized`, using the
  text in the image when Tesseract OCR is installed (it's found automatically
  in its standard install folder) and by month alone when it isn't. Exact and
  near-duplicates go to `Duplicates_Found/` for you to review, while the
  oldest copy of each group is sorted normally.
- **Custom mode** — tidies any folder you pick: files from all subfolders are
  gathered into the same type folders, exact duplicates are set aside, files
  past the archive age are zipped into `_Archive_<month>/` (each original is
  removed only after its zip is written and verified), and folders left empty
  are removed.
- **Leaves new files alone** — in Downloads and Screenshots mode, anything
  created in the last 24 hours is skipped so SortJunk never moves a file
  you're still using. It gets sorted on a later run.
- **Undo** — every applied run is logged. The Undo button (**Undo Last
  Downloads / Screenshots / Custom folder Sort**) lists exactly what it will
  reverse and asks you to confirm. It only undoes the last sort for the mode
  and folder you have selected, so a newer sort elsewhere (or an auto-clean)
  is never undone by mistake. A file is only moved back if it's still where
  SortJunk put it and its original spot is free.
- **Auto-Clean (opt-in)** — off until you turn it on under **Auto-Clean...**.
  It adds a Windows Task Scheduler task for your account only (no admin
  rights) that tidies Downloads and/or Screenshots daily or every Sunday at
  12:00, or as soon as your PC is on after that. It uses your saved settings,
  and every automatic clean-up can be reversed with Undo in that mode.
  Activity is logged to `%LOCALAPPDATA%\SortJunk\auto-clean.log`. If you move
  or replace `SortJunk.exe`, open Auto-Clean and click Save to point the task
  at the new copy.
- **OneDrive-aware** — "online-only" files are sorted by name and date
  without being opened, so SortJunk never triggers a download of your cloud
  files.
- **Finds your folders automatically** — via the Windows known-folder API, so
  it's correct even when OneDrive or a custom setup has moved Downloads or
  Screenshots.
- **Never deletes your files** — duplicates are set aside, and old Downloads
  are moved into `Storage`. The only removals are Custom mode's verified
  zip-then-remove archiving and folders left empty.
- **Refuses dangerous folders** — a drive root, your whole user profile or
  OneDrive, Windows / Program Files / ProgramData, AppData, or anything inside
  a code project. Project folders found while scanning (git repos, Python
  venvs, `node_modules`) are left completely untouched.

## Safety model

Scanning, categorizing, and planning are pure, read-only operations that
produce a `Plan`; only a separate apply step is allowed to move, archive, or
remove anything, and it only runs after you confirm. Every destination is
validated to stay inside your target folder, filenames are sanitized
(including Unicode tricks that disguise a file's real extension), symlinks and
junctions are never followed or moved, and SortJunk never overwrites an
existing file — it appends `(1)`, `(2)`, ... instead. The target folder is
checked against the refused list before scanning and again right before
anything moves. See [`target_guard.py`](src/sortjunk/target_guard.py),
[`pathsafety.py`](src/sortjunk/pathsafety.py), and
[`executor.py`](src/sortjunk/executor.py).

## Privacy

SortJunk is fully local: it never makes a network call. The only things it
stores outside the folder you point it at live in `%LOCALAPPDATA%\SortJunk`:
your settings, a log of recent runs (used for undo; the newest 100 are kept),
and the auto-clean log. Turning on Auto-Clean adds one Windows scheduled task
named "SortJunk Auto-Clean"; turning it off removes it.

## A note on SmartScreen

Windows SmartScreen and some antivirus tools may warn about the `.exe` because
it's unsigned and not yet widely downloaded. That's expected for a small
open-source tool; click **More info → Run anyway**. The source is all here if
you want to verify what it does, and every release is built by GitHub Actions
from this repository.

## Running from source

Requires Python 3.13+. Tesseract OCR is optional
([Windows installer](https://github.com/UB-Mannheim/tesseract/wiki)).

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\sortjunk-gui.exe
```

### Command line

Dry run by default — nothing is changed:

```powershell
.venv\Scripts\python -m sortjunk.cli --mode downloads
.venv\Scripts\python -m sortjunk.cli --mode custom --target "C:\Users\you\Desktop\Mess"
```

Add `--apply` to make the changes (after a confirmation prompt).

| Flag | Purpose |
|---|---|
| `--mode` | `downloads`, `screenshots`, or `custom` |
| `--target PATH` | Folder to sort (optional for downloads/screenshots; required for custom) |
| `--apply` | Actually make the changes (default is dry-run only) |
| `--yes` | Skip the confirmation prompt (for scripting) |
| `--undo` | Undo the last sort; add `--mode` (and `--target` for custom) to undo only that folder's last sort |
| `--move-folders` | Downloads mode: also move existing folders into `My Folders` |
| `--archive-after-days N` | Archive age for Downloads and Custom mode (default 180) |
| `--skip-ocr` / `--fast` | Never run OCR, even if Tesseract is available |
| `--tesseract-cmd PATH` | Use this `tesseract.exe` instead of auto-detecting it |
| `--max-files N` | Soft cap before SortJunk asks you to confirm a large scan |
| `-v` / `--verbose` | Debug-level logging |

### Development

```powershell
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m black src tests
```

### Building the .exe

```powershell
.venv\Scripts\pip install -e ".[build]"
.venv\Scripts\python -m PyInstaller packaging\SortJunk.spec --noconfirm
```

The result is `dist\SortJunk.exe`, a single file with the icon and version
metadata baked in. Everything in `packaging/` is hand-customized and checked
in; `build/` and `dist/` are disposable output.

### Releasing

Bump `version` in `pyproject.toml` and `packaging/version_info.txt`, commit,
then push a matching tag (`git tag v0.1.0 && git push origin v0.1.0`). GitHub
Actions runs the tests, builds `SortJunk.exe`, and publishes a GitHub Release
with a zip and SHA-256 checksums.

## Project structure

```
src/sortjunk/
    gui.py              Tkinter GUI
    cli.py              command line
    pipeline.py         shared read-only scan -> categorize -> plan
    scanner.py          read-only folder walk
    categorizer/        downloads.py (by type), screenshots.py (OCR + duplicates), custom.py
    planner.py          builds the dry-run Plan; never touches disk
    executor.py         the only module allowed to move/archive/remove files (and undo)
    target_guard.py     refuses drive roots, system folders, user profile, projects
    pathsafety.py       containment checks, filename sanitization, collisions, links
    history.py          per-run log in %LOCALAPPDATA%, used for undo
    settings.py         remembered options
    scheduler.py        opt-in auto-clean task (Windows Task Scheduler, per user)
    autoclean.py        headless run launched by that task (SortJunk.exe --auto-clean)
    archiver.py, hashing.py, ocr.py, special_folders.py, models.py, config.py
packaging/              PyInstaller spec, icon, version info, exe entry point
tests/
```

## Roadmap

- Code-sign the release `.exe` to reduce SmartScreen warnings.

Not planned: cloud sync, accounts, or Mac/Linux support — SortJunk is a
Windows tool and stays fully local.

## License

MIT — see [LICENSE](LICENSE).
