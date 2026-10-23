"""Document codes: created once at first upload, then stored (blueprint 12.1, FR20)."""
from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import PurePath

STOP = {"of", "the", "and", "for", "to", "a", "an", "in", "on", "policy", "document", "pdf", "md"}


def make_doc_code(filename: str, taken: Iterable[str] = ()) -> str:
    """'clinical-ai-guidelines-2024.pdf' -> 'CAIG'. Unique within the organisation."""
    stem = PurePath(filename).stem
    words = [w for w in re.split(r"[^A-Za-z]+", stem) if w and w.lower() not in STOP]
    if len(words) == 1:
        code = words[0][:4].upper()               # 'guide.md' -> 'GUID'
    else:
        parts = [w.upper() if len(w) <= 2 else w[0].upper() for w in words]
        code = "".join(parts)[:8]
    if len(code) < 2:
        code = "DOC"
    taken_set = set(taken)
    candidate, n = code, 2
    while candidate in taken_set:
        candidate = f"{code}{n}"
        n += 1
    return candidate
