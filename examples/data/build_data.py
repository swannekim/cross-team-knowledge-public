"""Deterministically (re)builds the generated parts of the synthetic data set.

* the large ETCH-07 FDC event log (> 4 MB; it is chunked into <=8,000-character
  externalItems for retrieval quality, far below the 30 MB per-item limit),
* data/team_a_library/manifest.json  (Graph driveItem-shaped metadata for the
  Process Engineering document library),
* data/teams_chat/onedrive_jisoo/manifest.json (the sender's OneDrive that holds
  the "Microsoft Teams Chat Files" attachment).

Everything is fictional ("Contoso"). Run:  python3 data/build_data.py
"""
from __future__ import annotations

import base64
import hashlib
import json
import random
import uuid
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

DATA = Path(__file__).resolve().parent
LIB = DATA / "team_a_library"
LIB_FILES = LIB / "files"
ONEDRIVE = DATA / "teams_chat" / "onedrive_jisoo"

GRP_TEAM_A = "a0a0a0a0-1111-4111-8111-0000000000aa"
TENANT_ID = "9c0a7e11-4f2b-4d3c-8e5f-6a7b8c9d0e1f"
USERS = {
    "alice": ("0a11ce00-0000-4000-8000-0000000000a1", "Alice Kim", "alice@contoso.com"),
    "minho": ("0a1b0c00-0000-4000-8000-0000000000f6", "Minho Park", "minho.park@contoso.com"),
    "jisoo": ("0a1b0c00-0000-4000-8000-0000000000f7", "Jisoo Lee", "jisoo.lee@contoso.com"),
}
LABELS = [
    {"id": "6e0e0000-1abe-4a1b-8e1a-000000000000", "name": "Public"},
    {"id": "6e0e0001-1abe-4a1b-8e1a-000000000001", "name": "General"},
    {"id": "6e0e0002-1abe-4a1b-8e1a-000000000002", "name": "Confidential"},
    {"id": "6e0e0003-1abe-4a1b-8e1a-000000000003", "name": "Highly Confidential"},
]
LABEL_ID = {entry["name"]: entry["id"] for entry in LABELS}
MIME = {".md": "text/markdown", ".txt": "text/plain", ".html": "text/html"}

LARGE_DOC_REL = "Shareable/Logs/ETCH-07_FDC_event_log_2026Q3.txt"

DOCS = [
    ("Shareable/Etch/ER-2291_poly_gate_etch_recipe_change.md", "Confidential", "2026-08-30T02:10:00Z", "2026-09-08T06:45:00Z", "alice", 4),
    ("Shareable/Etch/GL-ETCH-007_chamber_seasoning_guideline.txt", "General", "2025-03-02T01:00:00Z", "2026-09-20T03:00:00Z", "alice", 5),
    ("Shareable/Yield/YE-0412_NX7_yield_excursion_RCA.md", "Confidential", "2026-09-13T12:00:00Z", "2026-09-16T08:20:00Z", "jisoo", 3),
    ("Shareable/Maintenance/tool_PM_schedule_Q4_2026.html", "General", "2026-09-22T00:30:00Z", "2026-09-25T01:00:00Z", "minho", 2),
    ("Shareable/Supplier/SQ-118_photoresist_supplier_quality_issue.txt", "Confidential", "2026-08-27T04:00:00Z", "2026-09-05T07:30:00Z", "alice", 6),
    ("Shareable/Metrology/MET-CDSEM-02_calibration_runbook.md", "General", "2026-02-11T03:00:00Z", "2026-07-18T05:00:00Z", "minho", 7),
    (LARGE_DOC_REL, "General", "2026-10-01T00:20:00Z", "2026-10-01T00:30:00Z", "minho", 1),
    ("Shareable/Strategy/NX7_gate_stack_process_window_TRADE_SECRET.md", "Highly Confidential", "2026-05-02T09:00:00Z", "2026-06-12T09:00:00Z", "alice", 2),
    ("Internal/HR/team_a_staffing_and_review_notes.md", "General", "2026-08-20T09:00:00Z", "2026-09-01T09:00:00Z", "alice", 3),
    ("Shareable/Drafts/WIP_NX8_litho_overlay_budget_DRAFT.md", "General", "2026-10-02T08:00:00Z", "2026-10-02T09:00:00Z", "jisoo", 1),
]


