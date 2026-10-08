"""Blob persistence regressions: leases, CAS, crash recovery and release ordering."""
from __future__ import annotations

import sqlite3
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

try:
    from azure.core import MatchConditions
except ImportError:
    MatchConditions = None

from common.clock import FixedClock
from common.testing import OfflineTestCase
from arch_b_broker.broker import BrokerResult
from arch_b_broker.live import LiveBroker, build_live_broker
from arch_b_broker.live_auth import DependencyUnavailable
from arch_b_broker.live_blob import (BlobDurableState, LeasedBlobCheckpoint, MAX_STATE_BYTES,
                                    OPERATION_TIMEOUT, bootstrap_marker)
from arch_b_broker.live_state import DurableAudit, DurablePDP
from arch_b_broker.tests.test_live import APP, TENANT, USER, OTHER, GRAPH, LiveFixture, contract_data
from common.contract import contract_from_dict


class FakeCloud:
    def __init__(self, data):
        self.data, self.etag, self.now = data, 1, 1000.0
        self.owner, self.expiry = None, 0
        self.fail_renew, self.fail_upload, self.ambiguous_upload = False, False, False
        self.fail_release, self.missing_etag = False, False
        self.on_upload, self.on_renew = None, None
        self.in_upload = False
        self.calls = []
        self.upload_count = 0

    def client(self):
        cloud = self
        class Client:
            def acquire_lease(self, *, lease_duration, **kwargs):
                cloud.calls.append(("acquire", kwargs))
                if cloud.data is None or (cloud.owner is not None and cloud.now < cloud.expiry):
                    raise RuntimeError("missing blob or conflicting lease")
                lease = Lease()
                cloud.owner, cloud.expiry = lease, cloud.now + lease_duration
                return lease
            def get_blob_properties(self, **kwargs):
                cloud.calls.append(("properties", kwargs))
                kwargs["lease"].check()
                return SimpleNamespace(size=len(cloud.data), etag=str(cloud.etag))
            def download_blob(self, **kwargs):
                cloud.calls.append(("download", kwargs))
                self.check(kwargs)
                return SimpleNamespace(readall=lambda: cloud.data)
            def check(self, kwargs):
                kwargs["lease"].check()
                if kwargs["match_condition"] != MatchConditions.IfNotModified or kwargs["etag"] != str(cloud.etag):
                    raise RuntimeError("ETag mismatch")
            def upload_blob(self, data, **kwargs):
                cloud.calls.append(("upload", kwargs))
                self.check(kwargs)
                cloud.in_upload = True
                try:
                    if cloud.on_upload:
                        cloud.on_upload()
                    if cloud.fail_upload:
                        raise RuntimeError("storage unavailable")
                    cloud.data, cloud.etag = data, cloud.etag + 1
                    cloud.upload_count += 1
                    if cloud.ambiguous_upload:
                        raise RuntimeError("response lost after commit")
                    return {} if cloud.missing_etag else {"etag": str(cloud.etag)}
                finally:
                    cloud.in_upload = False
            def close(self):
                pass
        class Lease:
            def check(self):
                if cloud.owner is not self or cloud.now >= cloud.expiry:
                    raise RuntimeError("lease lost")
            def renew(self, **kwargs):
                cloud.calls.append(("renew", kwargs))
                self.check()
                if cloud.in_upload:
                    raise AssertionError("SDK lease mutation raced upload")
                if cloud.on_renew:
                    cloud.on_renew()
                if cloud.fail_renew:
                    raise RuntimeError("renewal unavailable")
                cloud.expiry = cloud.now + 60
            def release(self, **kwargs):
                cloud.calls.append(("release", kwargs))
                if cloud.fail_release:
                    raise RuntimeError("release failed")
                self.check()
                cloud.owner = None
        return Client()


