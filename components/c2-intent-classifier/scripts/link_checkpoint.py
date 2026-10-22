#!/usr/bin/env python3
"""Copy or symlink Local_dev ELECTRA weights into models/classifier/ (gitignored)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

COMPONENT = Path(__file__).resolve().parents[1]
DEST = COMPONENT / "models" / "classifier" / "model.safetensors"
CANDIDATES = [
    COMPONENT.parents[2] / "Local_dev" / "llm-c2-tester" / "c2_classifier_final" / "model.safetensors",
]


def main() -> int:
    source = next((path for path in CANDIDATES if path.is_file()), None)
    if source is None:
        print("Could not find model.safetensors in Local_dev/llm-c2-tester/c2_classifier_final/")
        print("Download it from Google Drive MyDrive/LLM-C2/ and pass the path as argv[1].")
        if len(sys.argv) > 1:
            source = Path(sys.argv[1])
            if not source.is_file():
                return 1
        else:
            return 1
    DEST.parent.mkdir(parents=True, exist_ok=True)
    if DEST.exists() or DEST.is_symlink():
        DEST.unlink()
    try:
        DEST.symlink_to(source.resolve())
        print(f"Symlinked {DEST} -> {source.resolve()}")
    except OSError:
        shutil.copy2(source, DEST)
        print(f"Copied {source} -> {DEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
