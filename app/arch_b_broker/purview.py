"""Local policy-hook interface, test doubles and a proposed Purview request shape.

The baseline broker calls process_content for prompts (uploadText) and responses
(downloadText) that reach those stages. A marker-based mock block withholds output;
it is not a real Purview policy decision. Neither client below makes a Graph call.
live.py rejects startup unless disabled-demo is explicit and reports not evaluated;
no live Purview adapter is shipped.

Target integration (verify against current docs, consent and policy): Microsoft Graph
``POST /users/{userId}/dataSecurityAndGovernance/processContent`` with ``contentToProcess`` (content entries,
``activityMetadata.activity``, device / integrated-app metadata). The response carries ``policyActions`` such as
``{"@odata.type": "#microsoft.graph.restrictAccessAction", "action": "restrictAccess", "restrictionAction": "block"}``;
``protectionScopes/compute`` can be used to decide when content must be sent. Only the request *shape* is built here.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Protocol

from common.clock import SystemClock, to_iso

from .constants import PURVIEW_ACTIVITY_PROMPT, PURVIEW_ACTIVITY_RESPONSE, PURVIEW_PROCESS_CONTENT_PATH

ACTIVITIES = (PURVIEW_ACTIVITY_PROMPT, PURVIEW_ACTIVITY_RESPONSE)


@dataclass(frozen=True)
class PurviewDecision:
    action: str                       # "allow" | "block"
    activity: str
    policy_actions: tuple = ()
    reason: str = ""

    @property
    def blocked(self) -> bool:
        return self.action == "block"


class PurviewClient(Protocol):
    def process_content(self, user, text: str, activity: str) -> PurviewDecision: ...


def build_process_content_request(user, text: str, activity: str, *, correlation_id: str, now) -> dict:
    """Graph processContent request as data (never sent by the prototype)."""
    if activity not in ACTIVITIES:
        raise ValueError(f"unsupported activity {activity!r}")
    return {
        "method": "POST",
        "url": "https://graph.microsoft.com/v1.0" + PURVIEW_PROCESS_CONTENT_PATH.format(userId=user.oid),
        "body": {"contentToProcess": {
            "contentEntries": [{
                "@odata.type": "microsoft.graph.processConversationMetadata",
                "identifier": correlation_id, "name": f"Knowledge Broker {activity}",
                "correlationId": correlation_id, "sequenceNumber": 0, "isTruncated": False,
                "createdDateTime": to_iso(now), "modifiedDateTime": to_iso(now),
                "content": {"@odata.type": "microsoft.graph.textContent", "data": text},
            }],
            "activityMetadata": {"activity": activity},
            "integratedAppMetadata": {"name": "Contoso Knowledge Broker", "version": "0.1.0"},
        }},
    }


class AllowAllPurviewClient:
    """Baseline no-policy test double; allows calls without evaluating Purview.

    The live entry point uses an explicit disabled-demo adapter instead. This class
    must not be presented as a successful or fail-closed live Purview integration.
    """

    def __init__(self):
        self.calls: list = []

    def process_content(self, user, text: str, activity: str) -> PurviewDecision:
        if activity not in ACTIVITIES:
            raise ValueError(f"unsupported activity {activity!r}")
        self.calls.append({"user": user.oid, "activity": activity, "chars": len(text)})
        return PurviewDecision("allow", activity)


@dataclass
class MockPurviewClient:
    """Test double: returns 'block' when the text contains a configured marker (optionally per activity)."""

    block_markers: tuple = ("PURVIEW-TEST-BLOCK",)
    activities: tuple = ACTIVITIES
    clock: object = field(default_factory=SystemClock)
    calls: list = field(default_factory=list)

    def process_content(self, user, text: str, activity: str) -> PurviewDecision:
        request = build_process_content_request(user, text, activity, now=self.clock.now(),
                                                correlation_id=hashlib.sha256(text.encode()).hexdigest()[:16])
        lowered = text.lower()
        hit = next((m for m in self.block_markers if activity in self.activities and m.lower() in lowered), None)
        self.calls.append({"user": user.oid, "activity": activity, "chars": len(text), "blocked": bool(hit),
                           "request": request})
        if hit:
            action = {"@odata.type": "#microsoft.graph.restrictAccessAction", "action": "restrictAccess",
                      "restrictionAction": "block"}
            return PurviewDecision("block", activity, (action,), "matched DLP policy marker")
        return PurviewDecision("allow", activity)
