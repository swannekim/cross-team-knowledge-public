"""Run the local unittest suite and record outcomes, not live-service evidence.

Writes results/test_results.json and results/TEST_RESULTS.md. Usage from repository
root: python tools/run_tests.py. Tests use fixtures, loopback, generated signing keys
and fake services, including modules named live. No tenant sign-in is performed here.
render(summary, records) can also re-render existing JSON without running tests or
changing its recorded timestamp, duration, descriptions or outcomes.
"""
from __future__ import annotations

import json
import platform
import sys
import time
import unittest
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
APP_ROOT = ROOT / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

RESULTS_DIR = ROOT / "results"
ARCHITECTURES = {"arch_a_connector": "A", "arch_b_broker": "B", "arch_c_publish": "C", "common": "Common",
                 "bench": "Benchmark"}
ORDER = ["A", "B", "C", "Common", "Benchmark", "Other"]
PREFIX = {"A": "A", "B": "B", "C": "C", "Common": "CM", "Benchmark": "BM", "Other": "X"}


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []
        self._started = {}

    def startTest(self, test):
        self._started[test.id()] = time.perf_counter()
        super().startTest(test)

    def _record(self, test, outcome, detail=""):
        test_id = test.id()
        doc = getattr(test, "_testMethodDoc", None) or test.shortDescription() or ""
        elapsed = time.perf_counter() - self._started.get(test_id, time.perf_counter())
        self.records.append({
            "unittestId": test_id,
            "architecture": ARCHITECTURES.get(test_id.split(".")[0], "Other"),
            "test": test_id.split(".")[-1],
            "verifies": " ".join(doc.split()),
            "result": outcome,
            "durationMs": round(elapsed * 1000, 1),
            "detail": detail,
        })

    def addSuccess(self, test):
        super().addSuccess(test)
        self._record(test, "pass")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._record(test, "fail", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self._record(test, "error", self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._record(test, "skip", reason)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._record(test, "expected-failure")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._record(test, "unexpected-success")


def main() -> int:
    suite = unittest.defaultTestLoader.discover(str(APP_ROOT), top_level_dir=str(APP_ROOT))
    runner = unittest.TextTestRunner(resultclass=RecordingResult, verbosity=2, stream=sys.stdout)
    started = time.perf_counter()
    result = runner.run(suite)
    duration = time.perf_counter() - started

    records = sorted(result.records, key=lambda r: (ORDER.index(r["architecture"]), r["unittestId"]))
    counters = Counter()
    for record in records:
        counters[record["architecture"]] += 1
        record["id"] = f"{PREFIX[record['architecture']]}-{counters[record['architecture']]:02d}"
    outcomes = Counter(r["result"] for r in records)
    by_arch = {}
    for record in records:
        entry = by_arch.setdefault(record["architecture"], {"total": 0, "passed": 0, "failed": 0})
        entry["total"] += 1
        entry["passed"] += record["result"] == "pass"
        entry["failed"] += record["result"] in ("fail", "error", "unexpected-success")
    summary = {
        "command": "python3 -m unittest discover -s app -t app -v   (recorded via python3 tools/run_tests.py)",
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "python": platform.python_version(),
        "total": result.testsRun,
        "passed": outcomes["pass"],
        "failed": outcomes["fail"],
        "errors": outcomes["error"],
        "skipped": outcomes["skip"],
        "durationSeconds": round(duration, 2),
        "successful": result.wasSuccessful(),
        "byArchitecture": by_arch,
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "test_results.json").write_text(json.dumps({"summary": summary, "tests": records}, indent=2,
                                                              ensure_ascii=False) + "\n", encoding="utf-8")
    (RESULTS_DIR / "TEST_RESULTS.md").write_text(render(summary, records), encoding="utf-8")
    print(f"\nwrote {RESULTS_DIR / 'TEST_RESULTS.md'} and {RESULTS_DIR / 'test_results.json'}")
    return 0 if result.wasSuccessful() else 1


def render(summary: dict, records: list) -> str:
    """Render local evidence, qualifying legacy descriptions without altering records."""
    names = {"A": "A - derived index via Copilot connector", "B": "B - Knowledge Broker API (PDP)",
             "C": "C - governed knowledge cards", "Common": "Common building blocks", "Benchmark": "Benchmark"}
    scopes = {
        "arch_a_connector.tests.test_arch_a": "Synthetic directory/delta/Graph emulator and local payload checks; "
        "no live Search/Copilot or membership/index/cache propagation measurement.",
        "arch_b_broker.tests.test_arch_b": "HS256 fixtures, loopback HTTP, BM25, mock Purview and example manifests; "
        "no tenant SSO or real Purview decisions.",
        "arch_b_broker.tests.test_live": "Live-adapter code tested with generated RSA keys, fake Graph and local "
        "state; not tenant user authentication or live retrieval.",
        "arch_b_broker.tests.test_live_blob": "Fake Blob SDK/lease failures and local SQLite recovery; "
        "not the separate Azure restart experiment.",
        "arch_c_publish.tests.test_arch_c": "Source-bound baseline approval and simulated SharePoint/native index; "
        "not exact-output live approval or effective user permissions.",
        "common.tests.test_common": "Fixed synthetic redaction, policy, audit and delta cases; "
        "not general leakage resistance or immutable audit.",
        "bench.tests.test_benchmark": "180 fixed synthetic probes; no detected canary leakage is not general "
        "or live-service safety.",
        "live_poc.tests.test_live": "Fake Graph preparation/publication/approval/lifecycle/transport checks "
        "(Other below); no live credentials or user-access checks.",
    }
    descriptions = {
        "test_audience_change_by_group_membership_only": "Local emulator evaluates group removal immediately "
        "with zero item writes; no live propagation latency is measured.",
        "test_suspended_contract_purges_on_next_run": "A local invocation purges simulated items for a suspended "
        "contract; this does not install or test a scheduled live job.",
        "test_artifacts_are_valid_graph_payloads": "Baseline artifacts pass local Graph-shape validation and "
        "defined synthetic leak probes; not service acceptance or general safety.",
        "test_purview_blocks_prompt": "Mock marker-based prompt block returns 403 before retrieval and is audited; "
        "no real Purview decision.",
        "test_purview_blocks_response_without_consuming_coverage": "Mock marker-based response block withholds "
        "content without consuming coverage; no real Purview decision.",
        "test_purview_called_for_prompt_and_response_and_audited": "Allowed baseline requests invoke the local "
        "process_content hook twice, not the Graph Purview service.",
        "test_manifests_valid_and_cross_consistent": "Baseline manifests match their generator and local "
        "shape/cross-reference rules; fictional endpoints/OAuthPluginVault reference are not working SSO.",
        "test_publish_manifest_columns_and_graph_requests": "Simulated publisher builds Graph request objects "
        "and custom columns; no real MIP or retention policy is applied.",
        "test_bob_finds_card_carol_and_dave_cannot": "Simulated native index permits bob and denies carol/dave; "
        "not effective SharePoint or Copilot user denial.",
        "test_expiry_removes_card": "Advancing the local clock and invoking the sweep removes the simulated "
        "card/index entry; not actual-deadline deletion or live search lag.",
        "test_zero_leaks_and_zero_unauthorised_results": "180 fixed synthetic runs detected no unauthorised "
        "results, seeded PII/secrets, original-location or HC canaries; not general or live-service safety.",
    }
    lines = ["# Local test results — not live-service evidence", "",
             "Every PASS below is a local assertion over fixtures, emulators, generated keys, fake services or "
             "loopback HTTP. Names containing `live`, `real`, `Purview` or `SSO` do not establish tenant sign-in, "
             "policy decisions, deployed agents or service permission trimming.", "",
             "Original evidence remains in [offline-baseline](offline-baseline/TEST_RESULTS.md). Actual service "
             "observations are in [A](live/reports/A_connector.md), [B](live/reports/B_broker.md) and [C](live/reports/C_publishing.md).", "",
             "This Markdown can be re-rendered from recorded JSON without a test rerun. Run metrics/outcomes below "
             "are unchanged by rendering; descriptions are qualified for local scope. Blank source descriptions "
             "are not additional evidence.", "",
             f"- Recorded command: `{summary['command']}`",
             f"- Python {summary['python']}, recorded {summary['generatedAt']}, wall time {summary['durationSeconds']} s",
             f"- **{summary['passed']} passed, {summary['failed']} failed, {summary['errors']} errors, "
             f"{summary['skipped']} skipped (total {summary['total']})**", "",
             "| Test module | Evidence boundary for every result in that file |", "|---|---|"]
    for module in sorted({record["unittestId"].rsplit(".", 2)[0] for record in records}):
        scope = scopes.get(module, "Local test only; inspect the test implementation before inferring coverage.")
        lines.append(f"| `{module}` | {scope} |")
    lines += ["", "| Architecture | Tests | Passed | Failed |", "|---|---|---|---|"]
    for arch in ORDER:
        if arch in summary["byArchitecture"]:
            entry = summary["byArchitecture"][arch]
            lines.append(f"| {names.get(arch, arch)} | {entry['total']} | {entry['passed']} | {entry['failed']} |")
    lines += ["", "| Test id | Architecture | Test module | Test | Local assertion (module scope above applies) | Result |",
              "|---|---|---|---|---|---|"]
    for record in records:
        module = record["unittestId"].rsplit(".", 2)[0]
        verifies = descriptions.get(record["test"], record["verifies"]).replace("|", "\\|")
        result = "PASS" if record["result"] == "pass" else record["result"].upper()
        lines.append(f"| {record['id']} | {record['architecture']} | `{module}` | `{record['test']}` | "
                     f"{verifies} | {result} |")
    failures = [r for r in records if r["result"] in ("fail", "error")]
    if failures:
        lines += ["", "## Failures", ""]
        for record in failures:
            lines += [f"### {record['id']} {record['unittestId']}", "", "```", record["detail"].strip(), "```", ""]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
