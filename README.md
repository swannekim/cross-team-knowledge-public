# Cross-team knowledge access without direct file access

> **Sanitized public-release copy — historical evidence, not new live tests.** Tenant/account/resource identifiers are placeholders; screenshots are labeled sanitized copies with opaque redactions where needed. Original private evidence is retained separately. Pass/fail/blocked/not-run distinctions are preserved. Historical approvals, deadlines and active-state statements describe their recorded checkpoint only, not current status or permission to act. Sanitized artifacts cannot establish the original cryptographic hashes.


Three ways to share approved knowledge in Microsoft 365 Copilot without granting original-file access:
**A — connector index**, **B — knowledge broker**, **C — SharePoint cards**.
Includes an offline synthetic prototype and a partial live demo, not a production deployment.

## Historical status and evidence

**Checkpoint: 8 October 2026, 08:24 KST. A worked for the tested ordinary reader.**
Reader's native Copilot answer returned **15 wet-clean wafers, <10 particles ≥0.12 µm per wafer, and etch rate within 3%**.
The actual clicked citation matched the approved A connector URL; Reader's original-source metadata/list APIs returned
403. No personal A agent was installed for Reader. Verified outsider Outsider received no facts or citations in one joint
A/C Copilot query. These cases do not establish general isolation or revocation.

