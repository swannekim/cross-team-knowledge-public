"""Offline tests of the deployable entry point; RSA credentials exist only in memory."""
from __future__ import annotations

import dataclasses
import json
import sqlite3
import threading
import time
import unittest
from http.server import HTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

try:
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
except ImportError:
    jwt = None

from common.clock import FixedClock
from common.contract import contract_from_dict
from common.sources import SourceDocument
from common.testing import OfflineTestCase
from arch_b_broker.auth import AuthError, Principal
from arch_b_broker.index import Chunk, _build
from arch_b_broker.live import (DEMO_PRIVACY_NOTICE, DEMO_TERMS_NOTICE, LiveBroker, LiveConfig, _digest,
                                build_live_broker, load_snapshot, main, make_live_handler)
from arch_b_broker.live_auth import (DependencyUnavailable, EntraTokenValidator, GraphDirectory,
                                     ManagedIdentityGraphToken, MicrosoftSigningKeys)
from arch_b_broker.live_state import DurableAudit, DurablePDP, DurableState

TENANT = "11111111-1111-1111-1111-111111111111"
APP = "22222222-2222-2222-2222-222222222222"
GROUP = "33333333-3333-3333-3333-333333333333"
GRAPH = "44444444-4444-4444-4444-444444444444"
USER = "55555555-5555-5555-5555-555555555555"
OTHER = "66666666-6666-6666-6666-666666666666"


def contract_data():
    return {
        "contractId": "demo-live-001", "sourceSite": "https://example.sharepoint.com/sites/demo",
        "includePaths": ["/Shareable"], "maxLabel": "General", "audienceGroupIds": [GROUP],
        "excludeGuests": True, "purpose": "demo-analysis", "derivativeTypes": ["redactedExtract"],
        "ttlDays": 7, "approvedBy": "demo.owner@example.com", "approvedAt": "2026-01-01T00:00:00Z",
        "maxExcerptChars": 100, "exfiltrationCoverageThreshold": 0.25, "rateLimitPerMinute": 10,
    }


def document():
    return SourceDocument(
        source_id="graph-demo-source", kind="file", name="demo.txt", title="Chamber seasoning",
        path="/Shareable/demo.txt", web_url="https://example.sharepoint.com/sites/demo/Shareable/demo.txt",
        label="General", etag='"demo-etag"', last_modified="2026-10-07T00:00:00Z",
        text="Chamber seasoning reduces particles after maintenance. Verify pressure before release. " * 25,
        fingerprint="a" * 64, container="demo", meta={"graphDriveId": "graph-drive", "graphItemId": "graph-item"})


class DemoNoticeTests(OfflineTestCase):
    def setUp(self):
        self.broker = Mock()
        self.broker.config.public_origin = "https://broker.example.com"
        server = HTTPServer(("127.0.0.1", 0), make_live_handler(self.broker))
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        def stop():
            server.shutdown()
            server.server_close()
            thread.join(2)
        self.addCleanup(stop)
        self.base = f"http://127.0.0.1:{server.server_address[1]}"

    def test_public_notices_are_exact_static_text_without_identity_or_state_calls(self):
        for path, expected in (("/demo/privacy", DEMO_PRIVACY_NOTICE), ("/demo/terms", DEMO_TERMS_NOTICE)):
            with self.subTest(path=path), urlopen(self.base + path + "?ref=do-not-reflect", timeout=5) as response:
                body = response.read()
                self.assertEqual(response.status, 200)
                self.assertEqual(body, expected.encode("utf-8"))
                self.assertEqual(int(response.headers["Content-Length"]), len(body))
                self.assertEqual(response.headers["Content-Type"], "text/plain; charset=utf-8")
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(response.headers["X-Purview-Evaluation"], "not-evaluated")
                self.assertNotIn(b"do-not-reflect", body)
        self.assertEqual(self.broker.mock_calls, [])

    def test_notice_routes_do_not_accept_posts_or_unknown_paths(self):
        for request in (Request(self.base + "/demo/privacy", method="POST"),
                        Request(self.base + "/demo/terms", method="POST"),
                        Request(self.base + "/demo/unknown")):
            with self.subTest(url=request.full_url):
                with self.assertRaises(HTTPError) as caught:
                    urlopen(request, timeout=5)
                self.assertEqual(caught.exception.code, 404)
                caught.exception.close()
        self.broker.execute.assert_not_called()

    def test_notices_disclose_actual_persistence_and_demo_limitations(self):
        for phrase in ("private Azure Blob", "user object IDs", "hashes of questions", "Copilot",
                       "does not store raw questions or response bodies", "manual expiry", "not evaluated"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, DEMO_PRIVACY_NOTICE)
        for phrase in ("KX-DEMO-20261007", "yield-excursion-analysis", "App-only callers are not supported",
                       "does not establish answer quality", "Original files", "manual expiry", "not evaluated"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, DEMO_TERMS_NOTICE)
        for notice in (DEMO_PRIVACY_NOTICE, DEMO_TERMS_NOTICE):
            self.assertIn("architecture A", notice)
            self.assertIn("ExampleDerived", notice)
            self.assertIn("KX-Synthetic-20261007", notice)
        self.assertIn("apply only to architecture B", DEMO_PRIVACY_NOTICE)
        self.assertIn("verify connector citation provenance", DEMO_TERMS_NOTICE)