def fake_item_id(seed: str) -> str:
    """SharePoint-style driveItem id: '01' + 32 upper-case base32 characters."""
    digest = hashlib.sha256(("driveItem:" + seed).encode()).digest()
    return "01" + base64.b32encode(digest).decode()[:32]


def fake_guid(seed: str) -> str:
    return str(uuid.UUID(bytes=hashlib.sha256(seed.encode()).digest()[:16], version=4)).upper()


def etag(seed: str, version: int) -> str:
    return f'"{{{fake_guid(seed)}}},{version}"'


def ctag(seed: str, version: int) -> str:
    return f'"c:{{{fake_guid(seed)}}},{version}"'


# --------------------------------------------------------------------------- large document
def _wafer_line(day: date, hour: int, minute: int, chamber: str, lot: str, wafer: int, rng: random.Random) -> str:
    new_recipe = chamber == "CH-B" and day >= date(2026, 9, 8)
    if new_recipe:
        rcp, fwd, cf4, o2, epd = "POLY_ME_V4", 1180.0, 50.0, 10.0, "520nm"
    else:
        rcp, fwd, cf4, o2, epd = "POLY_ME_V3", 1250.0, 48.0, 12.0, "405nm"
    status = "OK"
    refl = 3.0 + rng.random() * 0.6
    excursion = chamber == "CH-B" and date(2026, 9, 10) <= day <= date(2026, 9, 12)
    if excursion and rng.random() < 0.35:
        status = f"ALARM PARTICLE_ADDER_HIGH adders={rng.randint(30, 45)} limit=10"
        refl += 4.0
    return (
        f"{day.isoformat()}T{hour:02d}:{minute:02d}:{rng.randint(0, 59):02d}Z ETCH-07 {chamber} RUN=R{rng.randint(100000, 999999)} LOT={lot} "
        f"WFR={wafer:02d} RCP={rcp} RF_FWD={fwd + rng.uniform(-4, 4):.1f}W RF_REFL={refl:.1f}W "
        f"PRESS={12.0 + rng.uniform(-0.05, 0.05):.2f}mT CF4={cf4 + rng.uniform(-0.2, 0.2):.1f}sccm "
        f"O2={o2 + rng.uniform(-0.1, 0.1):.1f}sccm ESC_T={45.0 + rng.uniform(-0.4, 0.4):.1f}C EPD={epd} {status}"
    )


def _shift_note(day: date, hour: int, rng: random.Random) -> str:
    shift = "day" if hour == 6 else "night"
    if day == date(2026, 9, 9):
        return (f"Shift note ({shift}) {day}: chamber B preventive maintenance started at 08:00 with upper liner and "
                "focus ring replacement. Seasoning was cut short to 10 wafers to meet the release schedule.")
    if date(2026, 9, 10) <= day <= date(2026, 9, 12):
        return (f"Shift note ({shift}) {day}: particle adder alarms on chamber B continued and lots W2291, W2295 and "
                "W2302 were flagged for review. The tool owner was notified (minho.park@contoso.com, 010-3456-7890).")
    if day == date(2026, 9, 13):
        return (f"Shift note ({shift}) {day}: chamber B received 25 additional seasoning wafers and the particle "
                "monitor returned to 4 adders per wafer, so the chamber was released.")
    fwd = 1180 if day >= date(2026, 9, 8) else 1250
    extra = " after the ER-2291 recipe change" if day >= date(2026, 9, 8) else ""
    return (f"Shift note ({shift}) {day}: chamber B processed {rng.randint(150, 220)} wafers and chamber A processed "
            f"{rng.randint(150, 220)} wafers. RF forward power on chamber B averaged {fwd} W{extra} and no FDC "
            "alarms were raised.")


