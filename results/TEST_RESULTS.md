# Local test results — not live-service evidence

> **Sanitized public-release copy — historical evidence, not new live tests.** Tenant/account/resource identifiers are placeholders; screenshots are labeled sanitized copies with opaque redactions where needed. Original private evidence is retained separately. Pass/fail/blocked/not-run distinctions are preserved. Historical approvals, deadlines and active-state statements describe their recorded checkpoint only, not current status or permission to act. Sanitized artifacts cannot establish the original cryptographic hashes.


Every PASS below is a local assertion over fixtures, emulators, generated keys, fake services or loopback HTTP. Names containing `live`, `real`, `Purview` or `SSO` do not establish tenant sign-in, policy decisions, deployed agents or service permission trimming.

Original evidence remains in [offline-baseline](offline-baseline/TEST_RESULTS.md). Actual service observations are in [A](live/reports/A_connector.md), [B](live/reports/B_broker.md) and [C](live/reports/C_publishing.md).

This Markdown can be re-rendered from recorded JSON without a test rerun. Run metrics/outcomes below are unchanged by rendering; descriptions are qualified for local scope. Blank source descriptions are not additional evidence.

- Recorded command: `python3 -m unittest discover -v   (recorded via python3 tools/run_tests.py)`
- Python 3.14.0, recorded 2026-10-07T07:42:27Z, wall time 82.93 s
- **211 passed, 0 failed, 0 errors, 0 skipped (total 211)**

| Test module | Evidence boundary for every result in that file |
|---|---|
| `arch_a_connector.tests.test_arch_a` | Synthetic directory/delta/Graph emulator and local payload checks; no live Search/Copilot or membership/index/cache propagation measurement. |
| `arch_b_broker.tests.test_agent_package` | Local test only; inspect the test implementation before inferring coverage. |
| `arch_b_broker.tests.test_arch_b` | HS256 fixtures, loopback HTTP, BM25, mock Purview and example manifests; no tenant SSO or real Purview decisions. |
| `arch_b_broker.tests.test_live` | Live-adapter code tested with generated RSA keys, fake Graph and local state; not tenant user authentication or live retrieval. |
| `arch_b_broker.tests.test_live_blob` | Fake Blob SDK/lease failures and local SQLite recovery; not the separate Azure restart experiment. |
| `arch_c_publish.tests.test_arch_c` | Source-bound baseline approval and simulated SharePoint/native index; not exact-output live approval or effective user permissions. |
| `bench.tests.test_benchmark` | 180 fixed synthetic probes; no detected canary leakage is not general or live-service safety. |
| `common.tests.test_common` | Fixed synthetic redaction, policy, audit and delta cases; not general leakage resistance or immutable audit. |
| `live_poc.tests.test_live` | Fake Graph preparation/publication/approval/lifecycle/transport checks (Other below); no live credentials or user-access checks. |

| Architecture | Tests | Passed | Failed |
|---|---|---|---|
| A - derived index via Copilot connector | 38 | 38 | 0 |
| B - Knowledge Broker API (PDP) | 106 | 106 | 0 |
| C - governed knowledge cards | 18 | 18 | 0 |
| Common building blocks | 17 | 17 | 0 |
| Benchmark | 2 | 2 | 0 |
| Other | 30 | 30 | 0 |

