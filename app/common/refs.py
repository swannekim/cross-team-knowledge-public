"""Opaque source references and the broker "request access" link.

Derived content never carries the original webUrl, path or driveItem id. Instead it carries an
opaque, keyed reference (HMAC of the source id) that only the broker can resolve when a Team B
user asks the data owner for access to the original.
"""
from __future__ import annotations

import hashlib
import hmac
from urllib.parse import quote

BROKER_BASE_URL = "https://broker.contoso.com"
TEST_REF_KEY = b"prototype-ref-key-not-a-secret"


def opaque_ref(source_id: str, key: bytes = TEST_REF_KEY) -> str:
    return "ref-" + hmac.new(key, source_id.encode("utf-8"), hashlib.sha256).hexdigest()[:24]


def access_request_url(ref: str, base_url: str = BROKER_BASE_URL) -> str:
    return f"{base_url}/access-request?ref={quote(ref, safe='')}"
