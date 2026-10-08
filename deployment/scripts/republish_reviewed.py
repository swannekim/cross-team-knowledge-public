"""Create a new lifecycle approval for byte-identical, previously approved outputs."""
from __future__ import annotations

import argparse
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from live_poc.pipeline import atomic_json, digest, read_json, validated_plan


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--user-authorized-lifecycle", action="store_true")
    args = parser.parse_args()
    if not args.user_authorized_lifecycle:
        raise ValueError("Explicit authorization for withdrawal and byte-identical re-publication is required")
    state, now = read_json(args.state), datetime.now(timezone.utc)
    manifest, approval, contents = validated_plan(state, args.plan, args.plan / "approval.json", now)
    if args.out.exists():
        raise ValueError("Do not overwrite an existing lifecycle approval")
    shutil.copytree(args.plan, args.out)
    old_id = manifest["planId"]
    manifest["planId"] = str(uuid.uuid4())
    manifest["lifecycleParentPlanId"] = old_id
    manifest["lifecycleReason"] = "User-authorized withdrawal and byte-identical re-publication"
    atomic_json(args.out / "manifest.json", manifest)
    approval["planSha256"] = digest((args.out / "manifest.json").read_bytes())
    approval["actor"] = "user-approved-synthetic-lifecycle-test"
    approval["approvedAt"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    atomic_json(args.out / "approval.json", approval)
    _, _, after = validated_plan(state, args.out, args.out / "approval.json", now)
    if contents != after:
        raise ValueError("Lifecycle approval changed publication bytes")
    print(json.dumps({"outputsUnchanged": len(contents), "planSha256": approval["planSha256"],
                      "expiresAt": manifest["outputs"][0]["expiresAt"]}))


if __name__ == "__main__":
    main()
