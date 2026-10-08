"""Baseline broker engine reused by the separate live Container Apps entry point.

Request pipeline: token validation -> PDP (contract, audience, guests, purpose, rate)
-> local prompt-policy hook -> retrieval/coverage guard -> redaction/excerpt cap
-> local response-policy hook -> coverage/audit. Hooks do not call live Purview.
Answers are deterministic extracts with opaque citations, not LLM synthesis.

server.py serves the offline HS256/fixture implementation. live.py supplies Entra
RS256, Graph directory checks and Blob-checkpointed SQLite instead, with explicit
Purview-not-evaluated metadata. Example Copilot manifests are not deployed SSO.
HTTP/MCP availability and local tests do not establish successful user retrieval.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Optional

from common.audit import AuditLog
from common.contract import SharingContract
from common.directory import Directory
from common.injection import detect as detect_injection
from common.redaction import redact
from common.refs import access_request_url
from common.summarizer import select_excerpt
from common.text import truncate

from . import constants as k
from .auth import AuthError, TokenValidator
from .index import BM25Index
from .pdp import PolicyDecisionPoint
from .purview import AllowAllPurviewClient, PurviewClient


@dataclass
class BrokerResult:
    status: int
    body: Optional[dict]
    headers: dict = field(default_factory=dict)


def _error(status: int, code: str, message: str, request_id: str, headers=None) -> BrokerResult:
    return BrokerResult(status, {"error": {"code": code, "message": message}, "requestId": request_id}, headers or {})


class KnowledgeBroker:
    def __init__(self, *, contract: SharingContract, directory: Directory, index: BM25Index,
                 validator: TokenValidator, audit: AuditLog, clock, top_k: int = 12,
                 purview: Optional[PurviewClient] = None):
        self.contract, self.directory, self.index = contract, directory, index
        self.validator, self.audit, self.clock = validator, audit, clock
        self.pdp = PolicyDecisionPoint(contract, directory, clock)
        self.purview = purview or AllowAllPurviewClient()
        self.top_k = top_k

    # ------------------------------------------------------------------ shared gate
    def _authenticate(self, operation: str, authorization: Optional[str], request_id: str):
        try:
            return self.validator.from_authorization_header(authorization), None
        except AuthError as err:
            self.audit.append("auth_failure", arch="B", op=operation, requestId=request_id, reason=err.code,
                              detail=err.description)
            header = f'Bearer error="{err.code}", error_description="{err.description}"'
            if err.status == 403:
                header += f', scope="{k.BROKER_APP_ID_URI}/{k.REQUIRED_SCOPE}"'
            return None, _error(err.status, err.code, err.description, request_id, {"WWW-Authenticate": header})

    def _authorize(self, operation: str, principal, purpose, request_id: str):
        decision = self.pdp.authorize(principal, purpose)
        if not decision.allowed:
            self.audit.append("access_denied", arch="B", op=operation, requestId=request_id, user=principal.oid,
                              reason=decision.code)
            return None, _error(decision.status, decision.code, decision.message, request_id)
        decision = self.pdp.consume_rate(principal)
        if not decision.allowed:
            self.audit.append("rate_limited", arch="B", op=operation, requestId=request_id, user=principal.oid)
            return None, _error(429, decision.code, decision.message, request_id,
                                {"Retry-After": str(decision.retry_after)})
        return principal, None

    @staticmethod
    def _validate_text(payload, field_name: str, max_chars: int, request_id: str):
        if not isinstance(payload, dict):
            return None, _error(400, "invalid_request", "JSON object body required", request_id)
        value = payload.get(field_name)
        if not isinstance(value, str) or not value.strip() or len(value) > max_chars:
            return None, _error(400, "invalid_request", f"'{field_name}' must be 1..{max_chars} characters", request_id)
        return value.strip(), None

    def _candidates(self, principal, query: str, limit: int):
        """Top chunks the caller may see under the exfiltration guard. Returns (admitted, withheld); admitted chunks
        only count towards coverage once the response is actually released (see _release)."""
        admitted, withheld, best = [], 0, None
        for score, chunk in self.index.search(query, top_k=self.top_k, accept=self.pdp.chunk_permitted):
            best = score if best is None else best
            if len(admitted) >= limit or score < k.MIN_RELATIVE_SCORE * best:
                break
            if not self.pdp.exfiltration_admit(principal.oid, chunk, pending=admitted):
                withheld += 1
                continue
            admitted.append(chunk)
        return admitted, withheld

    def _release(self, principal, chunks) -> None:
        for chunk in chunks:
            self.pdp.record_served(principal.oid, chunk)

    def _purview_check(self, operation: str, principal, text: str, activity: str, request_id: str):
        """Microsoft Purview processContent for the prompt (uploadText) or response (downloadText); audited."""
        decision = self.purview.process_content(principal, text, activity)
        self.audit.append("purview_decision", arch="B", op=operation, requestId=request_id, user=principal.oid,
                          activity=activity, action=decision.action, reason=decision.reason,
                          textSha256=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16])
        if decision.blocked:
            return _error(403, "purview_blocked", f"blocked by a Microsoft Purview data security policy ({activity})",
                          request_id)
        return None

    def _render(self, chunk, query: str, max_chars: int) -> tuple:
        red = redact(chunk.text)
        excerpt = self.pdp.cap_excerpt(select_excerpt(red.text, min(max_chars, self.contract.maxExcerptChars), query,
                                                      exclude=(chunk.title,)))
        return truncate(excerpt, max_chars), red.counts

    def _source_view(self, chunk) -> dict:
        title = redact(chunk.title).text
        return {"ref": chunk.ref, "title": title, "sourceTeam": self.contract.sourceTeam, "sensitivity": chunk.label,
                "sensitivityLabelId": chunk.label_id, "accessRequestUrl": access_request_url(chunk.ref)}

    def _audit_query(self, event: str, request_id: str, principal, query: str, chunks, withheld: int,
                     redactions: dict, status: int, query_flags: list) -> None:
        self.audit.append(event, arch="B", requestId=request_id, user=principal.oid, status=status,
                          queryHash=hashlib.sha256(query.encode("utf-8")).hexdigest()[:16], queryLength=len(query),
                          refs=sorted({c.ref for c in chunks}), chunks=[c.chunk_id for c in chunks], withheld=withheld,
                          redactions=redactions, queryInjectionFlags=query_flags,
                          sourceInjectionFlagged=any(c.injection_flags for c in chunks))

    # ------------------------------------------------------------------ operations
    def ask(self, authorization: Optional[str], payload, request_id: Optional[str] = None) -> BrokerResult:
        request_id = request_id or uuid.uuid4().hex
        principal, err = self._authenticate("ask", authorization, request_id)
        if err:
            return err
        question, err = self._validate_text(payload, "question", k.MAX_QUESTION_CHARS, request_id)
        if err:
            return err
        principal, err = self._authorize("ask", principal, payload.get("purpose"), request_id)
        if err:
            return err
        query_flags = detect_injection(question)
        blocked = self._purview_check("ask", principal, question, k.PURVIEW_ACTIVITY_PROMPT, request_id)
        if blocked:
            self._audit_query("ask", request_id, principal, question, [], 0, {}, 403, query_flags)
            return blocked
        chunks, withheld = self._candidates(principal, question, self.pdp.max_citations)
        if not chunks and withheld:
            self.audit.append("exfiltration_guard", arch="B", requestId=request_id, user=principal.oid, withheld=withheld)
            self._audit_query("ask", request_id, principal, question, [], withheld, {}, 403, query_flags)
            return _error(403, "exfiltration_guard", "further excerpts from these documents are withheld for 24 hours "
                          "(coverage threshold reached); request access to the original instead", request_id)
        citations, redactions, lines = [], {}, []
        for number, chunk in enumerate(chunks, start=1):
            excerpt, counts = self._render(chunk, question, self.contract.maxExcerptChars)
            for name, n in counts.items():
                redactions[name] = redactions.get(name, 0) + n
            citation = dict(self._source_view(chunk), excerpt=excerpt)
            if chunk.injection_flags:
                citation["contentWarnings"] = ["prompt_injection_neutralised"]
            citations.append(citation)
            lines.append(f"[{number}] {excerpt}")
        if withheld:
            self.audit.append("exfiltration_guard", arch="B", requestId=request_id, user=principal.oid, withheld=withheld)
        answer = ("Derived answer from shared Process Engineering knowledge (redacted excerpts; originals are not "
                  "shared):\n" + "\n".join(lines)) if citations else "No shareable Process Engineering knowledge matched."
        body = {
            "requestId": request_id, "answer": answer, "citations": citations,
            "policy": {"contractId": self.contract.contractId, "purpose": self.contract.purpose,
                       "maxExcerptChars": self.contract.maxExcerptChars, "maxCitations": self.pdp.max_citations,
                       "withheld": [{"reason": "exfiltration_guard", "count": withheld}] if withheld else [],
                       "redactions": redactions},
            "notice": "Content is derived and redacted. Use accessRequestUrl to ask the data owner for the original.",
        }
        released_text = answer + "\n" + "\n".join(c["title"] for c in citations)
        blocked = self._purview_check("ask", principal, released_text, k.PURVIEW_ACTIVITY_RESPONSE, request_id)
        if blocked:
            self._audit_query("ask", request_id, principal, question, [], withheld, redactions, 403, query_flags)
            return blocked
        self._release(principal, chunks)
        self._audit_query("ask", request_id, principal, question, chunks, withheld, redactions, 200, query_flags)
        return BrokerResult(200, body)

    def search(self, authorization: Optional[str], payload, request_id: Optional[str] = None) -> BrokerResult:
        request_id = request_id or uuid.uuid4().hex
        principal, err = self._authenticate("search", authorization, request_id)
        if err:
            return err
        query, err = self._validate_text(payload, "query", k.MAX_QUESTION_CHARS, request_id)
        if err:
            return err
        top = payload.get("top", k.MAX_SEARCH_RESULTS)
        if not isinstance(top, int) or isinstance(top, bool) or not 1 <= top <= k.MAX_SEARCH_RESULTS:
            return _error(400, "invalid_request", f"'top' must be 1..{k.MAX_SEARCH_RESULTS}", request_id)
        principal, err = self._authorize("search", principal, payload.get("purpose"), request_id)
        if err:
            return err
        query_flags = detect_injection(query)
        blocked = self._purview_check("search", principal, query, k.PURVIEW_ACTIVITY_PROMPT, request_id)
        if blocked:
            self._audit_query("search", request_id, principal, query, [], 0, {}, 403, query_flags)
            return blocked
        chunks, withheld = self._candidates(principal, query, top)
        citations, redactions = [], {}
        for chunk in chunks:
            snippet, counts = self._render(chunk, query, 160)
            for name, n in counts.items():
                redactions[name] = redactions.get(name, 0) + n
            citations.append(dict(self._source_view(chunk), snippet=snippet))
        if withheld:
            self.audit.append("exfiltration_guard", arch="B", requestId=request_id, user=principal.oid, withheld=withheld)
        released_text = "\n".join(f"{c['title']}: {c['snippet']}" for c in citations)
        blocked = self._purview_check("search", principal, released_text, k.PURVIEW_ACTIVITY_RESPONSE, request_id)
        if blocked:
            self._audit_query("search", request_id, principal, query, [], withheld, redactions, 403, query_flags)
            return blocked
        self._release(principal, chunks)
        self._audit_query("search", request_id, principal, query, chunks, withheld, redactions, 200, query_flags)
        return BrokerResult(200, {"requestId": request_id, "citations": citations,
                                  "policy": {"contractId": self.contract.contractId,
                                             "withheld": [{"reason": "exfiltration_guard", "count": withheld}]
                                             if withheld else []}})

    # ------------------------------------------------------------------ MCP (JSON-RPC 2.0 over HTTP POST /mcp)
    def mcp(self, authorization: Optional[str], rpc) -> BrokerResult:
        from .manifest_builder import build_mcp_tools
        request_id = uuid.uuid4().hex
        if not isinstance(rpc, dict) or rpc.get("jsonrpc") != "2.0" or not isinstance(rpc.get("method"), str):
            return BrokerResult(400, {"jsonrpc": "2.0", "id": None,
                                      "error": {"code": -32600, "message": "invalid JSON-RPC request"}})
        rpc_id, method, params = rpc.get("id"), rpc["method"], rpc.get("params") or {}
        try:
            self.validator.from_authorization_header(authorization)
        except AuthError as err:
            self.audit.append("auth_failure", arch="B", op="mcp", requestId=request_id, reason=err.code)
            return BrokerResult(err.status, {"jsonrpc": "2.0", "id": rpc_id,
                                             "error": {"code": -32001, "message": err.description}},
                                {"WWW-Authenticate": f'Bearer error="{err.code}"'})
        if method.startswith("notifications/"):
            return BrokerResult(202, None)
        if method == "initialize":
            result = {"protocolVersion": k.MCP_PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": k.MCP_SERVER_NAME, "version": "0.1.0"}}
        elif method == "tools/list":
            result = build_mcp_tools()
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            if name == k.FUNCTION_ASK:
                inner = self.ask(authorization, args, request_id)
            elif name == k.FUNCTION_SEARCH:
                inner = self.search(authorization, args, request_id)
            else:
                return BrokerResult(200, {"jsonrpc": "2.0", "id": rpc_id,
                                          "error": {"code": -32602, "message": f"unknown tool {name!r}"}})
            result = {"content": [{"type": "text", "text": json.dumps(inner.body, ensure_ascii=False)}],
                      "structuredContent": inner.body, "isError": inner.status != 200}
        else:
            return BrokerResult(200, {"jsonrpc": "2.0", "id": rpc_id,
                                      "error": {"code": -32601, "message": f"method not found: {method}"}})
        return BrokerResult(200, {"jsonrpc": "2.0", "id": rpc_id, "result": result})


# ---------------------------------------------------------------------- wiring helpers (tests, benchmark, artifacts)
TEST_SIGNING_KEY = b"prototype-hs256-signing-key-not-a-secret"


def build_default_broker(env, *, clock, audit: Optional[AuditLog] = None, ingest_policy: str = "contract",
                         documents=None, contract: Optional[SharingContract] = None,
                         purview: Optional[PurviewClient] = None) -> KnowledgeBroker:
    """ingest_policy='contract' indexes only contract-compliant sources (default, data minimisation);
    'all' models an over-broad private index to prove the PDP still filters at query time."""
    from common.policy import PolicyGate
    from common.sources import all_source_documents, collect_chat_documents, collect_file_documents
    from .auth import TokenValidator
    from .index import build_index
    if documents is None:
        if ingest_policy == "all":
            documents = all_source_documents(env)
        else:
            gate = PolicyGate(env.contract)
            files, _ = collect_file_documents(env.drive, gate)
            chats, _ = collect_chat_documents(env.chat_export, env.chat_drive, gate)
            documents = files + chats
    validator = TokenValidator(key=TEST_SIGNING_KEY, audience=k.BROKER_CLIENT_ID, tenant_id=env.directory.tenant_id,
                               clock=clock)
    return KnowledgeBroker(contract=contract or env.contract, directory=env.directory, index=build_index(documents),
                           validator=validator, audit=audit or AuditLog(clock=clock), clock=clock, purview=purview)


def issue_token(env, user_key: str, clock, *, key: bytes = TEST_SIGNING_KEY, headers=None, **overrides) -> str:
    """Mints a test access token shaped like an Entra v2 delegated token for a directory user."""
    from .auth import mint_hs256, user_token_claims
    claims = user_token_claims(env.directory.user(user_key), tenant_id=env.directory.tenant_id,
                               audience=k.BROKER_CLIENT_ID, now=clock.now().timestamp(), **overrides)
    for name, value in list(claims.items()):
        if value is None:
            del claims[name]
    return mint_hs256(claims, key, headers)
