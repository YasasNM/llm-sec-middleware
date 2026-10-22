"""Delete ALL C4 data (the 5 collections) from the database. For demo data only.

python scripts/reset_demo_data.py --yes
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from c4.config import Settings  # noqa: E402
from c4.db import collections as col  # noqa: E402
from c4.db.mongo import get_db  # noqa: E402

if "--yes" not in sys.argv:
    sys.exit("This deletes all C4 data. Run again with --yes to confirm.")
settings = Settings()
db = get_db(settings.mongo_url)
for name in (col.EVENTS, col.HITS, col.FEATURES, col.PROFILES, col.ACTIONS):
    deleted = db[name].delete_many({}).deleted_count
    print(f"{name}: {deleted} documents deleted")
