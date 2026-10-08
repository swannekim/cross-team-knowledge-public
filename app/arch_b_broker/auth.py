"""Bearer token validation for the Knowledge Broker.

This baseline module uses HS256 (HMAC-SHA256, standard library only) and checks: structure, alg allow-list
(rejects 'none' and algorithm confusion), signature (constant-time), exp, nbf, aud, iss, tid, a
user context (oid + delegated 'scp') and the required scope 'Knowledge.Ask'.

The separate live_auth.EntraTokenValidator implements RS256 using PyJWT/cryptography,
tenant-pinned Microsoft signing keys, broker client-ID GUID audience, delegated scope
and required Entra v2 claims. live.py selects that adapter, never this HS256 validator.
Its Graph directory adapter checks current enabled/member/audience state.

Generated-key local tests are not evidence of tenant token issuance or Copilot SSO.
The checked-in OAuthPluginVault reference is an offline placeholder; a real agent
registration and successful user flow require separate deployment and live evidence.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Optional

from .constants import REQUIRED_SCOPE, TOKEN_VERSION_ISSUER


class AuthError(Exception):
    def __init__(self, code: str, description: str, status: int = 401):
        self.code, self.description, self.status = code, description, status
        super().__init__(f"{code}: {description}")


@dataclass(frozen=True)
class Principal:
    oid: str
    tid: str
    upn: Optional[str]
    name: Optional[str]
    scopes: tuple
    acct: Optional[int]
    claims: dict = field(default_factory=dict, compare=False, repr=False)


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(segment: str) -> bytes:
    if not isinstance(segment, str) or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
                                           for c in segment):
        raise ValueError("invalid base64url segment")
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def mint_hs256(claims: dict, key: bytes, headers: Optional[dict] = None) -> str:
    """Test token issuer (stands in for Entra ID in this offline prototype)."""
    header = {"alg": "HS256", "typ": "JWT", **(headers or {})}
    signing_input = f"{b64url_encode(json.dumps(header, separators=(',', ':')).encode())}." \
                    f"{b64url_encode(json.dumps(claims, separators=(',', ':')).encode())}"
    signature = hmac.new(key, signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{b64url_encode(signature)}"


class TokenValidator:
    def __init__(self, *, key: bytes, audience: str, tenant_id: str, clock, required_scope: str = REQUIRED_SCOPE,
                 leeway_seconds: int = 60, max_token_bytes: int = 8192):
        self._key, self.audience, self.tenant_id = key, audience, tenant_id
        self.issuer = TOKEN_VERSION_ISSUER.format(tid=tenant_id)
        self._clock, self.required_scope = clock, required_scope
        self.leeway, self.max_token_bytes = leeway_seconds, max_token_bytes

    def from_authorization_header(self, header: Optional[str]) -> Principal:
        if not header:
            raise AuthError("invalid_request", "missing bearer token")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise AuthError("invalid_request", "authorization scheme must be Bearer")
        return self.validate(token.strip())

    def validate(self, token: str) -> Principal:
        if len(token) > self.max_token_bytes:
            raise AuthError("invalid_token", "token too large")
        parts = token.split(".")
        if len(parts) != 3:
            raise AuthError("invalid_token", "malformed token")
        try:
            header = json.loads(b64url_decode(parts[0]))
            claims = json.loads(b64url_decode(parts[1]))
            signature = b64url_decode(parts[2])
        except (ValueError, json.JSONDecodeError):
            raise AuthError("invalid_token", "malformed token") from None
        if not isinstance(header, dict) or not isinstance(claims, dict):
            raise AuthError("invalid_token", "malformed token")
        if header.get("alg") != "HS256":
            raise AuthError("invalid_token", f"algorithm {header.get('alg')!r} not allowed")
        expected = hmac.new(self._key, f"{parts[0]}.{parts[1]}".encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, signature):
            raise AuthError("invalid_token", "signature verification failed")
        now = self._clock.now().timestamp()
        exp, nbf = claims.get("exp"), claims.get("nbf")
        if not isinstance(exp, (int, float)) or now > exp + self.leeway:
            raise AuthError("invalid_token", "token expired")
        if nbf is not None and (not isinstance(nbf, (int, float)) or now + self.leeway < nbf):
            raise AuthError("invalid_token", "token not yet valid")
        aud = claims.get("aud")
        if (aud if isinstance(aud, list) else [aud]).count(self.audience) == 0:
            raise AuthError("invalid_token", "audience mismatch")
        if claims.get("tid") != self.tenant_id:
            raise AuthError("invalid_token", "tenant mismatch")
        if claims.get("iss") != self.issuer:
            raise AuthError("invalid_token", "issuer mismatch")
        if not isinstance(claims.get("oid"), str) or "scp" not in claims:
            raise AuthError("invalid_token", "a delegated user token is required (oid + scp)")
        scopes = tuple(str(claims.get("scp", "")).split())
        if self.required_scope not in scopes:
            raise AuthError("insufficient_scope", f"scope {self.required_scope} required", status=403)
        acct = claims.get("acct")
        return Principal(oid=claims["oid"], tid=claims["tid"], upn=claims.get("preferred_username"),
                         name=claims.get("name"), scopes=scopes, acct=acct if isinstance(acct, int) else None,
                         claims=claims)


def user_token_claims(user, *, tenant_id: str, audience: str, now: float, lifetime: int = 3600,
                      scopes: str = REQUIRED_SCOPE, **overrides) -> dict:
    """Claims shaped like an Entra v2 delegated access token for a directory user."""
    claims = {
        "aud": audience, "iss": TOKEN_VERSION_ISSUER.format(tid=tenant_id), "tid": tenant_id,
        "iat": int(now), "nbf": int(now) - 5, "exp": int(now) + lifetime, "oid": user.id,
        "preferred_username": user.upn, "name": user.display_name, "scp": scopes,
        "acct": 1 if user.is_guest else 0, "ver": "2.0",
    }
    claims.update(overrides)
    return claims
