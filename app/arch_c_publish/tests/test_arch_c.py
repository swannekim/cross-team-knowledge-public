"""Architecture C - governed derivative publishing to a Knowledge Exchange site."""
from __future__ import annotations

import json
import re

from arch_c_publish.derivative import DERIVED_NOTICE, parse_front_matter
from arch_c_publish.publisher import KX_SITE_URL
from arch_c_publish.workflow import (COMPLIANCE_APPROVED, EXPIRED, OWNER_APPROVED, PUBLISHED, REJECTED, REPUBLISHED,
                                     REQUESTED, REVOKED, STALE, PublishingWorkflow, WorkflowError,
                                     build_default_workflow)
from common.audit import AuditLog
from common.paths import ARTIFACTS_DIR
from common.clock import FixedClock
from common.evaluation import (HIGHLY_CONFIDENTIAL_CANARIES, INJECTION_CANARY, PII_CANARIES, SECRET_CANARIES,
                               find_canaries, original_location_markers, pii_findings)
from common.sources import load_environment
from common.testing import OfflineTestCase

PURPOSE = "yield-excursion-analysis"


class ArchCCase(OfflineTestCase):
    on_disk = False

    def setUp(self):
        self.clock = FixedClock("2026-10-07T01:00:00Z")
        self.env = load_environment(self.clock)
        self.root = self.scratch("arch-c") if self.on_disk else None
        self.wf, self.site, self.index = build_default_workflow(
            self.env, clock=self.clock, root_dir=self.root,
            state_path=(self.root / "workflow.json") if self.on_disk else None)
        self.ids = {i["name"].split("_")[0]: i["id"] for i in self.env.drive.files()}

    def publish(self, key: str, requester: str = "bob"):
        req = self.wf.request(requester, self.ids[key], PURPOSE)
        self.wf.approve_owner("alice", req.request_id)
        if req.label == "Confidential":
            self.wf.approve_compliance("erin", req.request_id)
        return self.wf.publish(req.request_id)

    def assert_card_clean(self, text: str):
        text = re.sub(re.escape(KX_SITE_URL) + r"[^\s\"']*", "<knowledge-exchange-url>", text)  # the card's own location
        self.assertEqual(find_canaries(text, PII_CANARIES + SECRET_CANARIES + HIGHLY_CONFIDENTIAL_CANARIES), [])
        self.assertEqual(pii_findings(text), [])
        lowered = text.lower()
        self.assertEqual([m for m in original_location_markers(self.env, generic=False) if m.lower() in lowered], [])