@unittest.skipIf(MatchConditions is None, "install deployment/requirements-live.txt for blob persistence tests")
class BlobPersistenceTests(OfflineTestCase):
    def setUp(self):
        self.root = self.scratch("broker-blob")
        self.binding = f"{TENANT}:{APP}:demo-live-001"
        self.cloud = FakeCloud(bootstrap_marker(self.binding))

    def checkpoint(self):
        return LeasedBlobCheckpoint(self.cloud.client(), clock=lambda: self.cloud.now, renew_interval=3600)

    def state(self):
        return BlobDurableState(self.root / "broker.sqlite", self.binding, self.checkpoint())

    def test_committed_rate_and_coverage_restore_after_restart(self):
        state = self.state()
        key = state.ref_key
        clock = FixedClock()
        contract = contract_from_dict(contract_data()).with_changes(rateLimitPerMinute=1)
        principal = SimpleNamespace(oid=USER)
        chunk = SimpleNamespace(doc_id="source", chunk_id="chunk1", total_chunks=4)
        pdp = DurablePDP(contract, None, clock, state)
        with state.transaction():
            self.assertTrue(pdp.consume_rate(principal).allowed)
            pdp.record_served(USER, chunk)
        self.assertGreater(self.cloud.upload_count, 1)
        state.close()
        restarted = self.state()
        self.addCleanup(restarted.close)
        pdp = DurablePDP(contract, None, clock, restarted)
        with restarted.transaction():
            self.assertFalse(pdp.consume_rate(principal).allowed)
            self.assertTrue(pdp.consume_rate(SimpleNamespace(oid=OTHER)).allowed)
            self.assertFalse(pdp.exfiltration_admit(USER, SimpleNamespace(doc_id="source", chunk_id="chunk2", total_chunks=4)))
        self.assertEqual(restarted.ref_key, key)

    def test_overlapping_instance_cannot_acquire_blob_lease(self):
        first = self.state()
        self.addCleanup(first.close)
        with self.assertRaises(DependencyUnavailable):
            self.state()
        with first.transaction():
            first.db.execute("INSERT INTO rate VALUES (?, 1, 1)", (USER,))
        self.assertTrue(first.healthy())

    def test_lost_lease_fences_old_writer_and_restart_ignores_local_cache(self):
        first = self.state()
        self.addCleanup(first.close)
        self.cloud.now += 61
        second = self.state()
        self.addCleanup(second.close)
        with second.transaction():
            second.db.execute("INSERT INTO rate VALUES (?, 0, 1)", (USER,))
        with self.assertRaises(DependencyUnavailable):
            with first.transaction():
                first.db.execute("DELETE FROM rate")
        self.assertFalse(first.healthy())
        self.assertEqual(second.db.execute("SELECT COUNT(*) FROM rate").fetchone()[0], 1)

    def test_lease_renewal_failure_stops_requests(self):
        state = self.state()
        self.addCleanup(state.close)
        self.cloud.fail_renew = True
        with self.assertRaises(DependencyUnavailable):
            with state.transaction():
                self.fail("transaction must not start")
        self.assertFalse(state.healthy())

    def test_etag_conflict_never_overwrites_remote(self):
        state = self.state()
        self.addCleanup(state.close)
        before = self.cloud.data
        self.cloud.etag += 1
        with self.assertRaises(DependencyUnavailable):
            with state.transaction():
                state.db.execute("INSERT INTO rate VALUES (?, 0, 1)", (USER,))
        self.assertEqual(before, self.cloud.data)
        self.assertFalse(state.healthy())

    def test_upload_failure_withholds_answer_and_poisoned_process_cannot_resume(self):
        state = self.state()
        self.addCleanup(state.close)
        broker = object.__new__(LiveBroker)
        broker.state = state
        broker.valid = lambda: True
        broker.metadata = lambda: {"purview": {"evaluation": "not evaluated"}}
        self.cloud.fail_upload = True
        with patch("arch_b_broker.broker.KnowledgeBroker.ask", return_value=BrokerResult(
                200, {"answer": "MUST NOT BE RELEASED", "citations": [{"excerpt": "private text"}]})):
            response = broker.execute("ask", "Bearer test", {"question": "q"})
        self.assertEqual(response.status, 503)
        self.assertNotIn("answer", response.body)
        self.assertNotIn("citations", response.body)
        self.cloud.fail_upload = False
        with self.assertRaises(DependencyUnavailable):
            with state.transaction():
                self.fail("poisoned state must require restart")

    def test_ambiguous_remote_commit_is_restored_conservatively(self):
        state = self.state()
        self.cloud.ambiguous_upload = True
        with self.assertRaises(DependencyUnavailable):
            with state.transaction():
                state.db.execute("INSERT INTO rate VALUES (?, 0, 123)", (USER,))
        state.close()
        self.cloud.ambiguous_upload = False
        restarted = self.state()
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.db.execute("SELECT tokens FROM rate WHERE oid=?", (USER,)).fetchone()[0], 0)

    def test_missing_empty_wrong_binding_or_corrupt_blob_never_resets(self):
        for raw in (None, b"", b"invalid", bootstrap_marker("wrong-binding"), b"SQLite format 3\x00invalid",
                    bootstrap_marker(self.binding) + b"\n"):
            self.cloud = FakeCloud(raw)
            with self.subTest(raw=raw), self.assertRaises((DependencyUnavailable, ValueError, sqlite3.Error)):
                self.state()
            self.assertEqual(self.cloud.data, raw)

    def test_local_rollback_does_not_checkpoint_and_never_releases_result(self):
        state = self.state()
        self.addCleanup(state.close)
        before = self.cloud.data
        with self.assertRaises(RuntimeError):
            with state.transaction():
                state.db.execute("INSERT INTO rate VALUES (?, 0, 1)", (USER,))
                raise RuntimeError("request failed")
        self.assertEqual(self.cloud.data, before)
        self.assertEqual(state.db.execute("SELECT COUNT(*) FROM rate").fetchone()[0], 0)

    def test_smaller_local_stale_database_cannot_replace_remote(self):
        stale = self.root / "broker.sqlite"
        stale.write_bytes(b"untrusted stale local file")
        state = self.state()
        self.addCleanup(state.close)
        self.assertEqual(stale.read_bytes(), b"untrusted stale local file")
        self.assertTrue(self.cloud.data.startswith(b"SQLite format 3\x00"))

    def test_incomplete_or_malformed_sqlite_is_never_repaired_or_uploaded(self):
        state = self.state()
        with state.transaction():
            DurableAudit(state, FixedClock()).append("test", arch="B")
        state.close()
        valid = self.cloud.data
        mutations = [
            "DROP TABLE rate",
            "DELETE FROM meta WHERE key='binding'",
            "DELETE FROM meta WHERE key='ref_key'",
            "UPDATE meta SET value='wrong-binding' WHERE key='binding'",
            "UPDATE meta SET value=X'1234' WHERE key='ref_key'",
            "INSERT INTO rate VALUES ('user', -1, 1)",
            "INSERT INTO rate VALUES ('user', 'invalid', 1)",
            "INSERT INTO coverage VALUES ('user', 'doc', '', 1)",
            "UPDATE audit SET hash='invalid'",
            "UPDATE audit SET seq=5",
            "ALTER TABLE rate ADD COLUMN hidden TEXT GENERATED ALWAYS AS (oid) VIRTUAL",
            "CREATE TRIGGER erase_coverage AFTER INSERT ON rate BEGIN DELETE FROM coverage; END",
        ]
        for sql in mutations:
            with self.subTest(sql=sql):
                db = sqlite3.connect(":memory:")
                try:
                    db.deserialize(valid)
                    db.execute(sql)
                    db.commit()
                    raw = db.serialize()
                finally:
                    db.close()
                self.cloud = FakeCloud(raw)
                with self.assertRaises(ValueError):
                    self.state()
                self.assertEqual(self.cloud.data, raw)
                self.assertEqual(self.cloud.upload_count, 0)
                self.assertIsNone(self.cloud.owner)
                self.assertEqual(list(self.root.iterdir()), [])

    def test_unrelated_valid_sqlite_cannot_become_a_new_broker(self):
        db = sqlite3.connect(":memory:")
        try:
            db.execute("CREATE TABLE unrelated (value TEXT)")
            raw = db.serialize()
        finally:
            db.close()
        self.cloud = FakeCloud(raw)
        with self.assertRaises(ValueError):
            self.state()
        self.assertEqual(self.cloud.data, raw)
        self.assertEqual(self.cloud.upload_count, 0)

    def test_audit_chain_restores_without_reset(self):
        state = self.state()
        with state.transaction():
            first = DurableAudit(state, FixedClock()).append("test", arch="B")
        state.close()
        restarted = self.state()
        self.addCleanup(restarted.close)
        with restarted.transaction():
            second = DurableAudit(restarted, FixedClock()).append("test-again", arch="B")
        self.assertEqual(second["seq"], 2)
        self.assertEqual(second["prevHash"], first["hash"])

    def test_every_sdk_operation_is_bounded_and_lease_precedes_download(self):
        state = self.state()
        state.close()
        self.assertEqual([name for name, _ in self.cloud.calls[:3]], ["acquire", "properties", "download"])
        for name, kwargs in self.cloud.calls:
            with self.subTest(name=name):
                self.assertEqual(kwargs["timeout"], OPERATION_TIMEOUT)
                if name in ("download", "upload"):
                    self.assertEqual(kwargs["match_condition"], MatchConditions.IfNotModified)
                    self.assertIsNotNone(kwargs["lease"])
                    self.assertEqual(kwargs["max_concurrency"], 1)

    def test_upload_without_etag_poisoned_even_if_remote_was_committed(self):
        state = self.state()
        self.addCleanup(state.close)
        self.cloud.missing_etag = True
        with self.assertRaises(DependencyUnavailable):
            with state.transaction():
                state.db.execute("INSERT INTO rate VALUES (?, 0, 1)", (USER,))
        self.assertFalse(state.healthy())
        self.cloud.missing_etag = False
        with self.assertRaises(DependencyUnavailable):
            with state.transaction():
                pass

    def test_renewal_that_exceeds_safety_deadline_is_not_trusted(self):
        state = self.state()
        self.addCleanup(state.close)
        self.cloud.on_renew = lambda: setattr(self.cloud, "now", self.cloud.now + 46)
        with self.assertRaises(DependencyUnavailable):
            state.checkpoint.renew()
        self.assertFalse(state.healthy())

    def test_failed_initial_checkpoint_never_starts_broker_and_releases_lease(self):
        before = self.cloud.data
        self.cloud.fail_upload = True
        with self.assertRaises(DependencyUnavailable):
            self.state()
        self.assertEqual(self.cloud.data, before)
        self.assertIsNone(self.cloud.owner)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_expired_lease_during_upload_withholds_content_and_poison_is_permanent(self):
        state = self.state()
        self.addCleanup(state.close)
        self.cloud.on_upload = lambda: setattr(self.cloud, "now", self.cloud.now + 46)
        with self.assertRaises(DependencyUnavailable):
            with state.transaction():
                state.db.execute("INSERT INTO rate VALUES (?, 0, 1)", (USER,))
        self.assertFalse(state.healthy())
        self.cloud.on_upload = None
        with self.assertRaises(DependencyUnavailable):
            state.checkpoint.renew()

    def test_oversize_checkpoint_fails_without_replacing_remote(self):
        state = self.state()
        self.addCleanup(state.close)
        before = self.cloud.data
        with self.assertRaises(DependencyUnavailable):
            state.checkpoint.save(b"x" * (MAX_STATE_BYTES + 1))
        self.assertEqual(self.cloud.data, before)
        self.assertFalse(state.healthy())

    def test_background_lease_failure_is_visible_and_permanently_fences_requests(self):
        checkpoint = LeasedBlobCheckpoint(self.cloud.client(), clock=lambda: self.cloud.now, renew_interval=0.01)
        state = BlobDurableState(self.root / "broker.sqlite", self.binding, checkpoint)
        self.addCleanup(state.close)
        with self.assertLogs("arch_b_broker.live_blob", level="ERROR") as logged:
            self.cloud.fail_renew = True
            checkpoint.thread.join(2)
        self.assertFalse(checkpoint.thread.is_alive())
        self.assertIn("broker_blob_lease_renewal_failed", logged.output[0])
        self.cloud.fail_renew = False
        with self.assertRaises(DependencyUnavailable):
            with state.transaction():
                pass

    def test_release_failure_is_visible_and_close_removes_only_its_cache(self):
        state = self.state()
        path = state.local_path
        self.cloud.fail_release = True
        with self.assertLogs("arch_b_broker.live_blob", level="WARNING") as logged:
            state.close()
        self.assertIn("broker_blob_lease_release_failed", logged.output[0])
        self.assertFalse(path.exists())
        self.assertFalse(state.healthy())
        state.close()

    def test_identity_outage_rolls_back_without_poisoning_a_confirmed_lease(self):
        state = self.state()
        self.addCleanup(state.close)
        before = self.cloud.data
        with self.assertRaises(DependencyUnavailable):
            with state.transaction():
                state.db.execute("INSERT INTO rate VALUES (?, 0, 1)", (USER,))
                raise DependencyUnavailable("Graph temporarily unavailable")
        self.assertTrue(state.healthy())
        self.assertEqual(self.cloud.data, before)
        self.assertEqual(state.db.execute("SELECT COUNT(*) FROM rate").fetchone()[0], 0)

    def test_request_does_not_return_until_upload_and_renewal_never_races_sdk(self):
        state = self.state()
        self.addCleanup(state.close)
        entered, release, attempted, renewed, returned = [threading.Event() for _ in range(5)]
        errors, results = [], []
        def block_upload():
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test upload wait expired")
        self.cloud.on_upload = block_upload
        broker = object.__new__(LiveBroker)
        broker.state, broker.valid, broker.metadata = state, lambda: True, lambda: {}
        def ask():
            try:
                results.append(broker.execute("ask", None, {}))
                returned.set()
            except BaseException as exc:
                errors.append(exc)
        def renew():
            attempted.set()
            try:
                state.checkpoint.renew()
                renewed.set()
            except BaseException as exc:
                errors.append(exc)
        with patch("arch_b_broker.broker.KnowledgeBroker.ask", return_value=BrokerResult(
                200, {"answer": "released only after durable commit"})):
            writer = threading.Thread(target=ask, daemon=True)
            writer.start()
            try:
                self.assertTrue(entered.wait(2))
                renewer = threading.Thread(target=renew, daemon=True)
                renewer.start()
                self.assertTrue(attempted.wait(2))
                self.assertFalse(renewed.wait(0.05))
                self.assertFalse(returned.is_set())
            finally:
                release.set()
                writer.join(3)
                if "renewer" in locals():
                    renewer.join(3)
        self.assertFalse(errors)
        self.assertTrue(returned.is_set())
        self.assertTrue(renewed.is_set())
        self.assertEqual(results[0].status, 200)
        self.assertEqual(self.cloud.data, state.db.serialize())

    def test_search_and_mcp_never_return_generated_content_on_checkpoint_failure(self):
        for operation, target in (("search", "arch_b_broker.broker.KnowledgeBroker.search"),
                                  ("mcp", "arch_b_broker.live.LiveBroker._mcp")):
            with self.subTest(operation=operation):
                self.cloud = FakeCloud(bootstrap_marker(self.binding))
                state = self.state()
                try:
                    broker = object.__new__(LiveBroker)
                    broker.state, broker.valid, broker.metadata = state, lambda: True, lambda: {}
                    self.cloud.fail_upload = True
                    with patch(target, return_value=BrokerResult(200, {"result": "DO NOT RELEASE"})):
                        result = broker.execute(operation, None, {})
                    self.assertEqual(result.status, 503)
                    self.assertNotIn("DO NOT RELEASE", str(result.body))
                    self.cloud.fail_upload = False
                    self.assertEqual(broker.execute(operation, None, {}).status, 503)
                finally:
                    state.close()


