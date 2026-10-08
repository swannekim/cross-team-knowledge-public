"""Governed publishing workflow for knowledge cards.

States: REQUESTED -> OWNER_APPROVED -> (COMPLIANCE_APPROVED, Confidential only) -> PUBLISHED
        PUBLISHED -> STALE (source changed) -> OWNER_APPROVED ... -> REPUBLISHED
        any open state -> REJECTED / REVOKED; published -> EXPIRED (TTL) / REVOKED
Rules: Highly Confidential (above the contract ceiling) and out-of-scope sources are auto-rejected;
only audience members (non-guests) can request; the data owner approves; a different person in the
compliance role approves Confidential content; approvals are bound to the exact source version
(fingerprint); duplicate open requests are idempotent. Every transition is audited.

Production: Power Automate approvals or Teams adaptive-card approvals, with the state persisted in a
SharePoint list / Dataverse table and the publisher running under a managed identity.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from common.audit import AuditLog
from common.clock import parse_iso, to_iso
from common.contract import SharingContract
from common.directory import Directory
from common.drive_sim import ItemNotFound, SimulatedDrive
from common.fingerprint import stable_hash
from common.policy import PolicyGate
from common.refs import TEST_REF_KEY
from common.sources import document_from_drive_item

from .derivative import build_knowledge_card
from .publisher import Publisher

REQUESTED, OWNER_APPROVED, COMPLIANCE_APPROVED = "REQUESTED", "OWNER_APPROVED", "COMPLIANCE_APPROVED"
PUBLISHED, STALE, REPUBLISHED = "PUBLISHED", "STALE", "REPUBLISHED"
EXPIRED, REVOKED, REJECTED = "EXPIRED", "REVOKED", "REJECTED"
TERMINAL = frozenset({EXPIRED, REVOKED, REJECTED})
LIVE = frozenset({PUBLISHED, REPUBLISHED})
TRANSITIONS = {
    REQUESTED: {OWNER_APPROVED, REJECTED, REVOKED},
    OWNER_APPROVED: {COMPLIANCE_APPROVED, PUBLISHED, REPUBLISHED, STALE, REJECTED, REVOKED},
    COMPLIANCE_APPROVED: {PUBLISHED, REPUBLISHED, STALE, REJECTED, REVOKED},
    PUBLISHED: {STALE, EXPIRED, REVOKED},
    REPUBLISHED: {STALE, EXPIRED, REVOKED},
    STALE: {OWNER_APPROVED, REJECTED, REVOKED},
    REJECTED: set(), EXPIRED: set(), REVOKED: set(),
}
COMPLIANCE_LABELS = ("Confidential",)


class WorkflowError(RuntimeError):
    pass


@dataclass
class KnowledgeRequest:
    request_id: str
    source_id: str
    purpose: str
    requested_by: list
    state: str
    label: Optional[str]
    created_at: str
    need: str = ""
    cycle: int = 1
    reason: Optional[str] = None
    owner_approval: Optional[dict] = None
    compliance_approval: Optional[dict] = None
    card: Optional[dict] = None
    history: list = field(default_factory=list)


class PublishingWorkflow:
    def __init__(self, *, contract: SharingContract, directory: Directory, drive: SimulatedDrive, publisher: Publisher,
                 audit: AuditLog, clock, ref_key: bytes = TEST_REF_KEY, state_path=None):
        self.contract, self.directory, self.drive = contract, directory, drive
        self.publisher, self.audit, self.clock, self.ref_key = publisher, audit, clock, ref_key
        self.state_path = Path(state_path) if state_path else None
        self.requests: dict = {}
        self.notifications: list = []
        if self.state_path and self.state_path.exists():
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.requests = {rid: KnowledgeRequest(**data) for rid, data in raw["requests"].items()}

    # ------------------------------------------------------------------ helpers
    def _save(self) -> None:
        if not self.state_path:
            return
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"requests": {rid: asdict(r) for rid, r in self.requests.items()}}, indent=2,
                                  sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _transition(self, req: KnowledgeRequest, to: str, actor: str, note: str = "") -> None:
        if to not in TRANSITIONS[req.state]:
            raise WorkflowError(f"{req.request_id}: transition {req.state} -> {to} not allowed")
        req.history.append({"at": to_iso(self.clock.now()), "from": req.state, "to": to, "actor": actor, "note": note})
        self.audit.append("workflow_transition", arch="C", requestId=req.request_id, sourceId=req.source_id,
                          fromState=req.state, toState=to, actor=actor, note=note, cycle=req.cycle)
        req.state = to
        self._save()

    def _notify(self, to: str, subject: str, req: KnowledgeRequest) -> None:
        self.notifications.append({"to": to, "subject": subject, "requestId": req.request_id,
                                   "at": to_iso(self.clock.now())})

    def get(self, request_id: str) -> KnowledgeRequest:
        if request_id not in self.requests:
            raise WorkflowError(f"unknown request {request_id}")
        return self.requests[request_id]

    def _current(self, source_id: str):
        """(document, gate decision) for the current source version; (None, None) when the source is gone."""
        try:
            item = self.drive.get_item(source_id)
        except ItemNotFound:
            return None, None
        label = self.drive.label_of(source_id)
        decision = PolicyGate(self.contract).check_file(path=SimulatedDrive.item_path(item), label=label,
                                                        name=item["name"])
        return item, decision

    def _require_role(self, actor: str, role: str):
        user = self.directory.find_user(actor)
        if user is None or role not in user.roles or user.is_guest:
            raise WorkflowError(f"{actor} lacks the '{role}' role")
        return user

    # ------------------------------------------------------------------ operations
    def request(self, requester: str, source_id: str, purpose: str, need: str = "") -> KnowledgeRequest:
        user = self.directory.find_user(requester)
        if user is None:
            raise WorkflowError(f"unknown requester {requester}")
        for req in self.requests.values():
            if req.source_id == source_id and req.purpose == purpose and req.state not in TERMINAL:
                if user.upn not in req.requested_by:
                    req.requested_by.append(user.upn)
                    self._save()
                return req
        request_id = "KR-" + stable_hash(source_id, purpose, len(self.requests), length=10).upper()
        item, decision = self._current(source_id)
        req = KnowledgeRequest(request_id=request_id, source_id=source_id, purpose=purpose, requested_by=[user.upn],
                               state=REQUESTED, label=self.drive.label_of(source_id) if item else None,
                               created_at=to_iso(self.clock.now()), need=need)
        self.requests[request_id] = req
        self.audit.append("knowledge_requested", arch="C", requestId=request_id, sourceId=source_id, requester=user.id,
                          purpose=purpose)
        if not any(gid in user.groups for gid in self.contract.audienceGroupIds):
            reason = "requester_not_in_audience"
        elif user.is_guest and self.contract.excludeGuests:
            reason = "guest_excluded"
        elif purpose != self.contract.purpose:
            reason = "purpose_mismatch"
        elif not self.contract.is_active(self.clock.now()):
            reason = "contract_inactive"
        elif item is None:
            reason = "source_not_found"
        elif not decision.allowed:
            reason = f"auto_rejected:{decision.reason}"
        else:
            reason = None
        if reason:
            req.reason = reason
            self._transition(req, REJECTED, "policy-engine", reason)
        else:
            self._notify("data-owner", f"Knowledge request {request_id} awaits approval", req)
            self._save()
        return req

    def approve_owner(self, approver: str, request_id: str, note: str = "") -> KnowledgeRequest:
        req = self.get(request_id)
        user = self._require_role(approver, "dataOwner")
        if user.upn in req.requested_by:
            raise WorkflowError("requester cannot approve their own request")
        if req.state not in (REQUESTED, STALE):
            raise WorkflowError(f"{request_id}: owner approval not possible in state {req.state}")
        item, decision = self._current(req.source_id)
        if item is None or not decision.allowed:
            req.reason = "source_not_found" if item is None else f"auto_rejected:{decision.reason}"
            self._transition(req, REJECTED, "policy-engine", req.reason)
            return req
        doc = document_from_drive_item(self.drive, item)
        req.label = doc.label
        req.owner_approval = {"approvalId": "APR-" + stable_hash(request_id, req.cycle, user.id, "owner", length=12).upper(),
                              "by": user.upn, "at": to_iso(self.clock.now()), "fingerprint": doc.fingerprint}
        req.compliance_approval = None
        self._transition(req, OWNER_APPROVED, user.upn, note)
        if req.label in COMPLIANCE_LABELS:
            self._notify("compliance", f"Knowledge request {request_id} needs compliance approval", req)
        return req

    def approve_compliance(self, approver: str, request_id: str, note: str = "") -> KnowledgeRequest:
        req = self.get(request_id)
        user = self._require_role(approver, "complianceOfficer")
        if req.state != OWNER_APPROVED:
            raise WorkflowError(f"{request_id}: compliance approval requires OWNER_APPROVED (is {req.state})")
        if req.label not in COMPLIANCE_LABELS:
            raise WorkflowError(f"{request_id}: compliance approval not applicable to label {req.label}")
        if req.owner_approval and req.owner_approval["by"] == user.upn:
            raise WorkflowError("owner and compliance approvals must come from different people")
        req.compliance_approval = {"approvalId": "CMP-" + stable_hash(request_id, req.cycle, user.id, length=12).upper(),
                                   "by": user.upn, "at": to_iso(self.clock.now())}
        self._transition(req, COMPLIANCE_APPROVED, user.upn, note)
        return req

    def reject(self, actor: str, request_id: str, reason: str) -> KnowledgeRequest:
        req = self.get(request_id)
        user = self.directory.find_user(actor)
        if user is None or not ({"dataOwner", "complianceOfficer"} & set(user.roles)):
            raise WorkflowError(f"{actor} cannot reject requests")
        req.reason = reason
        self._transition(req, REJECTED, user.upn, reason)
        return req

    def publish(self, request_id: str) -> KnowledgeRequest:
        req = self.get(request_id)
        if req.state not in (OWNER_APPROVED, COMPLIANCE_APPROVED):
            raise WorkflowError(f"{request_id}: cannot publish from {req.state} (owner approval required)")
        if req.label in COMPLIANCE_LABELS and req.state != COMPLIANCE_APPROVED:
            raise WorkflowError(f"{request_id}: {req.label} content requires compliance approval before publishing")
        if not self.contract.is_active(self.clock.now()):
            raise WorkflowError("sharing contract is not active")
        item, decision = self._current(req.source_id)
        if item is None or not decision.allowed:
            req.reason = "source_not_found" if item is None else f"auto_rejected:{decision.reason}"
            self._transition(req, REJECTED, "policy-engine", req.reason)
            raise WorkflowError(f"{request_id}: source no longer eligible ({req.reason})")
        doc = document_from_drive_item(self.drive, item)
        if doc.fingerprint != req.owner_approval["fingerprint"]:
            req.cycle += 1
            self._transition(req, STALE, "policy-engine", "source changed after approval")
            self._notify("data-owner", f"Knowledge request {request_id} needs re-approval (source changed)", req)
            raise WorkflowError(f"{request_id}: source changed since approval; re-approval required")
        card = build_knowledge_card(doc, self.contract, approval_id=req.owner_approval["approvalId"],
                                    compliance_approval_id=(req.compliance_approval or {}).get("approvalId"),
                                    generated_at=self.clock.now(), card_version=req.cycle, ref_key=self.ref_key)
        record = self.publisher.publish(card)
        req.card = {"itemId": record["itemId"], "fileName": record["fileName"], "webUrl": record["webUrl"],
                    "fingerprint": doc.fingerprint, "approvalId": req.owner_approval["approvalId"],
                    "generatedAt": card.metadata["generatedAt"], "expiresAt": card.metadata["expiresAt"],
                    "fields": record["fields"]}
        self.audit.append("card_published", arch="C", requestId=request_id, itemId=record["itemId"],
                          sourceRef=card.metadata["sourceRef"], fingerprint=doc.fingerprint,
                          approvalId=req.owner_approval["approvalId"], expiresAt=card.metadata["expiresAt"])
        self._transition(req, REPUBLISHED if req.cycle > 1 else PUBLISHED, "publisher", record["fileName"])
        return req

    def _unpublish(self, req: KnowledgeRequest, reason: str) -> None:
        if req.card:
            self.publisher.unpublish(req.card["itemId"], reason)
            self.audit.append("card_unpublished", arch="C", requestId=req.request_id, itemId=req.card["itemId"],
                              reason=reason)

    def detect_stale(self) -> list:
        """Compares live cards with the current source; changed -> STALE (card withdrawn until re-approved)."""
        changed = []
        for req in self.requests.values():
            if req.state not in LIVE:
                continue
            item, decision = self._current(req.source_id)
            if item is None or not decision.allowed:
                reason = "source_deleted" if item is None else f"policy:{decision.reason}"
                self._unpublish(req, reason)
                req.reason = reason
                self._transition(req, REVOKED, "policy-engine", reason)
                changed.append(req.request_id)
                continue
            doc = document_from_drive_item(self.drive, item)
            if doc.fingerprint != req.card["fingerprint"]:
                self._unpublish(req, "source_changed")
                req.cycle += 1
                self._transition(req, STALE, "policy-engine", "source changed")
                self._notify("data-owner", f"Knowledge card for {req.request_id} is stale; re-approve to republish", req)
                changed.append(req.request_id)
        return changed

    def sweep_expired(self) -> list:
        expired = []
        now = self.clock.now()
        for req in self.requests.values():
            if req.state in LIVE and parse_iso(req.card["expiresAt"]) <= now:
                self._unpublish(req, "ttl_expired")
                self._transition(req, EXPIRED, "policy-engine", "ttl expired")
                expired.append(req.request_id)
        return expired

    def revoke(self, actor: str, request_id: str, reason: str) -> KnowledgeRequest:
        req = self.get(request_id)
        user = self.directory.find_user(actor)
        if user is None or not ({"dataOwner", "complianceOfficer"} & set(user.roles)):
            raise WorkflowError(f"{actor} cannot revoke knowledge cards")
        self._unpublish(req, f"revoked:{reason}")
        req.reason = reason
        self._transition(req, REVOKED, user.upn, reason)
        return req

    def revoke_contract(self, actor: str, reason: str) -> list:
        self.contract = self.contract.revoked(self.clock.now(), actor)
        revoked = []
        for req in self.requests.values():
            if req.state not in TERMINAL:
                self.revoke(actor, req.request_id, reason)
                revoked.append(req.request_id)
        return revoked


def build_default_workflow(env, *, clock, root_dir=None, audit: Optional[AuditLog] = None, state_path=None):
    """Wires workflow + publisher + simulated Knowledge Exchange site + native index (site on disk if root_dir)."""
    from .native_index_sim import NativeIndexSim
    from .publisher import KnowledgeExchangeSite
    site = KnowledgeExchangeSite(Path(root_dir) / "KnowledgeExchange" if root_dir else None,
                                 member_group_ids=env.contract.audienceGroupIds,
                                 allow_guests=not env.contract.excludeGuests)
    publisher = Publisher(site, clock)
    workflow = PublishingWorkflow(contract=env.contract, directory=env.directory, drive=env.drive, publisher=publisher,
                                  audit=audit or AuditLog(clock=clock), clock=clock, state_path=state_path)
    return workflow, site, NativeIndexSim(site, env.directory)
