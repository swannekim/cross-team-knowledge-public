"""Explicit Container Apps entry point; the offline prototype remains unchanged.

Run: python -m arch_b_broker.live
Required environment: KX_TENANT_ID, KX_BROKER_CLIENT_ID, KX_AUDIENCE_GROUP_ID,
KX_GRAPH_CLIENT_ID, KX_CONTRACT_PATH, KX_SOURCE_SNAPSHOT_PATH, KX_PUBLIC_ORIGIN,
KX_STORAGE_MODE=azure-blob, KX_STORAGE_ROOT, KX_STATE_PATH, KX_REPLICA_COUNT=1,
KX_BLOB_ACCOUNT_URL, KX_BLOB_CONTAINER, KX_BLOB_NAME. The same managed identity
needs Storage Blob Data Contributor on the private container. Precreate the blob
with exact UTF-8 bytes "KX_BROKER_STATE_V1\\n<tenant>:<broker-client>:<contractId>";
never recreate it after requests have been served. Missing blobs fail startup.
KX_PURVIEW_MODE defaults to required, which fails startup: no verified live
Purview adapter is shipped. Only explicit disabled-demo allows this synthetic
demo and every response labels Purview "not evaluated". PORT defaults to 8080.

The source is a bounded, contract-bound Graph snapshot, NOT continuous ingestion.
Retrieval is real local BM25 with extractive answers, NOT AI Search or Azure OpenAI.
Use a single active Container Apps revision with maxReplicas=1; minReplicas may be
zero for scale-to-zero or one to avoid cold starts. KX_REPLICA_COUNT=1 is the maximum
concurrent instance count. A leased Azure Blob retains counters across scale-to-zero.
SQLite runs on local /app/state, never on SMB; Azure Files failed COMMIT in the
actual deployment and is explicitly unsupported. Each committed database snapshot
is uploaded under a renewable lease and ETag before releasing a response.
The exclusive blob lease requires stopping the old revision before replacement;
this deliberately small demo does not support zero-downtime rolling updates.
Restart to apply a changed snapshot or contract; changes fail closed meanwhile.

For local validation only: KX_STORAGE_MODE=local-validation uses a designated
persistent local directory, binds 127.0.0.1 and permits a transient Graph app token
in KX_GRAPH_ACCESS_TOKEN. This mode is forbidden inside the packaged container or
Container Apps. No token is written to disk. KX_PUBLIC_ORIGIN may then be an HTTP
127.0.0.1 origin. KX_SOURCE_MANIFEST_PATH optionally supplies snapshot bindings for
a bare SourceDocument[] JSON file; otherwise the snapshot is a single envelope.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import signal
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from common.clock import SystemClock, parse_iso, to_iso
from common.contract import load_contract
from common.sources import SourceDocument

from . import constants as k
from .auth import AuthError
from .broker import BrokerResult, KnowledgeBroker
from .index import BM25Index, _build
from .live_auth import (DependencyUnavailable, EntraTokenValidator, GraphDirectory, ManagedIdentityGraphToken,
                        TransientGraphToken, guid)
from .live_state import DurableAudit, DurablePDP, DurableState
from .live_blob import BlobDurableState, LeasedBlobCheckpoint, MAX_STATE_BYTES
from .purview import PurviewDecision

MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024

DEMO_PRIVACY_NOTICE = """KX synthetic knowledge demo - demo privacy notice

This is an operator-approved synthetic-data demonstration, not a production service or a formal organizational privacy policy. Do not submit personal, confidential, customer, or production information.

The architecture A KX synthetic connector knowledge agent uses native Microsoft 365 signed-in user access and connector item ACLs to search published derived synthetic items in ExampleDerived under contract KX-Synthetic-20261007. It does not call the architecture B broker. Published connector items, native search processing, and Copilot history are handled by Microsoft 365; the broker-specific identity checks, BM25 retrieval, counters and audit storage described below apply only to architecture B.

Microsoft Entra identifies the signed-in caller. The broker checks delegated Knowledge.Ask permission, current Microsoft Graph group membership, guest exclusion, the sharing contract, and the allowed purpose. This does not grant access to original files.

