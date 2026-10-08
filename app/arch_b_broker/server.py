"""Standard-library HTTP JSON API for the Knowledge Broker.

Routes: POST /ask, POST /search, POST /mcp (MCP JSON-RPC), GET /healthz.
Status codes: 400 bad request, 401 invalid/missing token, 403 policy denial, 404, 405, 413, 429.
Production: Azure Functions or Container Apps behind Entra ID authentication, private networking to
the index, managed identity, and API Management for throttling/WAF.
"""
from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .broker import BrokerResult, KnowledgeBroker
from .constants import MAX_REQUEST_BYTES


def make_handler(broker: KnowledgeBroker):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ContosoKnowledgeBroker/0.1"
        sys_version = ""

        def log_message(self, fmt, *args):  # keep test output clean; requests are audited instead
            return

        def _send(self, result: BrokerResult) -> None:
            data = b"" if result.body is None else json.dumps(result.body, ensure_ascii=False).encode("utf-8")
            self.send_response(result.status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for name, value in (result.headers or {}).items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if data:
                self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                self._send(BrokerResult(200, {"status": "ok", "contractId": broker.contract.contractId,
                                              "indexedChunks": len(broker.index)}))
            elif path in ("/ask", "/search", "/mcp"):
                self._send(BrokerResult(405, {"error": {"code": "method_not_allowed", "message": "use POST"}},
                                        {"Allow": "POST"}))
            else:
                self._send(BrokerResult(404, {"error": {"code": "not_found", "message": path}}))

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            request_id = uuid.uuid4().hex
            if path not in ("/ask", "/search", "/mcp"):
                self._send(BrokerResult(404, {"error": {"code": "not_found", "message": path}}))
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length < 0 or length > MAX_REQUEST_BYTES:
                if MAX_REQUEST_BYTES < length <= 64 * MAX_REQUEST_BYTES:
                    self.rfile.read(length)  # drain so the client sees the 413 instead of a connection reset
                self._send(BrokerResult(413 if length > 0 else 400,
                                        {"error": {"code": "invalid_request", "message": "invalid body length"}}))
                return
            try:
                payload = json.loads(self.rfile.read(length) or b"null")
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send(BrokerResult(400, {"error": {"code": "invalid_request", "message": "body must be JSON"}}))
                return
            auth = self.headers.get("Authorization")
            if path == "/ask":
                result = broker.ask(auth, payload, request_id)
            elif path == "/search":
                result = broker.search(auth, payload, request_id)
            else:
                result = broker.mcp(auth, payload)
            result.headers = dict(result.headers or {}, **{"X-Request-Id": request_id})
            self._send(result)

    return Handler


class RunningServer:
    def __init__(self, broker: KnowledgeBroker, host: str = "127.0.0.1", port: int = 0):
        self.httpd = ThreadingHTTPServer((host, port), make_handler(broker))
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.base_url = f"http://{host}:{self.httpd.server_address[1]}"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def start_server(broker: KnowledgeBroker, host: str = "127.0.0.1", port: int = 0) -> RunningServer:
    return RunningServer(broker, host, port)
