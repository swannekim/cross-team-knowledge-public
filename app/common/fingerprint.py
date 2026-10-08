"""Hashing helpers: content fingerprints, canonical JSON and stable ids."""
from __future__ import annotations

import hashlib
import json


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def source_fingerprint(content, etag: str) -> str:
    """sha256 over the source bytes and the source eTag (per the design: sha256(content + eTag))."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    digest = hashlib.sha256()
    digest.update(content)
    digest.update(b"\x1f")
    digest.update((etag or "").encode("utf-8"))
    return "sha256:" + digest.hexdigest()


def stable_hash(*parts, length: int = 16) -> str:
    return sha256_hex("\x1f".join(str(p) for p in parts))[:length]
