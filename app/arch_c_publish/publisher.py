"""Publishes knowledge cards into a simulated "Knowledge Exchange" SharePoint site.

The simulation stores files under <root>/Shared Documents/Knowledge Cards and list-item metadata in
<root>/_listItems.json (columns SourceFingerprint, ExpiryDate, ApprovalId, RetentionLabel, ...).
It also emits the equivalent production Microsoft Graph requests as plain data (never executed):
  PUT   /sites/{siteId}/drive/items/{parentId}:/{fileName}:/content
  PATCH /sites/{siteId}/drive/items/{itemId}/listItem/fields
  PATCH /drives/{driveId}/items/{itemId}/retentionLabel
  DELETE /sites/{siteId}/drive/items/{itemId}            (unpublish on stale / expiry / revocation)
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote

from common.clock import to_iso

from .derivative import KnowledgeCard

GRAPH_V1 = "https://graph.microsoft.com/v1.0"  # verify against current docs
RETENTION_LABEL = "KX-Derived-30d"
KX_SITE_URL = "https://contoso.sharepoint.com/sites/KnowledgeExchange"
KX_SITE_ID = "contoso.sharepoint.com,8a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d,9b2c3d4e-5f6a-4b7c-8d9e-0f1a2b3c4d5e"
KX_DRIVE_ID = "b!a3gtc2l0ZS1mYWtlLWRyaXZlLWlkLTAwMDAwMDAwMDAwMDAwMDAwMDAwMDAw"
KX_FOLDER_ID = "01KXCARDSFOLDER0000000000000000000"
COLUMNS = ("Title", "SourceFingerprint", "ExpiryDate", "ApprovalId", "RetentionLabel", "ContractId", "SourceTeam",
           "Sensitivity", "DerivedFrom", "DerivedContent", "CardVersion")


class KnowledgeExchangeSite:
    """Simulated site. Always kept in memory; with root_dir it is written through to disk as
    <root>/Shared Documents/<folder>/<file>.md plus the list-item manifest <root>/_listItems.json."""

    def __init__(self, root_dir=None, *, member_group_ids, allow_guests: bool = False, site_url: str = KX_SITE_URL,
                 folder: str = "Knowledge Cards"):
        self.root = Path(root_dir) if root_dir else None
        self.member_group_ids = tuple(member_group_ids)
        self.allow_guests = allow_guests
        self.site_url, self.folder = site_url, folder
        self._files: dict = {}
        self._items: dict = {}
        self.library_dir = self.manifest_path = None
        if self.root:
            self.library_dir = self.root / "Shared Documents" / folder
            self.library_dir.mkdir(parents=True, exist_ok=True)
            self.manifest_path = self.root / "_listItems.json"
            self._flush_manifest()

    def _flush_manifest(self) -> None:
        if not self.root:
            return
        data = {"site": self.site_url, "library": f"Shared Documents/{self.folder}", "columns": list(COLUMNS),
                "items": self._items}
        tmp = self.manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.manifest_path)

    def web_url(self, file_name: str) -> str:
        return f"{self.site_url}/Shared%20Documents/{quote(self.folder)}/{quote(file_name)}"

    def upload(self, file_name: str, content: str, fields: dict, now) -> dict:
        item_id = "01KX" + hashlib.sha256(file_name.encode()).hexdigest()[:30].upper()
        item = {"id": item_id, "fileName": file_name, "webUrl": self.web_url(file_name),
                "lastModifiedDateTime": to_iso(now), "fields": dict(fields)}
        self._files[file_name] = content
        self._items[item_id] = item
        if self.root:
            (self.library_dir / file_name).write_text(content, encoding="utf-8")
            self._flush_manifest()
        return dict(item)

    def delete(self, item_id: str) -> bool:
        item = self._items.pop(item_id, None)
        if item is None:
            return False
        self._files.pop(item["fileName"], None)
        if self.root:
            (self.library_dir / item["fileName"]).unlink(missing_ok=True)
            self._flush_manifest()
        return True

    def items(self) -> list:
        return [dict(item) for item in self._items.values()]

    def read(self, item_id: str) -> str:
        return self._files[self._items[item_id]["fileName"]]

    def can_read(self, user) -> bool:
        """Site membership check; guests are blocked when the site does not allow guest access."""
        if user is None or (user.is_guest and not self.allow_guests):
            return False
        return any(gid in user.groups for gid in self.member_group_ids)


def build_graph_publish_requests(*, site_id: str, drive_id: str, parent_id: str, item_id: str, file_name: str,
                                 content: str, fields: dict, retention_label: str) -> list:
    return [
        {"method": "PUT",
         "url": f"{GRAPH_V1}/sites/{site_id}/drive/items/{parent_id}:/{quote(file_name)}:/content",
         "headers": {"Content-Type": "text/markdown; charset=utf-8"},
         "body": content, "bodySha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
         "note": "simple upload; use an upload session for large files"},
        {"method": "PATCH",
         "url": f"{GRAPH_V1}/sites/{site_id}/drive/items/{item_id}/listItem/fields",
         "headers": {"Content-Type": "application/json"}, "body": dict(fields)},
        {"method": "PATCH",
         "url": f"{GRAPH_V1}/drives/{drive_id}/items/{item_id}/retentionLabel",
         "headers": {"Content-Type": "application/json"}, "body": {"name": retention_label}},
    ]


class Publisher:
    def __init__(self, site: KnowledgeExchangeSite, clock, *, retention_label: str = RETENTION_LABEL,
                 site_id: str = KX_SITE_ID, drive_id: str = KX_DRIVE_ID, parent_id: str = KX_FOLDER_ID):
        self.site, self.clock, self.retention_label = site, clock, retention_label
        self.site_id, self.drive_id, self.parent_id = site_id, drive_id, parent_id
        self.graph_log: list = []

    def fields_for(self, card: KnowledgeCard) -> dict:
        m = card.metadata
        return {"Title": m["title"], "SourceFingerprint": m["sourceFingerprint"], "ExpiryDate": m["expiresAt"],
                "ApprovalId": m["approvalId"], "RetentionLabel": self.retention_label, "ContractId": m["contractId"],
                "SourceTeam": m["sourceTeam"], "Sensitivity": m["sensitivity"], "DerivedFrom": m["sourceRef"],
                "DerivedContent": True, "CardVersion": m["cardVersion"]}

    def publish(self, card: KnowledgeCard) -> dict:
        fields = self.fields_for(card)
        item = self.site.upload(card.file_name, card.markdown, fields, self.clock.now())
        requests = build_graph_publish_requests(site_id=self.site_id, drive_id=self.drive_id, parent_id=self.parent_id,
                                                item_id=item["id"], file_name=card.file_name, content=card.markdown,
                                                fields=fields, retention_label=self.retention_label)
        self.graph_log.extend(requests)
        return {"itemId": item["id"], "fileName": card.file_name, "webUrl": item["webUrl"], "fields": fields,
                "graphRequests": requests}

    def unpublish(self, item_id: str, reason: str) -> dict:
        removed = self.site.delete(item_id)
        request = {"method": "DELETE", "url": f"{GRAPH_V1}/sites/{self.site_id}/drive/items/{item_id}",
                   "reason": reason, "applied": removed}
        self.graph_log.append(request)
        return request
