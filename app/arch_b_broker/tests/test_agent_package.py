"""Offline package structure and negative-input checks; no real SSO or Copilot claim."""
from __future__ import annotations

import base64
import contextlib
import io
import json
import struct
import uuid
import zlib
from zipfile import ZipFile

from common.testing import OfflineTestCase
from deployment.scripts import build_agent_package as builder


class AgentPackageTests(OfflineTestCase):
    def setUp(self):
        self.root = self.scratch("agent-package")
        # An ephemeral test value, never a real service registration or retained artifact.
        self.reference = str(uuid.uuid4())

    def test_missing_and_placeholder_auth_references_fail_before_writing(self):
        values = (None, "", "   ", "${{BROKER_SSO_REFERENCE_ID}}", "<auth-config-id>", "auth-config-id",
                  "your-reference-id", "REPLACE_ME", "placeholder", "example-registration", "dummy-value",
                  "00000000-0000-0000-0000-000000000000", builder.APP_ID, builder.TENANT_ID,
                  builder.BROKER_CLIENT_ID, "https://not-an-id.invalid", "value with spaces")
        for index, value in enumerate(values):
            output = self.root / str(index)
            with self.subTest(value=value), self.assertRaises(ValueError):
                builder.build_package(output, auth_reference_id=value)
            self.assertFalse(output.exists())

    def test_cli_requires_explicit_registration_or_preview(self):
        for args in ([], ["--out", str(self.root / "missing")],
                     ["--preview", "--auth-reference-id", self.reference, "--out", str(self.root / "both")]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                builder.main(args)
            self.assertEqual(exc.exception.code, 2)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_service_base64_auth_references_are_preserved_in_package(self):
        service_shape = base64.b64encode(f"{builder.TENANT_ID}##{self.reference}".encode()).decode()
        self.assertTrue(service_shape.endswith("="))
        special_characters = base64.b64encode(b"\xfb\xff" * 8).decode()
        self.assertIn("+", special_characters)
        self.assertIn("/", special_characters)
        for index, value in enumerate((service_shape, special_characters)):
            with self.subTest(value=value):
                self.assertEqual(builder.validate_auth_reference(value), value)
                archive = builder.build_package(self.root / f"base64-{index}", auth_reference_id=value)
                with ZipFile(archive) as package:
                    auth = json.loads(package.read("ai-plugin.json"))["runtimes"][0]["auth"]
                    self.assertEqual(auth, {"type": "OAuthPluginVault", "reference_id": value})

    def test_base64_support_rejects_bad_padding_whitespace_and_placeholders(self):
        for value in ("YWJjZGVmZw===", "YWJj=ZGVmZw==", "YWJjZGVmZw==\n",
                      "YWJjZGVmZw== ", "YWJj:ZGVmZw==", "placeholder=", "your-reference-id=="):
            with self.subTest(value=value), self.assertRaises(ValueError):
                builder.validate_auth_reference(value)

    def test_preview_is_one_marked_noninstallable_document_and_no_reference(self):
        output = self.root / "preview"
        path = builder.build_package(output, preview=True)
        self.assertEqual([p.name for p in output.iterdir()], ["preview.json"])
        preview = json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(preview["installable"])
        self.assertIn("PREVIEW ONLY", preview["status"])
        self.assertEqual(preview["registration"]["tenantId"], builder.TENANT_ID)
        auth = preview["documents"]["ai-plugin.json"]["runtimes"][0]["auth"]
        self.assertEqual(auth, {"type": "OAuthPluginVault"})
        with self.assertRaises(ValueError):
            builder.build_package(self.root / "ambiguous", preview=True, auth_reference_id=self.reference)

    def test_archive_is_root_level_six_files_and_uses_supplied_vault_reference(self):
        output = self.root / "package"
        path = builder.build_package(output, auth_reference_id=self.reference)
        names = {"manifest.json", "declarativeAgent.json", "ai-plugin.json", "openapi.json", "color.png", "outline.png"}
        with ZipFile(path) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(set(archive.namelist()), names)
            for name in names:
                self.assertEqual(archive.read(name), (output / name).read_bytes())
            documents = {name: json.loads(archive.read(name)) for name in names if name.endswith(".json")}
        manifest = documents["manifest.json"]
        self.assertEqual(manifest["manifestVersion"], "1.21")
        self.assertEqual(manifest["id"], builder.APP_ID)
        self.assertEqual(manifest["copilotAgents"]["declarativeAgents"][0]["file"], "declarativeAgent.json")
        agent = documents["declarativeAgent.json"]
        self.assertEqual(agent["version"], "v1.8")
        self.assertEqual(agent["actions"], [{"id": "knowledgeBroker", "file": "ai-plugin.json"}])
        self.assertNotIn("capabilities", agent)
        plugin = documents["ai-plugin.json"]
        self.assertEqual(plugin["schema_version"], "v2.4")
        runtime = plugin["runtimes"][0]
        self.assertEqual(runtime["auth"], {"type": "OAuthPluginVault", "reference_id": self.reference})
        self.assertEqual(runtime["spec"], {"url": "openapi.json"})
        operation = documents["openapi.json"]["paths"]["/ask"]["post"]["operationId"]
        self.assertEqual(operation, "askKnowledge")
        self.assertEqual(runtime["run_for_functions"], [operation])
        self.assertEqual([item["name"] for item in plugin["functions"]], [operation])

    def test_output_never_overwrites_existing_artifacts(self):
        output = self.root / "existing"
        output.mkdir()
        marker = output / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        for preview in (False, True):
            with self.subTest(preview=preview), self.assertRaises(ValueError):
                builder.build_package(output, preview=preview, auth_reference_id=None if preview else self.reference)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertEqual(list(output.iterdir()), [marker])

    def test_origin_scope_and_request_are_pinned_without_app_only_flow(self):
        api = builder.package_documents(self.reference)["openapi.json"]
        self.assertEqual(api["servers"], [{"url": builder.ORIGIN}])
        self.assertEqual(set(api["paths"]), {"/ask"})
        scheme = api["components"]["securitySchemes"]["entraDelegated"]
        self.assertEqual(set(scheme["flows"]), {"authorizationCode"})
        flow = scheme["flows"]["authorizationCode"]
        self.assertEqual(flow["tokenUrl"],
                         f"https://login.microsoftonline.com/{builder.TENANT_ID}/oauth2/v2.0/token")
        self.assertEqual(list(flow["scopes"]), [builder.SCOPE])
        self.assertEqual(api["security"], [{"entraDelegated": [builder.SCOPE]}])
        body = api["paths"]["/ask"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        self.assertEqual(set(body["properties"]), {"question", "purpose"})
        self.assertEqual(set(body["required"]), {"question", "purpose"})
        self.assertFalse(body["additionalProperties"])
        self.assertEqual(body["properties"]["purpose"]["enum"], [builder.PURPOSE])
        self.assertEqual(body["properties"]["question"]["maxLength"], 1000)

    def test_response_schema_preserves_citations_limits_errors_and_purview_notice(self):
        documents = builder.package_documents(self.reference)
        api = documents["openapi.json"]
        answer = api["components"]["schemas"]["AskResponse"]
        self.assertTrue({"requestId", "citations", "answer", "policy", "service"}.issubset(answer["required"]))
        citation = answer["properties"]["citations"]["items"]
        self.assertTrue({"ref", "title", "sourceTeam", "excerpt", "accessRequestUrl"}.issubset(citation["required"]))
        self.assertIn("withheld", answer["properties"]["policy"]["required"])
        purview = answer["properties"]["service"]["properties"]["purview"]
        self.assertEqual(purview["properties"]["evaluation"]["enum"], ["not evaluated"])
        self.assertEqual(purview["properties"]["complianceClaim"]["enum"], [False])
        responses = api["paths"]["/ask"]["post"]["responses"]
        self.assertTrue({"200", "400", "401", "403", "429", "503"}.issubset(responses))
        self.assertIn("Retry-After", responses["429"]["headers"])
        self.assertNotIn("information_protection_label", json.dumps(documents))

    def test_human_strings_and_links_are_truthful_and_within_schema_limits(self):
        documents = builder.package_documents(self.reference)
        manifest, agent, plugin = (documents[name] for name in ("manifest.json", "declarativeAgent.json", "ai-plugin.json"))
        self.assertLessEqual(len(manifest["name"]["short"]), 30)
        self.assertLessEqual(len(manifest["description"]["short"]), 80)
        self.assertLessEqual(len(agent["instructions"]), 8000)
        self.assertLessEqual(len(agent["disclaimer"]["text"]), 500)
        self.assertLessEqual(len(plugin["name_for_human"]), 20)
        self.assertLessEqual(len(plugin["description_for_human"]), 100)
        self.assertEqual(manifest["developer"]["privacyUrl"], builder.ORIGIN + "/demo/privacy")
        self.assertEqual(manifest["developer"]["termsOfUseUrl"], builder.ORIGIN + "/demo/terms")
        self.assertEqual(plugin["privacy_policy_url"], manifest["developer"]["privacyUrl"])
        self.assertEqual(plugin["legal_info_url"], manifest["developer"]["termsOfUseUrl"])
        for phrase in ("insufficient", "general knowledge", "untrusted", "not evaluated", "original",
                       "PM value", "200 response", "withheld", "manual cleanup"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, agent["instructions"])

    def test_icons_are_valid_pngs_with_expected_size_and_transparent_white_outline(self):
        for size, outline in ((192, False), (32, True)):
            with self.subTest(size=size):
                png = builder._png(size, outline)
                self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
                offset, compressed = 8, bytearray()
                while offset < len(png):
                    length = struct.unpack(">I", png[offset:offset + 4])[0]
                    kind, data = png[offset + 4:offset + 8], png[offset + 8:offset + 8 + length]
                    crc = struct.unpack(">I", png[offset + 8 + length:offset + 12 + length])[0]
                    self.assertEqual(crc, zlib.crc32(kind + data))
                    if kind == b"IHDR":
                        self.assertEqual(struct.unpack(">IIBBBBB", data), (size, size, 8, 6, 0, 0, 0))
                    elif kind == b"IDAT":
                        compressed.extend(data)
                    offset += 12 + length
                rows = zlib.decompress(compressed)
                self.assertEqual(len(rows), size * (1 + size * 4))
                colors = set()
                for y in range(size):
                    row = rows[y * (1 + size * 4):(y + 1) * (1 + size * 4)]
                    self.assertEqual(row[0], 0)
                    colors.update(tuple(row[x:x + 4]) for x in range(1, len(row), 4))
                self.assertEqual(colors, {(255, 255, 255, 255), (0, 0, 0, 0) if outline else (22, 78, 99, 255)})


class ConnectorPackageTests(OfflineTestCase):
    def setUp(self):
        self.root = self.scratch("connector-agent-package")

    def test_capability_is_one_connection_with_contract_filter_and_no_fallback(self):
        documents = builder.package_documents(architecture="connector")
        self.assertEqual(set(documents), {"manifest.json", "declarativeAgent.json"})
        agent = documents["declarativeAgent.json"]
        self.assertEqual(agent["capabilities"], [{"name": "GraphConnectors", "connections": [{
            "connection_id": "ExampleDerived",
            "additional_search_terms": "contractId:KX-Synthetic-20261007",
        }]}])
        self.assertEqual(agent["behavior_overrides"],
                         {"special_instructions": {"discourage_model_knowledge": True}})
        self.assertNotIn("actions", agent)
        self.assertNotIn("user_overrides", agent)
        self.assertNotIn("webApplicationInfo", documents["manifest.json"])
        self.assertNotIn("OAuthPluginVault", json.dumps(documents))
        self.assertNotIn("OneDriveAndSharePoint", json.dumps(documents))
        self.assertNotIn('"WebSearch"', json.dumps(documents))

    def test_connector_identity_and_notices_are_distinct_and_schema_sized(self):
        documents = builder.package_documents(architecture="connector")
        manifest, agent = documents["manifest.json"], documents["declarativeAgent.json"]
        self.assertNotEqual(manifest["id"], builder.APP_ID)
        self.assertEqual(manifest["id"], builder.CONNECTOR_APP_ID)
        self.assertEqual(str(uuid.UUID(manifest["id"])), manifest["id"])
        self.assertEqual(manifest["name"]["full"], "KX synthetic connector knowledge")
        self.assertEqual(agent["name"], manifest["name"]["full"])
        self.assertLessEqual(len(manifest["name"]["short"]), 30)
        self.assertLessEqual(len(manifest["description"]["short"]), 80)
        self.assertLessEqual(len(agent["instructions"]), 8000)
        self.assertLessEqual(len(agent["disclaimer"]["text"]), 500)
        self.assertEqual(manifest["developer"], builder.package_documents()["manifest.json"]["developer"])
        for phrase in ("insufficient", "general knowledge", "untrusted", "not evaluated",
                       "correct answer alone", "provenance is unverified", "architecture C",
                       "original", "manual", "native", "connector"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, agent["instructions"])

    def test_connector_zip_has_four_files_and_needs_no_oauth_config(self):
        output = self.root / "package"
        archive = builder.build_package(output, architecture="connector")
        self.assertEqual(archive.name, builder.CONNECTOR_ZIP_NAME)
        with ZipFile(archive) as package:
            self.assertIsNone(package.testzip())
            self.assertEqual(set(package.namelist()),
                             {"manifest.json", "declarativeAgent.json", "color.png", "outline.png"})
            manifest = json.loads(package.read("manifest.json"))
            self.assertEqual(manifest["copilotAgents"]["declarativeAgents"],
                             [{"id": "kxSyntheticConnector", "file": "declarativeAgent.json"}])
            for name in package.namelist():
                self.assertEqual(package.read(name), (output / name).read_bytes())

    def test_connector_preview_is_noninstallable_and_discloses_native_access(self):
        output = self.root / "preview"
        path = builder.build_package(output, architecture="connector", preview=True)
        self.assertEqual([p.name for p in output.iterdir()], ["preview.json"])
        preview = json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(preview["installable"])
        self.assertIn("not approved or uploaded", preview["status"])
        self.assertEqual(preview["registration"]["authentication"], "native-graph-connectors-user-access")
        self.assertEqual(preview["registration"]["connectionId"], builder.CONNECTOR_ID)
        self.assertNotIn("brokerClientId", preview["registration"])
        self.assertNotIn("reference_id", json.dumps(preview))

    def test_connector_instructions_do_not_preseed_expected_numeric_answers(self):
        agent = builder.package_documents(architecture="connector")["declarativeAgent.json"]
        text = agent["instructions"] + json.dumps(agent["conversation_starters"])
        for answer in ("15 wafers", "0.12", "<10", "3%"):
            with self.subTest(answer=answer):
                self.assertNotIn(answer, text)

    def test_connector_rejects_vault_config_and_unknown_architectures_before_writes(self):
        for index, kwargs in enumerate((
            {"architecture": "connector", "auth_reference_id": str(uuid.uuid4())},
            {"architecture": "connector", "auth_reference_id": "placeholder"},
            {"architecture": "fourth"},
        )):
            output = self.root / str(index)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                builder.build_package(output, **kwargs)
            self.assertFalse(output.exists())
        with self.assertRaises(ValueError):
            builder.package_documents(str(uuid.uuid4()), architecture="connector")

    def test_connector_cli_builds_locally_without_registration(self):
        output = self.root / "cli"
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            builder.main(["--architecture", "connector", "--out", str(output)])
        self.assertTrue((output / builder.CONNECTOR_ZIP_NAME).is_file())
        self.assertIn("LOCAL PACKAGE ONLY", stdout.getvalue())
        self.assertIn("Operator approval", stdout.getvalue())