class ArchCWorkflow(ArchCCase):
    def test_no_publish_without_owner_approval(self):
        """A REQUESTED (unapproved) request cannot be published; nothing reaches the Knowledge Exchange site."""
        req = self.wf.request("bob", self.ids["GL-ETCH-007"], PURPOSE)
        self.assertEqual(req.state, REQUESTED)
        with self.assertRaises(WorkflowError):
            self.wf.publish(req.request_id)
        self.assertEqual(self.site.items(), [])
        with self.assertRaises(WorkflowError):
            self.wf.approve_owner("bob", req.request_id)  # requester / non-owner cannot approve

    def test_general_document_publishes_after_owner_approval(self):
        """A General document needs only the data owner's approval; compliance approval is not applicable."""
        req = self.wf.request("bob", self.ids["GL-ETCH-007"], PURPOSE)
        self.wf.approve_owner("alice", req.request_id)
        with self.assertRaises(WorkflowError):
            self.wf.approve_compliance("erin", req.request_id)
        self.assertEqual(self.wf.publish(req.request_id).state, PUBLISHED)
        self.assertEqual(len(self.site.items()), 1)

    def test_confidential_requires_compliance_approval(self):
        """Confidential content needs owner + a different compliance officer before publishing."""
        req = self.wf.request("bob", self.ids["ER-2291"], PURPOSE)
        self.assertEqual(req.label, "Confidential")
        with self.assertRaises(WorkflowError):
            self.wf.approve_owner("erin", req.request_id)  # compliance officer is not the data owner
        self.wf.approve_owner("alice", req.request_id)
        with self.assertRaises(WorkflowError):
            self.wf.publish(req.request_id)
        with self.assertRaises(WorkflowError):
            self.wf.approve_compliance("alice", req.request_id)  # owner lacks the compliance role
        self.assertEqual(req.state, OWNER_APPROVED)
        self.wf.approve_compliance("erin", req.request_id)
        self.assertEqual(req.state, COMPLIANCE_APPROVED)
        self.assertEqual(self.wf.publish(req.request_id).state, PUBLISHED)
        self.assertTrue(req.card["fields"]["ApprovalId"].startswith("APR-"))

    def test_highly_confidential_and_out_of_scope_auto_rejected(self):
        """HC (above ceiling), /Internal and /Shareable/Drafts requests are auto-rejected and never published."""
        expectations = {"NX7": "auto_rejected:label_above_ceiling", "team": "auto_rejected:out_of_scope_path",
                        "WIP": "auto_rejected:excluded_path"}
        for key, reason in expectations.items():
            req = self.wf.request("bob", self.ids[key], PURPOSE)
            self.assertEqual((req.state, req.reason), (REJECTED, reason))
            with self.assertRaises(WorkflowError):
                self.wf.approve_owner("alice", req.request_id)
        self.assertEqual(self.site.items(), [])

    def test_requester_eligibility(self):
        """Only non-guest audience members with the contract purpose can request (carol, dave, wrong purpose rejected)."""
        cases = [("carol", PURPOSE, "requester_not_in_audience"), ("dave", PURPOSE, "guest_excluded"),
                 ("bob", "marketing", "purpose_mismatch")]
        for user, purpose, reason in cases:
            req = self.wf.request(user, self.ids["GL-ETCH-007"], purpose)
            self.assertEqual((req.state, req.reason), (REJECTED, reason))

    def test_duplicate_requests_are_idempotent(self):
        """Repeated requests for the same source+purpose return the same open request; a new one follows a terminal state."""
        first = self.wf.request("bob", self.ids["YE-0412"], PURPOSE)
        again = self.wf.request("bob", self.ids["YE-0412"], PURPOSE)
        self.assertIs(first, again)
        self.assertEqual(len(self.wf.requests), 1)
        self.assertEqual(self.wf.audit.count("knowledge_requested"), 1)
        self.wf.reject("alice", first.request_id, "not needed")
        third = self.wf.request("bob", self.ids["YE-0412"], PURPOSE)
        self.assertNotEqual(third.request_id, first.request_id)
        self.assertEqual(third.state, REQUESTED)

    def test_card_provenance_redaction_and_no_original_url(self):
        """Cards carry the provenance header (fingerprint, approval id, generatedAt, expiresAt, notice), redacted text,
        an excerpt <= maxExcerptChars, the broker access link - and no original URL, path or id."""
        req = self.publish("SQ-118")
        text = self.site.read(req.card["itemId"])
        meta = parse_front_matter(text)
        for key in ("sourceFingerprint", "approvalId", "generatedAt", "expiresAt", "notice", "sourceRef"):
            self.assertIn(key, meta)
        self.assertEqual(meta["notice"], DERIVED_NOTICE)
        self.assertIn("Derived content – original not shared", text)
        self.assertEqual(meta["sourceFingerprint"], req.card["fingerprint"])
        self.assertRegex(meta["sourceRef"], r"^ref-[0-9a-f]{24}$")
        self.assertIn(f"https://broker.contoso.com/access-request?ref={meta['sourceRef']}", text)
        excerpt = re.search(r"## Redacted excerpt.*?\n\n> (.*?)\n", text, re.S).group(1)
        self.assertLessEqual(len(excerpt), self.env.contract.maxExcerptChars)
        self.assert_card_clean(text)
        self.assertTrue({"EMAIL", "KR_MOBILE", "KR_RRN"} <= set(meta["redactions"]))
        rca = self.publish("YE-0412")
        rca_text = self.site.read(rca.card["itemId"])
        self.assert_card_clean(rca_text)
        self.assertNotIn(INJECTION_CANARY.lower(), rca_text.lower())
        self.assertEqual(parse_front_matter(rca_text)["injectionFlags"], 1)

    def test_bob_finds_card_carol_and_dave_cannot(self):
        """Native index trimming: bob (site member) finds the card; carol (no access) and dave (guest) get nothing."""
        self.publish("YE-0412")
        hits = self.index.search("bob", "root cause seasoning fluorocarbon")
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0]["webUrl"].startswith("https://contoso.sharepoint.com/sites/KnowledgeExchange/"))
        self.assertEqual(self.index.search("carol", "root cause seasoning"), [])
        self.assertEqual(self.index.search("dave", "root cause seasoning"), [])
        self.assertEqual(self.index.search("alice", "root cause seasoning"), [])

    def test_source_change_makes_card_stale_then_republished(self):
        """A source edit withdraws the card (STALE); after re-approval it is regenerated and REPUBLISHED."""
        req = self.publish("ER-2291")
        old_item, old_fp, old_approval = req.card["itemId"], req.card["fingerprint"], req.owner_approval["approvalId"]
        self.clock.advance(days=2)
        doc = self.ids["ER-2291"]
        self.env.drive.update_content(doc, self.env.drive.get_content(doc).decode() +
                                      "\n\n## Update\n\nThe edge-ring setpoint was raised again to 52 C after a Hafnia-free audit.")
        self.assertEqual(self.wf.detect_stale(), [req.request_id])
        self.assertEqual(req.state, STALE)
        self.assertEqual(self.site.items(), [])
        self.assertEqual(self.index.search("bob", "etch recipe CF4"), [])
        with self.assertRaises(WorkflowError):
            self.wf.publish(req.request_id)
        self.wf.approve_owner("alice", req.request_id)
        self.wf.approve_compliance("erin", req.request_id)
        self.assertEqual(self.wf.publish(req.request_id).state, REPUBLISHED)
        self.assertNotEqual(req.card["fingerprint"], old_fp)
        self.assertNotEqual(req.owner_approval["approvalId"], old_approval)
        meta = parse_front_matter(self.site.read(req.card["itemId"]))
        self.assertEqual((meta["cardVersion"], meta["sourceFingerprint"]), (2, req.card["fingerprint"]))
        self.assertEqual(len(self.site.items()), 1)
        self.assertTrue(any(n["subject"].startswith("Knowledge card") for n in self.wf.notifications))
        self.assertTrue(old_item)

    def test_approval_is_bound_to_source_version(self):
        """If the source changes between approval and publishing, publishing is refused and the request goes STALE."""
        req = self.wf.request("bob", self.ids["GL-ETCH-007"], PURPOSE)
        self.wf.approve_owner("alice", req.request_id)
        self.env.drive.update_content(self.ids["GL-ETCH-007"], "ETCH CHAMBER SEASONING GUIDELINE rev 6\n\nNew rule.")
        with self.assertRaises(WorkflowError):
            self.wf.publish(req.request_id)
        self.assertEqual(req.state, STALE)
        self.assertEqual(self.site.items(), [])

    def test_expiry_removes_card(self):
        """After ttlDays the expiry sweep withdraws the card (EXPIRED) and it disappears from search."""
        req = self.publish("tool")
        self.assertEqual(len(self.index.search("bob", "preventive maintenance schedule")), 1)
        self.clock.advance(days=29)
        self.assertEqual(self.wf.sweep_expired(), [])
        self.clock.advance(days=2)
        self.assertEqual(self.wf.sweep_expired(), [req.request_id])
        self.assertEqual(req.state, EXPIRED)
        self.assertEqual(self.site.items(), [])
        self.assertEqual(self.index.search("bob", "preventive maintenance schedule"), [])

    def test_revoke_removes_card(self):
        """The data owner (or compliance) can revoke: the card is deleted and the request is REVOKED; others cannot."""
        req = self.publish("MET-CDSEM-02")
        with self.assertRaises(WorkflowError):
            self.wf.revoke("bob", req.request_id, "not allowed")
        self.wf.revoke("alice", req.request_id, "owner withdrew consent")
        self.assertEqual(req.state, REVOKED)
        self.assertEqual(self.site.items(), [])
        self.assertEqual(self.index.search("bob", "CD-SEM calibration"), [])
        self.assertEqual(self.wf.publisher.graph_log[-1]["method"], "DELETE")

    def test_label_raised_or_source_deleted_after_publish_revokes(self):
        """Relabelling a source to Highly Confidential or deleting it withdraws the published card on the next check."""
        relabel, delete = self.publish("GL-ETCH-007"), self.publish("tool")
        self.env.drive.set_label(self.ids["GL-ETCH-007"], "Highly Confidential")
        self.env.drive.delete(self.ids["tool"])
        self.assertEqual(sorted(self.wf.detect_stale()), sorted([relabel.request_id, delete.request_id]))
        self.assertEqual((relabel.state, relabel.reason), (REVOKED, "policy:label_above_ceiling"))
        self.assertEqual((delete.state, delete.reason), (REVOKED, "source_deleted"))
        self.assertEqual(self.site.items(), [])

    def test_contract_revocation_withdraws_all_cards(self):
        """Revoking the sharing contract revokes every open request and deletes all published cards."""
        self.publish("GL-ETCH-007")
        self.publish("YE-0412")
        pending = self.wf.request("bob", self.ids["SQ-118"], PURPOSE)
        revoked = self.wf.revoke_contract("alice", "contract terminated")
        self.assertEqual(len(revoked), 3)
        self.assertEqual(self.site.items(), [])
        self.assertEqual(pending.state, REVOKED)
        with self.assertRaises(WorkflowError):
            self.wf.publish(pending.request_id)

    def test_invalid_transitions_rejected(self):
        """The state machine refuses illegal transitions (double publish, approving a rejected request, etc.)."""
        req = self.publish("GL-ETCH-007")
        with self.assertRaises(WorkflowError):
            self.wf.publish(req.request_id)
        with self.assertRaises(WorkflowError):
            self.wf.approve_owner("alice", req.request_id)
        rejected = self.wf.request("bob", self.ids["NX7"], PURPOSE)
        with self.assertRaises(WorkflowError):
            self.wf.revoke("alice", rejected.request_id, "x")
        with self.assertRaises(WorkflowError):
            self.wf.get("KR-NOPE")


