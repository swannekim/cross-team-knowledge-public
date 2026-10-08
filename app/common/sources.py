"""Source documents: text extraction from drive items and Teams chat exports, plus environment loading.

A SourceDocument holds *internal* provenance (path, webUrl, driveItem id). These fields are used
for policy decisions and audit only and must never be copied into anything Team B can see.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Optional

from . import paths
from .clock import parse_iso
from .contract import SharingContract, load_contract
from .directory import Directory
from .drive_sim import SimulatedDrive, crawl
from .fingerprint import canonical_json, source_fingerprint
from .htmltext import html_title, html_to_text
from .policy import PolicyGate


@dataclass
class SourceDocument:
    source_id: str
    kind: str                # "file" | "chatDigest" | "chatFile"
    name: str
    title: str
    path: str                # internal only
    web_url: str             # internal only
    label: Optional[str]
    etag: str
    last_modified: str
    text: str                # extracted plain text: untrusted and not yet redacted
    fingerprint: str
    container: str
    chat_id: Optional[str] = None
    meta: dict = field(default_factory=dict)
    label_id: Optional[str] = None   # Purview sensitivity label GUID


_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")


def _markdown_to_text(raw: str) -> str:
    lines = []
    for line in raw.splitlines():
        heading = _MD_HEADING.match(line)
        if heading:
            line = heading.group(1).strip()
        line = re.sub(r"(\*\*|__|`)", "", line)
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def extract_text(name: str, data: bytes) -> tuple:
    """Returns (plain_text, title) for txt / md / html content."""
    raw = data.decode("utf-8", errors="replace").replace("\r\n", "\n")
    suffix = PurePosixPath(name).suffix.lower()
    title = None
    if suffix in (".html", ".htm"):
        text = html_to_text(raw)
        title = html_title(raw)
    elif suffix == ".md":
        for line in raw.splitlines():
            heading = _MD_HEADING.match(line)
            if heading:
                title = heading.group(1).strip()
                break
        text = _markdown_to_text(raw)
    else:
        text = raw.strip()
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        title = first if 0 < len(first) <= 120 else None
    if not title:
        title = PurePosixPath(name).stem.replace("_", " ")
    return text, title


def document_from_drive_item(drive: SimulatedDrive, item: dict, *, kind: str = "file",
                             chat_id: Optional[str] = None) -> SourceDocument:
    data = drive.get_content(item["id"])
    text, title = extract_text(item["name"], data)
    return SourceDocument(
        source_id=item["id"], kind=kind, name=item["name"], title=title, path=SimulatedDrive.item_path(item),
        web_url=item.get("webUrl", ""), label=drive.label_of(item["id"]), etag=item["eTag"],
        last_modified=item["lastModifiedDateTime"], text=text, fingerprint=source_fingerprint(data, item["eTag"]),
        container=drive.site.get("displayName", ""), chat_id=chat_id, label_id=drive.label_id_of(item["id"]),
    )


# ---------------------------------------------------------------------------- Teams chat
def _chat_messages(chat_export: dict) -> list:
    return [m for m in chat_export.get("messages", [])
            if m.get("messageType", "message") == "message" and not m.get("deletedDateTime") and m.get("from")]


def chat_digest_documents(chat_export: dict, label: str, label_id: Optional[str] = None) -> list:
    """One derived 'digest' document per chat per UTC day. Authors are pseudonymised (Participant N)."""
    chat = chat_export["chat"]
    participants: dict = {}
    by_day: dict = {}
    for message in _chat_messages(chat_export):
        author = message["from"]["user"]["id"]
        participants.setdefault(author, f"Participant {len(participants) + 1}")
        by_day.setdefault(message["createdDateTime"][:10], []).append(message)
    names = {}
    for member in chat.get("members", []):
        if member.get("userId") in participants and member.get("displayName"):
            names[member["displayName"]] = participants[member["userId"]]
    docs = []
    for day, messages in sorted(by_day.items()):
        lines, fingerprint_basis = [], []
        for message in sorted(messages, key=lambda m: m["createdDateTime"]):
            body = message["body"]["content"]
            text = html_to_text(body) if message["body"].get("contentType") == "html" else body
            for mention in message.get("mentions", []):
                mentioned = (mention.get("mentioned") or {}).get("user") or {}
                alias = participants.get(mentioned.get("id"), "a colleague")
                text = text.replace(mention.get("mentionText", "\x00"), alias)
            for display_name, alias in names.items():
                text = text.replace(display_name, alias)
            text = " ".join(text.split())
            stamp = parse_iso(message["createdDateTime"]).strftime("%H:%M")
            lines.append(f"[{stamp} UTC] {participants[message['from']['user']['id']]}: {text}")
            fingerprint_basis.append([message["id"], message.get("lastModifiedDateTime"), body])
        etag = hashlib.sha256(canonical_json(fingerprint_basis).encode()).hexdigest()[:32]
        content = f"Teams chat digest: {chat.get('topic', 'group chat')} ({day})\n\n" + "\n".join(lines)
        docs.append(SourceDocument(
            source_id=f"chat:{chat['id']}:{day}", kind="chatDigest", name=f"chat-digest-{day}.txt",
            title=f"Teams chat digest: {chat.get('topic', 'group chat')} ({day})", path=f"/chats/{chat['id']}/{day}",
            web_url=chat.get("webUrl", ""), label=label, etag=etag,
            last_modified=max(m.get("lastModifiedDateTime") or m["createdDateTime"] for m in messages).replace(".000Z", "Z"),
            text=content, fingerprint=source_fingerprint(content, etag), container="Microsoft Teams chat",
            chat_id=chat["id"], meta={"messageCount": len(messages)}, label_id=label_id,
        ))
    return docs


def chat_file_items(chat_export: dict, chat_drive: SimulatedDrive) -> list:
    """Resolves 'reference' attachments to driveItems in the sender's OneDrive (Microsoft Teams Chat Files)."""
    items, seen = [], set()
    for message in _chat_messages(chat_export):
        for attachment in message.get("attachments", []):
            if attachment.get("contentType") != "reference":
                continue
            item = chat_drive.find_by_web_url(attachment.get("contentUrl", "")) or chat_drive.find_by_name(attachment.get("name", ""))
            if item and item["id"] not in seen:
                seen.add(item["id"])
                items.append(item)
    return items