class LiveFixture(OfflineTestCase):
    def setUp(self):
        self.root = self.scratch("broker-live")
        self.clock = FixedClock()
        self.raw_contract = contract_data()
        self.contract = contract_from_dict(self.raw_contract)
        self.contract_path, self.snapshot_path = self.root / "contract.json", self.root / "snapshot.json"
        self.contract_path.write_text(json.dumps(self.raw_contract), encoding="utf-8")
        self.snapshot_data = {
            "schemaVersion": 1, "tenantId": TENANT, "sourceSite": self.contract.sourceSite,
            "contractFingerprint": self.contract.fingerprint(), "capturedAt": "2026-10-07T01:00:00Z",
            "synthetic": True, "documents": [dataclasses.asdict(document())],
        }
        self.write_snapshot()
        self.environ = {
            "KX_TENANT_ID": TENANT, "KX_BROKER_CLIENT_ID": APP, "KX_AUDIENCE_GROUP_ID": GROUP,
            "KX_GRAPH_CLIENT_ID": GRAPH, "KX_CONTRACT_PATH": str(self.contract_path),
            "KX_SOURCE_SNAPSHOT_PATH": str(self.snapshot_path), "KX_PUBLIC_ORIGIN": "https://broker.example.com",
            "KX_STORAGE_MODE": "azure-blob", "KX_STORAGE_ROOT": str(self.root),
            "KX_STATE_PATH": str(self.root / "broker.sqlite"), "KX_REPLICA_COUNT": "1",
            "KX_PURVIEW_MODE": "disabled-demo",
            "KX_BLOB_ACCOUNT_URL": "https://demostorage.blob.core.windows.net",
            "KX_BLOB_CONTAINER": "broker-state", "KX_BLOB_NAME": "broker-state.sqlite",
        }
        self.config = LiveConfig.from_env(self.environ)

    def write_snapshot(self):
        self.snapshot_path.write_text(json.dumps(self.snapshot_data), encoding="utf-8")

    def open_state(self):
        state = DurableState(self.config.state_path, f"{TENANT}:{APP}:{self.contract.contractId}")
        self.addCleanup(state.close)
        return state

    def local_environ(self):
        return {**{key: value for key, value in self.environ.items() if not key.startswith("KX_BLOB_")},
                "KX_STORAGE_MODE": "local-validation", "KX_GRAPH_ACCESS_TOKEN": "transient-test-token"}