class ArchCOnDisk(ArchCCase):
    """Site written through to disk (folder + list-item manifest) and workflow state persisted as JSON."""

    on_disk = True

    def test_publish_manifest_columns_and_graph_requests(self):
        """Publishing writes list-item columns (SourceFingerprint, ExpiryDate, ApprovalId, RetentionLabel) and emits the
        production Graph requests (PUT .../{parentId}:/{fileName}:/content, PATCH .../listItem/fields, retentionLabel)."""
        req = self.publish("YE-0412")
        manifest = json.loads(self.site.manifest_path.read_text())
        fields = manifest["items"][req.card["itemId"]]["fields"]
        for column in ("SourceFingerprint", "ExpiryDate", "ApprovalId", "RetentionLabel"):
            self.assertIn(column, fields)
        self.assertEqual(fields["ExpiryDate"], req.card["expiresAt"])
        put, patch, label = self.wf.publisher.graph_log[-3:]
        self.assertEqual(put["method"], "PUT")
        self.assertRegex(put["url"], r"^https://graph\.microsoft\.com/v1\.0/sites/[^/]+/drive/items/[^/]+:/KC-[^/]+\.md:/content$")
        self.assertEqual(patch["method"], "PATCH")
        self.assertTrue(patch["url"].endswith(f"/drive/items/{req.card['itemId']}/listItem/fields"))
        self.assertEqual(patch["body"]["SourceFingerprint"], req.card["fingerprint"])
        self.assertTrue(label["url"].endswith("/retentionLabel"))
        self.assertEqual(label["body"], {"name": fields["RetentionLabel"]})
        self.assert_card_clean(json.dumps(manifest) + json.dumps(self.wf.publisher.graph_log))

    def test_workflow_audit_and_persistence(self):
        """Every transition is audited in a verifiable hash chain and workflow state survives a restart."""
        req = self.publish("ER-2291")
        self.assertTrue(self.wf.audit.verify().ok)
        transitions = [r["data"]["toState"] for r in self.wf.audit.records("workflow_transition")]
        self.assertEqual(transitions, [OWNER_APPROVED, COMPLIANCE_APPROVED, PUBLISHED])
        reloaded = PublishingWorkflow(contract=self.env.contract, directory=self.env.directory, drive=self.env.drive,
                                      publisher=self.wf.publisher, audit=AuditLog(clock=self.clock), clock=self.clock,
                                      state_path=self.root / "workflow.json")
        self.assertEqual(reloaded.get(req.request_id).state, PUBLISHED)
        self.assertEqual(reloaded.get(req.request_id).card["itemId"], req.card["itemId"])

    def test_artifact_card_and_publish_manifest(self):
        """artifacts/arch_c: sample card carries provenance and no PII/original URL; manifest has columns + Graph requests."""
        card = (ARTIFACTS_DIR / "arch_c" / "knowledge_card_SQ-118.md").read_text(encoding="utf-8")
        meta = parse_front_matter(card)
        self.assertEqual(meta["notice"], DERIVED_NOTICE)
        self.assertTrue(meta["approvalId"].startswith("APR-") and meta["complianceApprovalId"].startswith("CMP-"))
        self.assert_card_clean(card)
        manifest = json.loads((ARTIFACTS_DIR / "arch_c" / "publish_manifest_SQ-118.json").read_text(encoding="utf-8"))
        published = manifest["publishedToKnowledgeExchange"]
        for column in ("SourceFingerprint", "ExpiryDate", "ApprovalId", "RetentionLabel"):
            self.assertIn(column, published["listItem"]["fields"])
        self.assertEqual([r["method"] for r in published["graphRequests"]], ["PUT", "PATCH", "PATCH"])
        self.assertFalse(published["allowGuests"])
        self.assertEqual(manifest["workflowRecordInternalNotPublished"]["state"], PUBLISHED)
        self.assert_card_clean(json.dumps(published))
