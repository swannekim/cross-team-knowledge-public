"""Regenerates the documentation artifacts deterministically (fixed clock, synthetic data, offline).

artifacts/arch_a : connection.json, schema.json, example externalItem PUT bodies, declarativeAgent.connector.json
artifacts/arch_b : declarativeAgent.json, ai-plugin.json, ai-plugin.mcp.json, openapi.json, mcp-tools.json
artifacts/arch_c : one knowledge card (.md) and its publish manifest (.json)

Usage (from the prototype root):  python3 tools/generate_artifacts.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
APP_ROOT = ROOT / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from arch_a_connector.sync_engine import build_default_engine  # noqa: E402
from arch_b_broker.manifest_builder import BROKER_MANIFESTS, CONNECTOR_MANIFESTS, render  # noqa: E402
from arch_c_publish.workflow import build_default_workflow  # noqa: E402
from common.clock import FixedClock  # noqa: E402
from common.paths import ARTIFACTS_DIR  # noqa: E402
from common.sources import chat_digest_documents, load_environment  # noqa: E402

START = "2026-10-07T01:00:00Z"
A_EXAMPLES = {
    "item_summary_YE-0412.json": ("YE-0412", "summary"),
    "item_extract_SQ-118.json": ("SQ-118", "redactedExtract"),
    "item_chat_digest_2026-09-12.json": ("chat-digest-2026-09-12", "redactedExtract"),
}


def _dump(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def generate_a(out: Path) -> list:
    clock = FixedClock(START)
    env = load_environment(clock)
    engine, transport = build_default_engine(env, clock=clock)
    engine.run_full()
    log = transport.request_log
    _dump(out / "connection.json", json.loads(next(e["body"] for e in log if e["path"] == "/external/connections")))
    _dump(out / "schema.json", json.loads(next(e["body"] for e in log if e["path"].endswith("/schema"))))
    written = [out / "connection.json", out / "schema.json"]
    source_names = {i["id"]: i["name"] for drive in (env.drive, env.chat_drive) for i in drive.files()}
    source_names.update({d.source_id: d.name for d in chat_digest_documents(env.chat_export, "Confidential")})
    items = transport.items(engine.client.connection_id)
    for file_name, (prefix, derivative) in A_EXAMPLES.items():
        source_id = next(sid for sid in engine.state.docs if source_names.get(sid, "").startswith(prefix))
        item_id = next(i for i in engine.state.docs[source_id]["itemIds"] if ("-sum" in i) == (derivative == "summary"))
        _dump(out / file_name, {k: v for k, v in items[item_id].items() if k != "id"})
        written.append(out / file_name)
    for name in CONNECTOR_MANIFESTS:  # declarative agent v1.8 grounded on this connection
        (out / name).write_text(render(name), encoding="utf-8")
        written.append(out / name)
    return written


def generate_b(out: Path) -> list:
    written = []
    for name in BROKER_MANIFESTS:
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(name), encoding="utf-8")
        written.append(path)
    return written


def generate_c(out: Path) -> list:
    clock = FixedClock(START)
    env = load_environment(clock)
    workflow, site, _ = build_default_workflow(env, clock=clock)
    source = next(i for i in env.drive.files() if i["name"].startswith("SQ-118"))
    req = workflow.request("bob", source["id"], env.contract.purpose, need="Supplier gel-defect history for NX-7 yield model")
    workflow.approve_owner("alice", req.request_id, "OK to share supplier issue summary")
    workflow.approve_compliance("erin", req.request_id, "PII redaction verified")
    workflow.publish(req.request_id)
    card = site.read(req.card["itemId"])
    card_name = "knowledge_card_SQ-118.md"
    (out / card_name).parent.mkdir(parents=True, exist_ok=True)
    (out / card_name).write_text(card, encoding="utf-8")
    requests = []
    for request in workflow.publisher.graph_log:
        request = dict(request)
        if isinstance(request.get("body"), str):
            request["body"] = f"<contents of {card_name}; sha256={hashlib.sha256(card.encode()).hexdigest()}>"
        requests.append(request)
    item = next(i for i in site.items() if i["id"] == req.card["itemId"])
    manifest = {
        "publishedToKnowledgeExchange": {
            "site": site.site_url,
            "library": f"Shared Documents/{site.folder}",
            "siteMembers": list(site.member_group_ids),
            "allowGuests": site.allow_guests,
            "listItem": item,
            "graphRequests": requests,
        },
        "workflowRecordInternalNotPublished": {
            "requestId": req.request_id, "state": req.state, "requestedBy": req.requested_by,
            "ownerApproval": req.owner_approval, "complianceApproval": req.compliance_approval, "history": req.history,
        },
    }
    _dump(out / "publish_manifest_SQ-118.json", manifest)
    return [out / card_name, out / "publish_manifest_SQ-118.json"]


def main() -> int:
    written = generate_a(ARTIFACTS_DIR / "arch_a") + generate_b(ARTIFACTS_DIR / "arch_b") + generate_c(ARTIFACTS_DIR / "arch_c")
    for path in written:
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
