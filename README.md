# Cross-team knowledge access without direct file access

Share approved cross-team knowledge through Microsoft 365 Copilot while keeping original files restricted.
This repository compares three approaches, with prototype code, architecture diagrams and demo results.

> Synthetic data; sanitized identifiers and screenshots. These are recorded results, not a running service or production certification. [Sanitization details](SANITIZATION_MANIFEST.json).

## Recommendation and results

**Start with A — connector index for the next PoC.** Both the administrator and an authorized ordinary user
received correct answers with connector citations, without adding access to the original files.
This shares separately approved content; Work IQ does not bypass source permissions.

Results from the 7–8 October 2026 demo:

| Architecture | Observed Copilot result | Diagram | Report + screenshots |
|---|---|---|---|
| **A: connector index** | Correct cited answers for admin and authorized reader. | [A diagram](docs/01_Technical_Architecture.md#architecture-a) | [A report](results/live/reports/A_connector.md#visual-evidence) |
| **B: broker** | Admin answered a maintenance question but could not answer a cleaning question. Ordinary-user Copilot was **not run** because agent installation was blocked. | [B diagram](docs/01_Technical_Architecture.md#architecture-b) | [B report](results/live/reports/B_broker.md#visual-evidence) |
| **C: SharePoint cards** | Admin received a cited answer; the authorized reader's three C-only attempts failed. | [C diagram](docs/01_Technical_Architecture.md#architecture-c) | [C report](results/live/reports/C_publishing.md#visual-evidence) |

An outsider received no answer in **one combined A/C query**. Separately, B's direct API admitted the reader
and denied the outsider with HTTP 403; that is not an ordinary-user Copilot result.

## Start here

| Path | Purpose |
|---|---|
| [docs/00_README.md](docs/00_README.md) | Short findings summary |
| [docs/01_Technical_Architecture.md](docs/01_Technical_Architecture.md) | Implemented demo, target designs and comparison |
| [docs/02_Test_Report_and_PoC_Plan.md](docs/02_Test_Report_and_PoC_Plan.md) | Results, limitations and next PoC acceptance criteria |
| [app/arch_a_connector/](app/arch_a_connector/) + [app/live_poc/](app/live_poc/) | A transforms and Graph integration; the live pipeline also publishes C cards |
| [app/arch_b_broker/](app/arch_b_broker/), [app/arch_c_publish/](app/arch_c_publish/), [app/common/](app/common/) | B/C implementation and shared policy helpers |
| [examples/](examples/) | Synthetic source files, sharing contract and generated connector payloads |
| [deployment/scripts/](deployment/scripts/) | Environment preparation, agent packaging and deployment examples |
| [results/live/](results/live/) | Reports, sanitized evidence and screenshots; detailed troubleshooting is folded in the reports |

## How to run (from this directory)

Run the existing offline tests from the repository root using Windows Python:

```powershell
$env:PYTHONPATH = (Resolve-Path .\app).Path
python -m pip install -r deployment\requirements-live.txt
python -m unittest discover -s app -t app -v
```

These tests use fake services and generated keys, not tenant credentials. The sanitized copy passed 211 tests;
the [local report](results/TEST_RESULTS.md) and [original baseline](results/offline-baseline/) remain separate from live-service evidence.

## Limits before a customer PoC

The live demo used manual snapshots and deterministic summaries, not Azure OpenAI, Azure AI Search or live Purview.
Approve the derivative content and audience, then validate guest access, effective permissions, revocation/cache delay
and expiry cleanup. A/C need an operator-run sweep; previously disclosed answers cannot be recalled.

<a id="live-demo-commands"></a>

<details>
<summary>Adapt the deployment and lifecycle scripts</summary>

PowerShell 7.5+ is required. `Demo.psm1` rejects the supplied placeholder tenant/subscription IDs before authentication
or writes. Configure your own approved tenant, groups, apps, resources, endpoints and runtime contract consistently;
`f0000000-*`, `example.invalid` and `EXAMPLE` values are not deployment inputs.

Use a new reviewed output plan and isolated state under `%LOCALAPPDATA%\CrossTeamKnowledgePublicExample\operator-configured`.
Private snapshots, credentials, consent backups and `.local/` are excluded from Git. Historical MFA-exception and
temporary-consent records are diagnostic history, not deployment prerequisites; use normal authentication.

### Content preparation and lifecycle

| Operation | Command |
|---|---|
| Prepare A/C outputs; no publication | `python -m live_poc prepare --state STATE --out PLAN --citation-base HTTPS_ORIGIN/access-request --ttl-days 1` |
| Publish exact approved hashes | `python -m live_poc apply --state STATE --plan PLAN --approval APPROVAL --ledger LEDGER` |
| Reconcile source/opt-in changes | `python -m live_poc reconcile --state STATE --ledger LEDGER` |
| Remove expired A/C content | `python -m live_poc sweep --state STATE --ledger LEDGER` |
| Withdraw one source | `python -m live_poc withdraw --state STATE --ledger LEDGER --source-id SOURCE_ID` |

Replace uppercase placeholders with reviewed paths/values.
Import `.\deployment\scripts\Demo.psm1`; obtain the pipeline token with `Get-DemoAppToken (Get-DemoState).apps.Pipeline` into
`$env:KX_GRAPH_TOKEN`, then remove the environment variable after use. Never write tokens to files or Git.
To also prepare a broker snapshot, supply `--broker-contract .\deployment\runtime\contract.json`; its output
`deployment\runtime\snapshot.json` is ignored by Git. The sanitized [historical output plan](results/live/PUBLICATION_PREVIEW.md)
is reference material only: its altered bytes cannot verify original approval hashes or authorize new publication.

### Setup, deployment and agent packages

| Operation | Command |
|---|---|
| Initial identities / source sites | `.\deployment\scripts\Initialize-Demo.ps1 -Apply` / `.\deployment\scripts\Prepare-Sites.ps1 -Apply` |
| Connector connection and schema | `.\deployment\scripts\Prepare-Connector.ps1 -Apply` |
| Remove temporary provisioning grant | `.\deployment\scripts\Remove-ProvisioningGrant.ps1 -Apply` |
| Provision approved Azure target | `.\deployment\scripts\Provision-Azure.ps1 -Apply` |
| Build broker image | `az acr build --subscription SUBSCRIPTION_ID --registry REGISTRY --image kx-broker:TAG --file deployment\Dockerfile .` |
| Deploy immutable image | `.\deployment\scripts\Deploy-Broker.ps1 -Apply -Image REGISTRY/kx-broker@sha256:DIGEST` |
| Configure approved SSO additively | `.\deployment\scripts\Configure-AgentSso.ps1 -Apply -RegistrationId REGISTRATION_ID -ApplicationIdUri APPLICATION_ID_URI` |
| Noninstallable broker preview | `python deployment\scripts\build_agent_package.py --architecture broker --preview --out .local\broker-preview` |
| Broker package using actual SSO registration | `python deployment\scripts\build_agent_package.py --architecture broker --auth-reference-id REGISTRATION_ID --out .local\broker-package` |
| Hard-scoped connector package | `python deployment\scripts\build_agent_package.py --architecture connector --out .local\connector-package` |
| Delegated checks; memory-only tokens | `.\deployment\scripts\Test-DelegatedAccess.ps1 -Identity admin -EvidencePath EVIDENCE_PATH` |
| Remove demo resources after review | `.\deployment\scripts\Remove-Demo.ps1 -Apply` |

Agent output directories must be new/empty. Package creation is **not installation**; A also needs connector Copilot
visibility enabled. Obtain SSO identifiers from your own tenant rather than the sanitized
[historical deployment record](results/live/evidence/agent-deployment.json).

</details>

<details>
<summary>B runtime configuration and recovery constraints</summary>

Entry point: `python -m arch_b_broker.live`, `PORT=8080`. Container paths below are Linux paths.

| Environment | Required value |
|---|---|
| `KX_TENANT_ID`, `KX_BROKER_CLIENT_ID`, `KX_AUDIENCE_GROUP_ID` | Approved tenant, broker GUID and audience IDs |
| `KX_GRAPH_CLIENT_ID` | Your managed identity client ID |
| `KX_CONTRACT_PATH`, `KX_SOURCE_SNAPSHOT_PATH` | Absolute approved JSON paths under `/app/runtime/` |
| `KX_SOURCE_MANIFEST_PATH` | Optional provenance sidecar when snapshot is a bare document array |
| `KX_PUBLIC_ORIGIN` | Your broker HTTPS origin; must match approved runtime inputs |
| `KX_STORAGE_MODE`, `KX_STORAGE_ROOT`, `KX_STATE_PATH` | `azure-blob`, `/app/state`, `/app/state/broker.sqlite` |
| `KX_BLOB_ACCOUNT_URL` | Your provisioned Blob storage account URL |
| `KX_BLOB_CONTAINER`, `KX_BLOB_NAME` | Provisioned private container; `broker-state.sqlite` |
| `KX_REPLICA_COUNT`, `KX_PURVIEW_MODE` | `1`, `disabled-demo` |

Tokens require the broker GUID audience and `Knowledge.Ask`; managed identity needs Graph `User.Read.All` and
`GroupMember.Read.All`. Runtime: identity/audience and snapshot-expiry checks, BM25 extracts, Blob-leased local SQLite.

**Do not reset the state marker or policy counters.** Replica limits alone do not prevent restart overlap.
The verified populated-state handoff fully drained revisions, waited 75 seconds, activated one without restart,
verified its hostname, then set 100% traffic. [Recovery evidence](results/live/evidence/broker-populated-restart.json).
This was not zero-downtime deployment. The recovery was manual; the revised deployment script has local mocked
validation, not a recorded live run. Cold starts and storage charges remain.

Local smoke mode: `KX_STORAGE_MODE=local-validation`, existing local state, transient `KX_GRAPH_ACCESS_TOKEN`,
`127.0.0.1` only; rejected in container deployment. See [runtime](app/arch_b_broker/live.py).

</details>

<details>
<summary>Offline generation commands and recorded runs</summary>

| Task | Command |
|---|---|
| Run suite and rewrite generated local-test reports | `python tools\run_tests.py` |
| Run synthetic benchmark | `python app\bench\run_benchmark.py` |
| Regenerate offline artifacts | `python tools\generate_artifacts.py` |
| Regenerate offline manifests after changing constants | `python -m arch_b_broker.manifest_builder` |
| Regenerate synthetic source data/manifests | `python examples\data\build_data.py` |

Regeneration changes generated files; it neither deploys agents nor refreshes the preserved baseline.
Offline A uses simulated synchronization, B uses HS256 test tokens and mocked Purview, and C uses simulated native indexing.

| Recorded run | Result | Evidence |
|---|---|---|
| Original synthetic baseline | 104 tests; 180 benchmark probes | [Baseline](results/offline-baseline/) |
| Expanded local suite, 7 October | 211 tests, 82.93 s | [Local report](results/TEST_RESULTS.md) |
| Targeted local run, 8 October | 52 tests, 6.572 s | [Run record](results/live/evidence/pre-push-local-validation.json) |
| Sanitized-copy local run, 8 October | 211 tests, 39.019 s | [Manifest](SANITIZATION_MANIFEST.json) |

All four rows are local tests, not tenant security or Copilot evidence. Later PowerShell
[operational-script](results/live/evidence/operational-script-regressions.json) and
[consent-helper](results/live/evidence/temporary-app-install-consent-local-tests.json) checks also used mocks.

Verify service/schema constants against current Microsoft documentation:
[connector validators](app/arch_a_connector/validators.py), [agent/MCP/Entra/Purview constants](app/arch_b_broker/constants.py),
and the [technical report's sources](docs/01_Technical_Architecture.md).

</details>
