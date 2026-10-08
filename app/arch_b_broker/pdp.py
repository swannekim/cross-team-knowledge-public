"""Policy Decision Point for the Knowledge Broker.

Decisions, all driven by the sharing contract:
  * contract active; caller is a known directory user;
  * audience membership (contract.audienceGroupIds);
  * guest exclusion (directory userType == Guest OR token 'acct' == 1 -> deny; fail closed);
  * purpose must equal contract.purpose;
  * label ceiling and path scope on every retrieved chunk (defence in depth: also applied at ingestion);
  * per-user token-bucket rate limit (rateLimitPerMinute);
  * exfiltration guard: per user, per document, rolling 24 h unique-chunk coverage may not exceed
    exfiltrationCoverageThreshold (the first chunk of any document is always allowed so that very
    short documents remain answerable; each served chunk is still capped at maxExcerptChars);
  * verbatim cap (maxExcerptChars per citation) and max citations per answer.
"""
from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from common.contract import SharingContract
from common.directory import Directory
from common.text import truncate

from .constants import EXFILTRATION_WINDOW_HOURS, MAX_CITATIONS_PER_ANSWER


@dataclass(frozen=True)
class Decision:
    allowed: bool
    status: int = 200
    code: str = "ok"
    message: str = ""
    retry_after: Optional[int] = None


ALLOW = Decision(True)


class PolicyDecisionPoint:
    def __init__(self, contract: SharingContract, directory: Directory, clock, *,
                 max_citations: int = MAX_CITATIONS_PER_ANSWER, min_chunks_per_doc: int = 1,
                 window_hours: float = EXFILTRATION_WINDOW_HOURS):
        self.contract, self.directory, self.clock = contract, directory, clock
        self.max_citations, self.min_chunks_per_doc = max_citations, min_chunks_per_doc
        self.window = timedelta(hours=window_hours)
        self._lock = threading.RLock()
        self._buckets: dict = {}
        self._served: dict = {}

    # ------------------------------------------------------------------ request-level decisions
    def authorize(self, principal, purpose) -> Decision:
        c = self.contract
        if not c.is_active(self.clock.now()):
            return Decision(False, 403, "contract_inactive", "the sharing contract is not active")
        user = self.directory.find_user(principal.oid)
        if user is None:
            return Decision(False, 403, "unknown_user", "caller is not a directory user")
        if not any(gid in user.groups for gid in c.audienceGroupIds):
            return Decision(False, 403, "not_in_audience", "caller is not a member of the contract audience")
        if c.excludeGuests and (user.is_guest or principal.acct == 1):
            return Decision(False, 403, "guest_excluded", "guest users are excluded by the sharing contract")
        if purpose != c.purpose:
            return Decision(False, 403, "purpose_mismatch", f"purpose must be '{c.purpose}'")
        return ALLOW

    def consume_rate(self, principal) -> Decision:
        rate = self.contract.rateLimitPerMinute
        refill_per_second = rate / 60.0
        now = self.clock.now()
        with self._lock:
            tokens, last = self._buckets.get(principal.oid, (float(rate), now))
            tokens = min(float(rate), tokens + max(0.0, (now - last).total_seconds()) * refill_per_second)
            if tokens >= 1.0:
                self._buckets[principal.oid] = (tokens - 1.0, now)
                return ALLOW
            self._buckets[principal.oid] = (tokens, now)
            wait = math.ceil((1.0 - tokens) / refill_per_second)
            return Decision(False, 429, "rate_limited", f"limit is {rate} requests per minute", retry_after=wait)

    # ------------------------------------------------------------------ chunk-level decisions
    def chunk_permitted(self, chunk) -> bool:
        if not self.contract.label_allowed(chunk.label):
            return False
        if chunk.kind == "file":
            return self.contract.path_in_scope(chunk.path)[0]
        if chunk.kind in ("chatDigest", "chatFile"):
            source = self.contract.chat_source(chunk.chat_id or "")
            return source is not None and (chunk.kind == "chatDigest" or bool(source.get("includeChatFiles")))
        return False

    def _events(self, oid: str, doc_id: str) -> deque:
        events = self._served.setdefault((oid, doc_id), deque())
        horizon = self.clock.now() - self.window
        while events and events[0][0] < horizon:
            events.popleft()
        return events

    def coverage(self, oid: str, doc_id: str, total_chunks: int) -> float:
        with self._lock:
            return len({cid for _, cid in self._events(oid, doc_id)}) / max(1, total_chunks)

    def exfiltration_admit(self, oid: str, chunk, pending=()) -> bool:
        """True when serving `chunk` keeps the caller's rolling coverage of its document within the threshold.
        `pending` are chunks already admitted for the same response but not yet recorded as served."""
        with self._lock:
            seen = {cid for _, cid in self._events(oid, chunk.doc_id)}
            seen |= {c.chunk_id for c in pending if c.doc_id == chunk.doc_id}
            if chunk.chunk_id in seen:
                return True
            allowed = max(self.min_chunks_per_doc,
                          math.floor(self.contract.exfiltrationCoverageThreshold * chunk.total_chunks + 1e-9))
            return len(seen) + 1 <= allowed

    def record_served(self, oid: str, chunk) -> None:
        with self._lock:
            self._events(oid, chunk.doc_id).append((self.clock.now(), chunk.chunk_id))

    def cap_excerpt(self, text: str) -> str:
        return truncate(text, self.contract.maxExcerptChars)
