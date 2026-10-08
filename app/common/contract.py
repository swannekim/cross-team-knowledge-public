"""Sharing Contract: the machine-readable agreement that governs what Team A shares with Team B.

The same contract drives all three architectures (scope, label ceiling, audience, guests,
purpose, derivative types, TTL, excerpt cap, exfiltration threshold and rate limit).
"""
from __future__ import annotations

import dataclasses
import json
import posixpath
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from .clock import parse_iso, to_iso
from .fingerprint import canonical_json, sha256_hex
from .labels import is_known_label, label_within

KNOWN_DERIVATIVE_TYPES = ("summary", "redactedExtract")
CONTRACT_STATUSES = ("active", "suspended", "revoked")
_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_CONTRACT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
_PURPOSE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
_UPN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ContractError(ValueError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("invalid sharing contract: " + "; ".join(self.errors))


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_contract(data: dict) -> list:
    """Returns a list of human-readable validation errors (empty when valid)."""
    errors = []
    if not isinstance(data, dict):
        return ["contract must be a JSON object"]

    def need(name, check, message):
        if name not in data:
            errors.append(f"{name}: required")
        elif not check(data[name]):
            errors.append(f"{name}: {message}")

    need("contractId", lambda v: isinstance(v, str) and bool(_CONTRACT_ID.match(v)), "3-64 chars [A-Za-z0-9._-]")
    need("sourceSite", lambda v: isinstance(v, str) and v.startswith("https://"), "must be an https URL")
    need("includePaths", lambda v: isinstance(v, list) and v and all(isinstance(p, str) and p.startswith("/") for p in v),
         "non-empty list of absolute library paths")
    need("maxLabel", is_known_label, "must be a known sensitivity label")
    need("audienceGroupIds", lambda v: isinstance(v, list) and v and all(isinstance(g, str) and _GUID.match(g) for g in v),
         "non-empty list of Entra group object ids")
    need("excludeGuests", lambda v: isinstance(v, bool), "must be boolean")
    need("purpose", lambda v: isinstance(v, str) and bool(_PURPOSE.match(v)), "lower-case slug")
    need("derivativeTypes", lambda v: isinstance(v, list) and v and all(t in KNOWN_DERIVATIVE_TYPES for t in v)
         and len(set(v)) == len(v), f"non-empty, unique subset of {KNOWN_DERIVATIVE_TYPES}")
    need("ttlDays", lambda v: _is_int(v) and 1 <= v <= 365, "integer 1..365")
    need("approvedBy", lambda v: isinstance(v, str) and bool(_UPN.match(v)), "UPN of the approving data owner")
    need("approvedAt", _valid_ts, "ISO-8601 UTC timestamp")
    need("maxExcerptChars", lambda v: _is_int(v) and 50 <= v <= 2000, "integer 50..2000")
    need("exfiltrationCoverageThreshold", lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
         and 0 < v <= 1, "number in (0, 1]")
    need("rateLimitPerMinute", lambda v: _is_int(v) and 1 <= v <= 600, "integer 1..600")

    if "excludePaths" in data and not (isinstance(data["excludePaths"], list)
                                       and all(isinstance(p, str) and p.startswith("/") for p in data["excludePaths"])):
        errors.append("excludePaths: list of absolute library paths")
    if "status" in data and data["status"] not in CONTRACT_STATUSES:
        errors.append(f"status: one of {CONTRACT_STATUSES}")
    if "expiresAt" in data and not _valid_ts(data["expiresAt"]):
        errors.append("expiresAt: ISO-8601 UTC timestamp")
    for key in ("audienceTeam", "displayName"):
        if key in data and not (isinstance(data[key], str) and 0 < len(data[key]) <= 128 and "/" not in data[key]):
            errors.append(f"{key}: non-empty string of at most 128 characters without '/'")
    if "allowTenantWideAudience" in data and not isinstance(data["allowTenantWideAudience"], bool):
        errors.append("allowTenantWideAudience: must be boolean")
    chats = data.get("teamsChatSources", [])
    if not isinstance(chats, list):
        errors.append("teamsChatSources: must be a list")
    else:
        for i, src in enumerate(chats):
            if not isinstance(src, dict) or not isinstance(src.get("chatId"), str) or not src["chatId"].startswith("19:"):
                errors.append(f"teamsChatSources[{i}].chatId: Teams chat id ('19:...')")
            elif not is_known_label(src.get("label", "Confidential")):
                errors.append(f"teamsChatSources[{i}].label: known sensitivity label")
            elif is_known_label(data.get("maxLabel")) and not label_within(src.get("label", "Confidential"), data["maxLabel"]):
                errors.append(f"teamsChatSources[{i}].label: above maxLabel")
    return errors


def _valid_ts(value) -> bool:
    try:
        parse_iso(value)
        return isinstance(value, str) and value.endswith("Z")
    except (ValueError, TypeError):
        return False


def normalise_library_path(path: str) -> str:
    """Decodes, normalises ('..', '//') and case-folds a library path for safe prefix comparison."""
    decoded = unquote(path or "").replace("\\", "/")
    if not decoded.startswith("/"):
        decoded = "/" + decoded
    return posixpath.normpath(decoded).casefold()


def _under(path_norm: str, prefix: str) -> bool:
    prefix_norm = normalise_library_path(prefix).rstrip("/")
    return path_norm == prefix_norm or path_norm.startswith(prefix_norm + "/")


@dataclass(frozen=True)
class SharingContract:
    contractId: str
    sourceSite: str
    includePaths: tuple
    maxLabel: str
    audienceGroupIds: tuple
    excludeGuests: bool
    purpose: str
    derivativeTypes: tuple
    ttlDays: int
    approvedBy: str
    approvedAt: str
    maxExcerptChars: int
    exfiltrationCoverageThreshold: float
    rateLimitPerMinute: int
    sourceTeam: str = "Process Engineering"
    audienceTeam: str = "Yield Analytics"
    displayName: Optional[str] = None
    title: str = ""
    excludePaths: tuple = ()
    status: str = "active"
    expiresAt: Optional[str] = None
    allowTenantWideAudience: bool = False
    teamsChatSources: tuple = ()
    revokedAt: Optional[str] = None
    revokedBy: Optional[str] = None
    raw: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def display_name(self) -> str:
        """Human-readable contract name, e.g. 'Process Engineering → Yield Analytics (SC-2026-0042)'."""
        return self.displayName or f"{self.sourceTeam} → {self.audienceTeam} ({self.contractId})"

    # ------------------------------------------------------------------ scope
    def path_in_scope(self, path: str) -> tuple:
        """(allowed, reason) for a library path such as '/Shareable/Etch/x.md'."""
        norm = normalise_library_path(path)
        if not any(_under(norm, p) for p in self.includePaths):
            return False, "out_of_scope_path"
        if any(_under(norm, p) for p in self.excludePaths):
            return False, "excluded_path"
        return True, "in_scope"

    def label_allowed(self, label: Optional[str]) -> bool:
        return label_within(label, self.maxLabel)

    def chat_source(self, chat_id: str) -> Optional[dict]:
        for src in self.teamsChatSources:
            if src.get("chatId") == chat_id:
                return dict(src)
        return None

    # ------------------------------------------------------------------ lifecycle
    def is_active(self, now: datetime) -> bool:
        if self.status != "active":
            return False
        if self.expiresAt and now >= parse_iso(self.expiresAt):
            return False
        return now >= parse_iso(self.approvedAt)

    def derivative_valid_until(self, generated_at: datetime) -> datetime:
        valid_until = generated_at + timedelta(days=self.ttlDays)
        if self.expiresAt:
            valid_until = min(valid_until, parse_iso(self.expiresAt))
        return valid_until

    def revoked(self, at: datetime, by: str) -> "SharingContract":
        return dataclasses.replace(self, status="revoked", revokedAt=to_iso(at), revokedBy=by)

    def with_changes(self, **changes) -> "SharingContract":
        return dataclasses.replace(self, **changes)

    def fingerprint(self) -> str:
        """Hash of the policy-relevant fields; a change forces re-derivation of published content."""
        relevant = {k: v for k, v in self.to_dict().items() if k not in ("status", "revokedAt", "revokedBy", "title")}
        return sha256_hex(canonical_json(relevant))[:16]

    def to_dict(self) -> dict:
        out = {}
        for f in dataclasses.fields(self):
            if f.name == "raw":
                continue
            value = getattr(self, f.name)
            out[f.name] = list(value) if isinstance(value, tuple) else value
        return out


def contract_from_dict(data: dict) -> SharingContract:
    errors = validate_contract(data)
    if errors:
        raise ContractError(errors)
    names = {f.name for f in dataclasses.fields(SharingContract)} - {"raw"}
    kwargs = {}
    for key, value in data.items():
        if key in names:
            if key == "teamsChatSources":
                value = tuple(dict(v) for v in value)
            elif isinstance(value, list):
                value = tuple(value)
            kwargs[key] = value
    kwargs["exfiltrationCoverageThreshold"] = float(data["exfiltrationCoverageThreshold"])
    return SharingContract(raw=dict(data), **kwargs)


def load_contract(path) -> SharingContract:
    return contract_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
