"""Append-only JSONL audit log with a SHA-256 hash chain (optionally keyed with HMAC).

Each record carries ``prevHash`` (hash of the previous record) and ``hash`` (hash of its own
canonical JSON without the ``hash`` field). ``verify()`` recomputes the chain and detects edits,
deletions, insertions and re-ordering.

Production note: ship records to immutable storage (Azure Blob immutability policy / WORM),
Microsoft Purview Audit or Log Analytics; a local hash chain only proves internal consistency.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .clock import SystemClock, to_iso
from .fingerprint import canonical_json

GENESIS = "0" * 64


@dataclass
class AuditVerification:
    ok: bool
    count: int
    error: Optional[str] = None
    bad_index: Optional[int] = None


class AuditLog:
    def __init__(self, path=None, *, clock=None, key: Optional[bytes] = None):
        self.path = Path(path) if path else None
        self._clock = clock or SystemClock()
        self._key = key
        self._lock = threading.RLock()
        self._memory: list = []
        self._handle = None
        self._seq = 0
        self._last_hash = GENESIS
        if self.path and self.path.exists():
            lines = [ln for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if lines:
                last = json.loads(lines[-1])
                self._seq, self._last_hash = last["seq"], last["hash"]

    def _digest(self, record: dict) -> str:
        body = canonical_json({k: v for k, v in record.items() if k != "hash"}).encode("utf-8")
        if self._key:
            return hmac.new(self._key, body, hashlib.sha256).hexdigest()
        return hashlib.sha256(body).hexdigest()

    def append(self, event: str, **data) -> dict:
        with self._lock:
            record = {
                "seq": self._seq + 1,
                "ts": to_iso(self._clock.now()),
                "event": event,
                "data": json.loads(json.dumps(data, default=str)),
                "prevHash": self._last_hash,
            }
            record["hash"] = self._digest(record)
            line = json.dumps(record, sort_keys=True, ensure_ascii=False)
            if self.path:
                if self._handle is None:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self._handle = self.path.open("a", encoding="utf-8")
                self._handle.write(line + "\n")
                self._handle.flush()
            else:
                self._memory.append(line)
            self._seq, self._last_hash = record["seq"], record["hash"]
            return record

    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                self._handle.close()
                self._handle = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def lines(self) -> list:
        with self._lock:
            if self.path:
                if not self.path.exists():
                    return []
                return [ln for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            return list(self._memory)

    def records(self, event: Optional[str] = None) -> list:
        out = [json.loads(line) for line in self.lines()]
        return [r for r in out if event is None or r["event"] == event]

    def count(self, event: Optional[str] = None) -> int:
        return len(self.records(event))

    def verify(self) -> AuditVerification:
        previous, expected_seq = GENESIS, 1
        lines = self.lines()
        for index, line in enumerate(lines):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                return AuditVerification(False, index, f"record {index}: not valid JSON", index)
            if record.get("seq") != expected_seq:
                return AuditVerification(False, index, f"record {index}: sequence gap (expected {expected_seq})", index)
            if record.get("prevHash") != previous:
                return AuditVerification(False, index, f"record {index}: prevHash does not match chain", index)
            if not hmac.compare_digest(str(record.get("hash")), self._digest(record)):
                return AuditVerification(False, index, f"record {index}: hash mismatch (record altered)", index)
            previous, expected_seq = record["hash"], expected_seq + 1
        return AuditVerification(True, len(lines))
