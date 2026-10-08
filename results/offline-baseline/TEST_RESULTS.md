# Test results

> **Sanitized public-release copy — historical evidence, not new live tests.** Tenant/account/resource identifiers are placeholders; screenshots are labeled sanitized copies with opaque redactions where needed. Original private evidence is retained separately. Pass/fail/blocked/not-run distinctions are preserved. Historical approvals, deadlines and active-state statements describe their recorded checkpoint only, not current status or permission to act. Sanitized artifacts cannot establish the original cryptographic hashes.

- Command: `python3 -m unittest discover -v   (recorded via python3 tools/run_tests.py)`
- Python 3.12.14, generated 2026-10-07T02:50:15Z, wall time 40.49 s
- **104 passed, 0 failed, 0 errors, 0 skipped (total 104)**

| Architecture | Tests | Passed | Failed |
|---|---|---|---|
| A - derived index via Copilot connector | 38 | 38 | 0 |
| B - Knowledge Broker API (PDP) | 29 | 29 | 0 |
| C - governed knowledge cards | 18 | 18 | 0 |
| Common building blocks | 17 | 17 | 0 |
| Benchmark | 2 | 2 | 0 |

| Test id | Architecture | Test | What it verifies | Result |
|---|---|---|---|---|
| A-01 | A | `test_acl_is_audience_group_only` | Every item ACL grants only the Team B group (never the source group/ACL, never everyone); denies are guests only. | PASS |
| A-02 | A | `test_audit_chain_verifies_and_matches_writes` | Every PUT has a 'publish' audit record, the hash chain verifies, and no PII or secrets reach the audit log. | PASS |
| A-03 | A | `test_connection_payload_valid` | Connection JSON {id,name,description} is valid: id 3-32 alphanumeric, not starting with 'Microsoft'. | PASS |
| A-04 | A | `test_container_name_is_contract_display_name` | Every item's containerName (semantic label) is the contract display name, never an original path. | PASS |
| A-05 | A | `test_highly_confidential_internal_and_drafts_excluded` | Highly Confidential (label ceiling), /Internal (scope) and /Shareable/Drafts (excluded path) produce no items. | PASS |
| A-06 | A | `test_item_ids_deterministic_and_valid` | Item ids are URL-safe, <=128 chars and identical across independent full crawls. | PASS |
| A-07 | A | `test_large_document_chunked_for_retrieval_within_item_limit` | The 4.5 MB log is split into <=8,000-char items (retrieval-quality choice); every item is far below the 30 MB per-item limit, and an item above the limit is rejected by the validator. | PASS |
| A-08 | A | `test_no_original_url_path_or_id_in_any_payload` | No original webUrl, library path, site/drive/driveItem id or Teams link appears in any Graph request body. | PASS |
| A-09 | A | `test_pii_and_secrets_redacted` | Emails, KR mobiles, KR RRN, API key, connection string and internal links are replaced by [REDACTED:*] tokens. | PASS |
| A-10 | A | `test_prompt_injection_neutralised` | The injection sentence is stripped from derived items, flagged in state and audited; other content is kept. | PASS |
| A-11 | A | `test_schema_valid_and_registered` | Schema uses baseType externalItem, required semantic labels, valid flags, and registers via PATCH + 202 polling. | PASS |
| A-12 | A | `test_teams_chat_ingested_as_derived_items` | Teams chat becomes per-day digests (pseudonymised, redacted) plus the chat file from the sender's OneDrive. | PASS |
| A-13 | A | `test_trimming_bob_sees_carol_and_guest_dave_do_not` | Search trimming: bob (Team B) gets results; carol (no group), dave (Team B guest, deny ACE) and alice get none. | PASS |
| A-14 | A | `test_audience_change_by_group_membership_only` | Removing bob from grp-team-b removes his access at once (the ACL references the group) with zero item writes. | PASS |
| A-15 | A | `test_audit_tampering_detected` | Editing a persisted audit record breaks the hash chain and verify() pinpoints it. | PASS |
| A-16 | A | `test_contract_revocation_deletes_all_items` | Revoking the contract deletes every derived item; later syncs publish nothing. | PASS |
| A-17 | A | `test_expired_delta_token_triggers_full_resync` | HTTP 410-style expired delta token falls back to a full crawl without duplicate writes. | PASS |
| A-18 | A | `test_incremental_delete_propagates` | A source deletion (delta 'deleted' facet) deletes all derived items for that doc. | PASS |
| A-19 | A | `test_incremental_update_republishes_only_changed_document` | An edit to one doc re-publishes only that doc's items via drive delta; new content becomes searchable. | PASS |
| A-20 | A | `test_move_out_of_scope_and_relabel_above_ceiling_remove_items` | Moving a doc to /Internal or relabelling it Highly Confidential removes its derived items on the next sync. | PASS |
| A-21 | A | `test_new_guest_visible_until_acl_refresh_then_denied` | ACL drift: a guest newly added to Team B can see items until the next sync re-publishes them with a deny ACE. | PASS |
| A-22 | A | `test_new_in_scope_file_is_published` | A new file under /Shareable is picked up by the next incremental sync. | PASS |
| A-23 | A | `test_no_change_rerun_zero_writes` | Re-running full and incremental sync with no source change performs zero PUT/DELETE calls. | PASS |
| A-24 | A | `test_refresh_before_expiry_extends_validity` | An incremental run inside the refresh window re-publishes due items with a later validUntil. | PASS |
| A-25 | A | `test_shrinking_document_deletes_superseded_chunks` | When a doc shrinks, extract items that are no longer produced are deleted (no orphans). | PASS |
| A-26 | A | `test_state_persists_and_resumes_without_rewrites` | JSON state survives a restart: a new engine resumes from the saved deltaLink with zero writes. | PASS |
| A-27 | A | `test_suspended_contract_purges_on_next_run` | A contract that is no longer active (suspended) purges derived content on the next scheduled run. | PASS |
| A-28 | A | `test_tenant_wide_audience_only_when_contract_allows` | everyone/everyoneExceptGuests ACEs appear only if the contract allows it; their value is the (configurable) tenant ID. | PASS |
| A-29 | A | `test_ttl_expiry_sweep_removes_items` | After ttlDays without refresh (e.g. engine down) the expiry sweep deletes every derived item. | PASS |
| A-30 | A | `test_artifacts_are_valid_graph_payloads` | artifacts/arch_a (connection, schema, example items) validate against the Graph constraints and leak nothing. | PASS |
| A-31 | A | `test_client_retries_throttling_with_retry_after` | ConnectorClient retries 429/503 honouring Retry-After before succeeding. | PASS |
| A-32 | A | `test_connection_id_derived_from_contract` | The connection id is derived deterministically from the contract id and is valid. | PASS |
| A-33 | A | `test_connector_declarative_agent_v18` | declarativeAgent.connector.json (v1.8) scopes Copilot to this connection via GraphConnectors and is up to date. | PASS |
| A-34 | A | `test_http_transport_request_formation` | HttpGraphTransport forms the client-credentials token request and Graph calls correctly (fake opener, no network). | PASS |
| A-35 | A | `test_mock_rejects_items_before_schema_and_invalid_payloads` | The emulator returns 400 before schema completion and for invalid items (incl. missing/empty acl), 409 for duplicates. | PASS |
| A-36 | A | `test_mock_trimming_semantics_deny_wins` | Emulator trimming: grant via user/group/everyoneExceptGuests, guests excluded from everyoneExceptGuests, deny wins. | PASS |
| A-37 | A | `test_validators_reject_invalid_items_and_connections` | Item/connection validators enforce id rules, ACL enums, content type, DateTime format, schema typing and the 30 MB item limit. | PASS |
| A-38 | A | `test_validators_reject_invalid_schemas` | Schema validator enforces name length/charset, property count, searchable types, searchable+refinable and unique labels. | PASS |
| B-01 | B | `test_audit_chain_verifies_and_detects_tampering` | The JSONL audit hash chain verifies; editing or deleting a record is detected at the right index. | PASS |
| B-02 | B | `test_exfiltration_guard_triggers_after_coverage_threshold` | Per user and document, rolling-24h unique chunk coverage is capped at the threshold; excess chunks are withheld. | PASS |
| B-03 | B | `test_guest_member_is_excluded` | dave (Team B member, userType Guest) gets 403 guest_excluded; a token claiming acct=1 is also refused (fail closed). | PASS |
| B-04 | B | `test_highly_confidential_never_returned` | Queries aimed at the Highly Confidential doc return none of its content. | PASS |
| B-05 | B | `test_http_routing_and_input_validation` | GET /healthz is 200; wrong method 405; unknown route 404; invalid JSON / bad fields 400; oversize body 413. | PASS |
| B-06 | B | `test_invalid_tokens_rejected_with_401` | Bad signature, expired, wrong audience/tenant/issuer, alg=none, malformed and missing tokens all get 401. | PASS |
| B-07 | B | `test_mcp_tools_list_and_call` | MCP JSON-RPC on /mcp: initialize, tools/list matches mcp-tools.json, tools/call enforces the same policy. | PASS |
| B-08 | B | `test_member_gets_answer_with_opaque_citations_over_http` | bob (Team B member) gets a 200 answer over localhost HTTP with opaque refs and broker access links only. | PASS |
| B-09 | B | `test_non_member_gets_403_and_is_audited` | carol (not in Team B) gets 403 not_in_audience with no content, and the denial is in the audit log. | PASS |
| B-10 | B | `test_purpose_must_match_contract` | A request whose declared purpose differs from the contract purpose is refused (403 purpose_mismatch). | PASS |
| B-11 | B | `test_rate_limit_returns_429_with_retry_after` | The 11th request inside one minute gets 429 + Retry-After; capacity refills over time. | PASS |
| B-12 | B | `test_redaction_applied_to_answers` | Answers redact e-mails, KR mobiles, RRNs and internal links; secrets never reach the private index at all. | PASS |
| B-13 | B | `test_responses_never_contain_original_locations` | Across a probe set, no response contains original URLs, paths, driveItem ids or unredacted PII. | PASS |
| B-14 | B | `test_revoked_contract_denies_everything` | When the contract is revoked the broker refuses all requests (403 contract_inactive). | PASS |
| B-15 | B | `test_scope_and_user_context_required` | A token without Knowledge.Ask gets 403 insufficient_scope; an app-only token (no scp) gets 401. | PASS |
| B-16 | B | `test_search_endpoint_applies_same_policy` | /search returns <=5 opaque citations (title, url, label id) with <=160-char redacted snippets; carol 403; top>5 is 400. | PASS |
| B-17 | B | `test_verbatim_cap_and_max_citations` | Every excerpt is <= maxExcerptChars (and visibly truncated from longer chunks); <= 3 citations per answer. | PASS |
| B-18 | B | `test_exfiltration_guard_returns_403_when_everything_is_withheld` | With only one document available, once its allowance is used further new excerpts are refused with 403. | PASS |
| B-19 | B | `test_source_injection_has_no_effect_and_is_flagged` | Answers from the RCA with the injected line equal answers from a copy without it; citations are flagged. | PASS |
| B-20 | B | `test_pdp_filters_hc_internal_and_drafts_even_if_indexed` | Even if the private index (wrongly) contains HC, /Internal and /Drafts content, the PDP never returns it. | PASS |
| B-21 | B | `test_purview_blocks_prompt` | A prompt matching a Purview policy is refused (403 purview_blocked) before any retrieval, and audited. | PASS |
| B-22 | B | `test_purview_blocks_response_without_consuming_coverage` | A response matching a Purview policy is withheld (403 purview_blocked); no excerpt is released or counted. | PASS |
| B-23 | B | `test_purview_called_for_prompt_and_response_and_audited` | Every allowed request calls process_content twice (uploadText for the prompt, downloadText for the response). | PASS |
| B-24 | B | `test_artifacts_match_generated_manifests` | artifacts/arch_b contains the broker manifests exactly as generated from the constants file. | PASS |
| B-25 | B | `test_declarative_agent_validator_rules_v18` | DA v1.8 rules: required keys, unrecognised keys invalid, length limits, <=12 starters, 1-10 {id,file} actions, disclaimer <=500, GraphConnectors connection ids must be valid. | PASS |
| B-26 | B | `test_jwt_validator_unit_rules` | HS256 validator: round-trip, leeway on exp, list audience, tampered payload and alg confusion rejected. | PASS |
| B-27 | B | `test_manifests_valid_and_cross_consistent` | Manifests match the generator (constants), validate as DA v1.8 / plugin v2.4 / MCP tools/list, and operationIds == plugin function names == MCP tool names; both plugins use OAuthPluginVault SSO. | PASS |
| B-28 | B | `test_pdp_unit_rules` | PDP: label/path/chat scope per chunk, min-one-chunk rule for tiny docs, per-user isolation, rolling window. | PASS |
| B-29 | B | `test_plugin_and_mcp_validator_rules_v24` | Plugin v2.4 rules: required keys, namespace ^[A-Za-z0-9]+$, function names ^[A-Za-z0-9_]+$, run_for_functions subset of functions, operationIds match; MCP tools need name/description/inputSchema. | PASS |
| C-01 | C | `test_artifact_card_and_publish_manifest` | artifacts/arch_c: sample card carries provenance and no PII/original URL; manifest has columns + Graph requests. | PASS |
| C-02 | C | `test_publish_manifest_columns_and_graph_requests` | Publishing writes list-item columns (SourceFingerprint, ExpiryDate, ApprovalId, RetentionLabel) and emits the production Graph requests (PUT .../{parentId}:/{fileName}:/content, PATCH .../listItem/fields, retentionLabel). | PASS |
| C-03 | C | `test_workflow_audit_and_persistence` | Every transition is audited in a verifiable hash chain and workflow state survives a restart. | PASS |
| C-04 | C | `test_approval_is_bound_to_source_version` | If the source changes between approval and publishing, publishing is refused and the request goes STALE. | PASS |
| C-05 | C | `test_bob_finds_card_carol_and_dave_cannot` | Native index trimming: bob (site member) finds the card; carol (no access) and dave (guest) get nothing. | PASS |
| C-06 | C | `test_card_provenance_redaction_and_no_original_url` | Cards carry the provenance header (fingerprint, approval id, generatedAt, expiresAt, notice), redacted text, an excerpt <= maxExcerptChars, the broker access link - and no original URL, path or id. | PASS |
| C-07 | C | `test_confidential_requires_compliance_approval` | Confidential content needs owner + a different compliance officer before publishing. | PASS |
| C-08 | C | `test_contract_revocation_withdraws_all_cards` | Revoking the sharing contract revokes every open request and deletes all published cards. | PASS |
| C-09 | C | `test_duplicate_requests_are_idempotent` | Repeated requests for the same source+purpose return the same open request; a new one follows a terminal state. | PASS |
| C-10 | C | `test_expiry_removes_card` | After ttlDays the expiry sweep withdraws the card (EXPIRED) and it disappears from search. | PASS |
| C-11 | C | `test_general_document_publishes_after_owner_approval` | A General document needs only the data owner's approval; compliance approval is not applicable. | PASS |
| C-12 | C | `test_highly_confidential_and_out_of_scope_auto_rejected` | HC (above ceiling), /Internal and /Shareable/Drafts requests are auto-rejected and never published. | PASS |
| C-13 | C | `test_invalid_transitions_rejected` | The state machine refuses illegal transitions (double publish, approving a rejected request, etc.). | PASS |
| C-14 | C | `test_label_raised_or_source_deleted_after_publish_revokes` | Relabelling a source to Highly Confidential or deleting it withdraws the published card on the next check. | PASS |
| C-15 | C | `test_no_publish_without_owner_approval` | A REQUESTED (unapproved) request cannot be published; nothing reaches the Knowledge Exchange site. | PASS |
| C-16 | C | `test_requester_eligibility` | Only non-guest audience members with the contract purpose can request (carol, dave, wrong purpose rejected). | PASS |
| C-17 | C | `test_revoke_removes_card` | The data owner (or compliance) can revoke: the card is deleted and the request is REVOKED; others cannot. | PASS |
| C-18 | C | `test_source_change_makes_card_stale_then_republished` | A source edit withdraws the card (STALE); after re-approval it is regenerated and REPUBLISHED. | PASS |
| CM-01 | Common | `test_audit_chain_detects_edit_delete_reorder_and_keyed_mode` | Hash chain verifies; editing, deleting or re-ordering records is detected; HMAC-keyed chains need the key. | PASS |
| CM-02 | Common | `test_drive_delta_paging_tombstones_and_resync` | Delta: paged full crawl with deltaLink, incremental changes incl. 'deleted' facet, 410-style resync. | PASS |
| CM-03 | Common | `test_fingerprint_and_clock_helpers` | Fingerprints change with content or eTag; ISO timestamps round-trip in UTC with a 'Z' suffix. | PASS |
| CM-04 | Common | `test_network_guard_blocks_non_loopback` | The offline guard blocks DNS/connections to Microsoft endpoints while allowing loopback. | PASS |
| CM-05 | Common | `test_contract_validator_rejects_bad_values` | Missing fields, unknown labels, bad GUIDs, out-of-range numbers and bad timestamps are rejected. | PASS |
| CM-06 | Common | `test_label_ordering_fails_closed` | Label order Public < General < Confidential < Highly Confidential; unknown/missing labels exceed any ceiling. | PASS |
| CM-07 | Common | `test_policy_gate_decisions` | The shared gate allows in-scope supported files and explains every denial. | PASS |
| CM-08 | Common | `test_sample_contract_loads_and_validates` | The sample Sharing Contract has every required field with the agreed values and validates cleanly. | PASS |
| CM-09 | Common | `test_scope_paths_are_normalised` | Path scope uses decoded, normalised, case-insensitive prefixes ('..' traversal and look-alike prefixes fail). | PASS |
| CM-10 | Common | `test_chat_digest_pseudonymises_participants` | Chat digests group messages per day, skip system events and replace author names/mentions with aliases. | PASS |
| CM-11 | Common | `test_chunker_respects_max_and_preserves_content` | Every chunk is <= max_chars and the chunks preserve all words in order (paragraph -> line -> sentence -> word). | PASS |
| CM-12 | Common | `test_html_to_text` | HTML extraction drops script/style/comments, keeps table cells pipe-separated and returns the title. | PASS |
| CM-13 | Common | `test_injection_lines_are_flagged_and_stripped` | Imperative injection sentences (EN/KR, tags, role overrides) are removed and flagged; benign text is kept. | PASS |
| CM-14 | Common | `test_summariser_is_deterministic_and_extractive` | Summaries/key facts/excerpts are deterministic, verbatim from the source, length-bounded and skip the title. | PASS |
| CM-15 | Common | `test_large_input_is_fast` | Redaction of a 4 MB+ log stays well under a second thanks to cheap prefilters. | PASS |
| CM-16 | Common | `test_no_false_positives_on_engineering_text` | Dates, lot ids, recipe values, timestamps and non-credential key=value pairs are left untouched. | PASS |
| CM-17 | Common | `test_redacts_each_category_with_tokens` | Emails, KR mobiles (local/+82), RRNs, API keys, JWT/bearer tokens, connection strings and SharePoint links. | PASS |
| BM-01 | Benchmark | `test_answer_rates_and_policy_signals` | bob gets useful answers everywhere; B shows its per-request controls (403s for carol/dave, guard withholds). | PASS |
| BM-02 | Benchmark | `test_zero_leaks_and_zero_unauthorised_results` | All 3 architectures x 20 probes x 3 users: 0 unauthorised results, 0 PII, 0 original URLs, 0 HC leaks. | PASS |
