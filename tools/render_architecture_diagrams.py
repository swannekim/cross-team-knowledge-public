"""Render the documented synthetic-demo topology as SVG and editable Excalidraw."""
from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "diagrams"
COLORS = {
    "source": ("#0078D4", "#CFE4FA"),
    "process": ("#C75B00", "#FFF4CE"),
    "index": ("#5C2D91", "#E8DAEF"),
    "consumer": ("#0C8599", "#DFF4F5"),
    "neutral": ("#495057", "#F3F2F1"),
}


class Diagram:
    def __init__(self, name: str, title: str, height: int = 1060):
        self.name, self.height = name, height
        self.elements = []
        self.svg = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="1460" height="{height}" '
            f'viewBox="0 0 1460 {height}" role="img" aria-labelledby="title description">',
            f"<title id=\"title\">{html.escape(title)}</title>",
            "<desc id=\"description\">Implemented synthetic demo, 7-8 October 2026. "
            "Not a production target or a new test result.</desc>",
            '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
            '<path d="M 0 0 L 10 5 L 0 10 z" fill="#495057"/></marker></defs>',
            f'<rect width="1460" height="{height}" fill="#ffffff"/>',
        ]
        self.text(40, 36, [title], size=34, bold=True, width=1380)
        self.text(40, 94, [
            "Implemented synthetic demo | 7-8 October 2026 | Original-file permissions are not widened"
        ], size=19, width=1380)

    def element(self, kind: str, x: int, y: int, width: int, height: int, **fields):
        index = len(self.elements)
        seed = int(hashlib.sha256(f"{self.name}:{index}".encode()).hexdigest()[:7], 16)
        item = {
            "id": f"{self.name}-{index}", "type": kind, "x": x, "y": y,
            "width": width, "height": height, "angle": 0, "strokeColor": "#495057",
            "backgroundColor": "transparent", "fillStyle": "solid", "strokeWidth": 2,
            "strokeStyle": "solid", "roughness": 0, "opacity": 100, "groupIds": [],
            "frameId": None, "roundness": None, "seed": seed, "version": 1,
            "versionNonce": seed, "isDeleted": False, "boundElements": None,
            "updated": 0, "link": None, "locked": False, **fields,
        }
        self.elements.append(item)
        return item

    def text(self, x, y, lines, *, size=19, bold=False, width=320):
        value = "\n".join(lines)
        self.element(
            "text", x, y, width, int(size * 2.5 * len(lines)),
            text=value, originalText=value, fontSize=size, fontFamily=2,
            textAlign="left", verticalAlign="top", containerId=None,
            autoResize=True, lineHeight=1.4, strokeColor="#000000",
        )
        weight = "700" if bold else "400"
        self.svg.append(
            f'<text x="{x}" y="{y}" font-family="Arial, Helvetica, sans-serif" '
            f'font-size="{size}" font-weight="{weight}" fill="#000000" '
            f'dominant-baseline="text-before-edge" data-max-width="{width}">'
        )
        for index, line in enumerate(lines):
            self.svg.append(
                f'<tspan x="{x}" dy="{0 if index == 0 else size * 1.4}">'
                f'{html.escape(line)}</tspan>'
            )
        self.svg.append("</text>")

    def box(self, x, y, title, lines, *, style="source", width=360, height=230):
        stroke, fill = COLORS[style]
        self.element("rectangle", x, y, width, height, strokeColor=stroke,
                     backgroundColor=fill, roundness={"type": 3})
        self.svg.append(
            f'<g data-node="{html.escape(title)}">'
            f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="12" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
        )
        self.text(x + 20, y + 20, [title], size=23, bold=True, width=width - 40)
        self.text(x + 20, y + 66, lines, width=width - 40)
        self.svg.append("</g>")

    def arrow(self, points, *, dashed=False, both=False):
        x, y = points[0]
        relative = [[px - x, py - y] for px, py in points]
        self.element(
            "arrow", x, y, max(px for px, _ in points) - min(px for px, _ in points),
            max(py for _, py in points) - min(py for _, py in points),
            points=relative, startBinding=None, endBinding=None,
            startArrowhead="arrow" if both else None, endArrowhead="arrow",
            strokeStyle="dashed" if dashed else "solid", elbowed=False,
        )
        dash = ' stroke-dasharray="8 6"' if dashed else ""
        start = ' marker-start="url(#arrow)"' if both else ""
        pairs = " ".join(f"{px},{py}" for px, py in points)
        self.svg.append(
            f'<polyline points="{pairs}" fill="none" stroke="#495057" stroke-width="2.5" '
            f'marker-end="url(#arrow)"{start}{dash}/>'
        )

    def footer(self, y, lines):
        self.box(40, y, "Evidence boundary", lines, style="neutral", width=1380, height=135)

    def save(self):
        OUT.mkdir(parents=True, exist_ok=True)
        self.svg.append("</svg>")
        (OUT / f"{self.name}.svg").write_text("\n".join(self.svg) + "\n", encoding="utf-8")
        (OUT / f"{self.name}.excalidraw").write_text(json.dumps({
            "type": "excalidraw", "version": 2, "source": "cross-team-knowledge",
            "elements": self.elements,
            "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None},
            "files": {},
        }, indent=2) + "\n", encoding="utf-8")