| Test id | Architecture | Test module | Test | Local assertion (module scope above applies) | Result |
|---|---|---|---|---|---|
| A-01 | A | `arch_a_connector.tests.test_arch_a` | `test_acl_is_audience_group_only` | Every item ACL grants only the Team B group (never the source group/ACL, never everyone); denies are guests only. | PASS |
| A-02 | A | `arch_a_connector.tests.test_arch_a` | `test_audit_chain_verifies_and_matches_writes` | Every PUT has a 'publish' audit record, the hash chain verifies, and no PII or secrets reach the audit log. | PASS |
| A-03 | A | `arch_a_connector.tests.test_arch_a` | `test_connection_payload_valid` | Connection JSON {id,name,description} is valid: id 3-32 alphanumeric, not starting with 'Microsoft'. | PASS |
| A-04 | A | `arch_a_connector.tests.test_arch_a` | `test_container_name_is_contract_display_name` | Every item's containerName (semantic label) is the contract display name, never an original path. | PASS |
| A-05 | A | `arch_a_connector.tests.test_arch_a` | `test_highly_confidential_internal_and_drafts_excluded` | Highly Confidential (label ceiling), /Internal (scope) and /Shareable/Drafts (excluded path) produce no items. | PASS |
| A-06 | A | `arch_a_connector.tests.test_arch_a` | `test_item_ids_deterministic_and_valid` | Item ids are URL-safe, <=128 chars and identical across independent full crawls. | PASS |
| A-07 | A | `arch_a_connector.tests.test_arch_a` | `test_large_document_chunked_for_retrieval_within_item_limit` | The 4.5 MB log is split into <=8,000-char items (retrieval-quality choice); every item is far below the 30 MB per-item limit, and an item above the limit is rejected by the validator. | PASS |
| A-08 | A | `arch_a_connector.tests.test_arch_a` | `test_no_original_url_path_or_id_in_any_payload` | No original webUrl, library path, site/drive/driveItem id or Teams link appears in any Graph request body. | PASS |
| A-09 | A | `arch_a_connector.tests.test_arch_a` | `test_pii_and_secrets_redacted` | Emails, KR mobiles, KR RRN, API key, connection string and internal links are replaced by [REDACTED:*] tokens. | PASS |
| A-10 | A | `arch_a_connector.tests.test_arch_a` | `test_prompt_injection_neutralised` | The injection sentence is stripped from derived items, flagged in state and audited; other content is kept. | PASS |
| A-11 | A | `arch_a_connector.tests.test_arch_a` | `test_schema_valid_and_registered` | Schema uses baseType externalItem, required semantic labels, valid flags, and registers via PATCH + 202 polling. | PASS |
| A-12 | A | `arch_a_connector.tests.test_arch_a` | `test_teams_chat_ingested_as_derived_items` | Teams chat becomes per-day digests (pseudonymised, redacted) plus the chat file from the sender's OneDrive. | PASS |
| A-13 | A | `arch_a_connector.tests.test_arch_a` | `test_trimming_bob_sees_carol_and_guest_dave_do_not` | Search trimming: bob (Team B) gets results; carol (no group), dave (Team B guest, deny ACE) and alice get none. | PASS |
| A-14 | A | `arch_a_connector.tests.test_arch_a` | `test_audience_change_by_group_membership_only` | Local emulator evaluates group removal immediately with zero item writes; no live propagation latency is measured. | PASS |
| A-15 | A | `arch_a_connector.tests.test_arch_a` | `test_audit_tampering_detected` | Editing a persisted audit record breaks the hash chain and verify() pinpoints it. | PASS |
| A-16 | A | `arch_a_connector.tests.test_arch_a` | `test_contract_revocation_deletes_all_items` | Revoking the contract deletes every derived item; later syncs publish nothing. | PASS |
| A-17 | A | `arch_a_connector.tests.test_arch_a` | `test_expired_delta_token_triggers_full_resync` | HTTP 410-style expired delta token falls back to a full crawl without duplicate writes. | PASS |
| A-18 | A | `arch_a_connector.tests.test_arch_a` | `test_incremental_delete_propagates` | A source deletion (delta 'deleted' facet) deletes all derived items for that doc. | PASS |
| A-19 | A | `arch_a_connector.tests.test_arch_a` | `test_incremental_update_republishes_only_changed_document` | An edit to one doc re-publishes only that doc's items via drive delta; new content becomes searchable. | PASS |
| A-20 | A | `arch_a_connector.tests.test_arch_a` | `test_move_out_of_scope_and_relabel_above_ceiling_remove_items` | Moving a doc to /Internal or relabelling it Highly Confidential removes its derived items on the next sync. | PASS |
| A-21 | A | `arch_a_connector.tests.test_arch_a` | `test_new_guest_visible_until_acl_refresh_then_denied` | ACL drift: a guest newly added to Team B can see items until the next sync re-publishes them with a deny ACE. | PASS |
| A-22 | A | `arch_a_connector.tests.test_arch_a` | `test_new_in_scope_file_is_published` | A new file under /Shareable is picked up by the next incremental sync. | PASS |
| A-23 | A | `arch_a_connector.tests.test_arch_a` | `test_no_change_rerun_zero_writes` | Re-running full and incremental sync with no source change performs zero PUT/DELETE calls. | PASS |
| A-24 | A | `arch_a_connector.tests.test_arch_a` | `test_refresh_before_expiry_extends_validity` | An incremental run inside the refresh window re-publishes due items with a later validUntil. | PASS |
| A-25 | A | `arch_a_connector.tests.test_arch_a` | `test_shrinking_document_deletes_superseded_chunks` | When a doc shrinks, extract items that are no longer produced are deleted (no orphans). | PASS |
| A-26 | A | `arch_a_connector.tests.test_arch_a` | `test_state_persists_and_resumes_without_rewrites` | JSON state survives a restart: a new engine resumes from the saved deltaLink with zero writes. | PASS |
| A-27 | A | `arch_a_connector.tests.test_arch_a` | `test_suspended_contract_purges_on_next_run` | A local invocation purges simulated items for a suspended contract; this does not install or test a scheduled live job. | PASS |
| A-28 | A | `arch_a_connector.tests.test_arch_a` | `test_tenant_wide_audience_only_when_contract_allows` | everyone/everyoneExceptGuests ACEs appear only if the contract allows it; their value is the (configurable) tenant ID. | PASS |
| A-29 | A | `arch_a_connector.tests.test_arch_a` | `test_ttl_expiry_sweep_removes_items` | After ttlDays without refresh (e.g. engine down) the expiry sweep deletes every derived item. | PASS |
| A-30 | A | `arch_a_connector.tests.test_arch_a` | `test_artifacts_are_valid_graph_payloads` | Baseline artifacts pass local Graph-shape validation and defined synthetic leak probes; not service acceptance or general safety. | PASS |
| A-31 | A | `arch_a_connector.tests.test_arch_a` | `test_client_retries_throttling_with_retry_after` | ConnectorClient retries 429/503 honouring Retry-After before succeeding. | PASS |
| A-32 | A | `arch_a_connector.tests.test_arch_a` | `test_connection_id_derived_from_contract` | The connection id is derived deterministically from the contract id and is valid. | PASS |
| A-33 | A | `arch_a_connector.tests.test_arch_a` | `test_connector_declarative_agent_v18` | declarativeAgent.connector.json (v1.8) scopes Copilot to this connection via GraphConnectors and is up to date. | PASS |
| A-34 | A | `arch_a_connector.tests.test_arch_a` | `test_http_transport_request_formation` | HttpGraphTransport forms the client-credentials token request and Graph calls correctly (fake opener, no network). | PASS |
| A-35 | A | `arch_a_connector.tests.test_arch_a` | `test_mock_rejects_items_before_schema_and_invalid_payloads` | The emulator returns 400 before schema completion and for invalid items (incl. missing/empty acl), 409 for duplicates. | PASS |
| A-36 | A | `arch_a_connector.tests.test_arch_a` | `test_mock_trimming_semantics_deny_wins` | Emulator trimming: grant via user/group/everyoneExceptGuests, guests excluded from everyoneExceptGuests, deny wins. | PASS |
| A-37 | A | `arch_a_connector.tests.test_arch_a` | `test_validators_reject_invalid_items_and_connections` | Item/connection validators enforce id rules, ACL enums, content type, DateTime format, schema typing and the 30 MB item limit. | PASS |
| A-38 | A | `arch_a_connector.tests.test_arch_a` | `test_validators_reject_invalid_schemas` | Schema validator enforces name length/charset, property count, searchable types, searchable+refinable and unique labels. | PASS |
| B-01 | B | `arch_b_broker.tests.test_agent_package` | `test_archive_is_root_level_six_files_and_uses_supplied_vault_reference` |  | PASS |
| B-02 | B | `arch_b_broker.tests.test_agent_package` | `test_base64_support_rejects_bad_padding_whitespace_and_placeholders` |  | PASS |
| B-03 | B | `arch_b_broker.tests.test_agent_package` | `test_cli_requires_explicit_registration_or_preview` |  | PASS |
| B-04 | B | `arch_b_broker.tests.test_agent_package` | `test_human_strings_and_links_are_truthful_and_within_schema_limits` |  | PASS |
| B-05 | B | `arch_b_broker.tests.test_agent_package` | `test_icons_are_valid_pngs_with_expected_size_and_transparent_white_outline` |  | PASS |
| B-06 | B | `arch_b_broker.tests.test_agent_package` | `test_missing_and_placeholder_auth_references_fail_before_writing` |  | PASS |
| B-07 | B | `arch_b_broker.tests.test_agent_package` | `test_origin_scope_and_request_are_pinned_without_app_only_flow` |  | PASS |
| B-08 | B | `arch_b_broker.tests.test_agent_package` | `test_output_never_overwrites_existing_artifacts` |  | PASS |
| B-09 | B | `arch_b_broker.tests.test_agent_package` | `test_preview_is_one_marked_noninstallable_document_and_no_reference` |  | PASS |
| B-10 | B | `arch_b_broker.tests.test_agent_package` | `test_response_schema_preserves_citations_limits_errors_and_purview_notice` |  | PASS |
| B-11 | B | `arch_b_broker.tests.test_agent_package` | `test_service_base64_auth_references_are_preserved_in_package` |  | PASS |
| B-12 | B | `arch_b_broker.tests.test_agent_package` | `test_capability_is_one_connection_with_contract_filter_and_no_fallback` |  | PASS |
| B-13 | B | `arch_b_broker.tests.test_agent_package` | `test_connector_cli_builds_locally_without_registration` |  | PASS |
| B-14 | B | `arch_b_broker.tests.test_agent_package` | `test_connector_identity_and_notices_are_distinct_and_schema_sized` |  | PASS |
| B-15 | B | `arch_b_broker.tests.test_agent_package` | `test_connector_instructions_do_not_preseed_expected_numeric_answers` |  | PASS |
| B-16 | B | `arch_b_broker.tests.test_agent_package` | `test_connector_preview_is_noninstallable_and_discloses_native_access` |  | PASS |
| B-17 | B | `arch_b_broker.tests.test_agent_package` | `test_connector_rejects_vault_config_and_unknown_architectures_before_writes` |  | PASS |
| B-18 | B | `arch_b_broker.tests.test_agent_package` | `test_connector_zip_has_four_files_and_needs_no_oauth_config` |  | PASS |
| B-19 | B | `arch_b_broker.tests.test_arch_b` | `test_audit_chain_verifies_and_detects_tampering` | The JSONL audit hash chain verifies; editing or deleting a record is detected at the right index. | PASS |
| B-20 | B | `arch_b_broker.tests.test_arch_b` | `test_exfiltration_guard_triggers_after_coverage_threshold` | Per user and document, rolling-24h unique chunk coverage is capped at the threshold; excess chunks are withheld. | PASS |
| B-21 | B | `arch_b_broker.tests.test_arch_b` | `test_guest_member_is_excluded` | dave (Team B member, userType Guest) gets 403 guest_excluded; a token claiming acct=1 is also refused (fail closed). | PASS |
| B-22 | B | `arch_b_broker.tests.test_arch_b` | `test_highly_confidential_never_returned` | Queries aimed at the Highly Confidential doc return none of its content. | PASS |
| B-23 | B | `arch_b_broker.tests.test_arch_b` | `test_http_routing_and_input_validation` | GET /healthz is 200; wrong method 405; unknown route 404; invalid JSON / bad fields 400; oversize body 413. | PASS |
| B-24 | B | `arch_b_broker.tests.test_arch_b` | `test_invalid_tokens_rejected_with_401` | Bad signature, expired, wrong audience/tenant/issuer, alg=none, malformed and missing tokens all get 401. | PASS |
| B-25 | B | `arch_b_broker.tests.test_arch_b` | `test_mcp_tools_list_and_call` | MCP JSON-RPC on /mcp: initialize, tools/list matches mcp-tools.json, tools/call enforces the same policy. | PASS |
| B-26 | B | `arch_b_broker.tests.test_arch_b` | `test_member_gets_answer_with_opaque_citations_over_http` | bob (Team B member) gets a 200 answer over localhost HTTP with opaque refs and broker access links only. | PASS |
| B-27 | B | `arch_b_broker.tests.test_arch_b` | `test_non_member_gets_403_and_is_audited` | carol (not in Team B) gets 403 not_in_audience with no content, and the denial is in the audit log. | PASS |
| B-28 | B | `arch_b_broker.tests.test_arch_b` | `test_purpose_must_match_contract` | A request whose declared purpose differs from the contract purpose is refused (403 purpose_mismatch). | PASS |
| B-29 | B | `arch_b_broker.tests.test_arch_b` | `test_rate_limit_returns_429_with_retry_after` | The 11th request inside one minute gets 429 + Retry-After; capacity refills over time. | PASS |
| B-30 | B | `arch_b_broker.tests.test_arch_b` | `test_redaction_applied_to_answers` | Answers redact e-mails, KR mobiles, RRNs and internal links; secrets never reach the private index at all. | PASS |
| B-31 | B | `arch_b_broker.tests.test_arch_b` | `test_responses_never_contain_original_locations` | Across a probe set, no response contains original URLs, paths, driveItem ids or unredacted PII. | PASS |
| B-32 | B | `arch_b_broker.tests.test_arch_b` | `test_revoked_contract_denies_everything` | When the contract is revoked the broker refuses all requests (403 contract_inactive). | PASS |
| B-33 | B | `arch_b_broker.tests.test_arch_b` | `test_scope_and_user_context_required` | A token without Knowledge.Ask gets 403 insufficient_scope; an app-only token (no scp) gets 401. | PASS |
| B-34 | B | `arch_b_broker.tests.test_arch_b` | `test_search_endpoint_applies_same_policy` | /search returns <=5 opaque citations (title, url, label id) with <=160-char redacted snippets; carol 403; top>5 is 400. | PASS |
| B-35 | B | `arch_b_broker.tests.test_arch_b` | `test_verbatim_cap_and_max_citations` | Every excerpt is <= maxExcerptChars (and visibly truncated from longer chunks); <= 3 citations per answer. | PASS |
| B-36 | B | `arch_b_broker.tests.test_arch_b` | `test_exfiltration_guard_returns_403_when_everything_is_withheld` | With only one document available, once its allowance is used further new excerpts are refused with 403. | PASS |
| B-37 | B | `arch_b_broker.tests.test_arch_b` | `test_source_injection_has_no_effect_and_is_flagged` | Answers from the RCA with the injected line equal answers from a copy without it; citations are flagged. | PASS |
| B-38 | B | `arch_b_broker.tests.test_arch_b` | `test_pdp_filters_hc_internal_and_drafts_even_if_indexed` | Even if the private index (wrongly) contains HC, /Internal and /Drafts content, the PDP never returns it. | PASS |
| B-39 | B | `arch_b_broker.tests.test_arch_b` | `test_purview_blocks_prompt` | Mock marker-based prompt block returns 403 before retrieval and is audited; no real Purview decision. | PASS |
| B-40 | B | `arch_b_broker.tests.test_arch_b` | `test_purview_blocks_response_without_consuming_coverage` | Mock marker-based response block withholds content without consuming coverage; no real Purview decision. | PASS |
| B-41 | B | `arch_b_broker.tests.test_arch_b` | `test_purview_called_for_prompt_and_response_and_audited` | Allowed baseline requests invoke the local process_content hook twice, not the Graph Purview service. | PASS |
| B-42 | B | `arch_b_broker.tests.test_arch_b` | `test_artifacts_match_generated_manifests` | artifacts/arch_b contains the broker manifests exactly as generated from the constants file. | PASS |
| B-43 | B | `arch_b_broker.tests.test_arch_b` | `test_declarative_agent_validator_rules_v18` | DA v1.8 rules: required keys, unrecognised keys invalid, length limits, <=12 starters, 1-10 {id,file} actions, disclaimer <=500, GraphConnectors connection ids must be valid. | PASS |
| B-44 | B | `arch_b_broker.tests.test_arch_b` | `test_jwt_validator_unit_rules` | HS256 validator: round-trip, leeway on exp, list audience, tampered payload and alg confusion rejected. | PASS |
| B-45 | B | `arch_b_broker.tests.test_arch_b` | `test_manifests_valid_and_cross_consistent` | Baseline manifests match their generator and local shape/cross-reference rules; fictional endpoints/OAuthPluginVault reference are not working SSO. | PASS |
| B-46 | B | `arch_b_broker.tests.test_arch_b` | `test_pdp_unit_rules` | PDP: label/path/chat scope per chunk, min-one-chunk rule for tiny docs, per-user isolation, rolling window. | PASS |
| B-47 | B | `arch_b_broker.tests.test_arch_b` | `test_plugin_and_mcp_validator_rules_v24` | Plugin v2.4 rules: required keys, namespace ^[A-Za-z0-9]+$, function names ^[A-Za-z0-9_]+$, run_for_functions subset of functions, operationIds match; MCP tools need name/description/inputSchema. | PASS |
| B-48 | B | `arch_b_broker.tests.test_live` | `test_blob_local_sqlite_explicitly_rejects_network_mounts` |  | PASS |
| B-49 | B | `arch_b_broker.tests.test_live` | `test_blob_requires_explicit_safe_endpoint_and_container` |  | PASS |
| B-50 | B | `arch_b_broker.tests.test_live` | `test_document_list_sidecar_binds_exact_bytes` |  | PASS |
| B-51 | B | `arch_b_broker.tests.test_live` | `test_local_disk_is_not_a_live_mount` |  | PASS |
| B-52 | B | `arch_b_broker.tests.test_live` | `test_local_entry_point_never_persists_transient_token` |  | PASS |
| B-53 | B | `arch_b_broker.tests.test_live` | `test_local_validation_is_explicit_and_forbidden_in_container_apps` |  | PASS |
| B-54 | B | `arch_b_broker.tests.test_live` | `test_sigterm_closes_server_state_and_managed_credential` |  | PASS |
| B-55 | B | `arch_b_broker.tests.test_live` | `test_snapshot_contract_tenant_source_and_time_binding` |  | PASS |
| B-56 | B | `arch_b_broker.tests.test_live` | `test_snapshot_requires_graph_provenance_and_policy_scope` |  | PASS |
| B-57 | B | `arch_b_broker.tests.test_live` | `test_source_locations_are_not_embedded_in_returnable_content` |  | PASS |
| B-58 | B | `arch_b_broker.tests.test_live` | `test_unknown_modes_and_missing_persistence_rejected` |  | PASS |
| B-59 | B | `arch_b_broker.tests.test_live` | `test_notice_routes_do_not_accept_posts_or_unknown_paths` |  | PASS |
| B-60 | B | `arch_b_broker.tests.test_live` | `test_notices_disclose_actual_persistence_and_demo_limitations` |  | PASS |
| B-61 | B | `arch_b_broker.tests.test_live` | `test_public_notices_are_exact_static_text_without_identity_or_state_calls` |  | PASS |
| B-62 | B | `arch_b_broker.tests.test_live` | `test_disabled_unknown_type_and_malformed_membership_fail_closed` |  | PASS |
| B-63 | B | `arch_b_broker.tests.test_live` | `test_managed_identity_requires_injected_local_endpoint` |  | PASS |
| B-64 | B | `arch_b_broker.tests.test_live` | `test_real_graph_request_shape_and_no_membership_cache` |  | PASS |
| B-65 | B | `arch_b_broker.tests.test_live` | `test_sdk_identity_failure_is_fail_closed` |  | PASS |
| B-66 | B | `arch_b_broker.tests.test_live` | `test_empty_or_weak_jwks_is_not_trusted` |  | PASS |
| B-67 | B | `arch_b_broker.tests.test_live` | `test_hs256_unsigned_and_wrong_signature_rejected` |  | PASS |
| B-68 | B | `arch_b_broker.tests.test_live` | `test_jwks_is_pinned_and_unknown_key_refresh_is_bounded` |  | PASS |
| B-69 | B | `arch_b_broker.tests.test_live` | `test_malformed_claims_and_cross_tenant_tokens_fail_closed` |  | PASS |
| B-70 | B | `arch_b_broker.tests.test_live` | `test_rs256_user_token_and_scope` |  | PASS |
| B-71 | B | `arch_b_broker.tests.test_live` | `test_contract_file_change_and_expiration_revoke_serving` |  | PASS |
| B-72 | B | `arch_b_broker.tests.test_live` | `test_http_health_access_request_auth_and_origin` |  | PASS |
| B-73 | B | `arch_b_broker.tests.test_live` | `test_live_directory_membership_guest_and_outage_denials` |  | PASS |
| B-74 | B | `arch_b_broker.tests.test_live` | `test_mcp_auth_tools_and_malformed_calls` |  | PASS |
| B-75 | B | `arch_b_broker.tests.test_live` | `test_missing_auth_never_releases_data` |  | PASS |
| B-76 | B | `arch_b_broker.tests.test_live` | `test_sqlite_failure_never_releases_answer` |  | PASS |
| B-77 | B | `arch_b_broker.tests.test_live` | `test_success_is_explicit_demo_and_never_reveals_source_location` |  | PASS |
| B-78 | B | `arch_b_broker.tests.test_live` | `test_unpaired_surrogates_are_rejected_before_state_transaction` |  | PASS |
| B-79 | B | `arch_b_broker.tests.test_live` | `test_rate_and_coverage_survive_restart_and_are_user_isolated` |  | PASS |
| B-80 | B | `arch_b_broker.tests.test_live` | `test_second_process_connection_cannot_reset_active_state` |  | PASS |
| B-81 | B | `arch_b_broker.tests.test_live` | `test_state_is_bound_to_deployment_and_transaction_failure_rolls_back` |  | PASS |
| B-82 | B | `arch_b_broker.tests.test_live_blob` | `test_ambiguous_remote_commit_is_restored_conservatively` |  | PASS |
| B-83 | B | `arch_b_broker.tests.test_live_blob` | `test_audit_chain_restores_without_reset` |  | PASS |
| B-84 | B | `arch_b_broker.tests.test_live_blob` | `test_background_lease_failure_is_visible_and_permanently_fences_requests` |  | PASS |
| B-85 | B | `arch_b_broker.tests.test_live_blob` | `test_committed_rate_and_coverage_restore_after_restart` |  | PASS |
| B-86 | B | `arch_b_broker.tests.test_live_blob` | `test_etag_conflict_never_overwrites_remote` |  | PASS |
| B-87 | B | `arch_b_broker.tests.test_live_blob` | `test_every_sdk_operation_is_bounded_and_lease_precedes_download` |  | PASS |
| B-88 | B | `arch_b_broker.tests.test_live_blob` | `test_expired_lease_during_upload_withholds_content_and_poison_is_permanent` |  | PASS |
| B-89 | B | `arch_b_broker.tests.test_live_blob` | `test_failed_initial_checkpoint_never_starts_broker_and_releases_lease` |  | PASS |
| B-90 | B | `arch_b_broker.tests.test_live_blob` | `test_identity_outage_rolls_back_without_poisoning_a_confirmed_lease` |  | PASS |
| B-91 | B | `arch_b_broker.tests.test_live_blob` | `test_incomplete_or_malformed_sqlite_is_never_repaired_or_uploaded` |  | PASS |
| B-92 | B | `arch_b_broker.tests.test_live_blob` | `test_lease_renewal_failure_stops_requests` |  | PASS |
| B-93 | B | `arch_b_broker.tests.test_live_blob` | `test_local_rollback_does_not_checkpoint_and_never_releases_result` |  | PASS |
| B-94 | B | `arch_b_broker.tests.test_live_blob` | `test_lost_lease_fences_old_writer_and_restart_ignores_local_cache` |  | PASS |
| B-95 | B | `arch_b_broker.tests.test_live_blob` | `test_missing_empty_wrong_binding_or_corrupt_blob_never_resets` |  | PASS |
| B-96 | B | `arch_b_broker.tests.test_live_blob` | `test_overlapping_instance_cannot_acquire_blob_lease` |  | PASS |
| B-97 | B | `arch_b_broker.tests.test_live_blob` | `test_oversize_checkpoint_fails_without_replacing_remote` |  | PASS |
| B-98 | B | `arch_b_broker.tests.test_live_blob` | `test_release_failure_is_visible_and_close_removes_only_its_cache` |  | PASS |
| B-99 | B | `arch_b_broker.tests.test_live_blob` | `test_renewal_that_exceeds_safety_deadline_is_not_trusted` |  | PASS |
| B-100 | B | `arch_b_broker.tests.test_live_blob` | `test_request_does_not_return_until_upload_and_renewal_never_races_sdk` |  | PASS |
| B-101 | B | `arch_b_broker.tests.test_live_blob` | `test_search_and_mcp_never_return_generated_content_on_checkpoint_failure` |  | PASS |
| B-102 | B | `arch_b_broker.tests.test_live_blob` | `test_smaller_local_stale_database_cannot_replace_remote` |  | PASS |
| B-103 | B | `arch_b_broker.tests.test_live_blob` | `test_unrelated_valid_sqlite_cannot_become_a_new_broker` |  | PASS |
| B-104 | B | `arch_b_broker.tests.test_live_blob` | `test_upload_failure_withholds_answer_and_poisoned_process_cannot_resume` |  | PASS |
| B-105 | B | `arch_b_broker.tests.test_live_blob` | `test_upload_without_etag_poisoned_even_if_remote_was_committed` |  | PASS |
| B-106 | B | `arch_b_broker.tests.test_live_blob` | `test_entry_point_uses_same_managed_credential_and_anonymous_request_never_calls_graph` |  | PASS |
| C-01 | C | `arch_c_publish.tests.test_arch_c` | `test_artifact_card_and_publish_manifest` | artifacts/arch_c: sample card carries provenance and no PII/original URL; manifest has columns + Graph requests. | PASS |
| C-02 | C | `arch_c_publish.tests.test_arch_c` | `test_publish_manifest_columns_and_graph_requests` | Simulated publisher builds Graph request objects and custom columns; no real MIP or retention policy is applied. | PASS |
| C-03 | C | `arch_c_publish.tests.test_arch_c` | `test_workflow_audit_and_persistence` | Every transition is audited in a verifiable hash chain and workflow state survives a restart. | PASS |
| C-04 | C | `arch_c_publish.tests.test_arch_c` | `test_approval_is_bound_to_source_version` | If the source changes between approval and publishing, publishing is refused and the request goes STALE. | PASS |
| C-05 | C | `arch_c_publish.tests.test_arch_c` | `test_bob_finds_card_carol_and_dave_cannot` | Simulated native index permits bob and denies carol/dave; not effective SharePoint or Copilot user denial. | PASS |
| C-06 | C | `arch_c_publish.tests.test_arch_c` | `test_card_provenance_redaction_and_no_original_url` | Cards carry the provenance header (fingerprint, approval id, generatedAt, expiresAt, notice), redacted text, an excerpt <= maxExcerptChars, the broker access link - and no original URL, path or id. | PASS |
| C-07 | C | `arch_c_publish.tests.test_arch_c` | `test_confidential_requires_compliance_approval` | Confidential content needs owner + a different compliance officer before publishing. | PASS |
| C-08 | C | `arch_c_publish.tests.test_arch_c` | `test_contract_revocation_withdraws_all_cards` | Revoking the sharing contract revokes every open request and deletes all published cards. | PASS |
| C-09 | C | `arch_c_publish.tests.test_arch_c` | `test_duplicate_requests_are_idempotent` | Repeated requests for the same source+purpose return the same open request; a new one follows a terminal state. | PASS |
| C-10 | C | `arch_c_publish.tests.test_arch_c` | `test_expiry_removes_card` | Advancing the local clock and invoking the sweep removes the simulated card/index entry; not actual-deadline deletion or live search lag. | PASS |
| C-11 | C | `arch_c_publish.tests.test_arch_c` | `test_general_document_publishes_after_owner_approval` | A General document needs only the data owner's approval; compliance approval is not applicable. | PASS |
| C-12 | C | `arch_c_publish.tests.test_arch_c` | `test_highly_confidential_and_out_of_scope_auto_rejected` | HC (above ceiling), /Internal and /Shareable/Drafts requests are auto-rejected and never published. | PASS |
| C-13 | C | `arch_c_publish.tests.test_arch_c` | `test_invalid_transitions_rejected` | The state machine refuses illegal transitions (double publish, approving a rejected request, etc.). | PASS |
| C-14 | C | `arch_c_publish.tests.test_arch_c` | `test_label_raised_or_source_deleted_after_publish_revokes` | Relabelling a source to Highly Confidential or deleting it withdraws the published card on the next check. | PASS |
| C-15 | C | `arch_c_publish.tests.test_arch_c` | `test_no_publish_without_owner_approval` | A REQUESTED (unapproved) request cannot be published; nothing reaches the Knowledge Exchange site. | PASS |
| C-16 | C | `arch_c_publish.tests.test_arch_c` | `test_requester_eligibility` | Only non-guest audience members with the contract purpose can request (carol, dave, wrong purpose rejected). | PASS |
| C-17 | C | `arch_c_publish.tests.test_arch_c` | `test_revoke_removes_card` | The data owner (or compliance) can revoke: the card is deleted and the request is REVOKED; others cannot. | PASS |
| C-18 | C | `arch_c_publish.tests.test_arch_c` | `test_source_change_makes_card_stale_then_republished` | A source edit withdraws the card (STALE); after re-approval it is regenerated and REPUBLISHED. | PASS |
| CM-01 | Common | `common.tests.test_common` | `test_audit_chain_detects_edit_delete_reorder_and_keyed_mode` | Hash chain verifies; editing, deleting or re-ordering records is detected; HMAC-keyed chains need the key. | PASS |
| CM-02 | Common | `common.tests.test_common` | `test_drive_delta_paging_tombstones_and_resync` | Delta: paged full crawl with deltaLink, incremental changes incl. 'deleted' facet, 410-style resync. | PASS |
| CM-03 | Common | `common.tests.test_common` | `test_fingerprint_and_clock_helpers` | Fingerprints change with content or eTag; ISO timestamps round-trip in UTC with a 'Z' suffix. | PASS |
| CM-04 | Common | `common.tests.test_common` | `test_network_guard_blocks_non_loopback` | The offline guard blocks DNS/connections to Microsoft endpoints while allowing loopback. | PASS |
| CM-05 | Common | `common.tests.test_common` | `test_contract_validator_rejects_bad_values` | Missing fields, unknown labels, bad GUIDs, out-of-range numbers and bad timestamps are rejected. | PASS |
| CM-06 | Common | `common.tests.test_common` | `test_label_ordering_fails_closed` | Label order Public < General < Confidential < Highly Confidential; unknown/missing labels exceed any ceiling. | PASS |
| CM-07 | Common | `common.tests.test_common` | `test_policy_gate_decisions` | The shared gate allows in-scope supported files and explains every denial. | PASS |
| CM-08 | Common | `common.tests.test_common` | `test_sample_contract_loads_and_validates` | The sample Sharing Contract has every required field with the agreed values and validates cleanly. | PASS |
| CM-09 | Common | `common.tests.test_common` | `test_scope_paths_are_normalised` | Path scope uses decoded, normalised, case-insensitive prefixes ('..' traversal and look-alike prefixes fail). | PASS |
| CM-10 | Common | `common.tests.test_common` | `test_chat_digest_pseudonymises_participants` | Chat digests group messages per day, skip system events and replace author names/mentions with aliases. | PASS |
| CM-11 | Common | `common.tests.test_common` | `test_chunker_respects_max_and_preserves_content` | Every chunk is <= max_chars and the chunks preserve all words in order (paragraph -> line -> sentence -> word). | PASS |
| CM-12 | Common | `common.tests.test_common` | `test_html_to_text` | HTML extraction drops script/style/comments, keeps table cells pipe-separated and returns the title. | PASS |
| CM-13 | Common | `common.tests.test_common` | `test_injection_lines_are_flagged_and_stripped` | Imperative injection sentences (EN/KR, tags, role overrides) are removed and flagged; benign text is kept. | PASS |
| CM-14 | Common | `common.tests.test_common` | `test_summariser_is_deterministic_and_extractive` | Summaries/key facts/excerpts are deterministic, verbatim from the source, length-bounded and skip the title. | PASS |
| CM-15 | Common | `common.tests.test_common` | `test_large_input_is_fast` | Redaction of a 4 MB+ log stays well under a second thanks to cheap prefilters. | PASS |
| CM-16 | Common | `common.tests.test_common` | `test_no_false_positives_on_engineering_text` | Dates, lot ids, recipe values, timestamps and non-credential key=value pairs are left untouched. | PASS |
| CM-17 | Common | `common.tests.test_common` | `test_redacts_each_category_with_tokens` | Emails, KR mobiles (local/+82), RRNs, API keys, JWT/bearer tokens, connection strings and SharePoint links. | PASS |
| BM-01 | Benchmark | `bench.tests.test_benchmark` | `test_answer_rates_and_policy_signals` | bob gets useful answers everywhere; B shows its per-request controls (403s for carol/dave, guard withholds). | PASS |
| BM-02 | Benchmark | `bench.tests.test_benchmark` | `test_zero_leaks_and_zero_unauthorised_results` | 180 fixed synthetic runs detected no unauthorised results, seeded PII/secrets, original-location or HC canaries; not general or live-service safety. | PASS |
| X-01 | Other | `live_poc.tests.test_live` | `test_a_and_c_timed_out_puts_remain_removable_including_unknown_remote_id` |  | PASS |
| X-02 | Other | `live_poc.tests.test_live` | `test_apply_persists_pending_before_every_put_then_commits_and_reuses_schema` |  | PASS |
| X-03 | Other | `live_poc.tests.test_live` | `test_approval_never_grants_everyone_even_if_manifest_reapproved` |  | PASS |
| X-04 | Other | `live_poc.tests.test_live` | `test_changed_output_bytes_or_source_fingerprint_refuses_publication` |  | PASS |
| X-05 | Other | `live_poc.tests.test_live` | `test_cli_does_not_surface_untrusted_validation_or_graph_error_details` |  | PASS |
| X-06 | Other | `live_poc.tests.test_live` | `test_cli_prepare_surfaces_safe_no_eligible_fixtures_diagnostic` |  | PASS |
| X-07 | Other | `live_poc.tests.test_live` | `test_failed_delete_retains_retryable_state_and_ttl_deletes_both` |  | PASS |
| X-08 | Other | `live_poc.tests.test_live` | `test_initial_delta_every_page_and_six_real_provenance_docs` |  | PASS |
| X-09 | Other | `live_poc.tests.test_live` | `test_live_default_opaque_refs_do_not_use_shared_prototype_key` |  | PASS |
| X-10 | Other | `live_poc.tests.test_live` | `test_missing_unsigned_or_changed_approval_refuses_publication` |  | PASS |
| X-11 | Other | `live_poc.tests.test_live` | `test_optional_broker_manifest_binds_document_bytes_and_contract` |  | PASS |
| X-12 | Other | `live_poc.tests.test_live` | `test_prepare_no_publishes_unsigned_template_real_snapshot` |  | PASS |
| X-13 | Other | `live_poc.tests.test_live` | `test_registry_optin_label_hash_and_unregistered_deny_before_transform` |  | PASS |
| X-14 | Other | `live_poc.tests.test_live` | `test_source_path_change_denied` |  | PASS |
| X-15 | Other | `live_poc.tests.test_live` | `test_source_withdrawal_reconciliation_removes_a_and_c` |  | PASS |
| X-16 | Other | `live_poc.tests.test_live` | `test_withdraw_republish_lost_response_then_withdraw_leaves_no_orphan` |  | PASS |
| X-17 | Other | `live_poc.tests.test_live` | `test_withdrawal_cannot_replay_previously_approved_plan` |  | PASS |
| X-18 | Other | `live_poc.tests.test_live` | `test_wrong_deployment_ledger_fails_closed` |  | PASS |
| X-19 | Other | `live_poc.tests.test_live` | `test_failed_unknown_and_timed_out_operations_never_ready` |  | PASS |
| X-20 | Other | `live_poc.tests.test_live` | `test_lowercase_location_and_terminal_status` |  | PASS |
| X-21 | Other | `live_poc.tests.test_live` | `test_missing_location_never_marks_ready` |  | PASS |
| X-22 | Other | `live_poc.tests.test_live` | `test_operation_location_cannot_change_host` |  | PASS |
| X-23 | Other | `live_poc.tests.test_live` | `test_readback_accepts_only_observed_service_metadata` |  | PASS |
| X-24 | Other | `live_poc.tests.test_live` | `test_content_redirect_strips_bearer` |  | PASS |
| X-25 | Other | `live_poc.tests.test_live` | `test_cross_tenant_download_redirect_rejected` |  | PASS |
| X-26 | Other | `live_poc.tests.test_live` | `test_graph_only_and_no_redirect_on_normal_requests` |  | PASS |
| X-27 | Other | `live_poc.tests.test_live` | `test_http_error_bodies_never_surface` |  | PASS |
| X-28 | Other | `live_poc.tests.test_live` | `test_pipeline_token_identity_and_app_only_roles_required` |  | PASS |
| X-29 | Other | `live_poc.tests.test_live` | `test_retry_after_case_insensitive_and_error_body_omitted` |  | PASS |
| X-30 | Other | `live_poc.tests.test_live` | `test_schema_deadline_bounds_transport_retry_after` |  | PASS |
