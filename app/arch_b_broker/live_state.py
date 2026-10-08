"""Single-process SQLite rate, coverage, reference-key and audit persistence.

This backend is used on a designated local disk. Azure Files failed SQLite COMMIT
in the demo deployment and is unsupported. live_blob.py checkpoints this local
database to a leased blob for deployed durability. This is not immutable audit.
"""
from __future__ import annotations

import hashlib
import json
import math
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from .pdp import ALLOW, Decision, PolicyDecisionPoint


class DurableState:
    def __init__(self, path, binding, *, initialize=True):
        path = Path(path)
        if not path.is_absolute() or not path.parent.is_dir():
            raise ValueError("state path must be absolute with an existing persistent parent directory")
        existing = path.exists() and path.stat().st_size > 0
        if not existing and not initialize:
            raise ValueError("an existing database checkpoint is required")
        self.lock = threading.RLock()
        self.closed = False
        self.db = sqlite3.connect(str(path), timeout=2, isolation_level=None, check_same_thread=False)
        try:
            self.db.execute("PRAGMA trusted_schema=OFF")
            if existing:
                self._validate(binding)
            self.db.execute("PRAGMA journal_mode=DELETE")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA locking_mode=EXCLUSIVE")
            with self.transaction():
                if not existing:
                    self.db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value BLOB NOT NULL)")
                    self.db.execute("""CREATE TABLE rate (
                        oid TEXT PRIMARY KEY, tokens REAL NOT NULL, last REAL NOT NULL)""")
                    self.db.execute("""CREATE TABLE coverage (
                        oid TEXT NOT NULL, doc TEXT NOT NULL, chunk TEXT NOT NULL, last REAL NOT NULL,
                        PRIMARY KEY (oid, doc, chunk))""")
                    self.db.execute("""CREATE TABLE audit (
                        seq INTEGER PRIMARY KEY, payload TEXT NOT NULL, hash TEXT NOT NULL)""")
                    self.db.execute("INSERT INTO meta VALUES ('binding', ?)", (binding,))
                    self.db.execute("INSERT INTO meta VALUES ('ref_key', ?)", (secrets.token_bytes(32),))
                    self._validate(binding)
                self.ref_key = self.db.execute("SELECT value FROM meta WHERE key='ref_key'").fetchone()[0]
        except BaseException:
            self.db.close()
            self.closed = True
            raise

    def _validate(self, binding):
        """Never repair a checkpoint: a missing key/table could reset replay controls."""
        schema = {
            "meta": [("key", "TEXT", 0, 1), ("value", "BLOB", 1, 0)],
            "rate": [("oid", "TEXT", 0, 1), ("tokens", "REAL", 1, 0), ("last", "REAL", 1, 0)],
            "coverage": [("oid", "TEXT", 1, 1), ("doc", "TEXT", 1, 2),
                         ("chunk", "TEXT", 1, 3), ("last", "REAL", 1, 0)],
            "audit": [("seq", "INTEGER", 0, 1), ("payload", "TEXT", 1, 0), ("hash", "TEXT", 1, 0)],
        }
        objects = self.db.execute("SELECT type, name, sql FROM sqlite_master").fetchall()
        if ({name for kind, name, _ in objects if kind == "table"} != set(schema)
                or any(kind not in ("table", "index") or
                       (kind == "index" and (sql is not None or name not in {
                           "sqlite_autoindex_meta_1", "sqlite_autoindex_rate_1", "sqlite_autoindex_coverage_1"}))
                       for kind, name, sql in objects)):
            raise ValueError("checkpoint schema is invalid")
        for table, columns in schema.items():
            actual = self.db.execute(f"PRAGMA table_xinfo({table})").fetchall()
            if ([(r[1], r[2], r[3], r[5]) for r in actual] != columns
                    or any(r[4] is not None or r[6] != 0 for r in actual)):
                raise ValueError("checkpoint columns are invalid")
        if self.db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("persistent state integrity check failed")
        meta = dict(self.db.execute("SELECT key, value FROM meta"))
        if (set(meta) != {"binding", "ref_key"} or meta["binding"] != binding
                or not isinstance(meta["ref_key"], bytes) or len(meta["ref_key"]) != 32):
            raise ValueError("checkpoint binding or reference key is invalid")
        for table, text_count in (("rate", 1), ("coverage", 3)):
            for row in self.db.execute(f"SELECT * FROM {table}"):
                if (any(not isinstance(v, str) or not v for v in row[:text_count])
                        or any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
                               for v in row[text_count:])):
                    raise ValueError("checkpoint policy counters are invalid")
        previous = "0" * 64
        for expected, (seq, payload, digest) in enumerate(
                self.db.execute("SELECT seq, payload, hash FROM audit ORDER BY seq"), 1):
            try:
                record = json.loads(payload)
                valid = (seq == expected and isinstance(record, dict)
                         and set(record) == {"seq", "ts", "event", "data", "prevHash"}
                         and type(record["seq"]) is int and record["seq"] == seq
                         and record["prevHash"] == previous and isinstance(record["ts"], str)
                         and isinstance(record["event"], str) and isinstance(record["data"], dict)
                         and hashlib.sha256(payload.encode("utf-8")).hexdigest() == digest)
            except (ValueError, TypeError, AttributeError):
                valid = False
            if not valid:
                raise ValueError("checkpoint audit chain is invalid")
            previous = digest

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def close(self):
        with self.lock:
            self.closed = True
            self.db.close()

    def healthy(self):
        return not self.closed


