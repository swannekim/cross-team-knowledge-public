"""Explicit prepare/approve/apply. Local ledger is a recovery journal, not a cloud policy engine."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit

from arch_a_connector.graph_client import ConnectorClient
from arch_a_connector.graph_models import build_schema
from arch_a_connector.sync_engine import SyncEngine
from common.clock import parse_iso, to_iso
from common.contract import SharingContract, load_contract

from .graph import check_response
from .sources import AUTHORITY, SourceReader, segment

CONNECTION_ID = "ExampleDerived"
CONTRACT_ID = "KX-Synthetic-20261007"
APPROVAL_KIND = "synthetic-demo-operator-approval"
MARKER = ("SYNTHETIC DEMO — fictional fixture; deterministic extractive L1 summary.\n"
          "Classification is a fixture-registry assertion, NOT a Purview label. "
          "No human owner/compliance signature or retention enforcement is asserted.\n")


class PreparationError(ValueError):
    """Fixed preparation diagnostics that contain no input values or Graph error bodies."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def atomic_bytes(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(path.name + ".new")
    with staging.open("wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(staging, path)


def atomic_json(path, value):
    atomic_bytes(path, json_bytes(value))


@contextmanager
def ledger_lock(path):
    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def binding(state):
    return {
        "tenantId": state["tenantId"], "pipelineClientId": state["apps"]["Pipeline"]["clientId"],
        "sourceSiteId": state["sites"]["Source"]["id"], "sourceDriveId": state["sites"]["Source"]["driveId"],
        "exchangeSiteId": state["sites"]["Exchange"]["id"],
        "exchangeDriveId": state["sites"]["Exchange"]["driveId"],
        "exchangeListId": state["sites"]["Exchange"]["listId"],
        "readersGroupId": state["groups"]["Readers"]["id"], "connectionId": CONNECTION_ID,
    }


def citation_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path.rstrip("/") != "/access-request"
            or parsed.hostname == "contoso.com" or parsed.hostname.endswith(".contoso.com")):
        raise PreparationError("Citation base must be the deployed HTTPS /access-request URL, without query")
    return value.rstrip("/")


def contract_for(state, now, ttl_days=1):
    if not 1 <= ttl_days <= 30:
        raise PreparationError("Demo TTL must be 1..30 days")
    # This internal transform configuration deliberately does not impersonate a data owner's approval.
    return SharingContract(
        contractId=CONTRACT_ID, sourceSite=state["sites"]["Source"]["url"],
        includePaths=("/Shareable",), excludePaths=("/Shareable/Drafts",), maxLabel="Confidential",
        audienceGroupIds=(state["groups"]["Readers"]["id"],), excludeGuests=True,
        purpose="synthetic-knowledge-demo", derivativeTypes=("summary",), ttlDays=ttl_days,
        approvedBy=APPROVAL_KIND, approvedAt=to_iso(now), maxExcerptChars=300,
        exfiltrationCoverageThreshold=0.4, rateLimitPerMinute=10,
        sourceTeam="Synthetic Process Engineering", audienceTeam="Synthetic Readers",
        displayName="KX synthetic derived knowledge",
    )


class SummaryBuilder(SyncEngine):
    """Reuse the pure transform only. There is no simulated drive or simulated directory."""
    def acl(self):
        return [{"type": "group", "value": self.contract.audienceGroupIds[0], "accessType": "grant"}]


