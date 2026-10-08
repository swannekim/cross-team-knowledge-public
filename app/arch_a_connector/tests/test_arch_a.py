"""Architecture A - derived index via a custom Copilot (Graph) connector."""
from __future__ import annotations

import io
import json
import urllib.error
from urllib.parse import parse_qs

from arch_a_connector import validators as v
from arch_a_connector.graph_client import ConnectorClient, GraphError, HttpGraphTransport, MockGraphTransport
from arch_a_connector.graph_models import acl_entry, build_external_item, build_schema, schema_property
from arch_a_connector.sync_engine import StateStore, build_default_engine, connection_id_for
from common.audit import AuditLog
from common.clock import FixedClock, parse_iso
from common.evaluation import (HIGHLY_CONFIDENTIAL_CANARIES, INJECTION_CANARY, OUT_OF_SCOPE_CANARIES, PII_CANARIES,
                               SECRET_CANARIES, find_canaries, original_location_markers, pii_findings)
from common.paths import ARTIFACTS_DIR
from common.refs import BROKER_BASE_URL
from common.sources import load_environment
from common.testing import OfflineTestCase

GRP_A = "a0a0a0a0-1111-4111-8111-0000000000aa"
GRP_B = "b0b0b0b0-2222-4222-8222-0000000000bb"
DAVE = "0dafe000-0000-4000-8000-0000000000d4"


def _doc_id(env, prefix: str) -> str:
    return next(i["id"] for i in env.drive.files() if i["name"].startswith(prefix))


def _new(**kwargs):
    clock = FixedClock("2026-10-07T01:00:00Z")
    env = load_environment(clock)
    engine, transport = build_default_engine(env, clock=clock, **kwargs)
    return env, clock, engine, transport


def _put_bodies(transport) -> list:
    return [json.loads(e["body"]) for e in transport.request_log if e["method"] == "PUT" and e["body"]]