class DurableAudit:
    def __init__(self, state, clock):
        self.state, self.clock = state, clock

    def append(self, event, **data):
        previous = self.state.db.execute("SELECT seq, hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        seq, prev = (previous[0] + 1, previous[1]) if previous else (1, "0" * 64)
        record = {"seq": seq, "ts": self.clock.now().isoformat(), "event": event, "data": data, "prevHash": prev}
        payload = json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        self.state.db.execute("INSERT INTO audit VALUES (?, ?, ?)", (seq, payload, digest))
        return {**record, "hash": digest}


class DurablePDP(PolicyDecisionPoint):
    def __init__(self, contract, directory, clock, state):
        super().__init__(contract, directory, clock)
        self.state = state

    def consume_rate(self, principal):
        now, rate = self.clock.now().timestamp(), self.contract.rateLimitPerMinute
        row = self.state.db.execute("SELECT tokens, last FROM rate WHERE oid=?", (principal.oid,)).fetchone()
        tokens, last = row if row else (float(rate), now)
        tokens = min(float(rate), tokens + max(0, now - last) * rate / 60)
        allowed = tokens >= 1
        self.state.db.execute("INSERT OR REPLACE INTO rate VALUES (?, ?, ?)",
                              (principal.oid, tokens - 1 if allowed else tokens, max(now, last)))
        if allowed:
            return ALLOW
        return Decision(False, 429, "rate_limited", "request rate exceeded",
                        retry_after=max(1, math.ceil((1 - tokens) * 60 / rate)))

    def _seen(self, oid, doc):
        horizon = (self.clock.now() - self.window).timestamp()
        return {row[0] for row in self.state.db.execute(
            "SELECT chunk FROM coverage WHERE oid=? AND doc=? AND last>=?", (oid, doc, horizon))}

    def coverage(self, oid, doc_id, total_chunks):
        return len(self._seen(oid, doc_id)) / max(1, total_chunks)

    def exfiltration_admit(self, oid, chunk, pending=()):
        seen = self._seen(oid, chunk.doc_id)
        seen.update(c.chunk_id for c in pending if c.doc_id == chunk.doc_id)
        allowed = max(self.min_chunks_per_doc,
                      math.floor(self.contract.exfiltrationCoverageThreshold * chunk.total_chunks + 1e-9))
        return chunk.chunk_id in seen or len(seen) + 1 <= allowed

    def record_served(self, oid, chunk):
        self.state.db.execute("""INSERT INTO coverage VALUES (?, ?, ?, ?)
            ON CONFLICT(oid, doc, chunk) DO UPDATE SET last=MAX(last, excluded.last)""",
                              (oid, chunk.doc_id, chunk.chunk_id, self.clock.now().timestamp()))
