import os
import sys

# PyInstaller GUI mode: sys.stdout/stderr are None, which breaks many libraries.
# This runtime hook runs before any third-party imports to fix stdio early.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")
