"""Shared test setup: lets tests import the c1 package (src/) and helpers.py without installing."""

import sys
from pathlib import Path

TESTS_FOLDER = Path(__file__).resolve().parent
SRC_FOLDER = TESTS_FOLDER.parent / "src"
sys.path.insert(0, str(SRC_FOLDER))
sys.path.insert(0, str(TESTS_FOLDER))  # so tests can import helpers.py
sys.path.insert(0, str(TESTS_FOLDER.parent))  # so tests can import stubs/c4_stub.py
