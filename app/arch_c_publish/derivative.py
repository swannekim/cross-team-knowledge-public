"""Knowledge card generator: Markdown with a provenance header, summary, key facts and a capped redacted excerpt.

The card is the only artefact Team B ever sees. It carries an opaque source reference and a broker
access-request link - never the original URL, path or driveItem id.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from common.clock import to_iso
from common.contract import SharingContract
from common.injection import neutralise
from common.redaction import redact
from common.refs import TEST_REF_KEY, access_request_url, opaque_ref
from common.sources import SourceDocument
from common.summarizer import key_facts, select_excerpt, summarize
from common.text import truncate

DERIVED_NOTICE = "Derived content – original not shared"
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)


@dataclass
class KnowledgeCard:
    file_name: str
    markdown: str
    metadata: dict


def _slug(text: str, limit: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit].rstrip("-") or "card"


def parse_front_matter(markdown: str) -> dict:
    match = _FRONT_MATTER.match(markdown)
    if not match:
        return {}
    out = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(": ")
        out[key.strip()] = json.loads(value)
    return out


def build_knowledge_card(doc: SourceDocument, contract: SharingContract, *, approval_id: str, generated_at,
                         compliance_approval_id: str = None, card_version: int = 1,
                         ref_key: bytes = TEST_REF_KEY) -> KnowledgeCard:
    ref = opaque_ref(doc.source_id, ref_key)
    neutral = neutralise(doc.text)
    red = redact(neutral.text)
    title = redact(neutralise(doc.title).text).text.strip() or "Untitled"
    has_summary = "summary" in contract.derivativeTypes
    summary = summarize(red.text, max_sentences=4, max_chars=800, exclude=(title,)) if has_summary else ""
    facts = key_facts(red.text, max_facts=5, exclude=(title,)) if has_summary else []
    excerpt = ""
    if "redactedExtract" in contract.derivativeTypes:
        excerpt = truncate(select_excerpt(red.text, contract.maxExcerptChars, exclude=(title,)), contract.maxExcerptChars)
    expires_at = contract.derivative_valid_until(generated_at)
    metadata = {
        "derivedContent": True,
        "notice": DERIVED_NOTICE,
        "title": title,
        "sourceTeam": contract.sourceTeam,
        "sourceRef": ref,
        "sourceFingerprint": doc.fingerprint,
        "approvalId": approval_id,
        "complianceApprovalId": compliance_approval_id,
        "contractId": contract.contractId,
        "purpose": contract.purpose,
        "sensitivity": doc.label,
        "generatedAt": to_iso(generated_at),
        "expiresAt": to_iso(expires_at),
        "cardVersion": card_version,
        "injectionFlags": len(neutral.flags),
        "redactions": dict(red.counts),
    }
    header = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in metadata.items())
    rows = [("Notice", f"**{DERIVED_NOTICE}**"), ("Source team", contract.sourceTeam), ("Source reference", f"`{ref}` (opaque)"),
            ("Source fingerprint", f"`{doc.fingerprint}`"), ("Approval ID", approval_id),
            ("Compliance approval ID", compliance_approval_id or "not required"), ("Sharing contract", contract.contractId),
            ("Purpose", contract.purpose), ("Sensitivity", doc.label), ("Generated at", metadata["generatedAt"]),
            ("Expires at", metadata["expiresAt"])]
    lines = [
        "---", header, "---", "",
        f"# Knowledge card: {title}", "",
        f"> **{DERIVED_NOTICE}.** Generated from {contract.sourceTeam} material under sharing contract "
        f"{contract.contractId} (approval {approval_id}). To request the original, use: {access_request_url(ref)}", "",
        "| Provenance | Value |", "|---|---|", *[f"| {k} | {v} |" for k, v in rows], "",
    ]
    if summary:
        lines += ["## Summary", "", summary, ""]
    if facts:
        lines += ["## Key facts", "", *[f"- {fact}" for fact in facts], ""]
    if excerpt:
        lines += [f"## Redacted excerpt (max {contract.maxExcerptChars} characters)", "", f"> {excerpt}", ""]
    lines += ["---", "*Generated from approved source content by the Knowledge Exchange workflow. Do not edit; "
              "changes to the source make this card stale until it is re-approved.*", ""]
    file_name = f"KC-{_slug(title)}-{ref[4:12]}.md"
    return KnowledgeCard(file_name=file_name, markdown="\n".join(lines), metadata=metadata)