def prepare(graph, state, out, now, *, citation_base, ttl_days=1, ref_key=None,
            broker_contract=None, runtime_out=None):
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise PreparationError("Preparation directory must be empty; do not overwrite reviewed outputs")
    base = citation_url(citation_base)
    contract = contract_for(state, now, ttl_days)
    reader = SourceReader(graph, state, contract)
    docs = reader.snapshot()
    if not docs:
        raise PreparationError("No eligible verified synthetic fixtures")
    broker = load_contract(broker_contract) if broker_contract else None
    if broker and (broker.sourceSite != state["sites"]["Source"]["url"]
                   or broker.audienceGroupIds != (state["groups"]["Readers"]["id"],)
                   or broker.allowTenantWideAudience or not broker.is_active(now)
                   or any(not broker.path_in_scope(d.path)[0] or not broker.label_allowed(d.label) for d in docs)):
        raise PreparationError("Broker contract is not bound to this source and restricted audience")
    # References are frozen in the reviewed payloads, not an authorization credential.
    # A fresh in-memory key avoids the prototype key without persisting any secret.
    ref_key = ref_key if ref_key is not None else secrets.token_bytes(32)
    builder = SummaryBuilder(contract=contract, directory=None, drive=None, client=None, audit=None,
                             clock=None, tenant_id=state["tenantId"], ref_key=ref_key)
    outputs = []
    for doc in docs:
        items, metadata = builder.build_items(doc, now)
        if len(items) != 1:
            raise PreparationError("Only one L1 summary per source is allowed")
        item_id, payload = items[0]
        payload["properties"]["url"] = base + "?ref=" + quote(metadata["ref"], safe="")
        payload["properties"]["title"] = "[Synthetic] " + payload["properties"]["title"]
        payload["properties"]["sensitivity"] = f"Synthetic fixture: {doc.label} (not Purview)"
        payload["content"]["value"] = MARKER + "\n" + payload["content"]["value"]
        card = (payload["content"]["value"] + "\n\nAccess request: " + payload["properties"]["url"]
                + "\nContract: " + CONTRACT_ID + "\nExpires: " + metadata["validUntil"]
                + "\nTTL cleanup requires an operator sweep; no Purview retention is configured.\n")
        for architecture, extension, content in (("A", "json", json_bytes(payload)),
                                                 ("C", "txt", card.encode("utf-8"))):
            file_name = item_id + "." + extension
            atomic_bytes(out / file_name, content)
            outputs.append({"architecture": architecture, "file": file_name, "itemId": item_id,
                            "sourceId": doc.source_id, "sourceFingerprint": doc.fingerprint,
                            "sha256": digest(content), "expiresAt": metadata["validUntil"]})
    manifest = {
        "version": 1, "planId": str(uuid.uuid4()), "createdAt": to_iso(now), "ttlDays": ttl_days,
        "classificationAuthority": AUTHORITY, "binding": binding(state), "citationBase": base,
        "mode": "full-root-delta-snapshot-manual-refresh", "outputs": outputs,
        "eligibleSources": len(docs), "exclusions": dict(reader.excluded),
        "notice": "Synthetic operator approval only; not owner/compliance approval, Purview, or production automation.",
    }
    atomic_json(out / "manifest.json", manifest)
    manifest_hash = digest((out / "manifest.json").read_bytes())
    atomic_json(out / "approval-template.json", {
        "approved": False, "approvalKind": APPROVAL_KIND, "actor": "", "approvedAt": None,
        "planSha256": manifest_hash, "outputs": {o["file"]: o["sha256"] for o in outputs},
    })
    atomic_json(out / "broker-sources.json", [asdict(doc) for doc in docs])
    if broker:
        snapshot_binding = {
            "schemaVersion": 1, "tenantId": state["tenantId"], "sourceSite": broker.sourceSite,
            "contractFingerprint": broker.fingerprint(), "capturedAt": to_iso(now), "synthetic": True,
        }
        atomic_json(out / "broker-sources.manifest.json", dict(
            snapshot_binding, documentsSha256=digest((out / "broker-sources.json").read_bytes())))
        if runtime_out:
            runtime = Path(runtime_out)
            current_contract = load_contract(broker_contract)
            if current_contract.fingerprint() != broker.fingerprint() or not current_contract.is_active(now):
                raise PreparationError("Broker contract changed during preparation")
            atomic_bytes(runtime / "contract.json", Path(broker_contract).read_bytes())
            atomic_json(runtime / "snapshot.json", dict(snapshot_binding, documents=[asdict(doc) for doc in docs]))
    atomic_json(out / "graph-evidence.json", {"counts": dict(graph.counts), "requests": graph.evidence})
    return {"eligibleSources": len(docs), "outputs": len(outputs), "planSha256": manifest_hash,
            "exclusions": dict(reader.excluded), "published": False}