def connector():
    d = Diagram("architecture-a-connector", "A | Connector-derived index")
    d.box(40, 190, "Private source site", [
        "Example-Source", "10 fictional fixture files",
        "Pipeline: Sites.Selected READ", "No user grant to originals",
    ])
    d.box(550, 190, "Derive + approve", [
        "Operator-run Python pipeline", "Eligibility / exact-hash checks",
        "Deterministic L1 summaries", "Approved bytes + audience",
    ], style="process")
    d.box(1060, 190, "Connector index", [
        "ExampleDerived", "6 approved summaries",
        "Per-item ACL: KX Readers", "Copilot visibility enabled",
    ], style="index")
    d.arrow([(400, 305), (550, 305)])
    d.text(418, 241, ["App-only", "source read"], size=17, width=130)
    d.arrow([(910, 305), (1060, 305)])
    d.text(932, 241, ["Publish", "summaries"], size=17, width=125)
    d.box(40, 590, "Outside the audience", [
        "Outsider: no KX Readers grant", "Scoped Search: 0 hits",
        "Joint A/C Copilot query:", "no evidence or citations",
    ], style="neutral", height=230)
    d.box(550, 590, "Authorized reader", [
        "Reader: KX Readers member", "Original metadata / list: 403",
        "Approved derivative answer:", "correct facts + A citation",
    ], style="consumer")
    d.box(1060, 590, "Microsoft 365 Copilot", [
        "Native connector grounding", "Delegated user access",
        "Admin and Reader answers tested", "Actual A citation verified",
    ], style="consumer")
    d.arrow([(910, 705), (1060, 705)])
    d.text(930, 643, ["Delegated", "query"], size=17, width=125)
    d.arrow([(1240, 590), (1240, 420)], both=True)
    d.text(1260, 480, ["ACL-trimmed", "retrieval"], size=17, width=160)
    d.arrow([(730, 590), (730, 505), (220, 505), (220, 420)], dashed=True)
    d.text(300, 460, ["Original-file APIs denied (403)"], size=18, width=400)
    d.footer(885, [
        "Only approved derivatives receive a new audience ACL. This is not a Work IQ permission bypass.",
        "Guest controls, revocation/expiry propagation and live Purview enforcement remain unverified.",
    ])
    d.save()


