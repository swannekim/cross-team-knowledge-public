"""Entra delegated-token validation and uncached Microsoft Graph audience checks.

Only the public Microsoft cloud is supported. The broker never issues access tokens.
Graph application permissions: User.Read.All and GroupMember.Read.All. The Container
App must have the user-assigned managed identity identified by KX_GRAPH_CLIENT_ID.
"""
from __future__ import annotations

import json
import math
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from common.directory import User

from .auth import AuthError, Principal
from .constants import REQUIRED_SCOPE


class DependencyUnavailable(RuntimeError):
    pass


def guid(value):
    if not isinstance(value, str):
        raise ValueError("expected a UUID")
    parsed = str(uuid.UUID(value))
    if value.lower() != parsed:
        raise ValueError("expected a canonical UUID")
    return parsed


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url, *, method="GET", headers=None, body=None):
    """Bounded requests, TLS verification, no redirects or inherited HTTP proxies."""
    from urllib.request import ProxyHandler
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(url, data=data, method=method,
                      headers={"Accept": "application/json", "Content-Type": "application/json", **(headers or {})})
    try:
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=15) as response:
            if response.status != 200:
                raise DependencyUnavailable("unexpected upstream response")
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise DependencyUnavailable("upstream response too large")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("expected an object")
            return result
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        raise DependencyUnavailable("identity dependency unavailable") from exc


class MicrosoftSigningKeys:
    def __init__(self, tenant_id, *, fetch=request_json, clock=time.time):
        self.url = f"https://login.microsoftonline.com/{guid(tenant_id)}/discovery/v2.0/keys"
        self.fetch, self.clock = fetch, clock
        self.keys, self.loaded_at = {}, -math.inf

    def get(self, kid):
        import jwt
        now = self.clock()
        stale = now - self.loaded_at >= 3600
        if stale or (kid not in self.keys and now - self.loaded_at >= 30):
            payload = self.fetch(self.url)
            raw_keys = payload.get("keys")
            if not isinstance(raw_keys, list) or not 1 <= len(raw_keys) <= 100:
                raise DependencyUnavailable("invalid Microsoft signing keys")
            keys = {}
            try:
                for raw in raw_keys:
                    if not isinstance(raw, dict):
                        raise ValueError("invalid key")
                    if raw.get("kty") != "RSA" or raw.get("use", "sig") != "sig":
                        continue
                    if raw.get("alg", "RS256") != "RS256":
                        continue
                    key_id = raw.get("kid")
                    if not isinstance(key_id, str) or not 1 <= len(key_id) <= 200 or key_id in keys:
                        raise ValueError("invalid key id")
                    key = jwt.PyJWK.from_dict(raw, algorithm="RS256").key
                    if key.key_size < 2048:
                        raise ValueError("RSA key too small")
                    keys[key_id] = key
            except (ValueError, TypeError, KeyError, jwt.PyJWTError) as exc:
                raise DependencyUnavailable("invalid Microsoft signing keys") from exc
            if not keys:
                raise DependencyUnavailable("no Microsoft signing keys")
            self.keys, self.loaded_at = keys, now
        if kid not in self.keys:
            raise AuthError("invalid_token", "unrecognized signing key")
        return self.keys[kid]