class ArchABaseline(OfflineTestCase):
    """Read-only checks against one full crawl (shared for speed)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env, cls.clock, cls.engine, cls.transport = _new()
        cls.stats = cls.engine.run_full()
        cls.cid = cls.engine.client.connection_id
        cls.items = cls.transport.items(cls.cid)
        cls.wire = "\n".join(cls.transport.logged_bodies())

    def test_connection_payload_valid(self):
        """Connection JSON {id,name,description} is valid: id 3-32 alphanumeric, not starting with 'Microsoft'."""
        body = json.loads(next(e["body"] for e in self.transport.request_log if e["path"] == "/external/connections"))
        self.assertEqual(set(body), {"id", "name", "description"})
        self.assertEqual(v.validate_connection(body), [])
        self.assertRegex(body["id"], r"^[A-Za-z0-9]{3,32}$")
        self.assertFalse(body["id"].lower().startswith("microsoft"))

    def test_schema_valid_and_registered(self):
        """Schema uses baseType externalItem, required semantic labels, valid flags, and registers via PATCH + 202 polling."""
        schema = build_schema()
        self.assertEqual(v.validate_schema(schema), [])
        self.assertEqual(schema["baseType"], "microsoft.graph.externalItem")
        props = {p["name"]: p for p in schema["properties"]}
        self.assertEqual(set(props), {"title", "url", "lastModifiedDateTime", "containerName", "sourceTeam", "sensitivity",
                                      "derivativeType", "contractId", "validUntil", "sourceFingerprint"})
        self.assertLessEqual(len(props), v.cfg("MAX_PROPERTIES"))
        self.assertEqual(props["containerName"]["labels"], ["containerName"])
        self.assertEqual(props["title"]["labels"], ["title"])
        self.assertEqual(props["url"]["labels"], ["url"])
        self.assertEqual(props["lastModifiedDateTime"]["labels"], ["lastModifiedDateTime"])
        self.assertEqual(props["validUntil"]["type"], "DateTime")
        for p in schema["properties"]:
            self.assertEqual(set(p), {"name", "type", "isSearchable", "isQueryable", "isRetrievable", "isRefinable", "labels"})
            if p["isSearchable"]:
                self.assertIn(p["type"], ("String", "StringCollection"))
                self.assertFalse(p["isRefinable"])
        patch = [e for e in self.transport.request_log if e["path"].endswith("/schema")]
        self.assertEqual([e["method"] for e in patch], ["PATCH"])
        self.assertEqual(self.transport.connections[self.cid]["schemaStatus"], "completed")

    def test_acl_is_audience_group_only(self):
        """Every item ACL grants only the Team B group (never the source group/ACL, never everyone); denies are guests only."""
        source_acl_groups = {p["grantedToV2"]["group"]["id"] for i in self.env.drive.files()
                             for p in self.env.drive.list_permissions(i["id"])["value"]}
        self.assertEqual(source_acl_groups, {GRP_A})
        bodies = _put_bodies(self.transport)
        self.assertGreater(len(bodies), 0)
        for body in bodies:
            grants = [a for a in body["acl"] if a["accessType"] == "grant"]
            denies = [a for a in body["acl"] if a["accessType"] == "deny"]
            self.assertEqual(grants, [{"type": "group", "value": GRP_B, "accessType": "grant"}])
            self.assertNotIn(GRP_A, json.dumps(body["acl"]))
            self.assertFalse({a["type"] for a in body["acl"]} & {"everyone", "everyoneExceptGuests"})
            self.assertEqual(denies, [{"type": "user", "value": DAVE, "accessType": "deny"}])

    def test_trimming_bob_sees_carol_and_guest_dave_do_not(self):
        """Search trimming: bob (Team B) gets results; carol (no group), dave (Team B guest, deny ACE) and alice get none."""
        query = "root cause yield excursion seasoning"
        self.assertGreater(len(self.transport.search("bob", query)), 0)
        for user in ("carol", "dave", "alice"):
            self.assertEqual(self.transport.search(user, query), [], user)
        self.assertEqual(self.transport.search("dave", "ETCH-07"), [])

    def test_no_original_url_path_or_id_in_any_payload(self):
        """No original webUrl, library path, site/drive/driveItem id or Teams link appears in any Graph request body."""
        wire = self.wire.lower()
        leaked = [m for m in original_location_markers(self.env) if m.lower() in wire]
        self.assertEqual(leaked, [])
        for item in self.items.values():
            self.assertTrue(item["properties"]["url"].startswith(f"{BROKER_BASE_URL}/access-request?ref=ref-"))

    def test_highly_confidential_internal_and_drafts_excluded(self):
        """Highly Confidential (label ceiling), /Internal (scope) and /Shareable/Drafts (excluded path) produce no items."""
        for prefix in ("NX7_gate_stack", "team_a_staffing", "WIP_NX8"):
            self.assertNotIn(_doc_id(self.env, prefix), self.engine.state.docs)
        self.assertEqual(find_canaries(self.wire, HIGHLY_CONFIDENTIAL_CANARIES + OUT_OF_SCOPE_CANARIES), [])
        self.assertEqual(dict(self.stats.excluded), {"label_above_ceiling": 1, "out_of_scope_path": 1, "excluded_path": 1})
        self.assertNotIn("Highly Confidential", {i["properties"]["sensitivity"] for i in self.items.values()})

    def test_pii_and_secrets_redacted(self):
        """Emails, KR mobiles, KR RRN, API key, connection string and internal links are replaced by [REDACTED:*] tokens."""
        self.assertEqual(find_canaries(self.wire, PII_CANARIES + SECRET_CANARIES), [])
        for item in self.items.values():
            self.assertEqual(pii_findings(item["content"]["value"] + json.dumps(item["properties"])), [])
        for token in ("EMAIL", "KR_MOBILE", "KR_RRN", "SECRET", "CONNECTION_STRING", "INTERNAL_URL"):
            self.assertIn(f"[REDACTED:{token}]", self.wire)

    def test_prompt_injection_neutralised(self):
        """The injection sentence is stripped from derived items, flagged in state and audited; other content is kept."""
        rca = _doc_id(self.env, "YE-0412")
        self.assertNotIn(INJECTION_CANARY.lower(), self.wire.lower())
        self.assertEqual(self.engine.state.docs[rca]["injectionFlags"], 1)
        self.assertTrue(any(r["data"]["sourceId"] == rca for r in self.engine.audit.records("injection_neutralised")))
        rca_text = " ".join(self.items[i]["content"]["value"] for i in self.engine.state.docs[rca]["itemIds"])
        self.assertIn("Containment", rca_text)
        self.assertIn("fluorocarbon", rca_text)

    def test_large_document_chunked_for_retrieval_within_item_limit(self):
        """The 4.5 MB log is split into <=8,000-char items (retrieval-quality choice); every item is far below the
        30 MB per-item limit, and an item above the limit is rejected by the validator."""
        limit = v.cfg("MAX_ITEM_SIZE_BYTES")
        self.assertEqual(limit, 30_000_000)
        big = _doc_id(self.env, "ETCH-07_FDC")
        raw = self.env.drive.get_content(big)
        self.assertGreater(len(raw), 4 * 1024 * 1024)
        ids = self.engine.state.docs[big]["itemIds"]
        extracts = [i for i in ids if "-x" in i]
        self.assertGreater(len(extracts), 100)
        for item_id in ids:
            item = {k: val for k, val in self.items[item_id].items() if k != "id"}
            self.assertLessEqual(len(item["content"]["value"]), self.engine.chunk_max_chars)
            self.assertLess(v.item_size_bytes(item), limit // 100)
        self.assertTrue(self.items[extracts[-1]]["properties"]["title"].endswith(f"(extract {len(extracts)}/{len(extracts)})"))
        oversize = build_external_item([acl_entry("group", GRP_B)], {"title": "x"}, "x" * (limit + 1))
        self.assertTrue(any("exceeds" in e for e in v.validate_item(oversize)))

    def test_container_name_is_contract_display_name(self):
        """Every item's containerName (semantic label) is the contract display name, never an original path."""
        names = {item["properties"]["containerName"] for item in self.items.values()}
        self.assertEqual(names, {self.env.contract.display_name})
        self.assertEqual(self.env.contract.display_name, "Process Engineering → Yield Analytics (SC-2026-0042)")
        self.assertNotIn("/", self.env.contract.display_name)

    def test_item_ids_deterministic_and_valid(self):
        """Item ids are URL-safe, <=128 chars and identical across independent full crawls."""
        _, _, engine2, transport2 = _new()
        engine2.run_full()
        ids1, ids2 = set(self.items), set(transport2.items(engine2.client.connection_id))
        self.assertEqual(ids1, ids2)
        for item_id in ids1:
            self.assertEqual(v.validate_item_id(item_id), [], item_id)
            self.assertLessEqual(len(item_id), 128)

    def test_audit_chain_verifies_and_matches_writes(self):
        """Every PUT has a 'publish' audit record, the hash chain verifies, and no PII or secrets reach the audit log."""
        result = self.engine.audit.verify()
        self.assertTrue(result.ok, result.error)
        self.assertEqual(self.engine.audit.count("publish"), self.transport.calls[("PUT", "item")])
        self.assertEqual(find_canaries("\n".join(self.engine.audit.lines()), PII_CANARIES + SECRET_CANARIES), [])

    def test_teams_chat_ingested_as_derived_items(self):
        """Teams chat becomes per-day digests (pseudonymised, redacted) plus the chat file from the sender's OneDrive."""
        chat = {sid: e for sid, e in self.engine.state.docs.items() if e.get("chatId")}
        kinds = sorted(e["kind"] for e in chat.values())
        self.assertEqual(kinds, ["chatDigest", "chatDigest", "chatFile"])
        text = "\n".join(self.items[i]["content"]["value"] for e in chat.values() for i in e["itemIds"])
        self.assertIn("Participant 1", text)
        self.assertIn("챔버 B", text)
        for name in ("Alice Kim", "Minho Park", "Jisoo Lee"):
            self.assertNotIn(name, text)
        self.assertEqual(find_canaries(text, PII_CANARIES), [])
        self.assertIn("monitor-wafer gate", text)
        self.assertGreater(len(self.transport.search("bob", "war room particle monitor adders")), 0)


