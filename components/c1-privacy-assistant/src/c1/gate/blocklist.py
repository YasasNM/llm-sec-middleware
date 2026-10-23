"""M1 block-list gate: is this user blocked by an admin in C4? (K12, BLUEPRINT 10.2)

How it works:
1. C1 asks C4: GET {C4_URL}/v1/blocklist?org_id=... with its X-Service-Key.
2. The answer is checked against the c4_blocklist_response contract, then cached per org.
3. A cached list younger than 30 s is used without asking C4 again.
4. If C4 fails, the last good list is still used while it is at most 300 s old.
5. Older than that, or never loaded: BlocklistUnavailable. /v1/check then fails closed (503).

Only ids are handled here. No prompt text ever reaches this module.
"""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger("c1.gate")

BLOCKLIST_PATH = "/v1/blocklist"
ID_PATTERN = r"^[A-Za-z0-9_.:-]+$"
TIMESTAMP_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$"
FETCH_TIMEOUT_SECONDS = 1.0


class BlocklistUnavailable(Exception):
    """No block list fresh enough to trust (none loaded, or the last one is over 300 s old)."""


class BlocklistFetchError(Exception):
    """C4 could not be reached, or it answered with something that breaks the contract."""


# One id as the contracts define it: 1 to 64 letters, digits or _ . : -
Id = Annotated[str, Field(min_length=1, max_length=64, pattern=ID_PATTERN)]


class BlocklistResponse(BaseModel):
    """Mirror of contracts/schemas/c4_blocklist_response.schema.json (checked by contract tests)."""

    model_config = ConfigDict(strict=True)

    org_id: Id
    user_ids: list[Id]
    generated_at: str = Field(pattern=TIMESTAMP_PATTERN)


@dataclass
class CachedList:
    """One org's block list and when C1 last received it from C4 (monotonic seconds)."""

    user_ids: frozenset[str]
    fetched_at: float


# Check C4's answer against the contract (and that it is for the org we asked about)
def parse_blocklist(payload: object, org_id: str) -> frozenset[str]:
    try:
        answer = BlocklistResponse.model_validate(payload)
    except ValidationError as error:
        raise BlocklistFetchError("block list breaks the contract") from error
    if len(set(answer.user_ids)) != len(answer.user_ids):  # the schema says uniqueItems
        raise BlocklistFetchError("block list has repeated user ids")
    if answer.org_id != org_id:
        raise BlocklistFetchError("block list is for another org")
    return frozenset(answer.user_ids)


# Ask C4 for one org's block list over HTTP; any failure becomes BlocklistFetchError
def fetch_from_c4(c4_url: str, service_key: str, org_id: str) -> object:
    query = urllib.parse.urlencode({"org_id": org_id})
    request = urllib.request.Request(
        f"{c4_url.rstrip('/')}{BLOCKLIST_PATH}?{query}", headers={"X-Service-Key": service_key}
    )
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise BlocklistFetchError("C4 block list request failed") from error


class BlocklistCache:
    """Per-org cache of C4's block list with the 30 s fresh and 300 s stale rules."""

    # Remember where C4 is and the two time limits; fetch and clock can be swapped in tests
    def __init__(
        self,
        c4_url: str,
        service_key: str,
        fresh_seconds: float = 30.0,
        max_stale_seconds: float = 300.0,
        fetch: Callable[[str], object] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.fresh_seconds = fresh_seconds
        self.max_stale_seconds = max_stale_seconds
        self.fetch = fetch or (lambda org_id: fetch_from_c4(c4_url, service_key, org_id))
        self.clock = clock
        self.lists: dict[str, CachedList] = {}

    # True when the user is on the org's block list; raises BlocklistUnavailable if unsure
    def is_blocked(self, org_id: str, user_id: str) -> bool:
        return user_id in self.current_list(org_id)

    # The org's list: cached if fresh, else from C4, else the stale copy within the limit
    def current_list(self, org_id: str) -> frozenset[str]:
        cached = self.lists.get(org_id)
        if cached and self.age_of(cached) <= self.fresh_seconds:
            return cached.user_ids
        try:
            user_ids = parse_blocklist(self.fetch(org_id), org_id)
        except BlocklistFetchError:
            return self.stale_or_fail(org_id, cached)
        self.lists[org_id] = CachedList(user_ids, self.clock())
        return user_ids

    # C4 failed: use the last good list if it is at most 300 s old, otherwise fail closed
    def stale_or_fail(self, org_id: str, cached: CachedList | None) -> frozenset[str]:
        if cached and self.age_of(cached) <= self.max_stale_seconds:
            logger.warning("C4 block list unavailable, using cached list org_id=%s", org_id)
            return cached.user_ids
        logger.warning("C4 block list unavailable and no usable cache org_id=%s", org_id)
        raise BlocklistUnavailable(org_id)

    # Seconds since a cached list was received
    def age_of(self, cached: CachedList) -> float:
        return self.clock() - cached.fetched_at

    # Age in seconds of the oldest cached list, for /health; None when nothing is cached
    def oldest_age(self) -> float | None:
        if not self.lists:
            return None
        return round(max(self.age_of(cached) for cached in self.lists.values()), 1)
