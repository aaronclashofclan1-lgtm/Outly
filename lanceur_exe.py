"""Windows executable entry point for Outly.

PyInstaller starts this file. `outly.lanceur` chooses the GUI for a double-click
and the CLI when a known command or global option is supplied.
"""

from outly.lanceur import main

if __name__ == "__main__":
    raise SystemExit(main())