class ArchAMutations(OfflineTestCase):
    """Incremental sync, deletes, TTL, revocation and ACL drift (fresh engine per test)."""

    def setUp(self):
        self.env, self.clock, self.engine, self.transport = _new()
        self.engine.run_full()
        self.cid = self.engine.client.connection_id

    def _items(self):
        return self.transport.items(self.cid)

    def test_no_change_rerun_zero_writes(self):
        """Re-running full and incremental sync with no source change performs zero PUT/DELETE calls."""
        before = self.transport.item_writes()
        self.assertEqual(self.engine.run_full().writes, 0)
        self.assertEqual(self.engine.run_incremental().writes, 0)
        self.assertEqual(self.transport.item_writes(), before)

    def test_incremental_update_republishes_only_changed_document(self):
        """An edit to one doc re-publishes only that doc's items via drive delta; new content becomes searchable."""
        doc = _doc_id(self.env, "ER-2291")
        old_fp = self.engine.state.docs[doc]["sourceFingerprint"]
        self.clock.advance(hours=1)
        text = self.env.drive.get_content(doc).decode() + "\n\nAddendum: CF4/O2 ratio confirmed with a Zirconia probe."
        self.env.drive.update_content(doc, text)
        stats = self.engine.run_incremental()
        self.assertEqual(stats.puts, len(self.engine.state.docs[doc]["itemIds"]))
        self.assertEqual(stats.published_docs, 1)
        self.assertNotEqual(self.engine.state.docs[doc]["sourceFingerprint"], old_fp)
        self.assertTrue(any("Zirconia" in h["summary"] for h in self.transport.search("bob", "zirconia probe")))

    def test_incremental_delete_propagates(self):
        """A source deletion (delta 'deleted' facet) deletes all derived items for that doc."""
        doc = _doc_id(self.env, "SQ-118")
        ids = list(self.engine.state.docs[doc]["itemIds"])
        self.env.drive.delete(doc)
        stats = self.engine.run_incremental()
        self.assertEqual(stats.deletes, len(ids))
        self.assertNotIn(doc, self.engine.state.docs)
        for item_id in ids:
            self.assertNotIn(item_id, self._items())
        self.assertEqual([h for h in self.transport.search("bob", "Northwind photoresist gel") if h["hitId"] in ids], [])

    def test_move_out_of_scope_and_relabel_above_ceiling_remove_items(self):
        """Moving a doc to /Internal or relabelling it Highly Confidential removes its derived items on the next sync."""
        moved, relabelled = _doc_id(self.env, "MET-CDSEM"), _doc_id(self.env, "tool_PM")
        self.env.drive.move(moved, "/Internal/Metrology")
        self.env.drive.set_label(relabelled, "Highly Confidential")
        stats = self.engine.run_incremental()
        self.assertNotIn(moved, self.engine.state.docs)
        self.assertNotIn(relabelled, self.engine.state.docs)
        self.assertEqual(stats.excluded["out_of_scope_path"], 1)
        self.assertEqual(stats.excluded["label_above_ceiling"], 1)
        self.assertGreaterEqual(stats.deletes, 4)

    def test_new_in_scope_file_is_published(self):
        """A new file under /Shareable is picked up by the next incremental sync."""
        new_id = self.env.drive.add_file("/Shareable/Yield", "YE-0420_note.md",
                                         "# YE-0420 note\n\nMinor yield dip on NX-7L traced to probe card wear.", "General")
        stats = self.engine.run_incremental()
        self.assertIn(new_id, self.engine.state.docs)
        self.assertEqual(stats.published_docs, 1)
        self.assertGreater(len(self.transport.search("bob", "probe card wear")), 0)

    def test_shrinking_document_deletes_superseded_chunks(self):
        """When a doc shrinks, extract items that are no longer produced are deleted (no orphans)."""
        big = _doc_id(self.env, "ETCH-07_FDC")
        before = list(self.engine.state.docs[big]["itemIds"])
        self.env.drive.update_content(big, "ETCH-07 FDC log truncated for archiving.\n\nOnly summary retained.")
        stats = self.engine.run_incremental()
        after = self.engine.state.docs[big]["itemIds"]
        self.assertEqual(len(after), 2)
        self.assertEqual(stats.deletes, len(set(before) - set(after)))
        self.assertTrue(set(self._items()).isdisjoint(set(before) - set(after)))

    def test_expired_delta_token_triggers_full_resync(self):
        """HTTP 410-style expired delta token falls back to a full crawl without duplicate writes."""
        self.env.drive.expire_delta_tokens()
        stats = self.engine.run_incremental()
        self.assertEqual(stats.mode, "full-resync")
        self.assertEqual(stats.writes, 0)
        self.assertEqual(self.engine.audit.count("delta_resync_required"), 1)

    def test_ttl_expiry_sweep_removes_items(self):
        """After ttlDays without refresh (e.g. engine down) the expiry sweep deletes every derived item."""
        self.assertGreater(len(self._items()), 0)
        self.clock.advance(days=31)
        stats = self.engine.sweep_expired()
        self.assertEqual(len(self._items()), 0)
        self.assertEqual(stats.expired, 10)
        self.assertEqual(self.transport.search("bob", "seasoning wafers"), [])
        self.assertTrue(all(r["data"]["reason"] == "ttl_expired" for r in self.engine.audit.records("delete")))

    def test_refresh_before_expiry_extends_validity(self):
        """An incremental run inside the refresh window re-publishes due items with a later validUntil."""
        doc = _doc_id(self.env, "GL-ETCH-007")
        before = parse_iso(self.engine.state.docs[doc]["validUntil"])
        self.clock.advance(days=26)
        stats = self.engine.run_incremental()
        self.assertGreater(stats.refreshed, 0)
        self.assertEqual(stats.expired, 0)
        self.assertGreater(parse_iso(self.engine.state.docs[doc]["validUntil"]), before)

    def test_contract_revocation_deletes_all_items(self):
        """Revoking the contract deletes every derived item; later syncs publish nothing."""
        stats = self.engine.revoke("alice@contoso.com", "audit finding")
        self.assertEqual(len(self._items()), 0)
        self.assertGreater(stats.deletes, 0)
        again = self.engine.run_full()
        self.assertEqual(again.puts, 0)
        self.assertFalse(again.contract_active)
        self.assertEqual(self.engine.audit.count("contract_revoked"), 1)

    def test_suspended_contract_purges_on_next_run(self):
        """A contract that is no longer active (suspended) purges derived content on the next scheduled run."""
        self.engine.contract = self.engine.contract.with_changes(status="suspended")
        stats = self.engine.run_incremental()
        self.assertFalse(stats.contract_active)
        self.assertEqual(len(self._items()), 0)

    def test_new_guest_visible_until_acl_refresh_then_denied(self):
        """ACL drift: a guest newly added to Team B can see items until the next sync re-publishes them with a deny ACE."""
        eve = self.env.directory.add_user(oid="0e0e0e0e-0000-4000-8000-0000000000e9", alias="eve",
                                          upn="eve_fabrikam.com#EXT#@contoso.onmicrosoft.com", display_name="Eve",
                                          user_type="Guest", groups=[GRP_B])
        self.assertGreater(len(self.transport.search("eve", "seasoning wafers")), 0)  # residual-risk window
        stats = self.engine.run_incremental()
        self.assertEqual(stats.puts, len(self._items()))
        self.assertEqual(self.transport.search("eve", "seasoning wafers"), [])
        self.assertTrue(all({"type": "user", "value": eve.id, "accessType": "deny"} in i["acl"] for i in self._items().values()))

    def test_audience_change_by_group_membership_only(self):
        """Removing bob from grp-team-b removes his access at once (the ACL references the group) with zero item writes."""
        self.assertGreater(len(self.transport.search("bob", "seasoning wafers")), 0)
        before = self.transport.item_writes()
        self.env.directory.remove_member(GRP_B, "bob")
        self.assertEqual(self.transport.search("bob", "seasoning wafers"), [])
        self.assertEqual(self.engine.run_incremental().writes, 0)
        self.assertEqual(self.transport.item_writes(), before)
        self.assertEqual(self.transport.search("bob", "root cause excursion"), [])

    def test_tenant_wide_audience_only_when_contract_allows(self):
        """everyone/everyoneExceptGuests ACEs appear only if the contract allows it; their value is the (configurable) tenant ID."""
        self.assertFalse({a["type"] for a in self.engine.acl()} & {"everyone", "everyoneExceptGuests"})
        self.engine.contract = self.engine.contract.with_changes(allowTenantWideAudience=True)
        grants = [a for a in self.engine.acl() if a["accessType"] == "grant"]
        self.assertEqual(grants, [{"type": "everyoneExceptGuests", "value": self.env.directory.tenant_id,
                                   "accessType": "grant"}])
        self.engine.tenant_id = "11111111-2222-4333-8444-555555555555"  # configurable
        self.assertEqual(self.engine.acl()[0]["value"], "11111111-2222-4333-8444-555555555555")

    def test_state_persists_and_resumes_without_rewrites(self):
        """JSON state survives a restart: a new engine resumes from the saved deltaLink with zero writes."""
        path = self.scratch("arch-a-state") / "state.json"
        env, clock, engine, transport = _new(state_path=path)
        engine.run_full()
        saved = json.loads(path.read_text())
        self.assertTrue(saved["deltaLink"].startswith("https://graph.microsoft.com/v1.0/drives/"))
        client = ConnectorClient(transport, engine.client.connection_id, sleep=lambda _s: None)
        from arch_a_connector.sync_engine import SyncEngine
        resumed = SyncEngine(contract=env.contract, directory=env.directory, drive=env.drive, client=client,
                             audit=AuditLog(clock=clock), clock=clock, state=StateStore(path),
                             chat_export=env.chat_export, chat_drive=env.chat_drive)
        before = transport.item_writes()
        self.assertEqual(resumed.run_incremental().writes, 0)
        self.assertEqual(transport.item_writes(), before)

    def test_audit_tampering_detected(self):
        """Editing a persisted audit record breaks the hash chain and verify() pinpoints it."""
        path = self.scratch("arch-a-audit") / "audit.jsonl"
        env, clock, engine, transport = _new(audit=AuditLog(path, clock=FixedClock()))
        engine.run_full()
        self.assertTrue(engine.audit.verify().ok)
        lines = path.read_text().splitlines()
        record = json.loads(lines[5])
        record["data"]["reason"] = "tampered"
        lines[5] = json.dumps(record, sort_keys=True)
        path.write_text("\n".join(lines) + "\n")
        result = engine.audit.verify()
        self.assertFalse(result.ok)
        self.assertEqual(result.bad_index, 5)


