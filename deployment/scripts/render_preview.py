"""Render frozen payload bytes/hashes with synthetic-demo evidence boundaries.

The published flag labels operator-supplied publication status; this renderer does
not query Graph, test effective user permissions or verify Copilot grounding.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--published", action="store_true")
    args = parser.parse_args()
    raw = (args.plan / "manifest.json").read_bytes()
    manifest = json.loads(raw)
    lines = [
        "# Synthetic publication preview", "",
        ("Status: PUBLISHED after the user's 7 October 2026, 15:02 KST approval. "
         "Lifecycle re-publication preserved these exact output bytes."
         if args.published else "Status: NOT PUBLISHED. Approval applies only to the exact hashes below."), "",
        "Recipients: Example-Readers (demo administrator and newly created synthetic reader). "
        "The configured Exchange library-root grant is read; the administrator owns the site. "
        "The synthetic reader was removed from the edit-granting Microsoft 365 group, and the outsider "
        "has no audience-group membership. These configuration observations do not establish effective "
        "reader read-only or outsider denial. No external invitations are sent.", "",
        "Destination A: connector ExampleDerived. Destination C: "
        "https://sharepoint.example.invalid/sites/Example-Exchange.", "",
        "The text is derived only from the fictional Contoso fixtures supplied in this chat. "
        "This is synthetic-demo operator approval, not customer data-owner or compliance approval.", "",
        "Fenced payloads and hashes below are frozen review evidence, not editable examples. "
        "Rendering verifies local hashes only; consult reports/A_connector.md and reports/C_publishing.md for actual "
        "publication, known-pattern scans, signed-in access and Copilot results.", "",
        f"Plan SHA-256: `{hashlib.sha256(raw).hexdigest()}`", "",
        f"Created: `{manifest['createdAt']}`. Expires: `{manifest['outputs'][0]['expiresAt']}`.", "",
        "The expiry field is not service-enforced TTL. The operator must run the supplied sweep; "
        "copies, audit records and chat history cannot be recalled.", "",
    ]
    for output in manifest["outputs"]:
        name = output["file"]
        if Path(name).name != name:
            raise ValueError("Unsafe output filename")
        content = (args.plan / name).read_bytes()
        if hashlib.sha256(content).hexdigest() != output["sha256"]:
            raise ValueError(f"Reviewed file hash mismatch: {name}")
        text = content.decode("utf-8")
        fence = "````" if "```" in text else "```"
        language = "json" if output["architecture"] == "A" else "text"
        lines.extend([
            f"## {output['architecture']} - {name}", "",
            f"SHA-256: `{output['sha256']}`", "", fence + language,
            text.rstrip("\n"), fence, "",
        ])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Exact publication preview: {args.out}; {len(manifest['outputs'])} hash-verified files")


if __name__ == "__main__":
    main()