def collect_chat_documents(chat_export: dict, chat_drive: Optional[SimulatedDrive], gate: PolicyGate) -> tuple:
    chat_id = chat_export["chat"]["id"]
    decision = gate.check_chat(chat_id)
    if not decision.allowed:
        return [], [(f"chat:{chat_id}", decision.reason)]
    label = gate.contract.chat_source(chat_id).get("label", "Confidential")
    docs = chat_digest_documents(chat_export, label, chat_drive.label_id(label) if chat_drive is not None else None)
    exclusions = []
    if chat_drive is not None:
        for item in chat_file_items(chat_export, chat_drive):
            file_decision = gate.check_chat_file(chat_id, label=chat_drive.label_of(item["id"]), name=item["name"])
            if file_decision.allowed:
                docs.append(document_from_drive_item(chat_drive, item, kind="chatFile", chat_id=chat_id))
            else:
                exclusions.append((item["id"], file_decision.reason))
    return docs, exclusions


# ---------------------------------------------------------------------------- full enumeration (B and C)
def collect_file_documents(drive: SimulatedDrive, gate: Optional[PolicyGate]) -> tuple:
    """Full crawl of a drive. With gate=None every supported file is returned (used to model an
    over-broad index in tests); otherwise only contract-compliant files are returned."""
    items, _ = crawl(drive)
    docs, exclusions = [], []
    for item in items:
        if "file" not in item:
            continue
        if gate is not None:
            decision = gate.check_file(path=SimulatedDrive.item_path(item), label=drive.label_of(item["id"]),
                                       name=item["name"])
            if not decision.allowed:
                exclusions.append((item["id"], decision.reason))
                continue
        docs.append(document_from_drive_item(drive, item))
    return docs, exclusions


@dataclass
class Environment:
    contract: SharingContract
    directory: Directory
    drive: SimulatedDrive
    chat_export: dict
    chat_drive: SimulatedDrive


def load_environment(clock=None) -> Environment:
    """Fresh, isolated copies of all synthetic inputs (tests may mutate them freely)."""
    return Environment(
        contract=load_contract(paths.CONTRACT_FILE),
        directory=Directory.load(paths.DIRECTORY_FILE),
        drive=SimulatedDrive.from_manifest_file(paths.LIBRARY_DIR / "manifest.json", clock),
        chat_export=json.loads(paths.CHAT_EXPORT_FILE.read_text(encoding="utf-8")),
        chat_drive=SimulatedDrive.from_manifest_file(paths.CHAT_ONEDRIVE_DIR / "manifest.json", clock),
    )


def all_source_documents(env: Environment) -> list:
    """Every source document regardless of policy (used by tests/benchmark as ground truth)."""
    docs, _ = collect_file_documents(env.drive, None)
    for item in env.chat_drive.files():
        docs.append(document_from_drive_item(env.chat_drive, item, kind="chatFile", chat_id=env.chat_export["chat"]["id"]))
    docs.extend(chat_digest_documents(env.chat_export, "Confidential", env.drive.label_id("Confidential")))
    return docs
