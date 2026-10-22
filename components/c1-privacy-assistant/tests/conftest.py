"""Shared test setup: lets the tests import the c1 package from src/ without installing it."""

import sys
from pathlib import Path

SRC_FOLDER = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_FOLDER))
