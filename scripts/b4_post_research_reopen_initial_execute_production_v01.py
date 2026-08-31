from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from aic.council.post_research_reopen_initial_execute_production_v01 import (
    CONTEXT_CAPABILITY_PATH, PostResearchInitialExecutionError, _read_object,
    execute_paid_initial, load_context_capability,
)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def _transport():
    from aic.research.runtime import StdlibResponsesTransport, load_openai_api_key
    client, key = StdlibResponsesTransport(), load_openai_api_key()
    return lambda payload: client.post(payload=payload, api_key=key)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-paid-initial", action="store_true")
    parser.add_argument("--owner-approval", type=Path)
    parser.add_argument("--readiness", type=Path, default=Path(".aic-runtime/b4_post_research_reopen_initial_production_dispatch_zero_call_preflight_v0_4.json"))
    parser.add_argument("--cost-preflight", type=Path, default=Path(".aic-runtime/b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json"))
    parser.add_argument("--ledger", type=Path, default=Path(".aic-runtime/b4_post_research_reopen_initial_paid_dispatch_ledger_v0_1.json"))
    parser.add_argument("--raw-response-dir", type=Path, default=Path(".aic-runtime/b4_post_research_reopen_initial_paid_raw_responses_v0_1"))
    parser.add_argument("--result", type=Path, default=Path(".aic-runtime/b4_post_research_reopen_initial_council_freeze_v0_1.json"))
    args = parser.parse_args()
    try:
        approval = _read_object(args.owner_approval, "owner approval") if args.owner_approval else None
        artifact = execute_paid_initial(execute_paid_initial=args.execute_paid_initial, branch=_git("branch", "--show-current"), code_commit_sha=_git("rev-parse", "HEAD"), worktree_clean=not bool(_git("status", "--porcelain")), cost_preflight=_read_object(args.cost_preflight, "cost preflight"), readiness=_read_object(args.readiness, "readiness"), approval=approval, context_capability=load_context_capability(CONTEXT_CAPABILITY_PATH), ledger_path=args.ledger, raw_response_dir=args.raw_response_dir, result_path=args.result, transport_factory=_transport)
        print(json.dumps({"status": artifact["status"], "artifact_hash": artifact["artifact_hash"]}))
        return 0
    except (PostResearchInitialExecutionError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"B4_POST_RESEARCH_REOPEN_INITIAL_PAID_EXECUTION_STOP: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
