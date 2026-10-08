"""Restricted stdlib Graph transport. Tokens, download URLs and error bodies are never evidence."""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

from arch_a_connector.graph_client import GraphError, GraphResponse

GRAPH = "https://graph.microsoft.com/v1.0"


def assert_pipeline_token(token, state):
    """Prevent accidentally running the demo with an admin/delegated token. Graph validates its signature."""
    try:
        encoded = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        required = {"Sites.Selected", "ExternalConnection.ReadWrite.OwnedBy", "ExternalItem.ReadWrite.OwnedBy"}
        valid = (claims.get("tid") == state["tenantId"]
                 and claims.get("appid", claims.get("azp")) == state["apps"]["Pipeline"]["clientId"]
                 and claims.get("aud") in ("https://graph.microsoft.com", "https://graph.microsoft.com/",
                                           "00000003-0000-0000-c000-000000000000")
                 and not claims.get("scp") and required.issubset(set(claims.get("roles", [])))
                 and float(claims.get("exp", 0)) > time.time())
    except (AttributeError, IndexError, TypeError, ValueError, KeyError):
        valid = False
    if not valid:
        raise ValueError("KX_GRAPH_TOKEN must be the current deployment's app-only pipeline Graph token")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def header(headers, name):
    return next((v for k, v in headers.items() if k.casefold() == name.casefold()), None)


def check_response(response, allowed=(200, 201, 204)):
    if response.status not in allowed:
        raise GraphError(response.status, "GraphRequestFailed", "Graph request failed (body omitted)")
    return response.body


def retry_delay(headers, attempt):
    value = header(headers, "retry-after")
    if value:
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return min(30.0, 2.0 ** attempt)


class GraphTransport:
    def __init__(self, token, *, sharepoint_host, opener=None, sleep=time.sleep, max_retries=3):
        if not token or not isinstance(token, str):
            raise ValueError("KX_GRAPH_TOKEN is required")
        self._token = token
        self.sharepoint_host = sharepoint_host.casefold()
        if not self.sharepoint_host.endswith(".sharepoint.com"):
            raise ValueError("Expected an approved SharePoint host")
        self._opener = opener or urllib.request.build_opener(NoRedirect()).open
        self._sleep, self.max_retries = sleep, max_retries
        self.deadline = None
        self.evidence = []
        self.counts = Counter()

    @staticmethod
    def graph_url(path):
        url = path if path.startswith("https://") else GRAPH + path
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname != "graph.microsoft.com"
                or parsed.port not in (None, 443) or parsed.username or parsed.password
                or parsed.fragment or not parsed.path.startswith("/v1.0/")):
            raise ValueError("Only Microsoft Graph v1.0 URLs are allowed")
        return url

    def _send(self, method, url, data=None, headers=None):
        for attempt in range(self.max_retries + 1):
            remaining = self.deadline - time.monotonic() if self.deadline is not None else 30.0
            if remaining <= 0:
                raise GraphError(504, "DeadlineExceeded", "Graph operation deadline exceeded")
            request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
            try:
                with self._opener(request, timeout=min(30.0, remaining)) as response:
                    status, hdrs = response.status, dict(response.headers.items())
                    raw = response.read(8 * 1024 * 1024 + 1) if status < 300 else b""
            except urllib.error.HTTPError as error:
                status, hdrs, raw = error.code, dict(error.headers.items()) if error.headers else {}, b""
                error.close()
            except (urllib.error.URLError, TimeoutError, OSError):
                raise GraphError(0, "TransportError", "Graph transport failed (details omitted)") from None
            self.counts[str(status)] += 1
            self.evidence.append({"status": status, "requestId": header(hdrs, "request-id"),
                                  "date": header(hdrs, "date")})
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError("Response exceeds demo byte limit")
            if status in (429, 503, 504) and attempt < self.max_retries:
                delay = retry_delay(hdrs, attempt)
                if (delay > 120 or (self.deadline is not None
                                   and delay >= self.deadline - time.monotonic())):
                    raise GraphError(status, "RetryDeferred", "Retry-After exceeds this run's retry budget")
                self._sleep(delay)
                continue
            return status, hdrs, raw

    def request(self, method, path, body=None, headers=None):
        data = body if isinstance(body, bytes) else (
            json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None)
        hdrs = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        if data is not None:
            hdrs["Content-Type"] = "application/octet-stream" if isinstance(body, bytes) else "application/json"
        if any(k.casefold() == "authorization" for k in (headers or {})):
            raise ValueError("Authorization overrides are forbidden")
        hdrs.update(headers or {})
        status, received, raw = self._send(method, self.graph_url(path), data, hdrs)
        if status >= 300:
            return GraphResponse(status, received, None)
        try:
            payload = json.loads(raw) if raw else None
        except (ValueError, UnicodeError):
            raise GraphError(status, "InvalidJson", "Graph response was not JSON") from None
        return GraphResponse(status, received, payload)

    def download(self, path):
        """Follow content redirects only, to this demo tenant's exact SharePoint host, without bearer."""
        url = self.graph_url(path)
        if not urlsplit(url).path.endswith("/content"):
            raise ValueError("Downloads must start at a Graph content endpoint")
        headers = {"Authorization": f"Bearer {self._token}"}
        for _ in range(5):
            status, received, raw = self._send("GET", url, headers=headers)
            if status == 200:
                return raw
            if status not in (301, 302, 303, 307, 308):
                check_response(GraphResponse(status))
            location = header(received, "location")
            if not location:
                raise ValueError("Content redirect missing Location")
            url = urljoin(url, location)
            parsed = urlsplit(url)
            if (parsed.scheme != "https" or parsed.hostname != self.sharepoint_host
                    or parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.fragment):
                raise ValueError("Content redirect host is not approved")
            headers = {}
        raise ValueError("Too many content redirects")
