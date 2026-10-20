"""Validate every example against its schema.
   pip install jsonschema
   python contracts/validate.py                      # all examples
   python contracts/validate.py c1_event my.json     # one payload of yours
"""
import json, sys
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker

HERE = Path(__file__).parent

def check(name, payload):
    schema = json.loads((HERE / "schemas" / f"{name}.schema.json").read_text())
    return [e.message for e in Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload)]

if len(sys.argv) == 3:
    errs = check(sys.argv[1], json.loads(Path(sys.argv[2]).read_text()))
    print("VALID" if not errs else "INVALID:\n  " + "\n  ".join(errs)); sys.exit(bool(errs))

fail = 0
for f in sorted((HERE / "examples").glob("*.json")):
    errs = check(f.stem, json.loads(f.read_text()))
    print(("ok      " if not errs else "FAIL    ") + f.name, *errs); fail += bool(errs)
for f in sorted((HERE / "examples" / "invalid").glob("*.json")):
    errs = check(f.stem.split(".")[0], json.loads(f.read_text()))
    print(("rejected " if errs else "ACCEPTED ") + f.name); fail += not errs
print("\nall good" if not fail else f"\n{fail} problem(s)"); sys.exit(bool(fail))
