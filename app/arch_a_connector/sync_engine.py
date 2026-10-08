"""Architecture A sync engine: Team A library (+ Teams chat) -> policy gate -> derived items -> Graph connector.

Pipeline per source document:
  extract -> neutralise prompt injection -> redact -> chunk (<= chunk_max_chars, a retrieval-quality choice far
  below the 30 MB per-item limit) / summarise -> externalItems whose ACL is
  the contract audience (never the source ACL) and whose url is the broker access-request page.

Incremental sync uses the drive delta API (deltaLink, deleted facet, 410 resync); publishing is
idempotent (skip when eTag/fingerprint and the policy context are unchanged); deletes, moves out of
scope, relabels above the ceiling, TTL expiry and contract revocation all remove derived items.
Every publish and delete is written to the hash-chained audit log.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Optional

from common.audit import AuditLog
from common.chunker import chunk_text
from common.clock import parse_iso, to_iso
from common.contract import SharingContract
from common.directory import Directory
from common.drive_sim import ResyncRequired, SimulatedDrive, crawl
from common.fingerprint import canonical_json, sha256_hex, stable_hash
from common.injection import neutralise
from common.policy import PolicyGate
from common.redaction import redact
from common.refs import TEST_REF_KEY, opaque_ref
from common.sources import SourceDocument, collect_chat_documents, document_from_drive_item
from common.summarizer import key_facts, summarize

from . import validators
from .graph_client import ConnectorClient
from .graph_models import acl_entry, build_external_item, build_schema, item_url

TRANSFORM_VERSION = "a-1.1"
# The transform is deterministic in (source fingerprint, policy context, validity, settings), so its
# output is memoised per process; repeated syncs in one process do not redo the redaction work.
_TRANSFORM_CACHE: dict = {}


class ContractInactiveError(RuntimeError):
    pass


@dataclass
class SyncStats:
    mode: str
    puts: int = 0
    deletes: int = 0
    skipped: int = 0
    refreshed: int = 0
    expired: int = 0
    published_docs: int = 0
    excluded: dict = field(default_factory=Counter)
    contract_active: bool = True

    @property
    def writes(self) -> int:
        return self.puts + self.deletes

    def as_dict(self) -> dict:
        out = asdict(self)
        out["excluded"] = dict(self.excluded)
        out["writes"] = self.writes
        return out


class StateStore:
    """JSON state (deltaLink + per-source publication records), written atomically."""

    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.data = {"version": 1, "deltaLink": None, "docs": {}}
        if self.path and self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))

    @property
    def docs(self) -> dict:
        return self.data["docs"]

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)


def connection_id_for(contract: SharingContract) -> str:
    return ("ContosoPEDerived" + "".join(ch for ch in contract.contractId if ch.isalnum()))[:32]


class SyncEngine:
    def __init__(self, *, contract: SharingContract, directory: Directory, drive: SimulatedDrive,
                 client: ConnectorClient, audit: AuditLog, clock, state: Optional[StateStore] = None,
                 chat_export: Optional[dict] = None, chat_drive: Optional[SimulatedDrive] = None,
                 ref_key: bytes = TEST_REF_KEY, chunk_max_chars: int = 8000, refresh_before_days: float = 5,
                 tenant_id: Optional[str] = None):
        self.contract, self.directory, self.drive = contract, directory, drive
        self.client, self.audit, self.clock = client, audit, clock
        self.state = state or StateStore()
        self.chat_export, self.chat_drive = chat_export, chat_drive
        self.ref_key, self.chunk_max_chars = ref_key, chunk_max_chars
        self.refresh_before = timedelta(days=refresh_before_days)
        # Value of everyone / everyoneExceptGuests ACEs: the tenant ID (validators.CONFIG["EVERYONE_ACE_VALUE"]).
        self.tenant_id = tenant_id or directory.tenant_id
        self._ready = False

    @property
    def gate(self) -> PolicyGate:
        return PolicyGate(self.contract)

    # ------------------------------------------------------------------ connection bootstrap
    def bootstrap(self) -> None:
        c = self.contract
        self.client.create_connection(
            name=f"{c.sourceTeam} knowledge ({c.contractId})",
            description=(f"Derived, redacted summaries and extracts of {c.sourceTeam} documents shared under "
                         f"sharing contract {c.contractId} for {c.purpose}. Originals are not shared; each "
                         "result links to an access-request page."))
        self.client.register_schema(build_schema())
        self.state.data["connectionId"] = self.client.connection_id
        self.audit.append("connection_ready", arch="A", connectionId=self.client.connection_id, contractId=c.contractId)
        self._ready = True

    # ------------------------------------------------------------------ policy context
    def acl(self) -> list:
        """Item ACL derived from the contract audience - never from the source permissions.

        Guests are excluded with explicit per-user deny ACEs for every guest member of the audience
        groups (deny wins over the group grant). The deny list is part of the publish context, so a
        membership change triggers re-publication of all items on the next sync.
        """
        c = self.contract
        if c.allowTenantWideAudience:
            entries = [acl_entry("everyoneExceptGuests" if c.excludeGuests else "everyone", self.tenant_id)]
        else:
            entries = [acl_entry("group", gid) for gid in c.audienceGroupIds]
        if c.excludeGuests:
            guests = sorted({u.id for gid in c.audienceGroupIds for u in self.directory.members_of(gid) if u.is_guest})
            entries.extend(acl_entry("user", oid, "deny") for oid in guests)
        return entries

    def _context(self) -> str:
        return sha256_hex(canonical_json([TRANSFORM_VERSION, self.contract.fingerprint(), self.acl(),
                                          self.chunk_max_chars, self.contract.display_name]))[:16]

    def _due_for_refresh(self, entry: dict, now) -> bool:
        return parse_iso(entry["validUntil"]) - now <= self.refresh_before

    # ------------------------------------------------------------------ public runs
    def run_full(self) -> SyncStats:
        stats, now = SyncStats("full"), self.clock.now()
        if not self._ensure_active(stats, now):
            return stats
        if not self._ready:
            self.bootstrap()
        self._sweep(stats, now)
        items, delta_link = crawl(self.drive)
        seen = set()
        for item in items:
            if "file" in item:
                seen.add(item["id"])
                self._process_file(item, stats, now)
        for source_id, entry in list(self.state.docs.items()):
            if entry["kind"] == "file" and source_id not in seen:
                self._delete_doc(source_id, stats, "deleted_at_source")
        self._process_chats(stats, now)
        self.state.data["deltaLink"] = delta_link
        self.state.data["lastSync"] = to_iso(now)
        self.state.save()
        self.audit.append("sync_completed", arch="A", **{k: v for k, v in stats.as_dict().items() if k != "excluded"})
        return stats

    def run_incremental(self) -> SyncStats:
        if not self.state.data.get("deltaLink"):
            return self.run_full()
        stats, now = SyncStats("incremental"), self.clock.now()
        if not self._ensure_active(stats, now):
            return stats
        if not self._ready:
            self.bootstrap()
        self._sweep(stats, now)
        try:
            page = self.drive.delta(self.state.data["deltaLink"])
        except ResyncRequired:
            self.audit.append("delta_resync_required", arch="A")
            self.state.data["deltaLink"] = None
            full = self.run_full()
            full.mode = "full-resync"
            return full
        while True:
            for change in page["value"]:
                if "deleted" in change:
                    self._delete_doc(change["id"], stats, "deleted_at_source")
                elif "file" in change:
                    self._process_file(change, stats, now)
            if "@odata.nextLink" in page:
                page = self.drive.delta(page["@odata.nextLink"])
                continue
            delta_link = page["@odata.deltaLink"]
            break
        self._refresh_due(stats, now)
        self._process_chats(stats, now)
        self.state.data["deltaLink"] = delta_link
        self.state.data["lastSync"] = to_iso(now)
        self.state.save()
        self.audit.append("sync_completed", arch="A", **{k: v for k, v in stats.as_dict().items() if k != "excluded"})
        return stats

    def sweep_expired(self) -> SyncStats:
        stats = SyncStats("sweep")
        self._sweep(stats, self.clock.now())
        self.state.save()
        return stats

    def revoke(self, actor: str, reason: str = "contract revoked") -> SyncStats:
        now = self.clock.now()
        self.contract = self.contract.revoked(now, actor)
        self.audit.append("contract_revoked", arch="A", contractId=self.contract.contractId, actor=actor, reason=reason)
        stats = SyncStats("revoke", contract_active=False)
        self._purge_all(stats, "contract_revoked")
        self.state.data["deltaLink"] = None
        self.state.save()
        return stats

    # ------------------------------------------------------------------ internals
    def _ensure_active(self, stats: SyncStats, now) -> bool:
        if self.contract.is_active(now):
            return True
        stats.contract_active = False
        self._purge_all(stats, "contract_inactive")
        self.state.save()
        return False

    def _purge_all(self, stats: SyncStats, reason: str) -> None:
        for source_id in list(self.state.docs):
            self._delete_doc(source_id, stats, reason)

    def _sweep(self, stats: SyncStats, now) -> None:
        for source_id, entry in list(self.state.docs.items()):
            if parse_iso(entry["validUntil"]) <= now:
                self._delete_doc(source_id, stats, "ttl_expired")
                stats.expired += 1

    def _refresh_due(self, stats: SyncStats, now) -> None:
        """Re-publishes unchanged files that are close to expiry or whose policy context changed
        (contract edit, audience/guest membership change, transform version)."""
        context = self._context()
        for source_id, entry in list(self.state.docs.items()):
            if entry["kind"] != "file":
                continue
            if not self._due_for_refresh(entry, now) and entry["context"] == context:
                continue
            try:
                item = self.drive.get_item(source_id)
            except KeyError:
                self._delete_doc(source_id, stats, "deleted_at_source")
                continue
            if self._process_file(item, stats, now, force=True):
                stats.refreshed += 1

    def _process_file(self, item: dict, stats: SyncStats, now, force: bool = False) -> bool:
        label = self.drive.label_of(item["id"])
        decision = self.gate.check_file(path=SimulatedDrive.item_path(item), label=label, name=item["name"])
        if not decision.allowed:
            stats.excluded[decision.reason] += 1
            self._delete_doc(item["id"], stats, f"policy:{decision.reason}")
            return False
        entry, context = self.state.docs.get(item["id"]), self._context()
        unchanged = entry is not None and entry["context"] == context and not self._due_for_refresh(entry, now)
        if not force and unchanged and entry["etag"] == item["eTag"]:
            stats.skipped += 1
            return False
        doc = document_from_drive_item(self.drive, item)
        if not force and unchanged and entry["sourceFingerprint"] == doc.fingerprint:
            stats.skipped += 1
            return False
        self._publish(doc, stats, now, "refresh" if force else ("update" if entry else "new"))
        return True

    def _process_chats(self, stats: SyncStats, now) -> None:
        if self.chat_export is None:
            return
        docs, exclusions = collect_chat_documents(self.chat_export, self.chat_drive, self.gate)
        for source_id, reason in exclusions:
            stats.excluded[reason] += 1
            if source_id.startswith("chat:"):
                chat_id = source_id[len("chat:"):]
                for sid, entry in list(self.state.docs.items()):
                    if entry.get("chatId") == chat_id:
                        self._delete_doc(sid, stats, f"policy:{reason}")
            else:
                self._delete_doc(source_id, stats, f"policy:{reason}")
        context = self._context()
        for doc in docs:
            entry = self.state.docs.get(doc.source_id)
            if (entry and entry["context"] == context and entry["sourceFingerprint"] == doc.fingerprint
                    and not self._due_for_refresh(entry, now)):
                stats.skipped += 1
                continue
            self._publish(doc, stats, now, "update" if entry else "new")
        current = {d.source_id for d in docs}
        chat_id = self.chat_export["chat"]["id"]
        for sid, entry in list(self.state.docs.items()):
            if entry.get("chatId") == chat_id and sid not in current:
                self._delete_doc(sid, stats, "deleted_at_source")

    def _item_id(self, doc: SourceDocument, kind: str, n: int) -> str:
        return f"kx-{stable_hash(self.contract.contractId, doc.source_id, length=20)}-{kind}{n:04d}"

    def build_items(self, doc: SourceDocument, now) -> tuple:
        """Pure transform: SourceDocument -> list of (itemId, externalItem) plus transform metadata."""
        c = self.contract
        valid_until = to_iso(c.derivative_valid_until(now))
        key = (doc.source_id, doc.kind, doc.fingerprint, doc.title, doc.label, doc.last_modified, self._context(),
               valid_until, self.chunk_max_chars, self.ref_key, c.contractId)
        cached = _TRANSFORM_CACHE.get(key)
        if cached is None:
            if len(_TRANSFORM_CACHE) > 256:
                _TRANSFORM_CACHE.clear()
            items, meta = self._transform(doc, valid_until)
            cached = _TRANSFORM_CACHE[key] = (json.dumps(items), meta)
        return [tuple(pair) for pair in json.loads(cached[0])], dict(cached[1])

    def _transform(self, doc: SourceDocument, valid_until: str) -> tuple:
        c = self.contract
        ref = opaque_ref(doc.source_id, self.ref_key)
        neutral = neutralise(doc.text)
        red = redact(neutral.text)
        title = redact(neutralise(doc.title).text).text.strip() or "Untitled"
        acl = self.acl()
        base = {
            "url": item_url(ref),
            "containerName": c.display_name,
            "lastModifiedDateTime": to_iso(parse_iso(doc.last_modified)),
            "sourceTeam": c.sourceTeam,
            "sensitivity": doc.label,
            "contractId": c.contractId,
            "validUntil": valid_until,
            "sourceFingerprint": doc.fingerprint,
        }
        items = []
        if "summary" in c.derivativeTypes:
            facts = key_facts(red.text, exclude=(title,))
            content = (f"{title}\nDerived summary - original not shared (source team: {c.sourceTeam}).\n\n"
                       f"{summarize(red.text, max_sentences=4, max_chars=900, exclude=(title,))}")
            if facts:
                content += "\n\nKey facts:\n" + "\n".join(f"- {fact}" for fact in facts)
            items.append((self._item_id(doc, "sum", 0),
                          build_external_item(acl, dict(base, title=f"{title} (summary)", derivativeType="summary"),
                                              content)))
        if "redactedExtract" in c.derivativeTypes:
            chunks = chunk_text(red.text, self.chunk_max_chars)
            for n, chunk in enumerate(chunks):
                props = dict(base, title=f"{title} (extract {n + 1}/{len(chunks)})", derivativeType="redactedExtract")
                items.append((self._item_id(doc, "x", n), build_external_item(acl, props, chunk)))
        limit = validators.cfg("MAX_ITEM_SIZE_BYTES")
        for item_id, payload in items:
            if not validators.item_within_size_limit(payload):
                raise validators.ValidationError([f"{item_id}: derived item exceeds {limit} bytes; lower chunk_max_chars"])
        meta = {"ref": ref, "validUntil": valid_until, "title": title, "injectionFlags": len(neutral.flags),
                "redactions": dict(red.counts)}
        return items, meta

    def _publish(self, doc: SourceDocument, stats: SyncStats, now, reason: str) -> None:
        items, meta = self.build_items(doc, now)
        entry = self.state.docs.get(doc.source_id)
        new_ids = []
        for item_id, payload in items:
            self.client.put_item(item_id, payload)
            stats.puts += 1
            new_ids.append(item_id)
            self.audit.append("publish", arch="A", itemId=item_id, sourceId=doc.source_id, ref=meta["ref"],
                              derivativeType=payload["properties"]["derivativeType"],
                              sourceFingerprint=doc.fingerprint, contractId=self.contract.contractId,
                              validUntil=meta["validUntil"], reason=reason)
        for stale_id in sorted(set(entry["itemIds"] if entry else []) - set(new_ids)):
            self.client.delete_item(stale_id)
            stats.deletes += 1
            self.audit.append("delete", arch="A", itemId=stale_id, sourceId=doc.source_id, reason="superseded")
        if meta["injectionFlags"]:
            self.audit.append("injection_neutralised", arch="A", sourceId=doc.source_id, ref=meta["ref"],
                              count=meta["injectionFlags"])
        self.state.docs[doc.source_id] = {
            "kind": doc.kind, "itemIds": new_ids, "sourceFingerprint": doc.fingerprint, "etag": doc.etag,
            "label": doc.label, "context": self._context(), "validUntil": meta["validUntil"],
            "publishedAt": to_iso(now), "ref": meta["ref"], "chatId": doc.chat_id, "title": meta["title"],
            "injectionFlags": meta["injectionFlags"], "redactions": meta["redactions"],
        }
        stats.published_docs += 1

    def _delete_doc(self, source_id: str, stats: SyncStats, reason: str) -> None:
        entry = self.state.docs.pop(source_id, None)
        if not entry:
            return
        for item_id in entry["itemIds"]:
            self.client.delete_item(item_id)
            stats.deletes += 1
            self.audit.append("delete", arch="A", itemId=item_id, sourceId=source_id, ref=entry["ref"], reason=reason)


def build_default_engine(env, *, clock, audit: Optional[AuditLog] = None, transport=None, state_path=None,
                         **kwargs):
    """Wires an engine against the in-memory Graph emulator (used by tests, benchmark and artifact generation)."""
    from .graph_client import MockGraphTransport
    transport = transport or MockGraphTransport(env.directory)
    client = ConnectorClient(transport, connection_id_for(env.contract), sleep=lambda _s: None)
    engine = SyncEngine(contract=env.contract, directory=env.directory, drive=env.drive, client=client,
                        audit=audit or AuditLog(clock=clock), clock=clock, state=StateStore(state_path),
                        chat_export=env.chat_export, chat_drive=env.chat_drive, **kwargs)
    return engine, transport