class ConfigurationTests(LiveFixture):
    def test_unknown_modes_and_missing_persistence_rejected(self):
        for change in ({"KX_MOCK_DIRECTORY": "true"}, {"KX_STORAGE_MODE": "memory"},
                       {"KX_STORAGE_MODE": "azure-files"},
                       {"KX_REPLICA_COUNT": "2"}, {"KX_PURVIEW_MODE": "allow"},
                       {"KX_PURVIEW_MODE": "required"}, {"KX_STATE_PATH": ":memory:"},
                       {"KX_PUBLIC_ORIGIN": "http://broker.example.com"}, {"KX_TENANT_ID": "common"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                LiveConfig.from_env({**self.environ, **change})
        for key in ("KX_PURVIEW_MODE", "KX_STATE_PATH", "KX_STORAGE_ROOT"):
            with self.subTest(missing=key), self.assertRaises(ValueError):
                LiveConfig.from_env({name: value for name, value in self.environ.items() if name != key})

    def test_local_disk_is_not_a_live_mount(self):
        config = dataclasses.replace(self.config, storage_mode="azure-files")
        with self.assertRaises(ValueError):
            config.verify_mount()

    def test_blob_requires_explicit_safe_endpoint_and_container(self):
        for key in ("KX_BLOB_ACCOUNT_URL", "KX_BLOB_CONTAINER", "KX_BLOB_NAME"):
            with self.subTest(missing=key), self.assertRaises(ValueError):
                LiveConfig.from_env({name: value for name, value in self.environ.items() if name != key})
        for change in ({"KX_BLOB_ACCOUNT_URL": "http://demostorage.blob.core.windows.net"},
                       {"KX_BLOB_ACCOUNT_URL": "https://demostorage.blob.core.windows.net?sig=secret"},
                       {"KX_BLOB_CONTAINER": "$root"}, {"KX_BLOB_NAME": "../other.sqlite"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                LiveConfig.from_env({**self.environ, **change})

    def test_blob_local_sqlite_explicitly_rejects_network_mounts(self):
        mount = str(self.root).replace("\\", "\\134").replace(" ", "\\040")
        for filesystem in ("cifs", "smb3", "nfs", "nfs4"):
            with self.subTest(filesystem=filesystem), patch("arch_b_broker.live.sys.platform", "linux"), \
                    patch.object(Path, "read_text", return_value=f"none {mount} {filesystem} rw 0 0"):
                with self.assertRaisesRegex(ValueError, "network filesystem"):
                    self.config.verify_mount()
        with patch("arch_b_broker.live.sys.platform", "linux"), \
                patch.object(Path, "read_text", return_value=f"none {mount} overlay rw 0 0"):
            self.config.verify_mount()

    def test_local_validation_is_explicit_and_forbidden_in_container_apps(self):
        env = {**self.local_environ(), "KX_PUBLIC_ORIGIN": "http://127.0.0.1:8080"}
        config = LiveConfig.from_env(env)
        config.verify_mount()
        for extra in ({"CONTAINER_APP_NAME": "demo"}, {"KX_CONTAINER_DEPLOYMENT": "1"},
                      {"KX_STORAGE_MODE": "azure-files"}, {"KX_GRAPH_ACCESS_TOKEN": ""}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                LiveConfig.from_env({**env, **extra})

    def test_document_list_sidecar_binds_exact_bytes(self):
        manifest = {key: value for key, value in self.snapshot_data.items() if key != "documents"}
        self.snapshot_path.write_text(json.dumps(self.snapshot_data["documents"]), encoding="utf-8")
        manifest["documentsSha256"] = _digest(self.snapshot_path.read_bytes())
        path = self.root / "snapshot-manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        config = LiveConfig.from_env({**self.environ, "KX_SOURCE_MANIFEST_PATH": str(path)})
        self.assertEqual(len(load_snapshot(config, self.contract, self.clock).documents), 1)
        self.snapshot_path.write_text(self.snapshot_path.read_text() + "\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_snapshot(config, self.contract, self.clock)

    @unittest.skipIf(jwt is None, "install deployment/requirements-live.txt for entry point tests")
    def test_local_entry_point_never_persists_transient_token(self):
        env = self.local_environ()
        config = LiveConfig.from_env(env)
        with patch("arch_b_broker.live.SystemClock", return_value=self.clock):
            broker = build_live_broker(config, env)
        try:
            self.assertEqual(broker.metadata()["deployment"], "local-validation")
            self.assertTrue(broker.valid())
            self.assertEqual(broker.execute("search", None, {}).status, 401)
        finally:
            broker.state.close()
            broker.directory.token_provider.close()
        self.assertNotIn(b"transient-test-token", config.state_path.read_bytes())

    def test_sigterm_closes_server_state_and_managed_credential(self):
        broker, server = Mock(), Mock()
        broker.metadata.return_value = {}
        with patch("arch_b_broker.live.LiveConfig.from_env", return_value=self.config), \
                patch("arch_b_broker.live.build_live_broker", return_value=broker), \
                patch("arch_b_broker.live.HTTPServer", return_value=server), \
                patch("arch_b_broker.live.signal.signal", return_value="previous") as register, \
                patch("builtins.print"):
            server.serve_forever.side_effect = lambda: register.call_args_list[0].args[1](15, None)
            with self.assertRaises(SystemExit):
                main()
        server.server_close.assert_called_once()
        broker.state.close.assert_called_once()
        broker.directory.token_provider.close.assert_called_once()
        self.assertEqual(register.call_args.args[1], "previous")

    def test_snapshot_contract_tenant_source_and_time_binding(self):
        snapshot = load_snapshot(self.config, self.contract, self.clock)
        self.assertEqual(len(snapshot.documents), 1)
        for name, value in (("tenantId", OTHER), ("contractFingerprint", "incorrect"),
                            ("sourceSite", "https://elsewhere.sharepoint.com/sites/demo"),
                            ("synthetic", False), ("capturedAt", "2025-01-01T00:00:00Z"),
                            ("capturedAt", "2030-01-01T00:00:00Z")):
            original = self.snapshot_data[name]
            self.snapshot_data[name] = value
            self.write_snapshot()
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                load_snapshot(self.config, self.contract, self.clock)
            self.snapshot_data[name] = original

    def test_snapshot_requires_graph_provenance_and_policy_scope(self):
        original = dict(self.snapshot_data["documents"][0])
        for field, value in (("meta", {}), ("path", "/Private/demo.txt"), ("label", "Highly Confidential"),
                             ("web_url", "https://attacker.example.com/doc"), ("kind", "chatDigest")):
            self.snapshot_data["documents"][0] = {**original, field: value}
            self.write_snapshot()
            with self.subTest(field=field), self.assertRaises(ValueError):
                load_snapshot(self.config, self.contract, self.clock)

    def test_source_locations_are_not_embedded_in_returnable_content(self):
        row = self.snapshot_data["documents"][0]
        row["text"] = "Chamber " + row["web_url"] + " " + row["path"] + " " + row["source_id"]
        self.write_snapshot()
        loaded = load_snapshot(self.config, self.contract, self.clock).documents[0]
        self.assertNotIn("https://", loaded.text)
        self.assertNotIn("/Shareable", loaded.text)
        self.assertNotIn(row["source_id"], loaded.text)


@unittest.skipIf(jwt is None, "install deployment/requirements-live.txt for RS256 tests")
class EntraValidationTests(OfflineTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        now = int(time.time())
        self.claims = {"iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": APP, "tid": TENANT,
                       "oid": USER, "scp": "Knowledge.Ask", "exp": now + 300, "iat": now - 5,
                       "nbf": now - 5, "ver": "2.0"}
        self.validator = EntraTokenValidator(TENANT, APP, keys={"test-key": self.key.public_key()})

    def token(self, **changes):
        return jwt.encode({**self.claims, **changes}, self.key, algorithm="RS256", headers={"kid": "test-key"})

    def test_rs256_user_token_and_scope(self):
        principal = self.validator.from_authorization_header("Bearer " + self.token())
        self.assertEqual(principal.oid, USER)
        self.assertEqual(principal.scopes, ("Knowledge.Ask",))

    def test_malformed_claims_and_cross_tenant_tokens_fail_closed(self):
        changes = [
            {"iss": "https://attacker.example.com"}, {"tid": OTHER}, {"aud": OTHER}, {"aud": [APP]},
            {"oid": ""}, {"oid": "not-a-guid"}, {"scp": ["Knowledge.Ask"]}, {"scp": ""}, {"scp": None},
            {"ver": "1.0"}, {"acct": True}, {"acct": 3}, {"exp": "9999999999"}, {"exp": True},
            {"iat": False}, {"nbf": False}, {"exp": time.time() - 120}, {"nbf": time.time() + 300},
            {"scp": "Other.Scope"},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(AuthError):
                self.validator.validate(self.token(**change))
        for claim in self.claims:
            claims = dict(self.claims)
            del claims[claim]
            token = jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "test-key"})
            with self.subTest(missing=claim), self.assertRaises(AuthError):
                self.validator.validate(token)

    def test_hs256_unsigned_and_wrong_signature_rejected(self):
        bad_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        tokens = [
            jwt.encode(self.claims, b"x" * 32, algorithm="HS256", headers={"kid": "test-key"}),
            jwt.encode(self.claims, "", algorithm="none", headers={"kid": "test-key"}),
            jwt.encode(self.claims, bad_key, algorithm="RS256", headers={"kid": "test-key"}),
            "malformed", "x" * 32769,
        ]
        for token in tokens:
            with self.subTest(token=token[:20]), self.assertRaises(AuthError):
                self.validator.validate(token)

    def test_jwks_is_pinned_and_unknown_key_refresh_is_bounded(self):
        raw = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        raw.update(kid="test-key", use="sig", alg="RS256")
        calls = []
        def fetch(url):
            calls.append(url)
            return {"keys": [raw]}
        keys = MicrosoftSigningKeys(TENANT, fetch=fetch, clock=lambda: 1000)
        validator = EntraTokenValidator(TENANT, APP, keys=keys)
        validator.validate(self.token())
        for _ in range(3):
            with self.assertRaises(AuthError):
                keys.get("unknown")
        self.assertEqual(calls, [f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys"])

    def test_empty_or_weak_jwks_is_not_trusted(self):
        for payload in ({"keys": []}, {"keys": "invalid"}, {"keys": [{"kty": "oct", "kid": "x"}]}):
            keys = MicrosoftSigningKeys(TENANT, fetch=lambda url: payload)
            with self.subTest(payload=payload), self.assertRaises(DependencyUnavailable):
                keys.get("x")


class DirectoryTests(OfflineTestCase):
    def test_real_graph_request_shape_and_no_membership_cache(self):
        calls = []
        def fetch(url, **kwargs):
            calls.append((url, kwargs))
            if "checkMemberGroups" in url:
                return {"value": [GROUP]}
            return {"id": USER, "userType": "Member", "accountEnabled": True}
        directory = GraphDirectory(SimpleNamespace(get=lambda: "opaque-test-token"), GROUP, fetch=fetch)
        self.assertEqual(directory.find_user(USER).groups, (GROUP,))
        self.assertEqual(directory.find_user(USER).groups, (GROUP,))
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[1][1]["body"], {"groupIds": [GROUP]})
        self.assertEqual(calls[1][1]["method"], "POST")
        self.assertTrue(all(url.startswith("https://graph.microsoft.com/v1.0/users/" + USER) for url, _ in calls))

    def test_disabled_unknown_type_and_malformed_membership_fail_closed(self):
        for response in ({"id": USER, "userType": "Member", "accountEnabled": False},
                         {"id": USER, "userType": "Unexpected", "accountEnabled": True}):
            directory = GraphDirectory(SimpleNamespace(get=lambda: "test"), GROUP, fetch=lambda *a, **kw: response)
            if response["userType"] == "Member":
                self.assertIsNone(directory.find_user(USER))
            else:
                with self.assertRaises(DependencyUnavailable):
                    directory.find_user(USER)

    def test_managed_identity_requires_injected_local_endpoint(self):
        for endpoint in ("https://evil.example/token", "http://169.254.169.254/token",
                         "http://localhost/token?resource=attacker"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                ManagedIdentityGraphToken(GRAPH, {"IDENTITY_ENDPOINT": endpoint, "IDENTITY_HEADER": "test"})
        provider = ManagedIdentityGraphToken(
            GRAPH, {"IDENTITY_ENDPOINT": "http://localhost:1234/msi/token", "IDENTITY_HEADER": "test"},
            credential=SimpleNamespace(get_token=lambda scope: SimpleNamespace(token="test", expires_on=2000)),
            clock=lambda: 1000)
        self.assertEqual(provider.get(), "test")

    def test_sdk_identity_failure_is_fail_closed(self):
        def unavailable(scope):
            raise RuntimeError("SDK could not reach identity endpoint")
        provider = ManagedIdentityGraphToken(
            GRAPH, {"IDENTITY_ENDPOINT": "http://localhost:1234/msi/token", "IDENTITY_HEADER": "test"},
            credential=SimpleNamespace(get_token=unavailable))
        with self.assertRaises(DependencyUnavailable):
            provider.get()


class PersistenceTests(LiveFixture):
    def principal(self, oid=USER):
        return Principal(oid, TENANT, None, None, ("Knowledge.Ask",), 0)

    def chunk(self, number):
        return Chunk("chunk-" + str(number), "document-1", "ref-opaque", "Title", "General",
                     "/Shareable/demo.txt", "file", number, 4, "chamber seasoning", 0)

    def test_rate_and_coverage_survive_restart_and_are_user_isolated(self):
        path = self.config.state_path
        state = DurableState(path, "test-binding")
        contract = self.contract.with_changes(rateLimitPerMinute=1)
        pdp = DurablePDP(contract, None, self.clock, state)
        key = state.ref_key
        with state.transaction():
            self.assertTrue(pdp.consume_rate(self.principal()).allowed)
            pdp.record_served(USER, self.chunk(0))
        state.close()
        restarted = DurableState(path, "test-binding")
        self.addCleanup(restarted.close)
        pdp = DurablePDP(contract, None, self.clock, restarted)
        with restarted.transaction():
            self.assertFalse(pdp.consume_rate(self.principal()).allowed)
            self.assertTrue(pdp.consume_rate(self.principal(OTHER)).allowed)
            self.assertFalse(pdp.exfiltration_admit(USER, self.chunk(1)))
            self.assertTrue(pdp.exfiltration_admit(OTHER, self.chunk(1)))
            self.assertTrue(pdp.exfiltration_admit(USER, self.chunk(0)))
        self.assertEqual(restarted.ref_key, key)
        self.clock.advance(hours=25)
        with restarted.transaction():
            self.assertTrue(pdp.exfiltration_admit(USER, self.chunk(1)))

    def test_state_is_bound_to_deployment_and_transaction_failure_rolls_back(self):
        state = DurableState(self.config.state_path, "test-binding")
        with self.assertRaises(RuntimeError):
            with state.transaction():
                state.db.execute("INSERT INTO rate VALUES (?, 0, 1)", (USER,))
                raise RuntimeError("commit was never reached")
        self.assertEqual(state.db.execute("SELECT COUNT(*) FROM rate").fetchone()[0], 0)
        state.close()
        with self.assertRaises(ValueError):
            DurableState(self.config.state_path, "different-binding")

    def test_second_process_connection_cannot_reset_active_state(self):
        state = self.open_state()
        with self.assertRaises(sqlite3.OperationalError):
            DurableState(self.config.state_path, f"{TENANT}:{APP}:{self.contract.contractId}")
        with state.transaction():
            self.assertEqual(state.db.execute("SELECT COUNT(*) FROM rate").fetchone()[0], 0)


@unittest.skipIf(jwt is None, "install deployment/requirements-live.txt for live integration tests")
class LiveServiceTests(LiveFixture):
    def setUp(self):
        super().setUp()
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.state = self.open_state()
        self.snapshot = load_snapshot(self.config, self.contract, self.clock)
        self.is_member, self.is_guest, self.fail_graph = True, False, False
        def fetch(url, **kwargs):
            if self.fail_graph:
                raise DependencyUnavailable("test outage")
            if "checkMemberGroups" in url:
                return {"value": [GROUP] if self.is_member else []}
            return {"id": USER, "userType": "Guest" if self.is_guest else "Member", "accountEnabled": True}
        directory = GraphDirectory(SimpleNamespace(get=lambda: "opaque-token"), GROUP, fetch=fetch)
        self.broker = LiveBroker(
            config=self.config, snapshot=self.snapshot, state=self.state,
            contract_hash=_digest(self.contract_path.read_bytes()), contract=self.contract, directory=directory,
            index=_build(self.snapshot.documents, 500, self.state.ref_key),
            validator=EntraTokenValidator(TENANT, APP, keys={"test": self.key.public_key()}),
            audit=DurableAudit(self.state, self.clock), clock=self.clock)
        now = int(time.time())
        token = jwt.encode({"iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": APP, "tid": TENANT,
                            "oid": USER, "scp": "Knowledge.Ask", "exp": now + 300, "iat": now - 1,
                            "nbf": now - 1, "ver": "2.0"}, self.key, algorithm="RS256", headers={"kid": "test"})
        self.authorization = "Bearer " + token

    def ask(self):
        return self.broker.execute("ask", self.authorization,
                                   {"question": "chamber seasoning", "purpose": self.contract.purpose})

    def test_success_is_explicit_demo_and_never_reveals_source_location(self):
        response = self.ask()
        self.assertEqual(response.status, 200)
        self.assertTrue(response.body["citations"])
        raw = json.dumps(response.body)
        self.assertNotIn("sharepoint.com", raw)
        self.assertNotIn("/Shareable", raw)
        self.assertNotIn("graph-demo-source", raw)
        self.assertEqual(response.body["service"]["purview"]["evaluation"], "not evaluated")
        self.assertEqual(response.body["service"]["retrieval"], "local-bm25")
        self.assertTrue(response.body["citations"][0]["accessRequestUrl"].startswith(
            "https://broker.example.com/access-request?ref=ref-"))
        self.assertGreater(self.state.db.execute("SELECT COUNT(*) FROM coverage").fetchone()[0], 0)

    def test_live_directory_membership_guest_and_outage_denials(self):
        self.is_member = False
        self.assertEqual(self.ask().status, 403)
        self.is_member, self.is_guest = True, True
        self.assertEqual(self.ask().status, 403)
        self.is_guest, self.fail_graph = False, True
        response = self.ask()
        self.assertEqual(response.status, 503)
        self.assertNotIn("citations", response.body)

    def test_missing_auth_never_releases_data(self):
        with patch.object(self.broker.directory, "find_user", side_effect=AssertionError("no Graph call")):
            response = self.broker.execute("ask", None, {"question": "chamber", "purpose": self.contract.purpose})
        self.assertEqual(response.status, 401)
        self.assertNotIn("citations", response.body)

    def test_unpaired_surrogates_are_rejected_before_state_transaction(self):
        for operation, payload in (
            ("ask", {"question": "\ud800", "purpose": self.contract.purpose}),
            ("search", {"query": "\udfff", "purpose": self.contract.purpose}),
            ("mcp", {"jsonrpc": "2.0", "id": "\ud800", "method": "tools/list"}),
            ("ask", {"\ud800": "invalid key"}),
        ):
            with self.subTest(operation=operation), patch.object(
                    self.state, "transaction", side_effect=AssertionError("state must remain untouched")):
                response = self.broker.execute(operation, self.authorization, payload)
            self.assertEqual(response.status, 400)
            self.assertNotIn("citations", response.body)
        self.assertTrue(self.state.healthy())
        self.assertEqual(self.ask().status, 200)

    def test_contract_file_change_and_expiration_revoke_serving(self):
        self.clock.advance(days=8)
        self.assertEqual(self.ask().status, 503)
        self.clock.advance(days=-8)
        self.contract_path.write_text('{"status":"revoked"}', encoding="utf-8")
        self.assertEqual(self.ask().status, 503)

    def test_sqlite_failure_never_releases_answer(self):
        with patch.object(self.broker.audit, "append", side_effect=sqlite3.OperationalError("disk full")):
            response = self.ask()
        self.assertEqual(response.status, 503)
        self.assertNotIn("citations", response.body)
        self.assertEqual(self.state.db.execute("SELECT COUNT(*) FROM coverage").fetchone()[0], 0)

    def test_mcp_auth_tools_and_malformed_calls(self):
        def call(method, params=None, authorization=None):
            return self.broker.execute("mcp", authorization or self.authorization,
                                       {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
        result = call("initialize", {"protocolVersion": "2025-06-18"})
        self.assertEqual(result.body["result"]["protocolVersion"], "2025-06-18")
        tools = call("tools/list").body["result"]["tools"]
        self.assertEqual({tool["name"] for tool in tools}, {"askKnowledge", "searchKnowledge"})
        self.assertEqual(tools[0]["inputSchema"]["properties"]["purpose"]["const"], self.contract.purpose)
        result = call("tools/call", {"name": "askKnowledge",
                                    "arguments": {"question": "chamber", "purpose": self.contract.purpose}})
        self.assertFalse(result.body["result"]["isError"])
        self.assertEqual(result.body["result"]["structuredContent"]["service"]["purview"]["evaluation"], "not evaluated")
        self.assertEqual(call("tools/call", {"name": []}).body["error"]["code"], -32602)
        self.assertEqual(self.broker.execute("mcp", None, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status, 401)
        self.is_member = False
        self.assertEqual(call("tools/list").status, 403)

    def test_http_health_access_request_auth_and_origin(self):
        server = HTTPServer(("127.0.0.1", 0), make_live_handler(self.broker))
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        def stop():
            server.shutdown()
            server.server_close()
            thread.join(2)
        self.addCleanup(stop)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urlopen(base + "/healthz", timeout=5) as response:
            body = json.load(response)
            self.assertEqual(body["service"]["purview"]["evaluation"], "not evaluated")
            self.assertEqual(body["directoryProbe"], "not-performed")
            self.assertTrue(all(body["checks"].values()))
        with urlopen(base + "/access-request?ref=secret-source", timeout=5) as response:
            self.assertNotIn("secret-source", response.read().decode())
        payload = json.dumps({"query": "chamber", "purpose": self.contract.purpose}).encode()
        request = Request(base + "/search", data=payload, headers={"Content-Type": "application/json",
                                                                 "Authorization": self.authorization})
        with urlopen(request, timeout=5) as response:
            self.assertTrue(json.load(response)["citations"])
            self.assertEqual(response.headers["X-Purview-Evaluation"], "not-evaluated")
        for headers, expected in (({}, 401), ({"Authorization": self.authorization, "Origin": "https://evil.example"}, 403)):
            # Origin is rejected before reading a body; unread bytes can cause a Windows TCP reset.
            request = Request(base + "/search", data=b"" if "Origin" in headers else payload,
                              headers={"Content-Type": "application/json", **headers})
            with self.assertRaises(HTTPError) as caught:
                urlopen(request, timeout=5)
            self.assertEqual(caught.exception.code, expected)
            caught.exception.close()
        with patch.object(self.state, "healthy", return_value=False):
            with self.assertRaises(HTTPError) as caught:
                urlopen(base + "/healthz", timeout=5)
            self.assertEqual(caught.exception.code, 503)
            body = json.load(caught.exception)
            caught.exception.close()
            self.assertFalse(body["checks"]["persistenceReady"])
            self.assertNotIn(USER, json.dumps(body))
            self.assertNotIn(str(self.config.state_path), json.dumps(body))
