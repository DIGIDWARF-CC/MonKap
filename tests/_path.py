"""Make `import monkap` work when the tests run from anywhere (no GUI, no GPU, no network).

Run:  python -m unittest discover -s tests
"""
import os
import sys

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC not in sys.path:
    sys.path.insert(0, SRC)
