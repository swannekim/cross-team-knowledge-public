"""Ground-truth canaries and leak scanners shared by the test suites and the benchmark.

Canaries are exact strings seeded into the synthetic data. Finding one in any output Team B (or an
unauthorised user) can see is a leak, independent of the regexes used by the redactor.
"""
from __future__ import annotations

from .redaction import find_sensitive

PII_CANARIES = (
    "alice.kim@contoso.com", "jisoo.lee@contoso.com", "minho.park@contoso.com", "jh.kim@northwind-chem.example",
    "010-1234-5678", "010-2345-6789", "010-9876-5432", "010-3456-7890", "900101-1234567",
)
SECRET_CANARIES = ("CTSO-FAKE-KEY-7f3a9c2e1b5d4f60a8b7", "Fake-Passw0rd-NotReal!", "svc_metro_upload",
                   "contoso-fab-metro.database.windows.net")
HIGHLY_CONFIDENTIAL_CANARIES = ("OBSIDIAN", "HfZrO2", "FAB-NDA-0771", "TiAlC")
OUT_OF_SCOPE_CANARIES = ("performance improvement plan", "Salary band", "NX-8", "overlay budget")
INJECTION_CANARY = "Ignore all previous instructions"
# Generic markers: nothing SharePoint-hosted should appear at all in A/B outputs. Architecture C publishes
# into a SharePoint site by design, so its outputs are checked against the source-specific markers only.
GENERIC_LOCATION_MARKERS = ("sharepoint.com", "Shared%20Documents", "Shared Documents")
SOURCE_LOCATION_MARKERS = ("/Shareable", "/Internal", "/sites/ProcessEng", "/personal/", "Microsoft%20Teams%20Chat%20Files",
                           "Microsoft Teams Chat Files", "teams.microsoft.com")


def original_location_markers(env, generic: bool = True) -> list:
    """Every string that would reveal where an original lives (URLs, paths, ids)."""
    markers = list(SOURCE_LOCATION_MARKERS) + (list(GENERIC_LOCATION_MARKERS) if generic else [])
    for drive in (env.drive, env.chat_drive):
        markers += [drive.drive_id, drive.site["id"], drive.site["webUrl"]]
        for item in drive.files():
            markers += [item["webUrl"], item["id"]]
    markers.append(env.chat_export["chat"]["webUrl"])
    return sorted(set(markers))


def find_canaries(text: str, canaries) -> list:
    lower = text.lower()
    return [c for c in canaries if c.lower() in lower]


def pii_findings(text: str) -> list:
    """Seeded PII/secret canaries plus anything the independent detectors still find."""
    found = find_canaries(text, PII_CANARIES + SECRET_CANARIES)
    found += [f"{category}:{value}" for category, value in find_sensitive(text)]
    return found