def generate_large_log() -> str:
    rng = random.Random(20260930)
    parts = [
        "ETCH-07 FAULT DETECTION AND CLASSIFICATION (FDC) EVENT LOG EXPORT - 2026 Q3\n"
        "Tool: ETCH-07 (chambers CH-A and CH-B), Contoso Fab 3. One line per wafer main-etch step.\n"
        "Purpose: per-wafer trace summaries for yield correlation studies. Values are step averages.\n\n"
    ]
    lot_no = {"CH-A": 1000, "CH-B": 5000}
    wafer_no = {"CH-A": 0, "CH-B": 0}
    special_lots = {date(2026, 9, 10): "W2291", date(2026, 9, 11): "W2295", date(2026, 9, 12): "W2302"}
    day = date(2026, 7, 1)
    while day <= date(2026, 9, 30):
        parts.append(f"=== {day.isoformat()} ===\n")
        for hour in range(24):
            lines = []
            if hour in (6, 18):
                lines.append(_shift_note(day, hour, rng))
            for chamber in ("CH-A", "CH-B"):
                if chamber == "CH-B" and day == date(2026, 9, 9) and 8 <= hour <= 18:
                    lines.append(f"{day.isoformat()}T{hour:02d}:00:00Z ETCH-07 CH-B STATE=PM_IN_PROGRESS "
                                 "TASK=UPPER_LINER_AND_FOCUS_RING_REPLACEMENT")
                    continue
                seasoning = chamber == "CH-B" and ((day == date(2026, 9, 9) and hour == 19)
                                                   or (day == date(2026, 9, 13) and hour in (9, 10)))
                for k in range(6):
                    minute = k * 10
                    if seasoning:
                        lines.append(f"{day.isoformat()}T{hour:02d}:{minute:02d}:00Z ETCH-07 CH-B LOT=SEASON "
                                     f"WFR={k + 1:02d} RCP=SEASON_PolyStd STATE=SEASONING")
                        continue
                    wafer_no[chamber] += 1
                    if wafer_no[chamber] > 25:
                        wafer_no[chamber] = 1
                        lot_no[chamber] += 1
                    lot = f"W{lot_no[chamber]}"
                    if chamber == "CH-B" and day in special_lots:
                        lot = special_lots[day]
                    lines.append(_wafer_line(day, hour, minute, chamber, lot, wafer_no[chamber], rng))
            parts.append("\n".join(lines) + "\n\n")
        day += timedelta(days=1)
    return "".join(parts)


# --------------------------------------------------------------------------- manifests
def _user(alias: str) -> dict:
    oid, name, mail = USERS[alias]
    return {"user": {"id": oid, "displayName": name, "email": mail}}


def build_library_manifest() -> dict:
    site_url = "https://contoso.sharepoint.com/sites/ProcessEng"
    drive_id = "b!cHJvY2Vzc2VuZy1zaXRlLWZha2UtZHJpdmUtaWQtMDAwMDAwMDAwMDAwMDAwMA"
    items = []
    for rel, label, created, modified, by, version in DOCS:
        path = LIB_FILES / rel
        data = path.read_bytes()
        folder = "/" + "/".join(rel.split("/")[:-1])
        name = rel.split("/")[-1]
        items.append({
            "id": fake_item_id(rel),
            "name": name,
            "folderPath": folder,
            "contentFile": "files/" + rel,
            "size": len(data),
            "mimeType": MIME[Path(name).suffix],
            "sensitivityLabelId": LABEL_ID[label],
            "sensitivityLabel": label,
            "createdDateTime": created,
            "lastModifiedDateTime": modified,
            "eTag": etag(rel, version),
            "cTag": ctag(rel, version),
            "webUrl": f"{site_url}/Shared%20Documents/{quote(rel)}",
            "lastModifiedBy": _user(by),
            "permissions": [
                {
                    "id": "c2l0ZS1ncnAtdGVhbS1h",
                    "roles": ["write"],
                    "grantedToV2": {"group": {"id": GRP_TEAM_A, "displayName": "grp-team-a"}},
                }
            ],
        })
    return {
        "_comment": "Synthetic Graph driveItem metadata for the fictional Process Engineering library.",
        "tenantId": TENANT_ID,
        "site": {
            "id": "contoso.sharepoint.com,5b1f0c3e-6a2d-4e8b-9f1c-2d3e4f5a6b7c,7c8d9e0f-1a2b-4c3d-8e4f-5a6b7c8d9e0f",
            "name": "ProcessEng",
            "displayName": "Process Engineering",
            "webUrl": site_url,
        },
        "drive": {"id": drive_id, "name": "Documents", "driveType": "documentLibrary",
                  "webUrl": f"{site_url}/Shared%20Documents"},
        "labels": LABELS,
        "items": items,
    }


