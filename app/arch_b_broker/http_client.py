"""Minimal JSON client for the broker HTTP API (used by tests and the benchmark; loopback only, no proxies)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request


class BrokerHttpClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, method: str, path: str, body=None, token=None, raw: bytes = None, headers=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode("utf-8"))
        hdrs = {"Content-Type": "application/json", **(headers or {})}
        if token:
            hdrs["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.base_url + path, data=data, method=method, headers=hdrs)
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                status, resp_headers, payload = resp.status, dict(resp.headers.items()), resp.read()
        except urllib.error.HTTPError as err:
            status, resp_headers, payload = err.code, dict(err.headers.items()), err.read()
        text = payload.decode("utf-8") if payload else ""
        return status, resp_headers, (json.loads(text) if text else None), text

    def ask(self, token, question, purpose="yield-excursion-analysis"):
        return self.call("POST", "/ask", {"question": question, "purpose": purpose}, token)

    def search(self, token, query, purpose="yield-excursion-analysis", top=None):
        body = {"query": query, "purpose": purpose}
        if top is not None:
            body["top"] = top
        return self.call("POST", "/search", body, token)

    def mcp(self, token, method, params=None, rpc_id=1):
        return self.call("POST", "/mcp", {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}}, token)