class Ledger:
    def __init__(self, path, state):
        self.path = Path(path)
        self.data = read_json(path) if self.path.exists() else {
            "version": 1, "binding": binding(state), "entries": {}, "schema": {},
        }
        if self.data.get("binding") != binding(state):
            raise ValueError("Ledger belongs to a different demo deployment")

    def save(self):
        atomic_json(self.path, self.data)


def validated_plan(state, directory, approval_path, now):
    directory = Path(directory)
    raw_manifest = (directory / "manifest.json").read_bytes()
    manifest = json.loads(raw_manifest)
    approval = read_json(approval_path)
    if (manifest.get("version") != 1 or manifest.get("classificationAuthority") != AUTHORITY
            or manifest.get("binding") != binding(state)
            or approval.get("approved") is not True or approval.get("approvalKind") != APPROVAL_KIND
            or not isinstance(approval.get("actor"), str) or not approval["actor"].strip()
            or len(approval["actor"]) > 200 or approval.get("planSha256") != digest(raw_manifest)):
        raise ValueError("Explicit synthetic operator approval of this exact deployment and manifest is required")
    if not (parse_iso(manifest["createdAt"]) <= parse_iso(approval.get("approvedAt"))
            <= now + timedelta(minutes=5)):
        raise ValueError("Approval time is invalid")
    outputs = manifest["outputs"]
    expected_hashes = {o["file"]: o["sha256"] for o in outputs}
    if not outputs or len(expected_hashes) != len(outputs) or approval.get("outputs") != expected_hashes:
        raise ValueError("Approval must contain every exact A and C output hash")
    if not 1 <= manifest.get("ttlDays", 0) <= 30:
        raise ValueError("Invalid plan TTL")
    base = citation_url(manifest["citationBase"])
    contents, by_source = {}, {}
    for output in outputs:
        architecture = output["architecture"]
        if architecture not in ("A", "C"):
            raise ValueError("Invalid output architecture")
        extension = "json" if architecture == "A" else "txt"
        if (not re.fullmatch(r"kx-[0-9a-f]{20}-sum0000", output["itemId"])
                or output["file"] != output["itemId"] + "." + extension):
            raise ValueError("Invalid output filename or item identifier")
        expires = parse_iso(output["expiresAt"])
        if expires <= now or expires > parse_iso(manifest["createdAt"]) + timedelta(days=manifest["ttlDays"]):
            raise ValueError("Plan output expired or exceeds approved TTL; prepare and approve again")
        content = (directory / output["file"]).read_bytes()
        if digest(content) != output["sha256"]:
            raise ValueError("Approved output bytes changed")
        if architecture == "A":
            payload = json.loads(content)
            if (payload.get("acl") != [{"type": "group", "value": state["groups"]["Readers"]["id"],
                                        "accessType": "grant"}]
                    or payload["properties"].get("derivativeType") != "summary"
                    or payload["properties"].get("contractId") != CONTRACT_ID
                    or payload["properties"].get("sourceFingerprint") != output["sourceFingerprint"]
                    or payload["properties"].get("validUntil") != output["expiresAt"]
                    or not payload["properties"].get("url", "").startswith(base + "?ref=ref-")
                    or not payload["content"].get("value", "").startswith(MARKER)):
                raise ValueError("A output violates the approved summary/ACL/citation policy")
        elif not content.decode("utf-8").startswith(MARKER):
            raise ValueError("C output is missing its synthetic classification notice")
        contents[output["file"]] = content
        architectures = by_source.setdefault(output["sourceId"], set())
        if architecture in architectures:
            raise ValueError("Duplicate architecture for one source")
        architectures.add(architecture)
    if any(v != {"A", "C"} for v in by_source.values()):
        raise ValueError("Every source needs one approved A summary and C card")
    return manifest, approval, contents


def connector(graph):
    return ConnectorClient(graph, CONNECTION_ID, max_retries=0, poll_interval=5, max_polls=240)


