import base64
import copy
import io
import json
import unittest
import urllib.error
from contextlib import redirect_stderr
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

from arch_a_connector.graph_client import ConnectorClient, GraphError, GraphResponse
from arch_a_connector.graph_models import build_schema
from common import paths
from common.clock import parse_iso, to_iso

from live_poc.graph import GraphTransport, assert_pipeline_token, retry_delay
from live_poc.__main__ import main
from live_poc.pipeline import (
    APPROVAL_KIND, CONNECTION_ID, Ledger, apply, atomic_json, cleanup, contract_for,
    connector_readback_matches, digest, prepare, read_json,
)
from live_poc.sources import AUTHORITY, SourceReader, fixture_registry

NOW = parse_iso("2026-10-07T05:00:00Z")


class Response:
    def __init__(self, status, body=b"", headers=None):
        self.status, self.body, self.headers = status, body, headers or {}

    def read(self, limit=-1):
        return self.body[:limit] if limit >= 0 else self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TransportTests(unittest.TestCase):
    def test_pipeline_token_identity_and_app_only_roles_required(self):
        state = {"tenantId": "tenant", "apps": {"Pipeline": {"clientId": "pipeline"}}}
        claims = {"tid": "tenant", "appid": "pipeline", "aud": "https://graph.microsoft.com",
                  "exp": 9999999999, "roles": ["Sites.Selected", "ExternalConnection.ReadWrite.OwnedBy",
                                              "ExternalItem.ReadWrite.OwnedBy"]}
        def token(value):
            return "header." + base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=") + ".signature"
        assert_pipeline_token(token(claims), state)
        for changes in ({"tid": "other"}, {"appid": "admin"}, {"roles": ["Sites.Read.All"]},
                        {"scp": "User.Read"}, {"exp": 1}, {"aud": "other"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                assert_pipeline_token(token(dict(claims, **changes)), state)

    def test_graph_only_and_no_redirect_on_normal_requests(self):
        calls = []
        transport = GraphTransport("secret", sharepoint_host="demo.sharepoint.com",
                                   opener=lambda req, **kw: calls.append(req) or Response(
                                       302, headers={"Location": "https://evil.example/"}))
        for url in ("https://evil.example/v1.0/me", "https://graph.microsoft.com@evil.example/v1.0/me",
                    "https://graph.microsoft.com/beta/me"):
            with self.assertRaises(ValueError):
                transport.request("GET", url)
        self.assertEqual(transport.request("GET", "/me").status, 302)
        self.assertEqual(len(calls), 1)

    def test_content_redirect_strips_bearer(self):
        calls = []
        responses = iter([Response(302, headers={"location": "https://demo.sharepoint.com/preauth?sig=private"}),
                          Response(200, b"fictional")])
        graph = GraphTransport("bearer-secret", sharepoint_host="demo.sharepoint.com",
                               opener=lambda req, **kw: calls.append(req) or next(responses))
        self.assertEqual(graph.download("/drives/d/items/i/content"), b"fictional")
        self.assertEqual(calls[0].get_header("Authorization"), "Bearer bearer-secret")
        self.assertIsNone(calls[1].get_header("Authorization"))
        self.assertNotIn("private", json.dumps(graph.evidence))
        self.assertNotIn("bearer", json.dumps(graph.evidence))

    def test_cross_tenant_download_redirect_rejected(self):
        graph = GraphTransport("secret", sharepoint_host="demo.sharepoint.com", opener=lambda *a, **k: Response(
            302, headers={"location": "https://other.sharepoint.com/file"}))
        with self.assertRaises(ValueError):
            graph.download("/drives/d/items/i/content")

    def test_retry_after_case_insensitive_and_error_body_omitted(self):
        delays, responses = [], iter([Response(429, b"private body", {"retry-after": "2"}),
                                      Response(200, b'{"value": []}', {"request-id": "request-1"})])
        graph = GraphTransport("secret", sharepoint_host="demo.sharepoint.com", sleep=delays.append,
                               opener=lambda *a, **k: next(responses))
        self.assertEqual(graph.request("GET", "/me").status, 200)
        self.assertEqual(delays, [2.0])
        self.assertNotIn("private", json.dumps(graph.evidence))
        self.assertEqual(graph.evidence[-1]["requestId"], "request-1")
        self.assertEqual(retry_delay({"Retry-After": "Wed, 01 Jan 2020 00:00:00 GMT"}, 1), 0)

    def test_http_error_bodies_never_surface(self):
        def opener(req, **kwargs):
            raise urllib.error.HTTPError(req.full_url, 403, "private", {"request-id": "r"},
                                         io.BytesIO(b'{"error":{"message":"password hidden"}}'))
        graph = GraphTransport("secret", sharepoint_host="demo.sharepoint.com", opener=opener)
        response = graph.request("GET", "/me")
        self.assertIsNone(response.body)
        self.assertNotIn("password", json.dumps(graph.evidence))

    def test_schema_deadline_bounds_transport_retry_after(self):
        delays = []
        graph = GraphTransport("secret", sharepoint_host="demo.sharepoint.com", sleep=delays.append,
                               opener=lambda *a, **kw: Response(429, headers={"Retry-After": "20"}))
        graph.deadline = 15
        with patch("live_poc.graph.time.monotonic", return_value=10), self.assertRaises(GraphError):
            graph.request("GET", "/external/connections")
        self.assertEqual(delays, [])


class SchemaTests(unittest.TestCase):
    def test_readback_accepts_only_observed_service_metadata(self):
        expected = {"acl": [{"type": "group", "value": "audience", "accessType": "grant"}],
                    "content": {"type": "text", "value": "approved"},
                    "properties": {"title": "approved", "sourceFingerprint": "hash"}}
        actual = copy.deepcopy(expected)
        actual["properties"].update(IsDGBasedSecurityEnabled=True, ows_SiteID="index-site",
                                    ows_WebId="index-web", ows_ListID="index-list", ows_UniqueId="index-item")
        self.assertTrue(connector_readback_matches(actual, expected))
        for change in ({"title": "changed"}, {"unexpected": "unapproved"}):
            altered = copy.deepcopy(actual)
            altered["properties"].update(change)
            self.assertFalse(connector_readback_matches(altered, expected))
        actual["acl"] = []
        self.assertFalse(connector_readback_matches(actual, expected))

    def client(self, responses, **kwargs):
        class Queue:
            def request(self, *args):
                return next(responses)
        return ConnectorClient(Queue(), CONNECTION_ID, sleep=lambda _: None, **kwargs)

    def test_missing_location_never_marks_ready(self):
        client = self.client(iter([GraphResponse(202)]))
        with self.assertRaises(GraphError):
            client.register_schema(build_schema())
        self.assertIsNone(client.schema)

    def test_lowercase_location_and_terminal_status(self):
        client = self.client(iter([GraphResponse(202, {"location": "/external/connections/x/operations/1"}),
                                   GraphResponse(200, {}, {"status": "inProgress"}),
                                   GraphResponse(200, {}, {"status": "completed"})]))
        locations = []
        self.assertEqual(client.register_schema(build_schema(), on_operation=locations.append),
                         {"status": "completed"})
        self.assertEqual(len(locations), 1)

    def test_failed_unknown_and_timed_out_operations_never_ready(self):
        for status in ("failed", "unknownFutureValue", "inprogress"):
            with self.subTest(status=status):
                client = self.client(iter([GraphResponse(202, {"Location": "/external/connections/x/operations/1"}),
                                           GraphResponse(200, {}, {"status": status})]), max_polls=1)
                with self.assertRaises(GraphError):
                    client.register_schema(build_schema())
                self.assertIsNone(client.schema)

    def test_operation_location_cannot_change_host(self):
        client = self.client(iter([]))
        with self.assertRaises(GraphError):
            client.wait_for_schema("https://evil.example/v1.0/external/connections/x/operations/1")


class FakeGraph:
    """Endpoint-shaped fake only; does not use the simulated drive or connector emulator."""
    def __init__(self):
        registry = fixture_registry()
        self.state = {
            "tenantId": "11111111-1111-4111-8111-111111111111",
            "apps": {"Pipeline": {"clientId": "22222222-2222-4222-8222-222222222222"}},
            "groups": {"Readers": {"id": "33333333-3333-4333-8333-333333333333"}},
            "sites": {
                "Source": {"id": "source-site", "driveId": "source-drive", "url": "https://demo.sharepoint.com/sites/source"},
                "Exchange": {"id": "exchange-site", "driveId": "exchange-drive", "listId": "exchange-list"},
            }, "sources": {},
        }
        self.items, self.data = {}, {}
        for n, (name, fixture) in enumerate(registry.items()):
            sid = f"source-{n}"
            self.state["sources"][name] = dict(fixture, id=sid, optIn="FDC_event_log" not in name,
                                              classificationAuthority=AUTHORITY)
            self.items[sid] = {"id": sid, "name": name, "file": {}, "eTag": '"real-etag"',
                               "lastModifiedDateTime": to_iso(NOW),
                               "webUrl": "https://demo.sharepoint.com/sites/source/" + name,
                               "parentReference": {"driveId": "source-drive",
                                                   "path": "/drives/source-drive/root:" + fixture["path"].rsplit("/", 1)[0]}}
            self.data[sid] = (paths.LIBRARY_DIR / "files" / fixture["path"].lstrip("/")).read_bytes()
        self.calls, self.evidence, self.counts = [], [], {}
        self.external, self.cards, self.fields = {}, {}, {}
        self.card_ids, self.next_card_id = {}, 0
        self.connection = None
        self.schema_patches = 0
        self.before_put = None
        self.fail_after_put = None
        self.fail_delete = False

    def request(self, method, path, body=None, headers=None):
        path = path.replace("https://graph.microsoft.com/v1.0", "")
        path = unquote(path)
        self.calls.append((method, path))
        if path.startswith("/drives/source-drive/root/delta"):
            items = list(self.items.values())
            if "page=2" in path:
                return GraphResponse(200, {}, {"value": copy.deepcopy(items[4:]),
                                              "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta/saved"})
            return GraphResponse(200, {}, {"value": copy.deepcopy(items[:4]),
                                          "@odata.nextLink": "https://graph.microsoft.com/v1.0/drives/source-drive/root/delta?page=2"})
        if path.startswith("/drives/source-drive/items/"):
            sid = path.rsplit("/", 1)[1]
            return GraphResponse(200, {}, copy.deepcopy(self.items[sid]))
        base = "/external/connections/" + CONNECTION_ID
        if method == "POST" and path == "/external/connections":
            self.connection = dict(body, state="draft")
            return GraphResponse(201, {}, self.connection)
        if path == base:
            return GraphResponse(200, {}, self.connection) if self.connection else GraphResponse(404)
        if path == base + "/schema":
            if method == "GET":
                return GraphResponse(200, {}, build_schema())
            self.schema_patches += 1
            return GraphResponse(202, {"location": base + "/operations/op1"})
        if path == base + "/operations/op1":
            self.connection["state"] = "ready"
            return GraphResponse(200, {}, {"status": "completed"})
        if path.startswith(base + "/items/kx-"):
            iid = path.rsplit("/", 1)[1]
            if method == "PUT":
                if self.before_put:
                    self.before_put("A", iid)
                self.external[iid] = copy.deepcopy(body)
                if self.fail_after_put == "A":
                    raise GraphError(0, "Timeout", "not retained")
                return GraphResponse(200, {}, {"id": iid})
            if method == "GET":
                return GraphResponse(200, {}, copy.deepcopy(self.external[iid]))
            if method == "DELETE":
                if self.fail_delete:
                    return GraphResponse(503)
                self.external.pop(iid, None)
                return GraphResponse(204)
        if path.startswith("/drives/exchange-drive/root:/"):
            name = path.split("root:/", 1)[1].removesuffix(":/content")
            if method == "PUT":
                if self.before_put:
                    self.before_put("C", name)
                if name not in self.card_ids:
                    self.next_card_id += 1
                    self.card_ids[name] = f"card-{self.next_card_id}"
                self.cards[name] = body
                if self.fail_after_put == "C":
                    raise GraphError(0, "Timeout", "not retained")
                return GraphResponse(201, {}, {"id": self.card_ids[name]})
            if method == "DELETE":
                self.cards.pop(name, None)
                self.card_ids.pop(name, None)
                return GraphResponse(204)
        if path.startswith("/drives/exchange-drive/items/"):
            remote_id = path.split("/items/", 1)[1].removesuffix("/listItem")
            name = next((name for name, card_id in self.card_ids.items() if card_id == remote_id), None)
            if name is None:
                return GraphResponse(404)
            if method == "DELETE":
                self.cards.pop(name, None)
                self.card_ids.pop(name, None)
                return GraphResponse(204)
            return GraphResponse(200, {}, {"id": name})
        if path.endswith("/fields"):
            name = path.split("/items/", 1)[1].removesuffix("/fields")
            if method == "PATCH":
                self.fields[name] = body
            return GraphResponse(200, {}, self.fields[name])
        raise AssertionError((method, path))

    def download(self, path):
        self.calls.append(("DOWNLOAD", path))
        if path.startswith("/drives/source-drive/"):
            return self.data[path.split("/items/", 1)[1].removesuffix("/content")]
        remote_id = path.split("/items/", 1)[1].removesuffix("/content")
        name = next(name for name, card_id in self.card_ids.items() if card_id == remote_id)
        return self.cards[name]


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.root = paths.new_scratch_dir("live-test")
        self.graph = FakeGraph()
        self.state = self.graph.state
        self.plan = self.root / "plan"
        self.approval = self.root / "approval.json"
        self.ledger_path = self.root / "ledger.json"

    def tearDown(self):
        paths.remove_scratch_dir(self.root)

    def prepared(self):
        result = prepare(self.graph, self.state, self.plan, NOW,
                         citation_base="https://broker.example/access-request")
        approval = read_json(self.plan / "approval-template.json")
        approval.update(approved=True, actor="test synthetic operator", approvedAt=to_iso(NOW))
        atomic_json(self.approval, approval)
        return result

    def apply(self):
        return apply(self.graph, self.state, self.plan, self.approval,
                     Ledger(self.ledger_path, self.state), NOW)

    def test_initial_delta_every_page_and_six_real_provenance_docs(self):
        reader = SourceReader(self.graph, self.state, contract_for(self.state, NOW))
        docs = reader.snapshot()
        self.assertEqual(len(docs), 6)
        self.assertTrue(any("page=2" in path for _, path in self.graph.calls))
        self.assertFalse(any("latest" in path for _, path in self.graph.calls))
        self.assertTrue(all(d.etag == '"real-etag"' and d.label_id is None for d in docs))
        self.assertTrue(all(d.meta["classificationAuthority"] == AUTHORITY for d in docs))

    def test_registry_optin_label_hash_and_unregistered_deny_before_transform(self):
        name = "GL-ETCH-007_chamber_seasoning_guideline.txt"
        for mutation in ({"optIn": False}, {"optIn": "true"}, {"fixtureLabel": None},
                         {"fixtureLabel": "Public"}, {"sha256": "0" * 64},
                         {"classificationAuthority": "Purview"}):
            with self.subTest(mutation=mutation):
                graph = FakeGraph()
                graph.state["sources"][name].update(mutation)
                docs = SourceReader(graph, graph.state, contract_for(graph.state, NOW)).snapshot()
                self.assertEqual(len(docs), 5)
        sid = self.state["sources"][name]["id"]
        self.graph.data[sid] = b"arbitrary content"
        docs = SourceReader(self.graph, self.state, contract_for(self.state, NOW)).snapshot()
        self.assertEqual(len(docs), 5)
        del self.state["sources"][name]
        self.assertEqual(len(SourceReader(self.graph, self.state, contract_for(self.state, NOW)).snapshot()), 5)

    def test_source_path_change_denied(self):
        name = "GL-ETCH-007_chamber_seasoning_guideline.txt"
        sid = self.state["sources"][name]["id"]
        self.graph.items[sid]["parentReference"]["path"] = "/drives/source-drive/root:/Internal"
        self.assertEqual(len(SourceReader(self.graph, self.state, contract_for(self.state, NOW)).snapshot()), 5)

    def test_prepare_no_publishes_unsigned_template_real_snapshot(self):
        self.assertEqual(self.prepared()["outputs"], 12)
        self.assertEqual(len(read_json(self.plan / "broker-sources.json")), 6)
        self.assertFalse(read_json(self.plan / "approval-template.json")["approved"])
        self.assertFalse(any(method in ("PUT", "POST", "PATCH") for method, _ in self.graph.calls))
        for output in read_json(self.plan / "manifest.json")["outputs"]:
            data = (self.plan / output["file"]).read_bytes()
            self.assertEqual(digest(data), output["sha256"])
            self.assertNotIn(b"contoso.com", data)
            self.assertNotIn(b"demo.sharepoint.com", data)

    def test_live_default_opaque_refs_do_not_use_shared_prototype_key(self):
        self.prepared()
        other = self.root / "other-plan"
        prepare(self.graph, self.state, other, NOW, citation_base="https://broker.example/access-request")
        manifest = read_json(self.plan / "manifest.json")
        output = next(o for o in manifest["outputs"] if o["architecture"] == "A")
        self.assertNotEqual(read_json(self.plan / output["file"])["properties"]["url"],
                            read_json(other / output["file"])["properties"]["url"])
        self.assertFalse(any("key" in path.name for path in self.plan.iterdir()))

    def test_optional_broker_manifest_binds_document_bytes_and_contract(self):
        contract = read_json(paths.CONTRACT_FILE)
        contract.update(sourceSite=self.state["sites"]["Source"]["url"],
                        audienceGroupIds=[self.state["groups"]["Readers"]["id"]], ttlDays=1,
                        approvedAt=to_iso(NOW), approvedBy="fictional-test-operator@example.invalid")
        contract_path = self.root / "broker-contract.json"
        atomic_json(contract_path, contract)
        prepare(self.graph, self.state, self.plan, NOW, citation_base="https://broker.example/access-request",
                broker_contract=contract_path, runtime_out=self.root / "demo-runtime")
        manifest = read_json(self.plan / "broker-sources.manifest.json")
        self.assertTrue(manifest["synthetic"])
        self.assertEqual(manifest["documentsSha256"], digest((self.plan / "broker-sources.json").read_bytes()))
        self.assertTrue(all(d["meta"]["graphItemId"] == d["source_id"]
                            for d in read_json(self.plan / "broker-sources.json")))
        runtime = self.root / "demo-runtime"
        envelope = read_json(runtime / "snapshot.json")
        self.assertEqual(set(envelope), {"schemaVersion", "tenantId", "sourceSite", "contractFingerprint",
                                         "capturedAt", "synthetic", "documents"})
        self.assertEqual(envelope["contractFingerprint"], manifest["contractFingerprint"])
        self.assertEqual(envelope["documents"], read_json(self.plan / "broker-sources.json"))
        self.assertEqual((runtime / "contract.json").read_bytes(), contract_path.read_bytes())

    def test_missing_unsigned_or_changed_approval_refuses_publication(self):
        self.prepared()
        self.approval.unlink()
        with self.assertRaises(OSError):
            self.apply()
        atomic_json(self.approval, read_json(self.plan / "approval-template.json"))
        with self.assertRaises(ValueError):
            self.apply()
        self.assertFalse(self.graph.external or self.graph.cards)

    def test_changed_output_bytes_or_source_fingerprint_refuses_publication(self):
        self.prepared()
        manifest = read_json(self.plan / "manifest.json")
        output = self.plan / manifest["outputs"][0]["file"]
        original = output.read_bytes()
        output.write_bytes(original + b" ")
        with self.assertRaises(ValueError):
            self.apply()
        output.write_bytes(original)
        sid = manifest["outputs"][0]["sourceId"]
        self.graph.items[sid]["eTag"] = '"changed"'
        with self.assertRaises(ValueError):
            self.apply()
        self.assertFalse(self.graph.external or self.graph.cards)

    def test_apply_persists_pending_before_every_put_then_commits_and_reuses_schema(self):
        self.prepared()
        checked = []
        def pending(architecture, item):
            entries = read_json(self.ledger_path)["entries"]
            self.assertTrue(any(e["architecture"] == architecture and e["status"] == "pending"
                                for e in entries.values()))
            checked.append(architecture)
        self.graph.before_put = pending
        self.assertEqual(self.apply()["applied"], 12)
        self.assertEqual(len(checked), 12)
        self.assertEqual(len(self.graph.external), 6)
        self.assertEqual(len(self.graph.cards), 6)
        self.assertTrue(all(e["status"] == "committed" for e in read_json(self.ledger_path)["entries"].values()))
        for fields in self.graph.fields.values():
            self.assertEqual(fields["KXClassificationAuthority"], AUTHORITY)
        self.apply()
        self.assertEqual(self.graph.schema_patches, 1)

    def test_a_and_c_timed_out_puts_remain_removable_including_unknown_remote_id(self):
        self.prepared()
        for architecture in ("A", "C"):
            with self.subTest(architecture=architecture):
                self.graph.fail_after_put = architecture
                with self.assertRaises(GraphError):
                    self.apply()
                ledger = Ledger(self.ledger_path, self.state)
                self.assertTrue(any(e["status"] == "pending" for e in ledger.data["entries"].values()))
                cleanup(self.graph, self.state, ledger, NOW, all_sources=True)
                self.assertFalse(self.graph.external or self.graph.cards)
                self.ledger_path.unlink()
        self.graph.fail_after_put = None

    def test_withdraw_republish_lost_response_then_withdraw_leaves_no_orphan(self):
        self.prepared()
        self.apply()
        old_ids = set(self.graph.card_ids.values())
        cleanup(self.graph, self.state, Ledger(self.ledger_path, self.state), NOW, all_sources=True)
        self.plan = self.root / "plan-v2"
        self.prepared()
        self.graph.fail_after_put = "C"
        def pending(architecture, name):
            if architecture == "C":
                entry = next(e for e in read_json(self.ledger_path)["entries"].values()
                             if e["architecture"] == "C" and e["file"] == name)
                self.assertEqual(entry["status"], "pending")
                for field in ("remoteId", "removalReason", "verifiedAt"):
                    self.assertNotIn(field, entry)
        self.graph.before_put = pending
        with self.assertRaises(GraphError):
            self.apply()
        self.assertEqual(len(self.graph.cards), 1)
        self.assertTrue(set(self.graph.card_ids.values()).isdisjoint(old_ids))
        ledger = Ledger(self.ledger_path, self.state)
        pending_card = next(e for e in ledger.data["entries"].values()
                            if e["architecture"] == "C" and e["status"] == "pending")
        self.assertNotIn("remoteId", pending_card)
        cleanup(self.graph, self.state, ledger, NOW, all_sources=True)
        self.assertFalse(self.graph.external or self.graph.cards)
        self.assertIn(("DELETE", "/drives/exchange-drive/root:/" + pending_card["file"]), self.graph.calls)

    def test_cli_prepare_surfaces_safe_no_eligible_fixtures_diagnostic(self):
        for record in self.state["sources"].values():
            record["optIn"] = False
        stderr = io.StringIO()
        with patch("live_poc.__main__.read_json", return_value=self.state), \
                patch("live_poc.__main__.assert_pipeline_token"), \
                patch("live_poc.__main__.GraphTransport", return_value=self.graph), redirect_stderr(stderr):
            result = main(["prepare", "--state", "private-state.json", "--out", str(self.plan),
                           "--citation-base", "https://broker.example/access-request"])
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(stderr.getvalue())["error"], "No eligible verified synthetic fixtures")
        self.assertFalse(self.plan.exists())

    def test_cli_does_not_surface_untrusted_validation_or_graph_error_details(self):
        errors = (ValueError("private-token"), KeyError("private-token"),
                  GraphError(403, "private-token", "private-token", {"secret": "private-token"}))
        for error in errors:
            with self.subTest(error_type=type(error).__name__):
                stderr = io.StringIO()
                with patch("live_poc.__main__.read_json", return_value=self.state), \
                        patch("live_poc.__main__.assert_pipeline_token"), \
                        patch("live_poc.__main__.GraphTransport", return_value=self.graph), \
                        patch("live_poc.__main__.prepare", side_effect=error), redirect_stderr(stderr):
                    result = main(["prepare", "--state", "private-state.json", "--out", str(self.plan),
                                   "--citation-base", "https://broker.example/access-request"])
                self.assertEqual(result, 1)
                self.assertNotIn("private-token", stderr.getvalue())
                if isinstance(error, GraphError):
                    self.assertEqual(json.loads(stderr.getvalue())["status"], 403)

    def test_failed_delete_retains_retryable_state_and_ttl_deletes_both(self):
        self.prepared()
        self.apply()
        self.graph.fail_delete = True
        with self.assertRaises(GraphError):
            cleanup(self.graph, self.state, Ledger(self.ledger_path, self.state), NOW + timedelta(days=2))
        ledger = Ledger(self.ledger_path, self.state)
        self.assertTrue(any(e["status"] == "deleting" for e in ledger.data["entries"].values()))
        self.graph.fail_delete = False
        self.assertEqual(cleanup(self.graph, self.state, ledger, NOW + timedelta(days=2))["deleted"], 12)
        self.assertFalse(self.graph.external or self.graph.cards)

    def test_withdrawal_cannot_replay_previously_approved_plan(self):
        self.prepared()
        self.apply()
        cleanup(self.graph, self.state, Ledger(self.ledger_path, self.state), NOW, all_sources=True)
        with self.assertRaises(ValueError):
            self.apply()

    def test_source_withdrawal_reconciliation_removes_a_and_c(self):
        self.prepared()
        self.apply()
        docs = SourceReader(self.graph, self.state, contract_for(self.state, NOW)).snapshot()
        current = {d.source_id: d.fingerprint for d in docs[1:]}
        result = cleanup(self.graph, self.state, Ledger(self.ledger_path, self.state), NOW, current=current)
        self.assertEqual(result["deleted"], 2)

    def test_wrong_deployment_ledger_fails_closed(self):
        ledger = Ledger(self.ledger_path, self.state)
        ledger.save()
        self.state["groups"]["Readers"]["id"] = "other-group"
        with self.assertRaises(ValueError):
            Ledger(self.ledger_path, self.state)

    def test_approval_never_grants_everyone_even_if_manifest_reapproved(self):
        self.prepared()
        manifest = read_json(self.plan / "manifest.json")
        output = next(o for o in manifest["outputs"] if o["architecture"] == "A")
        path = self.plan / output["file"]
        payload = read_json(path)
        payload["acl"] = [{"type": "everyone", "value": self.state["tenantId"], "accessType": "grant"}]
        atomic_json(path, payload)
        output["sha256"] = digest(path.read_bytes())
        atomic_json(self.plan / "manifest.json", manifest)
        approval = read_json(self.approval)
        approval["planSha256"] = digest((self.plan / "manifest.json").read_bytes())
        approval["outputs"][output["file"]] = output["sha256"]
        atomic_json(self.approval, approval)
        with self.assertRaises(ValueError):
            self.apply()


if __name__ == "__main__":
    unittest.main()
