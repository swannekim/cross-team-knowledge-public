"""Graph external-connector client with pluggable transports.

* MockGraphTransport - in-memory emulator of the v1.0 connector endpoints. It validates payloads
  like the service would and offers search(user, query) with Microsoft-Search-like security
  trimming on each item's ACL (deny wins).
* HttpGraphTransport - urllib transport with client-credentials token acquisition from
  login.microsoftonline.com. It is never executed against the network in this prototype; unit
  tests drive it through a fake opener to verify request formation.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote, unquote, urlencode

from common.text import tokenize

from . import validators as v


class GraphError(Exception):
    def __init__(self, status: int, code: str, message: str, body=None):
        self.status, self.code, self.message, self.body = status, code, message, body
        super().__init__(f"HTTP {status} {code}: {message}")


@dataclass
class GraphResponse:
    status: int
    headers: dict = field(default_factory=dict)
    body: object = None


_TOKEN_COUNTS: dict = {}


def _token_counts(text: str) -> Counter:
    """Tokenised bag-of-words, memoised by content hash across emulator instances."""
    key = hashlib.sha1(text.encode("utf-8")).hexdigest()
    counts = _TOKEN_COUNTS.get(key)
    if counts is None:
        if len(_TOKEN_COUNTS) > 50_000:
            _TOKEN_COUNTS.clear()
        counts = _TOKEN_COUNTS[key] = Counter(tokenize(text, stem_words=True))
    return counts


def _error(status: int, code: str, message: str) -> GraphResponse:
    return GraphResponse(status, {"Content-Type": "application/json"}, {"error": {"code": code, "message": message}})


# =============================================================================== mock transport
class MockGraphTransport:
    """In-memory emulator of POST /external/connections, PATCH .../schema, PUT/DELETE/GET .../items/{id}."""

    _CONN = re.compile(r"^/external/connections/([^/]+)$")
    _SCHEMA = re.compile(r"^/external/connections/([^/]+)/schema$")
    _OPERATION = re.compile(r"^/external/connections/([^/]+)/operations/([^/]+)$")
    _ITEM = re.compile(r"^/external/connections/([^/]+)/items/([^/]+)$")

    def __init__(self, directory, *, schema_poll_rounds: int = 1):
        self.directory = directory
        self.connections: dict = {}
        self.operations: dict = {}
        self.request_log: list = []
        self.calls = Counter()
        self._failures: list = []
        self._schema_poll_rounds = schema_poll_rounds
        self._op_ids = itertools.count(1)
        self._lock = threading.RLock()
        self._token_cache: dict = {}
        self._df_cache: dict = {}

    # ----------------------------------------------------------------- test helpers
    def inject_failure(self, method: str, status: int, *, times: int = 1, retry_after: Optional[str] = None) -> None:
        self._failures.append({"method": method, "status": status, "times": times, "retry_after": retry_after})

    def items(self, connection_id: str) -> dict:
        return self.connections.get(connection_id, {}).get("items", {})

    def item_writes(self) -> int:
        return self.calls[("PUT", "item")] + self.calls[("DELETE", "item")]

    def logged_bodies(self) -> list:
        return [entry["body"] for entry in self.request_log if entry["body"] is not None]

    # ----------------------------------------------------------------- transport API
    def request(self, method: str, path: str, body=None, headers=None) -> GraphResponse:
        with self._lock:
            path = path.split("?", 1)[0]
            for failure in self._failures:
                if failure["method"] == method and failure["times"] > 0:
                    failure["times"] -= 1
                    resp = _error(failure["status"], "TooManyRequests" if failure["status"] == 429 else "ServiceUnavailable",
                                  "injected failure")
                    if failure["retry_after"]:
                        resp.headers["Retry-After"] = failure["retry_after"]
                    return resp
            wire = None if body is None else json.dumps(body, ensure_ascii=False)
            self.request_log.append({"method": method, "path": path, "body": wire})
            payload = None if wire is None else json.loads(wire)
            if method == "POST" and path == "/external/connections":
                self.calls[("POST", "connection")] += 1
                return self._create_connection(payload)
            match = self._SCHEMA.match(path)
            if match:
                self.calls[(method, "schema")] += 1
                return self._schema(method, unquote(match.group(1)), payload)
            match = self._OPERATION.match(path)
            if match and method == "GET":
                return self._operation(unquote(match.group(2)))
            match = self._ITEM.match(path)
            if match:
                self.calls[(method, "item")] += 1
                return self._item(method, unquote(match.group(1)), unquote(match.group(2)), payload)
            match = self._CONN.match(path)
            if match:
                cid = unquote(match.group(1))
                if cid not in self.connections:
                    return _error(404, "NotFound", f"connection {cid} not found")
                if method == "GET":
                    return GraphResponse(200, {}, dict(self.connections[cid]["connection"]))
                if method == "DELETE":
                    del self.connections[cid]
                    return GraphResponse(204)
            return _error(400, "BadRequest", f"unsupported request {method} {path}")

    def _create_connection(self, payload) -> GraphResponse:
        errors = v.validate_connection(payload or {})
        if errors:
            return _error(400, "InvalidRequest", "; ".join(errors))
        if payload["id"] in self.connections:
            return _error(409, "Conflict", "connection already exists")
        self.connections[payload["id"]] = {"connection": dict(payload, state="draft"), "schema": None,
                                           "schemaStatus": None, "items": {}}
        return GraphResponse(201, {}, dict(payload, state="draft"))

    def _schema(self, method: str, cid: str, payload) -> GraphResponse:
        if cid not in self.connections:
            return _error(404, "NotFound", f"connection {cid} not found")
        conn = self.connections[cid]
        if method == "GET":
            if conn["schema"] is None:
                return _error(404, "NotFound", "schema not registered")
            return GraphResponse(200, {}, conn["schema"])
        if method not in ("PATCH", "POST"):
            return _error(405, "MethodNotAllowed", method)
        errors = v.validate_schema(payload or {})
        if errors:
            return _error(400, "InvalidRequest", "; ".join(errors))
        op_id = f"op{next(self._op_ids)}"
        conn["schema"], conn["schemaStatus"] = payload, "inprogress"
        self.operations[op_id] = {"cid": cid, "remaining": self._schema_poll_rounds}
        location = f"{v.cfg('GRAPH_BASE_URL')}/external/connections/{cid}/operations/{op_id}"
        return GraphResponse(202, {"Location": location})

    def _operation(self, op_id: str) -> GraphResponse:
        op = self.operations.get(op_id)
        if op is None:
            return _error(404, "NotFound", "operation not found")
        if op["remaining"] > 0:
            op["remaining"] -= 1
            return GraphResponse(200, {}, {"id": op_id, "status": "inprogress"})
        conn = self.connections[op["cid"]]
        conn["schemaStatus"] = "completed"
        conn["connection"]["state"] = "ready"
        return GraphResponse(200, {}, {"id": op_id, "status": "completed"})

    def _item(self, method: str, cid: str, item_id: str, payload) -> GraphResponse:
        conn = self.connections.get(cid)
        if conn is None:
            return _error(404, "NotFound", f"connection {cid} not found")
        if method == "GET":
            if item_id not in conn["items"]:
                return _error(404, "ItemNotFound", item_id)
            return GraphResponse(200, {}, json.loads(json.dumps(conn["items"][item_id])))
        if method == "DELETE":
            if item_id not in conn["items"]:
                return _error(404, "ItemNotFound", item_id)
            del conn["items"][item_id]
            self._token_cache.get(cid, {}).pop(item_id, None)
            self._df_cache.pop(cid, None)
            return GraphResponse(204)
        if method != "PUT":
            return _error(405, "MethodNotAllowed", method)
        if conn["schemaStatus"] != "completed":
            return _error(400, "InvalidRequest", "schema must be registered and completed before items are ingested")
        errors = v.validate_item(payload or {}, conn["schema"], item_id)
        if errors:
            return _error(400, "InvalidRequest", "; ".join(errors))
        conn["items"][item_id] = dict(payload, id=item_id)
        self._token_cache.get(cid, {}).pop(item_id, None)
        self._df_cache.pop(cid, None)
        return GraphResponse(200, {}, {"id": item_id})

    # ----------------------------------------------------------------- Microsoft-Search-like query + trimming
    def _ace_matches(self, ace: dict, user) -> bool:
        kind, value = ace.get("type"), ace.get("value")
        if kind == "user":
            return value == user.id
        if kind == "group":
            return value in user.groups
        if kind == "everyone":
            return True
        if kind == "everyoneExceptGuests":
            return not user.is_guest
        return False  # externalGroup membership is not modelled; never matches (fail closed)

    def is_visible(self, item: dict, user) -> bool:
        """Visible iff some grant ACE matches and no deny ACE matches (deny wins)."""
        granted = False
        for ace in item.get("acl", []):
            if self._ace_matches(ace, user):
                if ace.get("accessType") == "deny":
                    return False
                if ace.get("accessType") == "grant":
                    granted = True
        return granted

    def _searchable_text(self, cid: str, item: dict) -> str:
        schema = self.connections[cid]["schema"] or {"properties": []}
        searchable = [p["name"] for p in schema["properties"] if p.get("isSearchable")]
        parts = [str(item["properties"].get(name, "")) for name in searchable]
        parts.append(item.get("content", {}).get("value", ""))
        return "\n".join(parts)

    def _index(self, cid: str):
        cache = self._token_cache.setdefault(cid, {})
        for item_id, item in self.connections[cid]["items"].items():
            if item_id not in cache:
                cache[item_id] = _token_counts(self._searchable_text(cid, item))
        if cid not in self._df_cache:
            df = Counter()
            for counts in cache.values():
                df.update(counts.keys())
            self._df_cache[cid] = df
        return cache, self._df_cache[cid]

    def search(self, user_key: str, query: str, *, connection_id: Optional[str] = None, top: int = 10,
               include_content: bool = False) -> list:
        with self._lock:
            user = self.directory.find_user(user_key)
            if user is None:
                return []
            terms = set(tokenize(query, stem_words=True))
            hits = []
            for cid in ([connection_id] if connection_id else list(self.connections)):
                if cid not in self.connections or self.connections[cid]["schema"] is None:
                    continue
                tokens, df = self._index(cid)
                n_items = max(1, len(tokens))
                retrievable = [p["name"] for p in self.connections[cid]["schema"]["properties"] if p.get("isRetrievable")]
                for item_id, item in self.connections[cid]["items"].items():
                    if not self.is_visible(item, user):
                        continue
                    counts = tokens[item_id]
                    score = sum((1 + math.log(counts[t])) * math.log(1 + n_items / df[t]) for t in terms if counts.get(t))
                    if score <= 0:
                        continue
                    hit = {"hitId": item_id, "connectionId": cid, "score": round(score, 6),
                           "summary": _snippet(item.get("content", {}).get("value", ""), terms),
                           "resource": {"properties": {k: item["properties"][k] for k in retrievable
                                                       if k in item["properties"]}}}
                    if include_content:
                        hit["content"] = item.get("content", {}).get("value", "")
                    hits.append(hit)
            hits.sort(key=lambda h: (-h["score"], h["hitId"]))
            for rank, hit in enumerate(hits[:top], start=1):
                hit["rank"] = rank
            return hits[:top]


def _snippet(text: str, terms: set, width: int = 200) -> str:
    lower = text.lower()
    positions = [lower.find(t) for t in terms if lower.find(t) >= 0]
    start = max(0, min(positions) - width // 3) if positions else 0
    snippet = " ".join(text[start:start + width].split())
    return ("…" if start else "") + snippet + ("…" if start + width < len(text) else "")


# =============================================================================== HTTP transport
class HttpGraphTransport:
    """urllib-based transport; app-only token via the OAuth 2.0 client-credentials grant.

    Production notes: prefer a certificate credential (client_assertion) or workload identity
    federation / managed identity over a client secret; keep the secret in Key Vault.
    """

    def __init__(self, tenant_id: str, client_id: str, client_secret: str, *, opener=None, clock=time.time,
                 base_url: Optional[str] = None, authority: Optional[str] = None, scope: Optional[str] = None,
                 timeout: float = 30.0):
        self.tenant_id, self.client_id, self._secret = tenant_id, client_id, client_secret
        self._opener = opener or urllib.request.urlopen
        self._clock = clock
        self.base_url = (base_url or v.cfg("GRAPH_BASE_URL")).rstrip("/")
        self.authority = (authority or v.cfg("TOKEN_AUTHORITY")).rstrip("/")
        self.scope = scope or v.cfg("GRAPH_DEFAULT_SCOPE")
        self.timeout = timeout
        self._token: Optional[str] = None
        self._token_expiry = 0.0

    def token_endpoint(self) -> str:
        return f"{self.authority}/{self.tenant_id}/oauth2/v2.0/token"

    def _send(self, req: urllib.request.Request):
        try:
            resp = self._opener(req, timeout=self.timeout)
            with resp:
                raw = resp.read()
                status = getattr(resp, "status", None) or resp.getcode()
                headers = dict(resp.headers.items()) if resp.headers else {}
        except urllib.error.HTTPError as err:
            raw = err.read() if err.fp else b""
            status, headers = err.code, dict(err.headers.items()) if err.headers else {}
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = {"raw": raw.decode("utf-8", errors="replace")}
        return status, headers, payload

    def access_token(self) -> str:
        if self._token and self._clock() < self._token_expiry - 300:
            return self._token
        form = urlencode({"client_id": self.client_id, "client_secret": self._secret, "scope": self.scope,
                          "grant_type": "client_credentials"}).encode("ascii")
        req = urllib.request.Request(self.token_endpoint(), data=form, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        status, _, payload = self._send(req)
        if status != 200 or not isinstance(payload, dict) or "access_token" not in payload:
            code = (payload or {}).get("error", "token_error") if isinstance(payload, dict) else "token_error"
            raise GraphError(status, str(code), "token acquisition failed", payload)
        self._token = payload["access_token"]
        self._token_expiry = self._clock() + int(payload.get("expires_in", 3599))
        return self._token

    def request(self, method: str, path: str, body=None, headers=None) -> GraphResponse:
        url = path if path.startswith("https://") else f"{self.base_url}{path}"
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs = {"Authorization": f"Bearer {self.access_token()}", "Accept": "application/json"}
        if data is not None:
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        status, resp_headers, payload = self._send(urllib.request.Request(url, data=data, method=method, headers=hdrs))
        return GraphResponse(status, resp_headers, payload)


# =============================================================================== client
class ConnectorClient:
    RETRY_STATUSES = (429, 503, 504)

    def __init__(self, transport, connection_id: str, *, sleep=time.sleep, max_retries: int = 3,
                 poll_interval: float = 1.0, max_polls: int = 60, validate: bool = True):
        self.transport, self.connection_id = transport, connection_id
        self._sleep, self._max_retries = sleep, max_retries
        self._poll_interval, self._max_polls, self._validate = poll_interval, max_polls, validate
        self.schema: Optional[dict] = None

    def _base(self) -> str:
        return f"/external/connections/{quote(self.connection_id, safe='')}"

    def _call(self, method: str, path: str, body=None) -> GraphResponse:
        for attempt in range(self._max_retries + 1):
            resp = self.transport.request(method, path, body)
            if resp.status in self.RETRY_STATUSES and attempt < self._max_retries:
                retry_after = resp.headers.get("Retry-After") if resp.headers else None
                self._sleep(float(retry_after) if retry_after else min(30.0, 2.0 ** attempt))
                continue
            return resp
        return resp

    @staticmethod
    def _raise_for(resp: GraphResponse, ok=(200, 201, 202, 204)) -> None:
        if resp.status not in ok:
            err = (resp.body or {}).get("error", {}) if isinstance(resp.body, dict) else {}
            raise GraphError(resp.status, err.get("code", "Error"), err.get("message", ""), resp.body)

    def create_connection(self, name: str, description: str) -> dict:
        from .graph_models import build_connection
        connection = build_connection(self.connection_id, name, description)
        if self._validate:
            errors = v.validate_connection(connection)
            if errors:
                raise v.ValidationError(errors)
        resp = self._call("POST", "/external/connections", connection)
        if resp.status == 409:
            resp = self._call("GET", self._base())
        self._raise_for(resp)
        return resp.body

    def register_schema(self, schema: dict, *, on_operation=None) -> dict:
        if self._validate:
            errors = v.validate_schema(schema)
            if errors:
                raise v.ValidationError(errors)
        resp = self._call(v.cfg("SCHEMA_REGISTRATION_METHOD"), f"{self._base()}/schema", schema)
        self._raise_for(resp, ok=(200, 202, 204))
        if resp.status == 202:
            location = next((value for key, value in (resp.headers or {}).items()
                             if key.casefold() == "location"), None)
            if not location:
                raise GraphError(502, "SchemaOperationMissing", "202 response is missing Location")
            if on_operation:
                on_operation(location)
            self.wait_for_schema(location)
        self.schema = schema
        return {"status": "completed"}

    def wait_for_schema(self, location: str, *, timeout: float = 1200.0) -> dict:
        """Only a completed async operation is ready; callers may resume a saved Location."""
        base = v.cfg("GRAPH_BASE_URL").rstrip("/")
        if location.startswith(base + "/"):
            location = location[len(base):]
        elif not location.startswith("/external/connections/"):
            raise GraphError(502, "InvalidOperationLocation", "schema operation URL is not a Graph endpoint")
        deadline = time.monotonic() + timeout
        transport_deadline = getattr(self.transport, "deadline", None)
        if transport_deadline is not None:
            deadline = min(deadline, transport_deadline)
        status = "unknown"
        for _ in range(self._max_polls):
            if time.monotonic() >= deadline:
                break
            op = self._call("GET", location)
            self._raise_for(op, ok=(200,))
            status = str((op.body or {}).get("status", "unknown")).casefold()
            if status == "completed":
                return {"status": "completed"}
            if status not in ("inprogress", "notstarted"):
                raise GraphError(500, "SchemaRegistrationFailed", "schema operation did not complete")
            retry_after = next((v for k, v in (op.headers or {}).items()
                                if k.casefold() == "retry-after"), None)
            try:
                delay = max(self._poll_interval, float(retry_after)) if retry_after else self._poll_interval
            except (TypeError, ValueError):
                delay = self._poll_interval
            self._sleep(min(delay, max(0, deadline - time.monotonic())))
        raise GraphError(504, "SchemaRegistrationTimeout", "schema operation timed out before completion")

    def put_item(self, item_id: str, item: dict) -> None:
        if self._validate:
            errors = v.validate_item(item, self.schema, item_id)
            if errors:
                raise v.ValidationError(errors)
        resp = self._call("PUT", f"{self._base()}/items/{quote(item_id, safe='')}", item)
        self._raise_for(resp, ok=(200, 201, 204))

    def delete_item(self, item_id: str, missing_ok: bool = True) -> bool:
        resp = self._call("DELETE", f"{self._base()}/items/{quote(item_id, safe='')}")
        if resp.status == 404 and missing_ok:
            return False
        self._raise_for(resp, ok=(200, 204))
        return True

    def get_item(self, item_id: str) -> dict:
        resp = self._call("GET", f"{self._base()}/items/{quote(item_id, safe='')}")
        self._raise_for(resp, ok=(200,))
        return resp.body