class EntraTokenValidator:
    def __init__(self, tenant_id, audience, *, keys=None):
        self.tenant_id, self.audience = guid(tenant_id), guid(audience)
        self.issuer = f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"
        self.keys = keys if keys is not None else MicrosoftSigningKeys(self.tenant_id)

    def from_authorization_header(self, header):
        if not isinstance(header, str):
            raise AuthError("invalid_request", "a bearer token is required")
        parts = header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise AuthError("invalid_request", "a bearer token is required")
        return self.validate(parts[1])

    def validate(self, token):
        import jwt
        try:
            if not isinstance(token, str) or len(token) > 32768:
                raise ValueError("invalid token length")
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or header.get("crit"):
                raise ValueError("only RS256 is supported")
            kid = header.get("kid")
            if not isinstance(kid, str) or not 1 <= len(kid) <= 200:
                raise ValueError("invalid key id")
            claims = jwt.decode(
                token, self.keys.get(kid), algorithms=["RS256"],
                issuer=self.issuer, audience=self.audience, leeway=30,
                options={"require": ["iss", "aud", "tid", "oid", "scp", "exp", "nbf", "iat", "ver"],
                         "strict_aud": True},
            )
            for claim in ("exp", "nbf", "iat"):
                value = claims[claim]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError("invalid numeric date")
            if claims["exp"] <= claims["iat"] or claims["exp"] <= claims["nbf"]:
                raise ValueError("invalid token lifetime")
            if claims["tid"] != self.tenant_id or claims["ver"] != "2.0":
                raise ValueError("wrong tenant or token version")
            oid = guid(claims["oid"])
            scopes = claims["scp"]
            if not isinstance(scopes, str) or not scopes.strip():
                raise ValueError("a delegated scope is required")
            acct = claims.get("acct")
            if acct is not None and (type(acct) is not int or acct not in (0, 1)):
                raise ValueError("invalid account type")
            if REQUIRED_SCOPE not in scopes.split():
                raise AuthError("insufficient_scope", f"{REQUIRED_SCOPE} is required", status=403)
            return Principal(oid, self.tenant_id, None, None, tuple(scopes.split()), acct, claims)
        except (ValueError, TypeError, KeyError, OverflowError, jwt.PyJWTError):
            raise AuthError("invalid_token", "invalid Entra delegated access token") from None


class ManagedIdentityGraphToken:
    def __init__(self, client_id, environ, *, credential=None, clock=time.time):
        self.client_id, self.clock = guid(client_id), clock
        endpoint = environ.get("IDENTITY_ENDPOINT", "")
        parsed = urlsplit(endpoint)
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost")
                or parsed.username or parsed.password or parsed.fragment or parsed.query):
            raise ValueError("Container Apps local IDENTITY_ENDPOINT is required")
        if not environ.get("IDENTITY_HEADER"):
            raise ValueError("Container Apps IDENTITY_HEADER is required")
        if credential is None:
            from azure.identity import ManagedIdentityCredential
            credential = ManagedIdentityCredential(client_id=self.client_id, retry_total=0,
                                                   connection_timeout=5, read_timeout=15)
        self.credential = credential

    def get(self):
        try:
            access = self.credential.get_token("https://graph.microsoft.com/.default")
            token, expiry = access.token, float(access.expires_on)
            if not isinstance(token, str) or not token or not math.isfinite(expiry) or expiry <= self.clock() + 120:
                raise ValueError("invalid identity token")
        except Exception as exc:
            raise DependencyUnavailable("managed identity token unavailable") from exc
        return token

    def close(self):
        self.credential.close()


class TransientGraphToken:
    """Explicit local-validation only; no token acquisition, refresh or persistence."""
    def __init__(self, token):
        if not isinstance(token, str) or not token or len(token) > 32768 or any(c.isspace() for c in token):
            raise ValueError("invalid transient Graph token")
        self._token = token

    def get(self):
        return self._token

    def close(self):
        self._token = ""


class GraphDirectory:
    """Never uses token groups or fixture users; checks Graph on every request."""
    def __init__(self, token_provider, audience_group_id, *, fetch=request_json):
        self.token_provider, self.group_id, self.fetch = token_provider, guid(audience_group_id), fetch

    def find_user(self, oid):
        oid = guid(oid)
        headers = {"Authorization": "Bearer " + self.token_provider.get()}
        user = self.fetch(f"https://graph.microsoft.com/v1.0/users/{oid}?$select=id,userType,accountEnabled",
                          headers=headers)
        if user.get("id") != oid or user.get("userType") not in ("Member", "Guest"):
            raise DependencyUnavailable("Graph returned an invalid user")
        if user.get("accountEnabled") is not True:
            return None
        membership = self.fetch(f"https://graph.microsoft.com/v1.0/users/{oid}/checkMemberGroups",
                                method="POST", headers=headers, body={"groupIds": [self.group_id]})
        groups = membership.get("value")
        if not isinstance(groups, list) or any(group != self.group_id for group in groups):
            raise DependencyUnavailable("Graph returned invalid group membership")
        return User(oid, "", "", "", user["userType"], tuple(groups), ())
