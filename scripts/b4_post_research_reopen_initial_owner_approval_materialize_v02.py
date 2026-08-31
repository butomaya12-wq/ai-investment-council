from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from aic.council.post_research_reopen_initial_execute_production_v01 import _read_object, _write_exclusive, build_owner_approval


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner-approval-granted", action="store_true")
    parser.add_argument("--owner-approval-id")
    parser.add_argument("--owner-approval-at-utc")
    parser.add_argument("--readiness", type=Path, required=True, help="final v0.4 readiness artifact bound to the final executor HEAD")
    parser.add_argument("--cost-preflight", type=Path, default=Path(".aic-runtime/b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json"))
    parser.add_argument("--output", type=Path, default=Path(".aic-runtime/b4_post_research_reopen_initial_owner_approval_v0_3.json"))
    args = parser.parse_args()
    try:
        if not args.owner_approval_granted or not args.owner_approval_id or not args.owner_approval_at_utc:
            raise ValueError("explicit owner approval flag, id, and timestamp are required")
        readiness = _read_object(args.readiness, "readiness")
        head = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        artifact = build_owner_approval(code_commit_sha=head, readiness_hash=str(readiness.get("artifact_hash")), cost_preflight=_read_object(args.cost_preflight, "cost preflight"), owner_approval_id=args.owner_approval_id, owner_approval_at_utc=args.owner_approval_at_utc)
        _write_exclusive(args.output, artifact)
        print(json.dumps({"artifact_hash": artifact["artifact_hash"], "model_calls_this_step": 0}))
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"B4_POST_RESEARCH_REOPEN_INITIAL_OWNER_APPROVAL_STOP: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
