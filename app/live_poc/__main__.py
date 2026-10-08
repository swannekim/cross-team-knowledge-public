"""python -m live_poc: real Graph snapshot prepare/apply and manual lifecycle cleanup."""
import argparse
import json
import os
import sys
from urllib.parse import urlsplit

from arch_a_connector.graph_client import GraphError
from common.clock import SystemClock

from .graph import GraphTransport, assert_pipeline_token
from .pipeline import (
    Ledger, PreparationError, apply, atomic_json, cleanup, contract_for, ledger_lock, prepare, read_json,
)
from .sources import SourceReader


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare", help="Download and verify fixtures; write unsigned review payloads")
    prep.add_argument("--state", required=True)
    prep.add_argument("--out", required=True)
    prep.add_argument("--citation-base", default=os.environ.get("KX_CITATION_BASE"))
    prep.add_argument("--ttl-days", type=int, default=1)
    prep.add_argument("--broker-contract", help="Optionally bind the broker snapshot manifest to this contract")
    prep.add_argument("--runtime-out", default="deployment/runtime",
                      help="Broker Docker runtime directory (written when --broker-contract is supplied)")
    for name in ("apply", "sweep", "reconcile", "withdraw"):
        command = commands.add_parser(name)
        command.add_argument("--state", required=True)
        command.add_argument("--ledger", required=True)
        if name == "apply":
            command.add_argument("--plan", required=True)
            command.add_argument("--approval", required=True)
        if name == "withdraw":
            targets = command.add_mutually_exclusive_group(required=True)
            targets.add_argument("--source-id")
            targets.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)
    graph = None
    try:
        state = read_json(args.state)
        token = os.environ.get("KX_GRAPH_TOKEN")
        assert_pipeline_token(token, state)
        graph = GraphTransport(token,
                               sharepoint_host=urlsplit(state["sites"]["Source"]["url"]).hostname)
        now = SystemClock().now()
        if args.command == "prepare":
            if not args.citation_base:
                raise PreparationError("--citation-base or KX_CITATION_BASE is required")
            options = {}
            if os.environ.get("KX_REF_KEY"):
                options["ref_key"] = os.environ["KX_REF_KEY"].encode("utf-8")
            result = prepare(graph, state, args.out, now, citation_base=args.citation_base,
                             ttl_days=args.ttl_days, broker_contract=args.broker_contract,
                             runtime_out=args.runtime_out, **options)
        else:
            with ledger_lock(args.ledger):
                ledger = Ledger(args.ledger, state)
                if args.command == "apply":
                    result = apply(graph, state, args.plan, args.approval, ledger, now)
                else:
                    current = None
                    if args.command == "reconcile":
                        current = {d.source_id: d.fingerprint for d in SourceReader(
                            graph, state, contract_for(state, now)).snapshot()}
                    result = cleanup(graph, state, ledger, now, current=current,
                                     source_id=getattr(args, "source_id", None),
                                     all_sources=getattr(args, "all", False))
        print(json.dumps({"ok": True, **result}, sort_keys=True))
        return 0
    except GraphError as error:
        print(json.dumps({"ok": False, "error": "Graph request failed", "status": error.status}), file=sys.stderr)
        return 1
    except PreparationError as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1
    except json.JSONDecodeError:
        print(json.dumps({"ok": False, "error": "Invalid JSON in local configuration or plan"}), file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(json.dumps({"ok": False, "error": "Required local input file or directory was not found"}),
              file=sys.stderr)
        return 1
    except PermissionError:
        print(json.dumps({"ok": False, "error": "Access to a local input, output, or ledger was denied"}),
              file=sys.stderr)
        return 1
    except (ValueError, KeyError, TypeError, OSError):
        # Do not print full exception values: malformed input can contain private source URLs or tokens.
        print(json.dumps({"ok": False, "error": "Validation, approval, freshness or local state check failed"}),
              file=sys.stderr)
        return 1
    finally:
        if graph is not None and hasattr(args, "ledger"):
            atomic_json(str(args.ledger) + ".evidence.json",
                        {"counts": dict(graph.counts), "requests": graph.evidence})


if __name__ == "__main__":
    raise SystemExit(main())
