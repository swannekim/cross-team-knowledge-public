# Cross-team knowledge without original-file access

> **Sanitized public-release copy — historical evidence, not new live tests.** Tenant/account/resource identifiers are placeholders; screenshots are labeled sanitized copies with opaque redactions where needed. Original private evidence is retained separately. Pass/fail/blocked/not-run distinctions are preserved. Historical approvals, deadlines and active-state statements describe their recorded checkpoint only, not current status or permission to act. Sanitized artifacts cannot establish the original cryptographic hashes.


**Summary | 8 October 2026 | Synthetic demo in `example.invalid`**

## Conclusion

**Architecture A works for the tested use case.** Reader, an ordinary licensed user, received the correct Copilot answer from an approved connector summary while the original file's metadata and listing APIs returned **403**. The actual clicked citation matched the connector item. Outsider, outside the approved audience, received no evidence or answer in the tested Copilot scenario; separate scoped Search calls returned zero results.

This is **approved sharing of derived content**, not a permissions bypass. Work IQ uses delegated user access and cannot retrieve inaccessible originals. There is no general "upload an index to Work IQ" API.

## Three architectures and actual results

| Architecture | Implementation | Observed outcome |
|---|---|---|
| **A. Connector-derived index** | Six approved summaries indexed in `ExampleDerived`, with a Readers-group ACL; originals unchanged. | Administrator and ordinary-reader Copilot grounding passed with real connector citations. Named outsider checks returned no evidence. **Recommended next-stage design.** |
| **B. Knowledge broker** | Azure-hosted Entra-authenticated API, current group checks, capped BM25 extracts, and Blob-backed policy state. | Administrator Copilot SSO and a maintenance answer passed; reader/outsider API checks passed. Wet-clean answer failed. Ordinary-user Copilot installation remained blocked: both users' existing-app catalog lookups returned 404, so no installation POST was sent. |
| **C. Approved SharePoint cards** | Six approved TXT cards published to a separate Exchange site. | Administrator Copilot grounding passed. Reader could list the cards and later find a scoped Search hit, but three SharePoint-only Copilot attempts failed to retrieve the card. |

**Important boundary:** A's success does not establish production-wide security, immediate revocation, guest isolation, expiry removal, or live Purview enforcement. The owner must approve both the derivative content and its audience. Previously disclosed text and conversation history cannot be recalled.

## Evidence and implementation changes

The preserved offline baseline contains **104 passing synthetic tests**. The expanded Python suite last recorded **211 passing tests**; neither count is live-service evidence. Architecture-specific reports preserve successful, failed, blocked and unrun cases, with request IDs, raw evidence and screenshots.

Actual implementation required enabling connector Copilot visibility, configuring a real broker SSO registration, and replacing failed SQLite-on-Azure-Files persistence with leased Azure Blob checkpoints. The live demo uses manual snapshots and deterministic summaries/extracts, **not Azure OpenAI, Azure AI Search or evaluated Purview controls**.

## Operational limits

Content approval expires **8 October 2026, 14:59:02 KST**. B fails closed at snapshot expiry; A/C require an operator-run sweep. Temporary app-install consent was removed at **08:21:38 KST**; separate two-user MFA exceptions remain scheduled for restoration at **20:43 KST**. Original ACLs were not widened.

## Read next

[Technical architecture](01_Technical_Architecture.md) · [Test report and remaining plan](02_Test_Report_and_PoC_Plan.md) · [Setup and commands](../README.md)

[A: connector results](../results/live/reports/A_connector.md) · [B: broker results](../results/live/reports/B_broker.md) · [C: publishing results](../results/live/reports/C_publishing.md)

Intended public project (publication pending): [swannekim/cross-team-knowledge-public](https://github.com/swannekim/cross-team-knowledge-public).