@unittest.skipIf(MatchConditions is None, "install deployment/requirements-live.txt for blob startup tests")
class BlobStartupTests(LiveFixture):
    def test_entry_point_uses_same_managed_credential_and_anonymous_request_never_calls_graph(self):
        cloud = FakeCloud(bootstrap_marker(f"{TENANT}:{APP}:{self.contract.contractId}"))
        credential, provider = object(), Mock()
        provider.credential = credential
        with patch.object(type(self.config), "verify_mount"), \
                patch("arch_b_broker.live.SystemClock", return_value=self.clock), \
                patch("arch_b_broker.live.ManagedIdentityGraphToken", return_value=provider) as identity, \
                patch("azure.storage.blob.BlobClient", return_value=cloud.client()) as client:
            broker = build_live_broker(self.config, self.environ)
        try:
            self.assertIsInstance(broker.state, BlobDurableState)
            identity.assert_called_once_with(GRAPH, self.environ)
            self.assertIs(client.call_args.kwargs["credential"], credential)
            self.assertEqual(client.call_args.kwargs["retry_total"], 0)
            with patch.object(broker.directory, "find_user", side_effect=AssertionError("no Graph")):
                self.assertTrue(broker.valid())
                self.assertEqual(broker.execute("ask", None, {}).status, 401)
            provider.get.assert_not_called()
            self.assertGreaterEqual(cloud.upload_count, 3)
        finally:
            broker.state.close()
            provider.close()
