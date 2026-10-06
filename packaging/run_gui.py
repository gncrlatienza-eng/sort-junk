"""PyInstaller entry point. Kept as a standalone script (not run as part of
the sortjunk package) so PyInstaller's static analysis has a plain, non
package-relative entry to start from.

`SortJunk.exe --auto-clean` is what the opt-in scheduled task runs: a
headless clean-up with the saved settings, no window.
"""

import sys

if __name__ == "__main__":
    if "--auto-clean" in sys.argv[1:]:
        from sortjunk.autoclean import main
    else:
        from sortjunk.gui import main
    main()
