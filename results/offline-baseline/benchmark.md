# Benchmark results

> **Sanitized public-release copy — historical evidence, not new live tests.** Tenant/account/resource identifiers are placeholders; screenshots are labeled sanitized copies with opaque redactions where needed. Original private evidence is retained separately. Pass/fail/blocked/not-run distinctions are preserved. Historical approvals, deadlines and active-state statements describe their recorded checkpoint only, not current status or permission to act. Sanitized artifacts cannot establish the original cryptographic hashes.

20 probes x 3 users (bob, carol, dave) per architecture; fixed clock, fully offline, one warm-up query per architecture.

| Metric | A - derived index via Copilot connector | B - Knowledge Broker API (PDP) | C - governed knowledge cards |
|---|---|---|---|
| Authorised answer rate (bob, 13 answerable probes) | 100% | 69% | 62% |
| Unauthorised results returned (must be 0) | 0 | 0 | 0 |
| PII / secret leaks in any output (must be 0) | 0 | 0 | 0 |
| Original-URL / path / id exposures (must be 0) | 0 | 0 | 0 |
| Highly Confidential leaks (must be 0) | 0 | 0 | 0 |
| Results returned to carol / dave | 0 / 0 | 0 / 0 | 0 / 0 |
| Mean latency per query (ms) | 6.01 | 9.51 | 3.98 |
| p95 latency per query (ms) | 22.75 | 68.50 | 10.71 |
| Setup (sync / index / publish) time (ms) | 802 | 3493 | 3580 |
| Max source characters per result | 7838 | 299 | 1568 |
| Excerpts withheld by exfiltration guard | 0 | 18 | 0 |
| Explicit policy denials (403/429) | 0 | 45 | 0 |

## Bob's answerable probes

| Probe | Query | A | B | C |
|---|---|---|---|---|
| P01 | What changed in the poly gate etch recipe ER-2291? | yes | yes | yes |
| P02 | What was the root cause of the YE-0412 yield excursion? | yes | yes | yes |
| P03 | When is the next preventive maintenance for ETCH-07 chamber B? | yes | no | yes |
| P04 | Which supplier caused the photoresist contamination in SQ-118? | yes | yes | yes |
| P05 | How is the CD-SEM daily calibration done? | yes | yes | yes |
| P06 | How many seasoning wafers are required after preventive maintenance? | yes | yes | yes |
| P07 | RF forward power on ETCH-07 chamber B after the ER-2291 recipe change | yes | yes | no |
| P08 | particle adder alarms on chamber B during the September excursion | yes | yes | no |
| P09 | What did the excursion war room chat conclude about chamber B particles? | yes | no | yes |
| P10 | particle map notes: edge clustering at the upper liner seam | yes | yes | no |
| P11 | corrective and preventive actions for the NX-7 yield excursion | yes | no | yes |
| P12 | defect density at gate litho caused by photoresist gels | yes | no | no |
| P13 | endpoint detection wavelength and over-etch for the poly main etch | yes | yes | no |

Diagnostic: B's answer rate for bob with the exfiltration guard disabled (threshold 1.0) is 77%; the difference to the table above is the cost of the per-user coverage guard. The rest of B's gap is the 300-character verbatim cap plus the simple lexical retriever (production would use hybrid/semantic ranking and LLM synthesis over the same capped excerpts).

## Architecture C publish outcomes (bob requested every library document)

| Source document | Final state | Reason |
|---|---|---|
| ER-2291_poly_gate_etch_recipe_change.md | PUBLISHED |  |
| ETCH-07_FDC_event_log_2026Q3.txt | PUBLISHED |  |
| GL-ETCH-007_chamber_seasoning_guideline.txt | PUBLISHED |  |
| MET-CDSEM-02_calibration_runbook.md | PUBLISHED |  |
| NX7_gate_stack_process_window_TRADE_SECRET.md | REJECTED | auto_rejected:label_above_ceiling |
| SQ-118_photoresist_supplier_quality_issue.txt | PUBLISHED |  |
| WIP_NX8_litho_overlay_budget_DRAFT.md | REJECTED | auto_rejected:excluded_path |
| YE-0412_NX7_yield_excursion_RCA.md | PUBLISHED |  |
| team_a_staffing_and_review_notes.md | REJECTED | auto_rejected:out_of_scope_path |
| tool_PM_schedule_Q4_2026.html | PUBLISHED |  |

## Notes

- Latency is in-process for A (Graph emulator search) and C (native index simulation) and over a localhost HTTP round trip for B, so B includes HTTP/JSON overhead; none of the numbers reflect Microsoft 365 service latency.
- 'Max source characters per result' shows how much source-derived text a single result exposes: A returns whole redacted chunks, B caps verbatim excerpts at maxExcerptChars, C exposes only the approved card body.
- A answers from whatever was pushed (no per-query policy); B enforces audience, guest, purpose, rate limit, exfiltration guard and Purview prompt/response checks (allow-all policy in this run) per request; C only covers documents that were explicitly requested and approved (chat content was never requested, so chat probes are unanswered by design).
