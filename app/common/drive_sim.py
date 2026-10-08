"""In-memory emulation of a SharePoint document library / OneDrive drive using Graph driveItem shapes.

Supports the calls the architectures need (production equivalents in brackets):
  * delta(link)                       [GET /drives/{id}/root/delta, @odata.nextLink / @odata.deltaLink]
  * get_item / get_content            [GET /drives/{id}/items/{id}, GET .../content]
  * extract_sensitivity_labels        [POST /drives/{id}/items/{id}/extractSensitivityLabels]
  * list_permissions                  [GET /drives/{id}/items/{id}/permissions]
and simulated Team A activity (edit, relabel, move, delete, add) that feeds the delta journal.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import uuid
from pathlib import Path, PurePosixPath
from typing import Optional
from urllib.parse import parse_qs, quote, urlparse

from .clock import SystemClock, to_iso

GRAPH_V1 = "https://graph.microsoft.com/v1.0"
_MIME = {".md": "text/markdown", ".txt": "text/plain", ".html": "text/html", ".htm": "text/html"}


class ItemNotFound(KeyError):
    pass


class ResyncRequired(Exception):
    """Emulates HTTP 410 Gone (resyncRequired) for an expired delta token."""


def fake_item_id(seed: str) -> str:
    digest = hashlib.sha256(("driveItem:" + seed).encode()).digest()
    return "01" + base64.b32encode(digest).decode()[:32]


def _guid(seed: str) -> str:
    return str(uuid.UUID(bytes=hashlib.sha256(seed.encode()).digest()[:16], version=4)).upper()


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).decode().rstrip("=")


def _unb64(token: str):
    return json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))


class SimulatedDrive:
    def __init__(self, manifest: dict, base_dir: Path, clock=None):
        self.tenant_id = manifest.get("tenantId")
        self.site = dict(manifest["site"])
        self.drive = dict(manifest["drive"])
        self.drive_id = self.drive["id"]
        self._clock = clock or SystemClock()
        self._label_names = {entry["id"]: entry["name"] for entry in manifest["labels"]}
        self._label_ids = {entry["name"]: entry["id"] for entry in manifest["labels"]}
        self._items: dict = {}
        self._content: dict = {}
        self._labels: dict = {}
        self._perms: dict = {}
        self._versions: dict = {}
        self._tombstones: dict = {}
        self._journal: list = []
        self._seq = 0
        self._min_valid_token = 0
        self.content_downloads = 0
        self.root_id = fake_item_id(f"{self.drive_id}:root")
        self._items[self.root_id] = {
            "id": self.root_id, "name": "root", "root": {}, "folder": {"childCount": 0},
            "webUrl": self.drive["webUrl"], "parentReference": {"driveId": self.drive_id,
                                                                "driveType": self.drive.get("driveType")},
        }
        self._journal_change(self.root_id)
        for entry in manifest["items"]:
            data = (base_dir / entry["contentFile"]).read_bytes()
            self._insert_file(entry, data)

    @classmethod
    def from_manifest_file(cls, manifest_path, clock=None) -> "SimulatedDrive":
        manifest_path = Path(manifest_path)
        return cls(json.loads(manifest_path.read_text(encoding="utf-8")), manifest_path.parent, clock)

    # ------------------------------------------------------------------ internals
    def _journal_change(self, item_id: str) -> None:
        self._seq += 1
        self._journal.append((self._seq, item_id))

    def _parent_path(self, folder_path: str) -> str:
        return f"/drives/{self.drive_id}/root:{'' if folder_path == '/' else folder_path}"

    def _ensure_folder(self, folder_path: str) -> str:
        folder_path = "/" + folder_path.strip("/") if folder_path.strip("/") else "/"
        if folder_path == "/":
            return self.root_id
        folder_id = fake_item_id(f"{self.drive_id}:folder:{folder_path.casefold()}")
        if folder_id in self._items:
            return folder_id
        parent = str(PurePosixPath(folder_path).parent)
        parent_id = self._ensure_folder(parent)
        name = PurePosixPath(folder_path).name
        self._items[folder_id] = {
            "id": folder_id, "name": name, "folder": {"childCount": 0},
            "eTag": f'"{{{_guid(folder_id)}}},1"', "lastModifiedDateTime": to_iso(self._clock.now()),
            "webUrl": f"{self.drive['webUrl']}{quote(folder_path)}",
            "parentReference": {"driveId": self.drive_id, "driveType": self.drive.get("driveType"),
                                "id": parent_id, "path": self._parent_path(parent)},
        }
        self._journal_change(folder_id)
        return folder_id

    def _insert_file(self, entry: dict, data: bytes) -> None:
        folder_id = self._ensure_folder(entry["folderPath"])
        item = {
            "id": entry["id"], "name": entry["name"], "eTag": entry["eTag"], "cTag": entry["cTag"],
            "createdDateTime": entry["createdDateTime"], "lastModifiedDateTime": entry["lastModifiedDateTime"],
            "size": len(data), "webUrl": entry["webUrl"],
            "parentReference": {"driveId": self.drive_id, "driveType": self.drive.get("driveType"),
                                "id": folder_id, "path": self._parent_path(entry["folderPath"])},
            "file": {"mimeType": entry["mimeType"]},
            "lastModifiedBy": entry.get("lastModifiedBy", {}),
        }
        self._items[item["id"]] = item
        self._content[item["id"]] = data
        self._labels[item["id"]] = entry.get("sensitivityLabelId")
        self._perms[item["id"]] = entry.get("permissions", [])
        self._versions[item["id"]] = [int(entry["eTag"].rsplit(",", 1)[1].rstrip('"')),
                                      int(entry["cTag"].rsplit(",", 1)[1].rstrip('"'))]
        self._journal_change(item["id"])

    def _require(self, item_id: str) -> dict:
        if item_id not in self._items:
            raise ItemNotFound(item_id)
        return self._items[item_id]

    def _bump(self, item_id: str, content_changed: bool) -> None:
        item = self._items[item_id]
        versions = self._versions[item_id]
        versions[0] += 1
        guid = item["eTag"].split("{", 1)[1].split("}", 1)[0]
        item["eTag"] = f'"{{{guid}}},{versions[0]}"'
        if content_changed:
            versions[1] += 1
            item["cTag"] = f'"c:{{{guid}}},{versions[1]}"'
        item["lastModifiedDateTime"] = to_iso(self._clock.now())
        self._journal_change(item_id)

    # ------------------------------------------------------------------ Graph-like reads
    @staticmethod
    def item_path(item: dict) -> str:
        """Library-relative path, e.g. '/Shareable/Etch/x.md' (root is '/')."""
        if "root" in item:
            return "/"
        parent = item.get("parentReference", {}).get("path", "")
        folder = parent.split("root:", 1)[1] if "root:" in parent else ""
        return f"{folder}/{item['name']}" if folder else f"/{item['name']}"

    def delta(self, link: Optional[str] = None, page_size: int = 200) -> dict:
        if link is None:
            since, offset, end = None, 0, self._seq
        else:
            query = parse_qs(urlparse(link).query)
            if "token" in query:
                since, offset, end = int(_unb64(query["token"][0])["s"]), 0, self._seq
            elif "$skiptoken" in query:
                state = _unb64(query["$skiptoken"][0])
                since, offset, end = state["since"], state["offset"], state["end"]
            else:
                raise ValueError("unrecognised delta link")
            if since is not None and since < self._min_valid_token:
                raise ResyncRequired("delta token expired; resync required (HTTP 410)")
        if since is None:
            ids = sorted(self._items, key=lambda i: (i != self.root_id, self.item_path(self._items[i]).casefold()))
        else:
            last_change = {}
            for seq, item_id in self._journal:
                if since < seq <= end:
                    last_change[item_id] = seq
            ids = sorted(last_change, key=last_change.get)
        page = ids[offset: offset + page_size]
        value = []
        for item_id in page:
            if item_id in self._items:
                value.append(copy.deepcopy(self._items[item_id]))
            else:
                value.append({"id": item_id, "deleted": {"state": "deleted"},
                              "parentReference": {"driveId": self.drive_id}})
        base = f"{GRAPH_V1}/drives/{self.drive_id}/root/delta"
        if offset + page_size < len(ids):
            skip = _b64({"since": since, "offset": offset + page_size, "end": end})
            return {"value": value, "@odata.nextLink": f"{base}?$skiptoken={skip}"}
        return {"value": value, "@odata.deltaLink": f"{base}?token={_b64({'s': end})}"}

    def get_item(self, item_id: str) -> dict:
        return copy.deepcopy(self._require(item_id))

    def get_content(self, item_id: str) -> bytes:
        self._require(item_id)
        self.content_downloads += 1
        return self._content[item_id]

    def extract_sensitivity_labels(self, item_id: str) -> dict:
        self._require(item_id)
        label_id = self._labels.get(item_id)
        labels = [] if label_id is None else [{"sensitivityLabelId": label_id, "assignmentMethod": "standard",
                                               "tenantId": self.tenant_id}]
        return {"labels": labels}

    def label_name(self, label_id: Optional[str]) -> Optional[str]:
        return self._label_names.get(label_id) if label_id else None

    def label_id(self, label_name: Optional[str]) -> Optional[str]:
        """Purview label GUID for a label display name from this tenant's label catalog."""
        return self._label_ids.get(label_name) if label_name else None

    def label_id_of(self, item_id: str) -> Optional[str]:
        labels = self.extract_sensitivity_labels(item_id)["labels"]
        return labels[0]["sensitivityLabelId"] if labels else None

    def label_of(self, item_id: str) -> Optional[str]:
        labels = self.extract_sensitivity_labels(item_id)["labels"]
        return self.label_name(labels[0]["sensitivityLabelId"]) if labels else None

    def list_permissions(self, item_id: str) -> dict:
        self._require(item_id)
        return {"value": copy.deepcopy(self._perms.get(item_id, []))}

    def files(self) -> list:
        return [copy.deepcopy(i) for i in self._items.values() if "file" in i]

    def find_by_name(self, name: str) -> Optional[dict]:
        for item in self._items.values():
            if "file" in item and item["name"] == name:
                return copy.deepcopy(item)
        return None

    def find_by_web_url(self, url: str) -> Optional[dict]:
        for item in self._items.values():
            if item.get("webUrl") == url:
                return copy.deepcopy(item)
        return None

    # ------------------------------------------------------------------ simulated Team A activity
    def update_content(self, item_id: str, content) -> None:
        self._require(item_id)
        data = content.encode("utf-8") if isinstance(content, str) else content
        self._content[item_id] = data
        self._items[item_id]["size"] = len(data)
        self._bump(item_id, content_changed=True)

    def set_label(self, item_id: str, label_name: str) -> None:
        self._require(item_id)
        self._labels[item_id] = self._label_ids[label_name]
        self._bump(item_id, content_changed=False)

    def move(self, item_id: str, new_folder_path: str) -> None:
        item = self._require(item_id)
        folder_id = self._ensure_folder(new_folder_path)
        item["parentReference"].update({"id": folder_id, "path": self._parent_path("/" + new_folder_path.strip("/"))})
        item["webUrl"] = f"{self.drive['webUrl']}/{quote(new_folder_path.strip('/'))}/{quote(item['name'])}"
        self._bump(item_id, content_changed=False)

    def delete(self, item_id: str) -> None:
        item = self._require(item_id)
        self._tombstones[item_id] = item
        del self._items[item_id]
        self._content.pop(item_id, None)
        self._journal_change(item_id)

    def add_file(self, folder_path: str, name: str, content, label: str) -> str:
        data = content.encode("utf-8") if isinstance(content, str) else content
        rel = f"{folder_path.strip('/')}/{name}"
        item_id = fake_item_id(f"{self.drive_id}:added:{rel}:{self._seq}")
        now = to_iso(self._clock.now())
        self._insert_file({
            "id": item_id, "name": name, "folderPath": "/" + folder_path.strip("/"),
            "eTag": f'"{{{_guid(item_id)}}},1"', "cTag": f'"c:{{{_guid(item_id)}}},1"',
            "createdDateTime": now, "lastModifiedDateTime": now,
            "webUrl": f"{self.drive['webUrl']}/{quote(rel)}",
            "mimeType": _MIME.get(PurePosixPath(name).suffix.lower(), "application/octet-stream"),
            "sensitivityLabelId": self._label_ids[label],
        }, data)
        return item_id

    def expire_delta_tokens(self) -> None:
        """Makes every previously issued delta token invalid (next use raises ResyncRequired)."""
        self._min_valid_token = self._seq + 1


def crawl(drive: SimulatedDrive, page_size: int = 200) -> tuple:
    """Full enumeration via delta (follows nextLink pages). Returns (items, deltaLink)."""
    items, page = [], drive.delta(None, page_size=page_size)
    while True:
        items.extend(page["value"])
        if "@odata.nextLink" in page:
            page = drive.delta(page["@odata.nextLink"], page_size=page_size)
            continue
        return items, page["@odata.deltaLink"]