def ensure_schema(client, ledger):
    prior = getattr(client.transport, "deadline", None)
    client.transport.deadline = time.monotonic() + 1200
    try:
        _ensure_schema(client, ledger)
    finally:
        client.transport.deadline = prior


def _ensure_schema(client, ledger):
    schema = build_schema()
    base = f"/external/connections/{CONNECTION_ID}"
    response = client.transport.request("GET", base)
    if response.status == 404:
        client.create_connection("KX synthetic derived knowledge",
                                 "Approved fictional L1 summaries. Registry classification is not Purview.")
        ledger.data["schema"] = {}
        ledger.save()
    else:
        check_response(response)
    stored = ledger.data["schema"]
    schema_hash = digest(json_bytes(schema))
    if stored.get("operation") and not stored.get("ready"):
        client.wait_for_schema(stored["operation"])
    elif (isinstance(response.body, dict) and response.body.get("state") == "ready"):
        actual = check_response(client.transport.request("GET", base + "/schema"))
        expected = {p["name"]: p["type"].casefold() for p in schema["properties"]}
        found = {p["name"]: p["type"].casefold() for p in actual.get("properties", [])}
        if expected != found:
            raise ValueError("Existing connector schema differs; do not mutate an active demo silently")
        client.schema = schema
        ledger.data["schema"] = {"ready": True, "sha256": schema_hash}
        ledger.save()
        return
    else:
        def save_operation(location):
            ledger.data["schema"] = {"operation": location, "ready": False, "sha256": schema_hash}
            ledger.save()
        client.register_schema(schema, on_operation=save_operation)
    client.schema = schema
    ledger.data["schema"].update(ready=True, sha256=schema_hash)
    ledger.save()


def entry_key(output):
    return output["architecture"] + ":" + output["itemId"]


def connector_readback_matches(actual, expected):
    properties = actual.get("properties")
    if not isinstance(properties, dict):
        return False
    # Graph adds these connector-index identifiers; they are not source-site permissions or provenance.
    service_fields = {"IsDGBasedSecurityEnabled", "ows_SiteID", "ows_WebId", "ows_ListID", "ows_UniqueId"}
    return (actual.get("acl") == expected["acl"] and actual.get("content") == expected["content"]
            and all(properties.get(key) == value for key, value in expected["properties"].items())
            and set(properties) - set(expected["properties"]) <= service_fields)


def remove_entry(graph, state, ledger, key, reason):
    entry = ledger.data["entries"][key]
    if entry["status"] == "deleted":
        return False
    entry.update(status="deleting", removalReason=reason)
    ledger.save()
    if entry["architecture"] == "A":
        path = f"/external/connections/{CONNECTION_ID}/items/{segment(entry['itemId'])}"
    else:
        drive = segment(state["sites"]["Exchange"]["driveId"])
        path = (f"/drives/{drive}/items/{segment(entry['remoteId'])}" if entry.get("remoteId")
                else f"/drives/{drive}/root:/{segment(entry['file'])}")
    check_response(graph.request("DELETE", path), allowed=(200, 204, 404))
    entry["status"] = "deleted"
    ledger.save()
    return True


def cleanup(graph, state, ledger, now, *, source_id=None, all_sources=False, current=None):
    deleted = 0
    for key, entry in list(ledger.data["entries"].items()):
        reason = None
        if all_sources or source_id == entry["sourceId"]:
            reason = "operator_withdrawal"
        elif parse_iso(entry["expiresAt"]) <= now:
            reason = "ttl_expired"
        elif entry["status"] == "deleting":
            reason = entry.get("removalReason", "retry_delete")
        elif current is not None and current.get(entry["sourceId"]) != entry["sourceFingerprint"]:
            reason = "source_withdrawn_or_changed"
        if reason:
            deleted += remove_entry(graph, state, ledger, key, reason)
    return {"deleted": deleted}


