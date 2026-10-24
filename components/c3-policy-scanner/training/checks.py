"""Run the automatic data checks (fails on any breach).

Thin wrapper around c3.training.pipeline (blueprint section 18). Run from the component folder:

    python training/checks.py [--seed 7] [--epochs-gen 8]
"""
import sys

from c3.training.pipeline import main

if __name__ == "__main__":
    sys.argv[1:1] = ["--stage", "rows"]
    main()