Questions and returned synthetic text are processed by the broker and Microsoft 365 Copilot. The deployment contains a manually captured synthetic source snapshot. The broker stores user object IDs, rate and coverage counters, request IDs, timestamps, policy decisions, question lengths, hashes of questions and processed text, and opaque source/chunk references in local SQLite checkpointed to a private Azure Blob. The current broker audit does not store raw questions or response bodies. These audit records are not an immutable compliance archive.

Copilot conversation history and service diagnostics may retain questions, responses, and operational records under their own settings. Broker contract/snapshot expiry stops new retrieval; it does not erase audit records, existing copies, or Copilot history. The operator must arrange the manual expiry, cleanup, and retention schedule.

Live Microsoft Purview is not evaluated. No production privacy, security, or compliance warranty is made. Contact the demo operator through your approved internal channel for access, retention, or removal requests; these public notice pages do not submit requests.
"""

DEMO_TERMS_NOTICE = """KX synthetic knowledge demo - demo usage notice

Use only for the operator-approved synthetic demonstration. Personal installation by an approved tester is not tenant-wide publication or approval for production use. Do not submit personal, confidential, customer, or production information.

The architecture A KX synthetic connector knowledge agent is scoped to GraphConnectors connection ExampleDerived and contract KX-Synthetic-20261007, with native Microsoft 365 user access and connector ACLs. It has no broker action, SharePoint search capability, or web search capability. A correct answer alone does not prove connector grounding: verify connector citation provenance. Published derived items require operator-managed expiry cleanup; index/cache propagation and recall of existing copies are not guaranteed.

The broker requires Microsoft Entra delegated identity and scoped authorization for contract KX-DEMO-20261007 and purpose yield-excursion-analysis. App-only callers are not supported. Original files and their locations remain protected; opaque citations and the informational access-request page do not grant or request original-file access.

Broker results are capped, redacted, deterministic BM25 extracts from a manual Graph snapshot, not a complete answer, continuous ingestion, Azure AI Search, or an LLM-generated broker answer. A successful request does not establish answer quality or ordinary-user/Copilot validation. If evidence is insufficient, say so; do not substitute general knowledge, guess a numeric answer, or evade rate or coverage limits.

Live Microsoft Purview is not evaluated and demo sensitivity metadata is not a live label or compliance decision. No production availability, accuracy, privacy, or compliance warranty is made.