def apply(graph, state, plan, approval_path, ledger, now):
    started = time.monotonic()
    manifest, approval, contents = validated_plan(state, plan, approval_path, now)
    for output in manifest["outputs"]:
        prior = ledger.data["entries"].get(entry_key(output), {})
        if (prior.get("removalReason") == "operator_withdrawal"
                and prior.get("approvalId") == manifest["planId"]):
            raise ValueError("A withdrawn approval cannot be replayed; prepare and explicitly approve a new plan")
    reader = SourceReader(graph, state, contract_for(state, now, manifest["ttlDays"]))
    current = {doc.source_id: doc.fingerprint for doc in reader.snapshot()}
    cleanup(graph, state, ledger, now, current=current)
    if any(current.get(o["sourceId"]) != o["sourceFingerprint"] for o in manifest["outputs"]):
        raise ValueError("Approved source changed, moved, opted out or disappeared; prepare and approve again")
    client = connector(graph)
    ensure_schema(client, ledger)
    # Schema provisioning may take 20 minutes. Recheck source bytes after it, not only before it.
    current = {doc.source_id: doc.fingerprint for doc in SourceReader(
        graph, state, contract_for(state, now, manifest["ttlDays"])).snapshot()}
    cleanup(graph, state, ledger, now, current=current)
    if any(current.get(o["sourceId"]) != o["sourceFingerprint"] for o in manifest["outputs"]):
        raise ValueError("Source changed during schema registration; prepare and approve again")
    applied, skipped = 0, 0
    for output in manifest["outputs"]:
        effective_now = now + timedelta(seconds=time.monotonic() - started)
        if parse_iso(output["expiresAt"]) <= effective_now:
            cleanup(graph, state, ledger, effective_now)
            raise ValueError("Plan expired during this run; prepare and approve again")
        key = entry_key(output)
        # Journal the stable remote target BEFORE any put: a timed-out put is still removable/retryable.
        # Recreated files can have new IDs. Only this put's acknowledged ID may be used for cleanup.
        entry = dict(output, status="pending", approvalId=manifest["planId"],
                     approvalKind=APPROVAL_KIND, approvalActor=approval["actor"])
        ledger.data["entries"][key] = entry
        ledger.save()
        content = contents[output["file"]]
        if output["architecture"] == "A":
            payload = json.loads(content)
            client.put_item(output["itemId"], payload)
            actual = client.get_item(output["itemId"])
            if not connector_readback_matches(actual, payload):
                raise ValueError("Connector readback did not match approved output")
        else:
            site = state["sites"]["Exchange"]
            drive_base = f"/drives/{segment(site['driveId'])}"
            uploaded = check_response(graph.request(
                "PUT", f"{drive_base}/root:/{segment(output['file'])}:/content", content,
                headers={"Content-Type": "text/plain; charset=utf-8"}))
            entry["remoteId"] = uploaded["id"]
            ledger.save()
            item_path = f"{drive_base}/items/{segment(uploaded['id'])}"
            list_item = check_response(graph.request("GET", item_path + "/listItem"))
            fields_path = (f"/sites/{segment(site['id'])}/lists/{segment(site['listId'])}"
                           f"/items/{segment(list_item['id'])}/fields")
            fields = {"KXContractId": CONTRACT_ID, "KXSourceFingerprint": output["sourceFingerprint"],
                      "KXApprovalId": manifest["planId"], "KXApprovedOutputHash": output["sha256"],
                      "KXExpiresAt": output["expiresAt"], "KXClassificationAuthority": AUTHORITY}
            check_response(graph.request("PATCH", fields_path, fields))
            actual_fields = check_response(graph.request("GET", fields_path))
            if any((parse_iso(actual_fields[k]) != parse_iso(v) if k == "KXExpiresAt"
                    else actual_fields.get(k) != v) for k, v in fields.items()):
                raise ValueError("Exchange metadata readback did not match")
            if digest(graph.download(item_path + "/content")) != output["sha256"]:
                raise ValueError("Exchange content readback did not match approved bytes")
        entry.update(status="committed", verifiedAt=to_iso(effective_now))
        ledger.save()
        applied += 1
    return {"applied": applied, "skipped": skipped, "eligibleSources": len(current),
            "approvalKind": APPROVAL_KIND, "classificationAuthority": AUTHORITY}
