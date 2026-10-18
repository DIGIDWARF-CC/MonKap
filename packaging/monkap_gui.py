"""The entry point PyInstaller builds MonKap.exe from."""
import sys

from monkap.gui import main

if __name__ == "__main__":
    sys.exit(main())