class ArchAValidatorsAndTransport(OfflineTestCase):
    def test_validators_reject_invalid_schemas(self):
        """Schema validator enforces name length/charset, property count, searchable types, searchable+refinable and unique labels."""
        good = build_schema()
        cases = {
            "too long": schema_property("x" * 33, "String"),
            "charset": schema_property("bad_name", "String"),
            "searchable int": schema_property("count", "Int64", searchable=True),
            "searchable+refinable": schema_property("tag", "String", searchable=True, refinable=True),
            "duplicate label": schema_property("other", "String", labels=["title"]),
            "bad type": schema_property("weird", "Text"),
        }
        for name, prop in cases.items():
            schema = dict(good, properties=good["properties"] + [prop])
            self.assertNotEqual(v.validate_schema(schema), [], name)
        many = dict(good, properties=[schema_property(f"p{i}", "String") for i in range(129)])
        self.assertTrue(any("at most" in e for e in v.validate_schema(many)))
        self.assertTrue(v.validate_schema(dict(good, baseType="microsoft.graph.item")))

    def test_validators_reject_invalid_items_and_connections(self):
        """Item/connection validators enforce id rules, ACL enums, content type, DateTime format, schema typing and the 30 MB item limit."""
        schema = build_schema()
        acl = [acl_entry("group", GRP_B)]
        ok = build_external_item(acl, {"title": "t", "validUntil": "2026-11-06T01:00:00Z"}, "body")
        self.assertEqual(v.validate_item(ok, schema, "kx-1"), [])
        bad = {
            "id too long": (ok, "a" * 129), "id not url-safe": (ok, "a/b c"),
            "acl type": (build_external_item([acl_entry("role", "x")], {}, "b"), "i1"),
            "access type": (build_external_item([acl_entry("group", GRP_B, "allow")], {}, "b"), "i2"),
            "empty acl": (build_external_item([], {}, "b"), "i3"),
            "datetime": (build_external_item(acl, {"validUntil": "2026-11-06 01:00"}, "b"), "i4"),
            "undeclared": (build_external_item(acl, {"secretColumn": "x"}, "b"), "i5"),
            "content type": (dict(ok, content={"type": "pdf", "value": "b"}), "i6"),
            "oversize": (build_external_item(acl, {}, "x" * (v.cfg("MAX_ITEM_SIZE_BYTES") + 1)), "i7"),
            "everyone not tenant id": (build_external_item([acl_entry("everyone", "everyone")], {}, "b"), "i8"),
            "acl missing": ({"properties": {}, "content": {"type": "text", "value": "b"}}, "i9"),
        }
        for name, (item, item_id) in bad.items():
            self.assertNotEqual(v.validate_item(item, schema, item_id), [], name)
        tenant_ace = build_external_item([acl_entry("everyoneExceptGuests", "9c0a7e11-4f2b-4d3c-8e5f-6a7b8c9d0e1f")], {}, "b")
        self.assertEqual(v.validate_item(tenant_ace, schema, "kx-2"), [])
        self.assertIn("Microsoft", v.cfg("CONNECTION_ID_RESERVED"))
        for cid in ("ab", "x" * 33, "abc-def", "MicrosoftKnowledge", "Microsoft", "SharePoint", "teams", "TopicEngine"):
            self.assertNotEqual(v.validate_connection({"id": cid, "name": "n"}), [], cid)
        self.assertEqual(v.validate_connection({"id": "ContosoPEDerivedSC20260042", "name": "n"}), [])
        self.assertTrue(all(entry["verify"] == "verify against current docs" for entry in v.CONFIG.values()))

    def test_mock_trimming_semantics_deny_wins(self):
        """Emulator trimming: grant via user/group/everyoneExceptGuests, guests excluded from everyoneExceptGuests, deny wins."""
        env = load_environment(FixedClock())
        t = MockGraphTransport(env.directory)
        bob, dave, carol = (env.directory.user(u) for u in ("bob", "dave", "carol"))
        item = lambda *acl: {"acl": list(acl)}
        self.assertTrue(t.is_visible(item(acl_entry("group", GRP_B)), bob))
        self.assertFalse(t.is_visible(item(acl_entry("group", GRP_B), acl_entry("user", bob.id, "deny")), bob))
        self.assertTrue(t.is_visible(item(acl_entry("everyoneExceptGuests", "t")), carol))
        self.assertFalse(t.is_visible(item(acl_entry("everyoneExceptGuests", "t")), dave))
        self.assertTrue(t.is_visible(item(acl_entry("everyone", "t")), dave))
        self.assertFalse(t.is_visible(item(acl_entry("user", carol.id, "deny")), carol))
        self.assertFalse(t.is_visible(item(acl_entry("externalGroup", "eg1")), bob))

    def test_mock_rejects_items_before_schema_and_invalid_payloads(self):
        """The emulator returns 400 before schema completion and for invalid items (incl. missing/empty acl), 409 for duplicates."""
        env = load_environment(FixedClock())
        t = MockGraphTransport(env.directory)
        self.assertEqual(t.request("POST", "/external/connections", {"id": "ContosoTest", "name": "n"}).status, 201)
        self.assertEqual(t.request("POST", "/external/connections", {"id": "ContosoTest", "name": "n"}).status, 409)
        item = build_external_item([acl_entry("group", GRP_B)], {"title": "t"}, "x")
        self.assertEqual(t.request("PUT", "/external/connections/ContosoTest/items/i1", item).status, 400)
        client = ConnectorClient(t, "ContosoTest", sleep=lambda _s: None)
        self.assertEqual(client.register_schema(build_schema()), {"status": "completed"})
        self.assertEqual(t.request("PUT", "/external/connections/ContosoTest/items/i1", item).status, 200)
        self.assertEqual(t.request("PUT", "/external/connections/ContosoTest/items/i2", dict(item, acl=[])).status, 400)
        no_acl = {key: val for key, val in item.items() if key != "acl"}
        self.assertEqual(t.request("PUT", "/external/connections/ContosoTest/items/i3", no_acl).status, 400)
        self.assertEqual(t.request("DELETE", "/external/connections/ContosoTest/items/nope").status, 404)
        with self.assertRaises(v.ValidationError):
            client.put_item("bad id", item)

    def test_client_retries_throttling_with_retry_after(self):
        """ConnectorClient retries 429/503 honouring Retry-After before succeeding."""
        env = load_environment(FixedClock())
        t = MockGraphTransport(env.directory)
        sleeps = []
        client = ConnectorClient(t, "ContosoRetry", sleep=sleeps.append)
        client.create_connection("n", "d")
        client.register_schema(build_schema())
        t.inject_failure("PUT", 429, times=2, retry_after="7")
        client.put_item("i1", build_external_item([acl_entry("group", GRP_B)], {"title": "t"}, "x"))
        self.assertEqual(sleeps.count(7.0), 2)
        self.assertIn("i1", t.items("ContosoRetry"))
        t.inject_failure("PUT", 503, times=10)
        with self.assertRaises(GraphError):
            client.put_item("i2", build_external_item([acl_entry("group", GRP_B)], {"title": "t"}, "x"))

    def test_http_transport_request_formation(self):
        """HttpGraphTransport forms the client-credentials token request and Graph calls correctly (fake opener, no network)."""
        calls = []

        class FakeResponse(io.BytesIO):
            def __init__(self, status, body, headers=None):
                super().__init__(json.dumps(body).encode() if body is not None else b"")
                self.status, self.headers = status, _Headers(headers or {})

            def getcode(self):
                return self.status

        class _Headers(dict):
            def items(self):
                return super().items()

        def opener(req, timeout):
            calls.append(req)
            if req.full_url.endswith("/oauth2/v2.0/token"):
                return FakeResponse(200, {"access_token": "fake-token", "expires_in": 3599, "token_type": "Bearer"})
            if req.get_method() == "PATCH":
                return FakeResponse(202, None, {"Location": "https://graph.microsoft.com/v1.0/external/connections/c/operations/op1"})
            if req.get_method() == "DELETE":
                raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {},
                                             io.BytesIO(b'{"error":{"code":"ItemNotFound","message":"x"}}'))
            return FakeResponse(200, {"id": "x"})

        tenant = "9c0a7e11-4f2b-4d3c-8e5f-6a7b8c9d0e1f"
        transport = HttpGraphTransport(tenant, "app-client-id", "not-a-real-secret", opener=opener, clock=lambda: 1000.0)
        item = build_external_item([acl_entry("group", GRP_B)], {"title": "t"}, "derived text")
        resp = transport.request("PUT", "/external/connections/ContosoPE/items/kx-1", item)
        self.assertEqual(resp.status, 200)
        token_req, put_req = calls
        self.assertEqual(token_req.full_url, f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token")
        self.assertEqual(token_req.get_method(), "POST")
        form = parse_qs(token_req.data.decode())
        self.assertEqual(form["grant_type"], ["client_credentials"])
        self.assertEqual(form["scope"], ["https://graph.microsoft.com/.default"])
        self.assertEqual(form["client_id"], ["app-client-id"])
        self.assertEqual(put_req.full_url, "https://graph.microsoft.com/v1.0/external/connections/ContosoPE/items/kx-1")
        self.assertEqual(put_req.get_method(), "PUT")
        self.assertEqual(put_req.get_header("Authorization"), "Bearer fake-token")
        self.assertEqual(put_req.get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(put_req.data), item)
        self.assertEqual(transport.request("PATCH", "/external/connections/ContosoPE/schema", build_schema()).status, 202)
        self.assertEqual(sum(1 for c in calls if c.full_url.endswith("/token")), 1)  # token cached
        missing = transport.request("DELETE", "/external/connections/ContosoPE/items/kx-404")
        self.assertEqual((missing.status, missing.body["error"]["code"]), (404, "ItemNotFound"))

    def test_connection_id_derived_from_contract(self):
        """The connection id is derived deterministically from the contract id and is valid."""
        env = load_environment(FixedClock())
        cid = connection_id_for(env.contract)
        self.assertEqual(cid, "ContosoPEDerivedSC20260042")
        self.assertEqual(v.validate_connection({"id": cid, "name": "n"}), [])

    def test_artifacts_are_valid_graph_payloads(self):
        """artifacts/arch_a (connection, schema, example items) validate against the Graph constraints and leak nothing."""
        base = ARTIFACTS_DIR / "arch_a"
        schema = json.loads((base / "schema.json").read_text(encoding="utf-8"))
        self.assertEqual(v.validate_connection(json.loads((base / "connection.json").read_text(encoding="utf-8"))), [])
        self.assertEqual(v.validate_schema(schema), [])
        items = sorted(base.glob("item_*.json"))
        self.assertGreaterEqual(len(items), 2)
        for path in items:
            self.assertEqual(v.validate_item(json.loads(path.read_text(encoding="utf-8")), schema), [], path.name)
        text = "\n".join(p.read_text(encoding="utf-8") for p in base.glob("*.json"))
        env = load_environment(FixedClock())
        self.assertEqual([m for m in original_location_markers(env) if m.lower() in text.lower()], [])
        self.assertEqual(find_canaries(text, PII_CANARIES + SECRET_CANARIES), [])

    def test_connector_declarative_agent_v18(self):
        """declarativeAgent.connector.json (v1.8) scopes Copilot to this connection via GraphConnectors and is up to date."""
        from arch_b_broker.manifest_builder import MANIFEST_DIR, render
        from arch_b_broker.manifest_validator import validate_declarative_agent
        env = load_environment(FixedClock())
        for path in (MANIFEST_DIR / "declarativeAgent.connector.json", ARTIFACTS_DIR / "arch_a" / "declarativeAgent.connector.json"):
            self.assertEqual(path.read_text(encoding="utf-8"), render("declarativeAgent.connector.json"), path)
        agent = json.loads((MANIFEST_DIR / "declarativeAgent.connector.json").read_text(encoding="utf-8"))
        self.assertEqual(validate_declarative_agent(agent), [])
        self.assertEqual(agent["capabilities"], [{"name": "GraphConnectors", "connections": [
            {"connection_id": connection_id_for(env.contract), "additional_search_terms": "contractId:SC-2026-0042"}]}])
        self.assertEqual(agent["behavior_overrides"], {"special_instructions": {"discourage_model_knowledge": True}})
        self.assertIn("not shared", agent["disclaimer"]["text"])
        self.assertNotIn("actions", agent)