def broker():
    d = Diagram("architecture-b-broker", "B | Policy-enforcing knowledge broker", height=1250)
    d.box(40, 165, "Private source site", [
        "Synthetic SharePoint files", "Pipeline: selected-site READ",
        "User original-file access denied",
    ], height=190)
    d.box(550, 165, "Manual snapshot capture", [
        "Operator-run Graph ingestion", "Pinned synthetic source content",
        "No continuous ingestion",
    ], style="process", height=190)
    d.box(1060, 165, "Broker source snapshot", [
        "6 source documents + contract", "Capture time and expiry bound",
        "Private container-image input",
    ], style="index", height=190)
    d.arrow([(400, 260), (550, 260)])
    d.text(416, 196, ["App-only", "read"], size=17, width=130)
    d.arrow([(910, 260), (1060, 260)])
    d.text(925, 196, ["Prepare", "snapshot"], size=17, width=130)
    d.box(40, 510, "Clients and actual coverage", [
        "Admin: personal Copilot agent", "Entra SSO / OpenAPI /ask",
        "Reader / Outsider: API tests only", "Their Copilot install: blocked",
    ], style="consumer", height=245)
    d.box(550, 510, "Azure Container App", [
        "RS256 + delegated scope", "Live Graph identity / group gate",
        "Purpose + freshness + caps", "BM25 deterministic extracts",
    ], style="process", height=245)
    d.box(1060, 510, "Policy-controlled response", [
        "Capped text + opaque citations", "or denial / safe abstention",
        "Admin PM answer: correct", "Wet-clean fact: not returned",
    ], style="consumer", height=245)
    d.arrow([(400, 633), (550, 633)])
    d.text(418, 569, ["User token", "POST /ask"], size=17, width=130)
    d.arrow([(910, 633), (1060, 633)])
    d.text(932, 569, ["Answer", "or refusal"], size=17, width=125)
    d.arrow([(1240, 355), (1240, 425), (730, 425), (730, 510)])
    d.text(815, 387, ["Load bounded snapshot"], size=18, width=360)
    d.box(550, 900, "Microsoft Graph", [
        "Managed identity calls", "Current user + membership",
    ], style="source", height=155)
    d.box(1060, 900, "SQLite + private Azure Blob", [
        "Exclusive lease + ETag", "Rate / coverage / audit",
        "Checkpoint before response",
    ], style="index", height=185)
    d.arrow([(680, 755), (680, 900)], both=True)
    d.text(520, 813, ["Identity /", "audience check"], size=17, width=160)
    d.arrow([(800, 755), (800, 825), (1240, 825), (1240, 900)], both=True)
    d.text(916, 786, ["Persist policy state"], size=18, width=310)
    d.footer(1105, [
        "No live Purview, Azure AI Search or Azure OpenAI. This diagram does not claim ordinary-user Copilot success.",
        "Snapshot expiry blocks new retrieval; previously returned text and conversation history are not recalled.",
    ])
    d.save()


def publishing():
    d = Diagram("architecture-c-publishing", "C | Approved SharePoint knowledge cards")
    d.box(40, 190, "Private source site", [
        "Example-Source", "Synthetic source files",
        "Pipeline: Sites.Selected READ", "No user grant to originals",
    ])
    d.box(550, 190, "Derive + approve", [
        "Operator-run Python pipeline", "Eligibility / exact-hash checks",
        "6 approved TXT cards", "Approved bytes + audience",
    ], style="process")
    d.box(1060, 190, "SharePoint Exchange site", [
        "Example-Exchange", "Pipeline: selected-site WRITE",
        "Library-root READ: KX Readers", "Provenance / expiry columns",
    ], style="index")
    d.arrow([(400, 305), (550, 305)])
    d.text(418, 241, ["App-only", "source read"], size=17, width=130)
    d.arrow([(910, 305), (1060, 305)])
    d.text(932, 241, ["Publish", "6 cards"], size=17, width=125)
    d.box(40, 590, "Outside the audience", [
        "Outsider: no KX Readers grant", "Exchange API: 403",
        "Scoped Search: 0 hits", "Joint A/C query: no evidence",
    ], style="neutral")
    d.box(550, 590, "Authorized reader", [
        "Reader: KX Readers member", "Lists 6 cards; later Search hit",
        "Original metadata / list: 403", "No original-file permission",
    ], style="consumer")
    d.box(1060, 590, "Native SharePoint grounding", [
        "Microsoft 365 Copilot", "Admin: cited answer passed",
        "Reader: 3 SharePoint-only", "retrieval attempts failed",
    ], style="consumer")
    d.arrow([(910, 705), (1060, 705)])
    d.text(930, 643, ["Delegated", "query"], size=17, width=125)
    d.arrow([(1240, 590), (1240, 420)], dashed=True, both=True)
    d.text(1260, 473, ["Native", "retrieval:", "mixed results"], size=17, width=160)
    d.arrow([(730, 590), (730, 505), (220, 505), (220, 420)], dashed=True)
    d.text(300, 460, ["Original-file APIs denied (403)"], size=18, width=400)
    d.footer(885, [
        "Card listing or a Search hit does not establish a Copilot answer. Reader edit denial remains untested.",
        "Custom metadata is not MIP/DLP/retention enforcement. A/C expiry removal requires an operator-run sweep.",
    ])
    d.save()


if __name__ == "__main__":
    connector()
    broker()
    publishing()
    print(f"Created three SVG diagrams and editable Excalidraw sources in {OUT}")
