from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from aic.council.post_research_reopen_initial_execute_production_v01 import CONTEXT_CAPABILITY_PATH, _read_object, load_context_capability
from aic.council.post_research_reopen_initial_full_recovery_v01 import FullInitialRecoveryError, execute_paid_full_recovery
from aic.council.post_research_reopen_initial_paid_failure_recovery_preflight_v01 import file_sha256


COST = Path(".aic-runtime/b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json")
PREFLIGHT = Path(".aic-runtime/b4_post_research_reopen_initial_paid_failure_recovery_preflight_v0_1.json")
HISTORICAL_LEDGER = Path(".aic-runtime/b4_post_research_reopen_initial_paid_dispatch_ledger_v0_1.json")
HISTORICAL_RAW = Path(".aic-runtime/b4_post_research_reopen_initial_paid_raw_responses_v0_1")
ORIGINAL_RESULT = Path(".aic-runtime/b4_post_research_reopen_initial_council_freeze_v0_1.json")
READINESS = Path(".aic-runtime/b4_post_research_reopen_initial_full_recovery_readiness_zero_call_v0_1.json")
RECOVERY_LEDGER = Path(".aic-runtime/b4_post_research_reopen_initial_recovery_paid_dispatch_ledger_v0_1.json")
RECOVERY_RAW = Path(".aic-runtime/b4_post_research_reopen_initial_recovery_paid_raw_responses_v0_1")
RESULT = Path(".aic-runtime/b4_post_research_reopen_initial_full_recovery_council_freeze_v0_1.json")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def _transport():
    from aic.research.runtime import StdlibResponsesTransport, load_openai_api_key

    client, key = StdlibResponsesTransport(), load_openai_api_key()
    return lambda payload: client.post(payload=payload, api_key=key)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-paid-full-recovery", action="store_true")
    parser.add_argument("--owner-approval", type=Path)
    parser.add_argument("--readiness", type=Path, default=READINESS)
    parser.add_argument("--cost-preflight", type=Path, default=COST)
    parser.add_argument("--recovery-preflight", type=Path, default=PREFLIGHT)
    parser.add_argument("--historical-ledger", type=Path, default=HISTORICAL_LEDGER)
    parser.add_argument("--historical-raw-response-dir", type=Path, default=HISTORICAL_RAW)
    parser.add_argument("--recovery-ledger", type=Path, default=RECOVERY_LEDGER)
    parser.add_argument("--recovery-raw-response-dir", type=Path, default=RECOVERY_RAW)
    parser.add_argument("--result", type=Path, default=RESULT)
    args = parser.parse_args()
    try:
        approval = _read_object(args.owner_approval, "full recovery owner approval") if args.owner_approval else None
        historical_raw_count = len(list(args.historical_raw_response_dir.glob("*.json"))) if args.historical_raw_response_dir.exists() else 0
        artifact = execute_paid_full_recovery(
            execute_paid_full_recovery=args.execute_paid_full_recovery,
            branch=_git("branch", "--show-current"),
            code_commit_sha=_git("rev-parse", "HEAD"),
            worktree_clean=not bool(_git("status", "--porcelain")),
            cost_preflight=_read_object(args.cost_preflight, "cost preflight"),
            recovery_readiness=_read_object(args.readiness, "full recovery readiness"),
            recovery_preflight=_read_object(args.recovery_preflight, "recovery preflight"),
            historical_ledger=_read_object(args.historical_ledger, "historical failed paid ledger"),
            historical_ledger_file_sha256=file_sha256(args.historical_ledger),
            historical_raw_response_dir_exists=args.historical_raw_response_dir.exists(),
            historical_raw_response_file_count=historical_raw_count,
            fresh_initial_result_exists=ORIGINAL_RESULT.exists(),
            approval=approval,
            context_capability=load_context_capability(CONTEXT_CAPABILITY_PATH),
            recovery_ledger_path=args.recovery_ledger,
            raw_response_dir=args.recovery_raw_response_dir,
            result_path=args.result,
            transport_factory=_transport,
        )
        print(json.dumps({"status": artifact["status"], "artifact_hash": artifact["artifact_hash"]}))
        return 0
    except (FullInitialRecoveryError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"B4_POST_RESEARCH_REOPEN_INITIAL_FULL_RECOVERY_STOP: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