def build_onedrive_manifest() -> dict:
    site_url = "https://contoso-my.sharepoint.com/personal/jisoo_lee_contoso_com"
    rel = "Microsoft Teams Chat Files/ETCH-07_CH-B_particle_map_notes.txt"
    data = (ONEDRIVE / "files" / rel).read_bytes()
    return {
        "_comment": "Synthetic OneDrive of the chat sender (Jisoo Lee). Teams stores chat attachments here.",
        "tenantId": TENANT_ID,
        "site": {"id": "contoso-my.sharepoint.com,1d2e3f40-5a6b-4c7d-8e9f-0a1b2c3d4e5f,2e3f4a5b-6c7d-4e8f-9a0b-1c2d3e4f5a6b",
                 "name": "jisoo_lee_contoso_com", "displayName": "Jisoo Lee", "webUrl": site_url},
        "drive": {"id": "b!amlzb28tb25lZHJpdmUtZmFrZS1kcml2ZS1pZC0wMDAwMDAwMDAwMDAwMDAwMDA",
                  "name": "OneDrive", "driveType": "business", "webUrl": f"{site_url}/Documents"},
        "labels": LABELS,
        "items": [{
            "id": fake_item_id("onedrive-jisoo/" + rel),
            "name": rel.split("/")[-1],
            "folderPath": "/Microsoft Teams Chat Files",
            "contentFile": "files/" + rel,
            "size": len(data),
            "mimeType": "text/plain",
            "sensitivityLabelId": LABEL_ID["Confidential"],
            "sensitivityLabel": "Confidential",
            "createdDateTime": "2026-09-11T16:29:40Z",
            "lastModifiedDateTime": "2026-09-12T09:20:00Z",
            "eTag": etag("onedrive-jisoo/" + rel, 2),
            "cTag": ctag("onedrive-jisoo/" + rel, 2),
            "webUrl": f"{site_url}/Documents/{quote(rel)}",
            "lastModifiedBy": _user("jisoo"),
            "permissions": [{
                "id": "Y2hhdC1maWxlLWxpbms",
                "roles": ["read"],
                "link": {"scope": "users", "type": "view"},
                "grantedToIdentitiesV2": [_user("alice"), _user("minho")],
            }],
        }],
    }


def main() -> None:
    large = LIB_FILES / LARGE_DOC_REL
    large.parent.mkdir(parents=True, exist_ok=True)
    large.write_text(generate_large_log(), encoding="utf-8", newline="\n")
    (LIB / "manifest.json").write_text(json.dumps(build_library_manifest(), indent=2) + "\n", encoding="utf-8")
    (ONEDRIVE / "manifest.json").write_text(json.dumps(build_onedrive_manifest(), indent=2) + "\n", encoding="utf-8")
    print(f"large doc: {large.stat().st_size:,} bytes")
    print("manifests written")


if __name__ == "__main__":
    main()