The operator must approve use and arrange the manual expiry and cleanup schedule. Broker contract/snapshot expiry can block new retrieval but cannot recall returned text, copies, audit records, or Copilot history. See /demo/privacy for processing and persistence details. Contact the demo operator through your approved internal channel for support; these pages do not submit requests.
"""


def _read_bounded(path, limit):
    with Path(path).open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("configuration file is too large")
    return data


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _origin(value, local=False):
    parsed = urlsplit(value)
    allowed_scheme = parsed.scheme == "https" or (local and parsed.scheme == "http" and parsed.hostname == "127.0.0.1")
    if (not allowed_scheme or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("KX_PUBLIC_ORIGIN must be an HTTPS origin")
    return value.rstrip("/")


@dataclass(frozen=True)
class LiveConfig:
    tenant_id: str
    broker_client_id: str
    audience_group_id: str
    graph_client_id: str
    contract_path: Path
    snapshot_path: Path
    public_origin: str
    storage_root: Path
    state_path: Path
    port: int
    storage_mode: str = "azure-blob"
    manifest_path: Path | None = None
    blob_account_url: str | None = None
    blob_container: str | None = None
    blob_name: str | None = None

    @classmethod
    def from_env(cls, env):
        names = {
            "KX_TENANT_ID", "KX_BROKER_CLIENT_ID", "KX_AUDIENCE_GROUP_ID", "KX_GRAPH_CLIENT_ID",
            "KX_CONTRACT_PATH", "KX_SOURCE_SNAPSHOT_PATH", "KX_PUBLIC_ORIGIN",
            "KX_STORAGE_MODE", "KX_STORAGE_ROOT", "KX_STATE_PATH", "KX_REPLICA_COUNT", "KX_PURVIEW_MODE",
            "KX_SOURCE_MANIFEST_PATH", "KX_GRAPH_ACCESS_TOKEN", "KX_CONTAINER_DEPLOYMENT",
            "KX_BLOB_ACCOUNT_URL", "KX_BLOB_CONTAINER", "KX_BLOB_NAME",
        }
        unknown = sorted(name for name in env if name.startswith("KX_") and name not in names)
        if unknown:
            raise ValueError("unknown broker configuration keys: " + ", ".join(unknown))
        optional = {"KX_PURVIEW_MODE", "KX_SOURCE_MANIFEST_PATH", "KX_GRAPH_ACCESS_TOKEN", "KX_CONTAINER_DEPLOYMENT",
                    "KX_BLOB_ACCOUNT_URL", "KX_BLOB_CONTAINER", "KX_BLOB_NAME"}
        for name in names - optional:
            if not env.get(name):
                raise ValueError(f"{name} is required")
        if env.get("KX_PURVIEW_MODE", "required") != "disabled-demo":
            raise ValueError("live Purview is required but unavailable; only explicit disabled-demo supports this demo")
        local = env["KX_STORAGE_MODE"] == "local-validation"
        if env["KX_STORAGE_MODE"] == "azure-files":
            raise ValueError("Azure Files SQLite is unsupported; configure azure-blob instead")
        if env["KX_STORAGE_MODE"] not in ("azure-blob", "local-validation") or env["KX_REPLICA_COUNT"] != "1":
            raise ValueError("require explicit persistent storage and exactly one replica")
        if local and (env.get("CONTAINER_APP_NAME") or env.get("CONTAINER_APP_REVISION")
                      or env.get("KX_CONTAINER_DEPLOYMENT")):
            raise ValueError("local validation is forbidden in deployed containers")
        if env.get("KX_GRAPH_ACCESS_TOKEN") and not local:
            raise ValueError("transient Graph tokens are permitted only in local validation")
        if local and not env.get("KX_GRAPH_ACCESS_TOKEN"):
            raise ValueError("local validation requires an explicit transient Graph app token")
        paths = [Path(env[name]) for name in
                 ("KX_CONTRACT_PATH", "KX_SOURCE_SNAPSHOT_PATH", "KX_STORAGE_ROOT", "KX_STATE_PATH")]
        if any(not path.is_absolute() for path in paths):
            raise ValueError("all broker paths must be absolute")
        contract, snapshot, root, state = [path.resolve() for path in paths]
        if not state.is_relative_to(root) or state == root:
            raise ValueError("KX_STATE_PATH must be beneath KX_STORAGE_ROOT")
        if state in (contract, snapshot):
            raise ValueError("state must not overwrite input configuration")
        port = int(env.get("PORT", "8080"))
        if not 1 <= port <= 65535:
            raise ValueError("invalid PORT")
        manifest = Path(env["KX_SOURCE_MANIFEST_PATH"]) if env.get("KX_SOURCE_MANIFEST_PATH") else None
        if manifest is not None and (not manifest.is_absolute() or manifest.resolve() == state):
            raise ValueError("snapshot manifest must be an absolute configuration path, not the state database")
        blob_url, blob_container, blob_name = (env.get(name) for name in
                                               ("KX_BLOB_ACCOUNT_URL", "KX_BLOB_CONTAINER", "KX_BLOB_NAME"))
        if env["KX_STORAGE_MODE"] == "azure-blob":
            if (not blob_url or not re.fullmatch(r"https://[a-z0-9]{3,24}\.blob\.core\.windows\.net", blob_url)
                    or not blob_container or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", blob_container)
                    or "--" in blob_container or not blob_name
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", blob_name)):
                raise ValueError("azure-blob requires a public-cloud account URL, private container and simple blob name")
        elif any((blob_url, blob_container, blob_name)):
            raise ValueError("blob configuration requires azure-blob storage mode")
        return cls(*(guid(env[name]) for name in
                     ("KX_TENANT_ID", "KX_BROKER_CLIENT_ID", "KX_AUDIENCE_GROUP_ID", "KX_GRAPH_CLIENT_ID")),
                   contract, snapshot, _origin(env["KX_PUBLIC_ORIGIN"], local), root, state, port,
                   env["KX_STORAGE_MODE"], manifest.resolve() if manifest else None,
                   blob_url, blob_container, blob_name)

    def verify_mount(self):
        if self.storage_mode == "local-validation":
            if not self.storage_root.is_dir() or not self.state_path.parent.is_dir():
                raise ValueError("local validation requires an existing designated persistent directory")
            return
        if self.storage_mode == "azure-files":
            raise ValueError("Azure Files SQLite is unsupported: actual deployment COMMIT failed with database locked; "
                             "configure azure-blob instead")
        if self.storage_mode == "azure-blob":
            if sys.platform != "linux" or not self.state_path.parent.is_dir():
                raise ValueError("azure-blob requires an existing local Linux state directory")
            for line in Path("/proc/mounts").read_text().splitlines():
                fields = line.split()
                if len(fields) >= 3:
                    mount = Path(fields[1].replace("\\040", " ").replace("\\134", "\\")).resolve()
                    if self.state_path.parent.is_relative_to(mount) and fields[2] in ("cifs", "smb3", "nfs", "nfs4"):
                        raise ValueError("azure-blob local SQLite cache must not be on a network filesystem")
            return
        raise ValueError("unsupported storage mode")


@dataclass(frozen=True)
class Snapshot:
    documents: tuple
    captured_at: object
    valid_until: object
    content_hash: str
    manifest_hash: str | None = None


def load_snapshot(config, contract, clock):
    raw = _read_bounded(config.snapshot_path, MAX_SNAPSHOT_BYTES)
    payload = json.loads(raw)
    manifest_hash = None
    if config.manifest_path:
        manifest_raw = _read_bounded(config.manifest_path, 65536)
        manifest = json.loads(manifest_raw)
        if not isinstance(manifest, dict) or manifest.get("documentsSha256") != _digest(raw):
            raise ValueError("snapshot manifest does not bind these document bytes")
        if not isinstance(payload, list) or "documents" in manifest:
            raise ValueError("a separate snapshot manifest requires a bare document list")
        manifest_hash = _digest(manifest_raw)
        payload = {**manifest, "documents": payload}
        del payload["documentsSha256"]
    expected = {"schemaVersion", "tenantId", "sourceSite", "contractFingerprint", "capturedAt", "synthetic", "documents"}
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError("snapshot must use the documented envelope fields")
    if (type(payload["schemaVersion"]) is not int or payload["schemaVersion"] != 1
            or payload["tenantId"] != config.tenant_id or payload["synthetic"] is not True
            or payload["sourceSite"] != contract.sourceSite
            or payload["contractFingerprint"] != contract.fingerprint()):
        raise ValueError("snapshot tenant, source or contract binding mismatch")
    captured_at = parse_iso(payload["capturedAt"])
    valid_until = contract.derivative_valid_until(captured_at)
    if captured_at > clock.now() + timedelta(seconds=30) or clock.now() >= valid_until:
        raise ValueError("snapshot is future-dated or expired")
    site = urlsplit(contract.sourceSite)
    if site.scheme != "https" or not site.hostname or not site.hostname.endswith(".sharepoint.com"):
        raise ValueError("snapshot source must be a SharePoint Online site")
    rows = payload["documents"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise ValueError("snapshot requires 1..1000 documents")
    documents, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid snapshot document")
        doc = SourceDocument(**row)
        if any(not isinstance(getattr(doc, field), str) or not getattr(doc, field) for field in
               ("source_id", "name", "title", "path", "web_url", "etag", "last_modified", "text", "fingerprint")):
            raise ValueError("snapshot document is missing provenance")
        if doc.source_id in seen or len(doc.text) > 500000:
            raise ValueError("duplicate or oversized snapshot document")
        seen.add(doc.source_id)
        if (not isinstance(doc.meta, dict) or not isinstance(doc.meta.get("graphDriveId"), str)
                or not doc.meta["graphDriveId"] or not isinstance(doc.meta.get("graphItemId"), str)
                or not doc.meta["graphItemId"]):
            raise ValueError("Graph drive and item provenance required")
        location = urlsplit(doc.web_url)
        if (location.scheme != "https" or location.netloc != site.netloc
                or not unquote(location.path).startswith(unquote(site.path).rstrip("/") + "/")
                or location.username or location.password):
            raise ValueError("snapshot source location is outside the contract site")
        if (doc.kind != "file" or not contract.path_in_scope(doc.path)[0]
                or not contract.label_allowed(doc.label)):
            raise ValueError("snapshot document is outside the sharing contract")
        if parse_iso(doc.last_modified) > captured_at + timedelta(seconds=30):
            raise ValueError("snapshot predates its source revision")
        def strip_locations(text):
            for marker in (doc.web_url, contract.sourceSite, doc.path, doc.source_id,
                           doc.meta["graphDriveId"], doc.meta["graphItemId"]):
                text = text.replace(marker, "[SOURCE LOCATION REDACTED]")
            return re.sub(r"https?://[^\s<>\"']*\.sharepoint\.com[^\s<>\"']*", "[SOURCE LOCATION REDACTED]",
                          text, flags=re.IGNORECASE)
        documents.append(dataclasses.replace(doc, text=strip_locations(doc.text), title=strip_locations(doc.title)))
    return Snapshot(tuple(documents), captured_at, valid_until, _digest(raw), manifest_hash)


class ExplicitlyDisabledDemoPurview:
    def process_content(self, user, text, activity):
        return PurviewDecision("not_evaluated", activity, reason="explicit disabled-demo; no compliance evaluation")


class LiveBroker(KnowledgeBroker):
    def __init__(self, *, config, snapshot, state, contract_hash, **kwargs):
        super().__init__(**kwargs, purview=ExplicitlyDisabledDemoPurview())
        self.config, self.snapshot, self.state, self.contract_hash = config, snapshot, state, contract_hash
        self.pdp = DurablePDP(self.contract, self.directory, self.clock, state)

    def metadata(self):
        return {"mode": "live-synthetic-demo", "authentication": "entra-rs256", "directory": "microsoft-graph",
                "deployment": self.config.storage_mode,
                "retrieval": "local-bm25", "answerGeneration": "extractive-no-model",
                "ingestion": "graph-snapshot-not-continuous", "capturedAt": to_iso(self.snapshot.captured_at),
                "validUntil": to_iso(self.snapshot.valid_until),
                "purview": {"mode": "disabled-demo", "evaluation": "not evaluated", "complianceClaim": False}}

    def diagnostics(self):
        checks = {"persistenceReady": self.state.healthy(),
                  "contractActive": self.contract.is_active(self.clock.now()),
                  "snapshotFresh": self.clock.now() < self.snapshot.valid_until,
                  "configurationUnchanged": False}
        try:
            checks["configurationUnchanged"] = (
                _digest(_read_bounded(self.config.contract_path, 65536)) == self.contract_hash
                and _digest(_read_bounded(self.config.snapshot_path, MAX_SNAPSHOT_BYTES)) == self.snapshot.content_hash
                and (self.config.manifest_path is None or
                     _digest(_read_bounded(self.config.manifest_path, 65536)) == self.snapshot.manifest_hash))
        except (OSError, ValueError):
            pass
        return checks

    def valid(self):
        return all(self.diagnostics().values())

    def _source_view(self, chunk):
        source = super()._source_view(chunk)
        source["accessRequestUrl"] = self.config.public_origin + "/access-request?ref=" + chunk.ref
        return source

    def execute(self, operation, authorization, payload):
        request_id = uuid.uuid4().hex
        try:
            json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return BrokerResult(400, {"error": {"code": "invalid_json",
                                               "message": "request must contain valid Unicode JSON"},
                                      "service": self.metadata(), "requestId": request_id},
                                {"X-Request-Id": request_id, "X-Purview-Evaluation": "not-evaluated"})
        try:
            with self.state.transaction():
                if not self.valid():
                    result = BrokerResult(503, {"error": {"code": "snapshot_or_contract_inactive",
                                                        "message": "source snapshot or contract requires refresh"}})
                elif operation == "mcp":
                    result = self._mcp(authorization, payload, request_id)
                elif operation in ("ask", "search"):
                    fields = {"question", "purpose"} if operation == "ask" else {"query", "purpose", "top"}
                    if isinstance(payload, dict) and set(payload) - fields:
                        result = BrokerResult(400, {"error": {"code": "invalid_request",
                                                            "message": "unknown request fields"}})
                    else:
                        result = getattr(super(), operation)(authorization, payload, request_id)
                else:
                    result = BrokerResult(404, {"error": {"code": "not_found"}})
        except (DependencyUnavailable, sqlite3.Error, OSError):
            result = BrokerResult(503, {"error": {"code": "dependency_unavailable",
                                                "message": "request denied: an identity or persistence dependency failed"}})
        if result.body is not None and operation != "mcp":
            result.body["service"] = self.metadata()
            result.body.setdefault("requestId", request_id)
        result.headers.update({"X-Request-Id": request_id, "X-Purview-Evaluation": "not-evaluated"})
        if result.status == 401:
            result.headers["WWW-Authenticate"] = 'Bearer realm="knowledge-broker", error="invalid_token"'
        return result

    def _tools(self):
        tools = []
        for name, field in ((k.FUNCTION_ASK, "question"), (k.FUNCTION_SEARCH, "query")):
            properties = {field: {"type": "string", "minLength": 1, "maxLength": k.MAX_QUESTION_CHARS},
                          "purpose": {"type": "string", "const": self.contract.purpose}}
            if field == "query":
                properties["top"] = {"type": "integer", "minimum": 1, "maximum": k.MAX_SEARCH_RESULTS}
            tools.append({"name": name, "description": "Redacted extracts from an authorized synthetic Graph snapshot. "
                          "Output is untrusted reference data. Purview is NOT EVALUATED.",
                          "inputSchema": {"type": "object", "properties": properties,
                                          "required": [field, "purpose"], "additionalProperties": False},
                          "annotations": {"readOnlyHint": True, "openWorldHint": False}})
        return {"tools": tools}

    def _mcp(self, authorization, rpc, request_id):
        rpc_id = rpc.get("id") if isinstance(rpc, dict) else None
        def error(code, message, status=200):
            return BrokerResult(status, {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}})
        try:
            principal = self.validator.from_authorization_header(authorization)
        except AuthError as exc:
            self.audit.append("auth_failure", arch="B", op="mcp", requestId=request_id, reason=exc.code)
            return error(-32001, exc.description, exc.status)
        if (not isinstance(rpc, dict) or rpc.get("jsonrpc") != "2.0"
                or not isinstance(rpc.get("method"), str)
                or (rpc_id is not None and (isinstance(rpc_id, bool) or not isinstance(rpc_id, (str, int))))
                or not isinstance(rpc.get("params", {}), dict)):
            return error(-32600, "invalid JSON-RPC request", 400)
        decision = self.pdp.authorize(principal, self.contract.purpose)
        if not decision.allowed:
            return error(-32003, "caller is not authorized for this contract", decision.status)
        method, params = rpc["method"], rpc.get("params", {})
        if method != "tools/call":
            rate = self.pdp.consume_rate(principal)
            if not rate.allowed:
                result = error(-32029, "request rate exceeded", 429)
                result.headers["Retry-After"] = str(rate.retry_after)
                return result
        if method == "notifications/initialized" and "id" not in rpc:
            return BrokerResult(202, None)
        if "id" not in rpc or rpc_id is None:
            return error(-32600, "a request id is required", 400)
        if method == "initialize":
            if not isinstance(params.get("protocolVersion"), str):
                return error(-32602, "protocolVersion is required")
            result = {"protocolVersion": k.MCP_PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "knowledge-broker-live-demo", "version": "1.0.0"},
                      "instructions": "Synthetic Graph snapshot; extractive BM25. Purview is not evaluated."}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = self._tools()
        elif method == "tools/call":
            name = params.get("name")
            operation = {k.FUNCTION_ASK: "ask", k.FUNCTION_SEARCH: "search"}.get(name) if isinstance(name, str) else None
            args = params.get("arguments")
            if operation is None or not isinstance(args, dict):
                return error(-32602, "unknown tool or invalid arguments")
            fields = {"question", "purpose"} if operation == "ask" else {"query", "purpose", "top"}
            if set(args) - fields:
                return error(-32602, "unknown tool arguments")
            inner = getattr(super(), operation)(authorization, args, request_id)
            inner.body["service"] = self.metadata()
            result = {"content": [{"type": "text", "text": json.dumps(inner.body, ensure_ascii=False)}],
                      "structuredContent": inner.body, "isError": inner.status != 200}
        else:
            return error(-32601, "method not found")
        return BrokerResult(200, {"jsonrpc": "2.0", "id": rpc_id, "result": result})


def build_live_broker(config, env):
    import jwt  # Fail startup, not the first authenticated request, if live dependencies are absent.
    config.verify_mount()
    clock = SystemClock()
    contract_hash = _digest(_read_bounded(config.contract_path, 65536))
    contract = load_contract(config.contract_path)
    if (not contract.is_active(clock.now()) or contract.audienceGroupIds != (config.audience_group_id,)
            or not contract.excludeGuests or contract.allowTenantWideAudience
            or "redactedExtract" not in contract.derivativeTypes):
        raise ValueError("live contract must be active, single-audience, guest-excluding and allow redacted extracts")
    snapshot = load_snapshot(config, contract, clock)
    token_provider = (TransientGraphToken(env["KX_GRAPH_ACCESS_TOKEN"]) if config.storage_mode == "local-validation"
                      else ManagedIdentityGraphToken(config.graph_client_id, env))
    directory = GraphDirectory(token_provider, config.audience_group_id)
    binding = f"{config.tenant_id}:{config.broker_client_id}:{contract.contractId}"
    try:
        if config.storage_mode == "azure-blob":
            from azure.storage.blob import BlobClient
            blob = BlobClient(account_url=config.blob_account_url, container_name=config.blob_container,
                              blob_name=config.blob_name, credential=token_provider.credential,
                              retry_total=0, connection_timeout=5, read_timeout=15,
                              max_single_put_size=MAX_STATE_BYTES, max_single_get_size=MAX_STATE_BYTES)
            state = BlobDurableState(config.state_path, binding, LeasedBlobCheckpoint(blob))
        else:
            state = DurableState(config.state_path, binding)
    except Exception:
        token_provider.close()
        raise
    try:
        source_index = _build(snapshot.documents, k.INDEX_CHUNK_MAX_CHARS, state.ref_key)
        index = BM25Index()
        for chunk in source_index.chunks:
            index.add(dataclasses.replace(chunk, chunk_id=chunk.ref + "-" + _digest(chunk.text.encode())[:24]))
        index.finalize()
        validator = EntraTokenValidator(config.tenant_id, config.broker_client_id)
        broker = LiveBroker(config=config, snapshot=snapshot, state=state, contract_hash=contract_hash,
                            contract=contract, directory=directory, index=index, validator=validator,
                            audit=DurableAudit(state, clock), clock=clock)
        with state.transaction():
            broker.audit.append("live_start", arch="B", snapshotHash=snapshot.content_hash,
                                contractFingerprint=contract.fingerprint(), purview="not evaluated")
        return broker
    except Exception:
        state.close()
        token_provider.close()
        raise


def make_live_handler(broker):
    class Handler(BaseHTTPRequestHandler):
        server_version, sys_version = "KnowledgeBrokerLiveDemo/1.0", ""

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, *args):
            return

        def send_result(self, result):
            data = b"" if result.body is None else json.dumps(result.body, ensure_ascii=False).encode("utf-8")
            self.send_response(result.status)
            for name, value in {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store",
                                "X-Content-Type-Options": "nosniff", "X-Purview-Evaluation": "not-evaluated",
                                "Content-Length": str(len(data)), **result.headers}.items():
                self.send_header(name, value)
            self.end_headers()
            if data:
                self.wfile.write(data)

        def do_GET(self):
            path = urlsplit(self.path).path
            notices = {"/demo/privacy": DEMO_PRIVACY_NOTICE, "/demo/terms": DEMO_TERMS_NOTICE}
            if path in notices:
                data = notices[path].encode("utf-8")
                self.send_response(200)
                for name, value in {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store",
                                    "X-Content-Type-Options": "nosniff", "X-Purview-Evaluation": "not-evaluated",
                                    "Content-Length": str(len(data))}.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(data)
            elif path == "/healthz":
                checks = broker.diagnostics()
                valid = all(checks.values())
                self.send_result(BrokerResult(200 if valid else 503, {"status": "ok" if valid else "unavailable",
                    "checks": checks, "directoryProbe": "not-performed", "service": broker.metadata()}))
            elif path == "/access-request":
                self.send_result(BrokerResult(200, {"message": "Contact the demo data owner through your approved "
                    "internal channel and provide the opaque reference. This endpoint does not grant access, "
                    "resolve references, or submit requests.", "purview": "not evaluated"}))
            else:
                method_error = path in ("/ask", "/search", "/mcp")
                self.send_result(BrokerResult(405 if method_error else 404,
                                               {"error": {"code": "method_not_allowed_or_not_found"}},
                                               {"Allow": "POST"} if method_error else {}))

        def do_POST(self):
            path = urlsplit(self.path).path
            origin = self.headers.get("Origin")
            if origin is not None and origin != broker.config.public_origin:
                self.send_result(BrokerResult(403, {"error": {"code": "origin_not_allowed"}}))
                return
            if path not in ("/ask", "/search", "/mcp"):
                self.send_result(BrokerResult(404, {"error": {"code": "not_found"}}))
                return
            if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Authorization", [])) > 1:
                self.send_result(BrokerResult(400, {"error": {"code": "invalid_headers"}}))
                return
            if self.headers.get_content_type() != "application/json":
                self.send_result(BrokerResult(415, {"error": {"code": "json_required"}}))
                return
            try:
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) != 1:
                    raise ValueError("a single Content-Length is required")
                length = int(lengths[0])
                if not 1 <= length <= k.MAX_REQUEST_BYTES:
                    self.send_result(BrokerResult(413, {"error": {"code": "invalid_body_length"}}))
                    return
                data = self.rfile.read(length)
                if len(data) != length:
                    raise ValueError("incomplete request body")
                payload = json.loads(data, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            except (ValueError, UnicodeDecodeError, TimeoutError):
                self.send_result(BrokerResult(400, {"error": {"code": "invalid_json"}}))
                return
            if path == "/mcp" and self.headers.get("MCP-Protocol-Version", k.MCP_PROTOCOL_VERSION) != k.MCP_PROTOCOL_VERSION:
                self.send_result(BrokerResult(400, {"error": {"code": "unsupported_mcp_version"}}))
                return
            self.send_result(broker.execute(path[1:], self.headers.get("Authorization"), payload))

    return Handler


def main():
    config = LiveConfig.from_env(os.environ)
    broker = build_live_broker(config, os.environ)
    server = None
    def terminate(signum, frame):
        raise SystemExit(0)
    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        host = "127.0.0.1" if config.storage_mode == "local-validation" else "0.0.0.0"
        server = HTTPServer((host, config.port), make_live_handler(broker))
        print(json.dumps({"event": "listening", "port": config.port, **broker.metadata()}), flush=True)
        server.serve_forever()
    finally:
        signal.signal(signal.SIGTERM, previous)
        if server is not None:
            server.server_close()
        try:
            broker.state.close()
        finally:
            broker.directory.token_provider.close()


if __name__ == "__main__":
    main()
