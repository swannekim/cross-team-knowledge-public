# Cross-team knowledge without original-file access

**Summary | Demo results recorded 7–8 October 2026**

> Synthetic data; identifiers and screenshots are sanitized. [Publication details](../SANITIZATION_MANIFEST.json).

## Conclusion

**Use A as the starting point for the next PoC.** An authorized ordinary user received a correct, cited Copilot answer from an approved connector summary while original-file metadata/listing requests returned **403**. The outsider received no answer in one combined A/C query; separate scoped Search calls returned zero results.

This is **approved sharing of derived content**, not a permissions bypass. Work IQ uses delegated user access and cannot retrieve inaccessible originals. There is no general "upload an index to Work IQ" API.

## Three architectures and actual results

| Architecture | Implementation | Observed outcome |
|---|---|---|
| **A. Connector-derived index** | Six approved summaries with audience-group ACLs. | Administrator and ordinary reader received correct answers with connector citations. |
| **B. Knowledge broker** | Entra-authenticated Azure API, group checks, capped search extracts and Blob-backed state. | Admin answered a maintenance question, but not a chamber-cleaning question. Direct APIs admitted the reader and denied the outsider. Ordinary-user Copilot was not run because agent installation was blocked. |
| **C. Approved SharePoint cards** | Six approved TXT cards published to a separate Exchange site. | Administrator Copilot grounding passed. Reader could list the cards and later find a scoped Search hit, but three SharePoint-only Copilot attempts failed to retrieve the card. |

## Scope and next step

The demo used manual snapshots and deterministic summaries/extracts, not Azure OpenAI, Azure AI Search or live Purview.
The 104-test baseline and expanded 211-test suite are local checks, separate from these service observations.

For a customer PoC, approve the derivative content and audience, then validate guest access, effective permissions,
revocation/cache delay and expiry removal. A/C require an operator-run sweep; B rejects expired snapshots.
Previously disclosed answers and copies cannot be recalled. Detailed failures and recovery records remain in the reports below.

## Read next

[Technical architecture](01_Technical_Architecture.md) · [Test report and remaining plan](02_Test_Report_and_PoC_Plan.md) · [Setup and commands](../README.md)

[A: connector results](../results/live/reports/A_connector.md) · [B: broker results](../results/live/reports/B_broker.md) · [C: publishing results](../results/live/reports/C_publishing.md)

Public repository: [swannekim/cross-team-knowledge-public](https://github.com/swannekim/cross-team-knowledge-public).