| Architecture | Observed live result | Diagram | Report + screenshots |
|---|---|---|---|
| **A: connector index** | Six approved summaries published with Readers ACLs. Admin and Reader grounding/citation passed; Outsider scoped Search and joint A/C UI negative passed. Earlier failed attempts retained. | [A diagram](docs/01_Technical_Architecture.md#architecture-a) | [A visual evidence](results/live/reports/A_connector.md#visual-evidence) |
| **B: broker** | Admin SSO/PM and independent APIs passed; wet-clean failed. Both users' catalog preflights returned 404. No installation POST/install; **ordinary-user Copilot NOT RUN**. | [B diagram](docs/01_Technical_Architecture.md#architecture-b) | [B visual evidence](results/live/reports/B_broker.md#visual-evidence) |
| **C: SharePoint cards** | Six approved TXT cards published. Admin grounding passed; Reader could list cards and later find one Search hit, but **three C-only Copilot attempts failed**. Mixed-source Reader answer cited A, not C. | [C diagram](docs/01_Technical_Architecture.md#architecture-c) | [C visual evidence](results/live/reports/C_publishing.md#visual-evidence) |

Earlier API totals: **Outsider 7/7, Reader 7/10**. Later Reader: five Graph passes, then broker-token `AADSTS50076`;
suite incomplete, not 5/5 overall. Search-failure cause remains unknown. Catalog 404s do not prove global app absence
or broker denial. Timelines, request IDs and screenshots are in the reports.

### Lifecycle and remaining work at the recorded checkpoint

- **Recorded content expiry: 8 October 2026, 14:59:02 KST** (`2026-10-08T05:59:02Z`). A/C removal requires the manual
  sweep below; no content-cleanup scheduler exists. B fails closed at snapshot expiry but does not delete A/C.
- **Temporary install consent recorded restored at 08:21:38 KST on 8 October.** Fresh Graph readback found zero introduced Principal
  grants and unchanged baseline grants. The initial restore failure remains recorded; the 09:00 backup is verification
  only. Consent removal does not revoke issued tokens/sessions.
- **Separate two-user MFA exception was recorded active with an 8 October 20:43 KST restoration deadline**, with a private watchdog/backup.
  This does not extend content expiry. [Authentication record](results/live/test-authentication-exception.json).
- B installation fallback stopped. No new app ID, tenant publication, SSO broadening or policy/counter reset is approved.
  Reader edit denial, guest controls, measured revocation/index-cache removal, actual expiry and live governance remain open.

## Layout

| Path | Purpose |
|---|---|
| [docs/00_README.md](docs/00_README.md) | Short findings summary |
| [docs/01_Technical_Architecture.md](docs/01_Technical_Architecture.md) | Current implementation and target designs |
| [docs/02_Test_Report_and_PoC_Plan.md](docs/02_Test_Report_and_PoC_Plan.md) | Evidence and outstanding acceptance cases |
| `app/arch_a_connector/`, `app/arch_b_broker/`, `app/arch_c_publish/`, `app/common/` | Prototype and live adapters, with local tests |
| `app/live_poc/`, `deployment/scripts/` | Real Graph preparation/publication/lifecycle and deployment/validation scripts |
| `examples/data/`, `examples/artifacts/`, `app/arch_b_broker/manifests/` | Fictional fixtures and offline artifacts |
| `deployment/runtime/contract.json` | Sanitized historical configuration with inert placeholders; not current sharing approval |
| `deployment/runtime/snapshot.json`, `.local/`, local caches | Generated/private state; ignored by Git. Never commit tokens, credentials or consent backups |
| [results/offline-baseline/](results/offline-baseline/) | Preserved original test/benchmark evidence |
| [results/live/reports/](results/live/reports/), [PUBLICATION_PREVIEW.md](results/live/PUBLICATION_PREVIEW.md), [evidence/](results/live/evidence/), [screenshots/](results/live/screenshots/) | Per-architecture reports, approved payload record and sanitized live evidence |

Public-example state namespace: `%LOCALAPPDATA%\CrossTeamKnowledgePublicExample\operator-configured`;
it is deliberately separate from the original private deployment. No private state, certificate or credential is included.

## How to run (from this directory)

Use Windows Python from the repository root. Expanded adapter tests require
[deployment/requirements-live.txt](deployment/requirements-live.txt); install if imports are missing:

```powershell
$env:PYTHONPATH = (Resolve-Path .\app).Path
python -m pip install -r deployment\requirements-live.txt
python -m unittest discover -s app -t app -v
```

Live PowerShell scripts require PowerShell 7.5 or later; this demo used 7.6.6.

| Task | Command |
|---|---|
| Run suite and rewrite generated local-test reports | `python tools\run_tests.py` |
| Run synthetic benchmark | `python app\bench\run_benchmark.py` |
| Regenerate offline artifacts | `python tools\generate_artifacts.py` |
| Regenerate offline manifests after changing constants | `python -m arch_b_broker.manifest_builder` |
| Regenerate synthetic source data/manifests | `python examples\data\build_data.py` |

Tests use fake services/generated signing keys and repository-local `.scratch/`, not tenant credentials.
Regeneration changes generated files; it does not deploy agents or refresh `results/offline-baseline/`.

## Results

- **Preserved baseline:** 104/104 tests, 24.423 s; 180 benchmark runs. No leaks detected on those defined synthetic
  probes, not a general safety claim. [Baseline](results/offline-baseline/).
- **Historical full Python run:** 211/211, 82.93 s, **7 October 2026 at 16:42:27 KST**; not rerun at the original 8 October checkpoint.
  [Generated report](results/TEST_RESULTS.md).
- **8 October targeted run:** agent-package and live-broker local suites passed **52 tests in 6.572 s**.
  [Run record](results/live/evidence/pre-push-local-validation.json); not a full-suite or cloud test.
- **Later PowerShell mocks:** [operational scripts](results/live/evidence/operational-script-regressions.json) and
  [consent helper](results/live/evidence/temporary-app-install-consent-local-tests.json). Not cloud/Copilot tests;
  consent restoration has separate Graph evidence.
- **Separate sanitized-copy validation (8 October):** the existing offline suite passed **211 tests in 39.019 s**.
  This was a local compatibility check only; historical result files were not regenerated and no live test was repeated.

## Live demo commands

**Disabled-by-default examples, not a setup checklist or permission to deploy.**
`Demo.psm1` refuses to load with the supplied placeholder tenant/subscription IDs, before authentication or writes.
To adapt this code, explicitly configure your own approved tenant, subscription, administrator, groups, app/SSO IDs,
resource names, endpoints and isolated state location consistently across `deployment/scripts/`, the runtime contract
and the agent-package builder. `f0000000-*`, `example.invalid`, and `EXAMPLE` values are not real deployment inputs.
Provide a newly reviewed contract, exact-output plan and explicit authorization; historical approvals cannot be reused.
Never copy the original private state or watchdog namespaces into this public copy.

**Tenant-security exception artifacts are historical, not a recommendation.** MFA exclusions and temporary app-install
consents are not prerequisites for the offline tests and must not be replayed from the reports. Their public-copy scripts
retain scope/identity/time/restoration checks and use separate example state/event names; they do not control any original
private watchdog. Prefer normal authentication and least-privilege administrative processes.

No live tests, new cloud resources, public repository creation or push were performed to prepare this copy.
No license or ownership claim is added; publication remains subject to rights review and explicit final approval.

### Content preparation and lifecycle

| Operation | Command |
|---|---|
| Prepare; no publication | `python -m live_poc prepare --state STATE --out PLAN --citation-base HTTPS_ORIGIN/access-request --ttl-days 1 --broker-contract .\deployment\runtime\contract.json` |
| Publish exact approved hashes | `python -m live_poc apply --state STATE --plan PLAN --approval APPROVAL --ledger LEDGER` |
| Reconcile source/opt-in changes | `python -m live_poc reconcile --state STATE --ledger LEDGER` |
| Remove expired A/C content | `python -m live_poc sweep --state STATE --ledger LEDGER` |
| Withdraw one source | `python -m live_poc withdraw --state STATE --ledger LEDGER --source-id SOURCE_ID` |

Replace uppercase placeholders with reviewed paths/values.
Import `.\deployment\scripts\Demo.psm1`; obtain the pipeline token with `Get-DemoAppToken (Get-DemoState).apps.Pipeline` into
`$env:KX_GRAPH_TOKEN`, then remove the environment variable after use. Never write tokens to files or Git.
Preparation regenerates the ignored `deployment\runtime\snapshot.json`; it does not authorize new content or extend existing expiry.
The sanitized historical outputs and plan are in [PUBLICATION_PREVIEW.md](results/live/PUBLICATION_PREVIEW.md).
Their altered bytes cannot be used to validate or authorize the original private hashes.

### Setup, deployment and agent packages

| Operation | Command |
|---|---|
| Initial identities / source sites | `.\deployment\scripts\Initialize-Demo.ps1 -Apply` / `.\deployment\scripts\Prepare-Sites.ps1 -Apply` |
| Remove temporary provisioning grant | `.\deployment\scripts\Remove-ProvisioningGrant.ps1 -Apply` |
| Provision approved Azure target | `.\deployment\scripts\Provision-Azure.ps1 -Apply` |
| Build broker image | `az acr build --subscription f0000000-0000-4000-8000-00000000003f --registry exampleknowledgeacr --image kx-broker:TAG --file deployment\Dockerfile .` |
| Deploy immutable image | `.\deployment\scripts\Deploy-Broker.ps1 -Apply -Image REGISTRY/kx-broker@sha256:DIGEST` |
| Configure approved SSO additively | `.\deployment\scripts\Configure-AgentSso.ps1 -Apply -RegistrationId REGISTRATION_ID -ApplicationIdUri APPLICATION_ID_URI` |
| Noninstallable broker preview | `python deployment\scripts\build_agent_package.py --architecture broker --preview --out .local\broker-preview` |
| Broker package using actual SSO registration | `python deployment\scripts\build_agent_package.py --architecture broker --auth-reference-id REGISTRATION_ID --out .local\broker-package` |
| Hard-scoped connector package | `python deployment\scripts\build_agent_package.py --architecture connector --out .local\connector-package` |
| Delegated checks; memory-only tokens | `.\deployment\scripts\Test-DelegatedAccess.ps1 -Identity admin -EvidencePath EVIDENCE_PATH` |
| Remove demo resources after review | `.\deployment\scripts\Remove-Demo.ps1 -Apply` |

Agent output directories must be new/empty. Package creation is **not installation**. Obtain newly approved SSO identifiers
from your own tenant; the [historical deployment record](results/live/evidence/agent-deployment.json) contains only placeholders,
including its encoded registration ID.
The deployment handoff script has local mocked validation; the recorded live recovery was manual, not a script-run pass.

## B historical Azure target — admin Copilot SSO/PM answer passed; wet-clean answer failed

Sanitized representation of the recorded deployment; not a reachable target or a new deployment verification.

| Setting | Value |
|---|---|
| Subscription | `f0000000-0000-4000-8000-00000000003f` (`EXAMPLE-SUBSCRIPTION-CURRENT`) |
| Resource group / region | `rg-example-knowledge` / Korea Central |
| Registry / storage | `exampleknowledgeacr` / `exampleknowledgestorage` |
| Managed identity client ID | `f0000000-0000-4000-8000-000000000036` |
| Broker origin | `https://broker.example.invalid` |
| Image | `sha256:REDACTED_IMAGE_DIGEST_01` (`de3`, `20261007-5`) |
| Revision / scaling | `example-broker--0000002`; Multiple mode, one active revision, min 0/max 1, explicit 100% traffic |
| Verified endpoints | `/healthz`, `/demo/privacy`, `/demo/terms`: 200; notices matched approval |

### Runtime configuration

Entry point: `python -m arch_b_broker.live`, `PORT=8080`. Linux container paths below are runtime paths, not Windows commands.

| Environment | Required value |
|---|---|
| `KX_TENANT_ID`, `KX_BROKER_CLIENT_ID`, `KX_AUDIENCE_GROUP_ID` | Approved tenant, broker GUID and audience IDs |
| `KX_GRAPH_CLIENT_ID` | Managed identity client ID above |
| `KX_CONTRACT_PATH`, `KX_SOURCE_SNAPSHOT_PATH` | Absolute approved JSON paths under `/app/runtime/` |
| `KX_SOURCE_MANIFEST_PATH` | Optional provenance sidecar when snapshot is a bare document array |
| `KX_PUBLIC_ORIGIN` | Broker origin above; must match approved runtime inputs |
| `KX_STORAGE_MODE`, `KX_STORAGE_ROOT`, `KX_STATE_PATH` | `azure-blob`, `/app/state`, `/app/state/broker.sqlite` |
| `KX_BLOB_ACCOUNT_URL` | `https://storage.example.invalid` |
| `KX_BLOB_CONTAINER`, `KX_BLOB_NAME` | Provisioned private container; `broker-state.sqlite` |
| `KX_REPLICA_COUNT`, `KX_PURVIEW_MODE` | `1`, `disabled-demo` |

Tokens require the broker GUID audience and `Knowledge.Ask`; managed identity needs Graph `User.Read.All` and
`GroupMember.Read.All`. Runtime: identity/audience and snapshot-expiry checks, BM25 extracts, Blob-leased local SQLite.

**Do not reset the state marker or policy counters.** Replica limits alone do not prevent restart overlap.
The verified populated-state handoff fully drained revisions, waited 75 seconds, activated one without restart,
verified its hostname, then set 100% traffic. [Recovery evidence](results/live/evidence/broker-populated-restart.json).
Not zero-downtime deployment. Cold starts/storage charges remain. Old `-1` group deleted; current `-2` retained.

Local smoke mode: `KX_STORAGE_MODE=local-validation`, existing local state, transient `KX_GRAPH_ACCESS_TOKEN`,
`127.0.0.1` only; rejected in container deployment. See [runtime](app/arch_b_broker/live.py).

## Original offline prototype (not the deployed live path)

- **A:** synthetic Graph connection/schema/items, audience ACLs and simulated synchronization/revocation.
- **B:** BM25 retrieval, purpose/audience/disclosure controls, HS256 test tokens and mocked Purview.
- **C:** approval/card workflow, generated Markdown artifacts and simulated native indexing.

Offline hosts/IDs/OAuth placeholders are not deployment settings. Live: real Graph, exact-output operator approval,
six A summaries/six C TXT cards, bounded B snapshot and Entra RS256/directory checks.
No scheduled ingestion, Azure AI Search, Azure OpenAI or live Purview.

## Key design decisions and assumptions

Work IQ does not bypass permissions. Approve **derived content and audience**; derivatives have separate ACLs.
A/C contract: `KX-Synthetic-20261007`; B: `KX-DEMO-20261007`. Synthetic classification/custom columns are not Purview
labels. Citation links neither grant original access nor submit an access request.

## Constants that must be verified against current Microsoft documentation

Connector: [validators.py](app/arch_a_connector/validators.py). Agent/MCP/Entra/Purview:
[constants.py](app/arch_b_broker/constants.py). References: [technical report](docs/01_Technical_Architecture.md).

## Limitations

Synthetic canary scans and local tests do not prove general leak prevention, permission trimming or compliance.
Read-only grants do not imply download prevention; existing responses/copies cannot be recalled.
No ordinary-user B Copilot result, guest/revocation guarantee, live Purview enforcement or expiry-removal success is claimed.
See the [test report](docs/02_Test_Report_and_PoC_Plan.md) for unmet acceptance criteria and preserved failures.
