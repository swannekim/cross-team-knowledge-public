"""Full real Graph snapshots, gated by the shipped fictional fixture registry, never Purview."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from urllib.parse import quote, unquote

from common import paths
from common.fingerprint import source_fingerprint
from common.policy import PolicyGate
from common.sources import SourceDocument, extract_text

from .graph import check_response

AUTHORITY = "synthetic-fixture-registry-not-Purview"


def segment(value):
    return quote(str(value), safe="")


def fixture_registry():
    manifest = json.loads((paths.LIBRARY_DIR / "manifest.json").read_text(encoding="utf-8"))
    return {item["name"]: {
        "path": item["folderPath"] + "/" + item["name"],
        "fixtureLabel": item["sensitivityLabel"],
        "sha256": hashlib.sha256((paths.LIBRARY_DIR / item["contentFile"]).read_bytes()).hexdigest(),
    } for item in manifest["items"]}


class SourceReader:
    def __init__(self, graph, state, contract):
        self.graph, self.state, self.contract = graph, state, contract
        self.site = state["sites"]["Source"]
        self.base = f"/drives/{segment(self.site['driveId'])}"
        self.excluded = Counter()
        self.delta_link = None

    def metadata(self, item_id):
        return check_response(self.graph.request("GET", f"{self.base}/items/{segment(item_id)}"))

    def item_path(self, item):
        names, current, seen = [item["name"]], item, set()
        for _ in range(32):
            parent = current.get("parentReference", {})
            if parent.get("driveId") not in (None, self.site["driveId"]):
                raise ValueError("Source drive mismatch")
            if "root:" in parent.get("path", ""):
                return unquote(parent["path"].split("root:", 1)[1]).rstrip("/") + "/" + "/".join(names)
            parent_id = parent.get("id")
            if not parent_id or parent_id in seen:
                raise ValueError("Source parent path unavailable")
            seen.add(parent_id)
            current = self.metadata(parent_id)
            if "root" in current:
                return "/" + "/".join(names)
            names.insert(0, current["name"])
        raise ValueError("Source parent depth exceeded")

    def snapshot(self):
        # Intentionally restart at root/delta, not a stored delta token: each run is a complete snapshot.
        pending, url, seen_urls = {}, self.base + "/root/delta", set()
        while url:
            if url in seen_urls or len(seen_urls) >= 1000:
                raise ValueError("Invalid snapshot pagination")
            seen_urls.add(url)
            page = check_response(self.graph.request("GET", url))
            for item in page["value"]:
                if "deleted" in item:
                    pending.pop(item["id"], None)
                else:
                    pending[item["id"]] = item
            url = page.get("@odata.nextLink")
            if not url:
                self.delta_link = page.get("@odata.deltaLink")
                if not self.delta_link:
                    raise ValueError("Initial delta snapshot has no terminal deltaLink")
        registry, gate, docs = fixture_registry(), PolicyGate(self.contract), []
        by_id = {record["id"]: (name, record) for name, record in self.state["sources"].items()}
        for sid, listed in sorted(pending.items()):
            if "file" not in listed:
                continue
            registered = by_id.get(sid)
            if not registered:
                self.excluded["unregistered_source"] += 1
                continue
            name, record = registered
            fixture = registry.get(name)
            if (not fixture or record.get("optIn") is not True
                    or record.get("classificationAuthority") != AUTHORITY
                    or any(record.get(k) != fixture[k] for k in ("sha256", "fixtureLabel", "path"))):
                self.excluded["registry_denied"] += 1
                continue
            item = self.metadata(sid)
            actual_path = self.item_path(item)
            if item.get("name") != name or actual_path != fixture["path"] or "file" not in item:
                self.excluded["source_moved"] += 1
                continue
            decision = gate.check_file(path=actual_path, label=record["fixtureLabel"], name=name)
            if not decision.allowed:
                self.excluded[decision.reason] += 1
                continue
            if not item.get("eTag") or not item.get("lastModifiedDateTime"):
                raise ValueError("Source provenance is incomplete")
            data = self.graph.download(f"{self.base}/items/{segment(sid)}/content")
            content_hash = hashlib.sha256(data).hexdigest()
            if content_hash != fixture["sha256"]:
                self.excluded["source_hash_mismatch"] += 1
                continue
            after = self.metadata(sid)
            if after.get("eTag") != item["eTag"] or self.item_path(after) != actual_path:
                raise ValueError("Source changed during download; prepare again")
            text, title = extract_text(name, data)
            docs.append(SourceDocument(
                source_id=sid, kind="file", name=name, title=title, path=actual_path,
                web_url=item["webUrl"], label=record["fixtureLabel"], etag=item["eTag"],
                last_modified=item["lastModifiedDateTime"], text=text,
                fingerprint=source_fingerprint(data, item["eTag"]), container="KX synthetic source",
                label_id=None, meta={"classificationAuthority": AUTHORITY, "contentSha256": content_hash,
                                     "siteId": self.site["id"], "driveId": self.site["driveId"],
                                     "graphDriveId": self.site["driveId"], "graphItemId": sid},
            ))
        return docs
