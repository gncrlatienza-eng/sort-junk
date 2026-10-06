"""PyInstaller entry point. Kept as a standalone script (not run as part of
the sortjunk package) so PyInstaller's static analysis has a plain, non
package-relative entry to start from.
"""

from sortjunk.gui import main

if __name__ == "__main__":
    main()
