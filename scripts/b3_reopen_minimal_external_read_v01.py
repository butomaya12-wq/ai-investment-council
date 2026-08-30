from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from aic.research.reopen_minimal_external_read import (
    EXPECTED_OWNER_APPROVAL_ID,
    EXPECTED_PREFLIGHT_HASH,
    build_authorization_artifact,
    execute_provider_reads,
    load_approved_preflight,
    verify_cli_help_still_bound,
    write_authorization_artifact_exclusive,
)


EXPECTED_BRANCH = "hackathon/alpaca-2026"


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Owner-approval-gated bounded Alpaca read capture for B3 research reopen."
    )
    parser.add_argument(
        "--preflight",
        default=".aic-runtime/b3_reopen_minimal_external_read_preflight_zero_call_v0_1.json",
    )
    parser.add_argument(
        "--authorization-output",
        default=".aic-runtime/b3_reopen_minimal_external_read_authorization_v0_1.json",
    )
    parser.add_argument(
        "--receipts-output",
        default=".aic-runtime/b3_reopen_minimal_external_read_receipts_v0_1.jsonl",
    )
    parser.add_argument(
        "--result-output",
        default=".aic-runtime/b3_reopen_minimal_external_read_result_v0_1.json",
    )
    parser.add_argument(
        "--raw-dir",
        default=".aic-runtime/b3_reopen_minimal_external_read_raw_v0_1",
    )
    parser.add_argument("--execute-provider-reads", action="store_true")
    parser.add_argument("--owner-approval-id")
    parser.add_argument("--approve-preflight-hash")
    return parser


def main() -> int:
    args = _parser().parse_args()

    branch = _git("branch", "--show-current")
    if branch != EXPECTED_BRANCH:
        print(f"STOP: expected branch {EXPECTED_BRANCH}, got {branch}", file=sys.stderr)
        return 2
    if _git("status", "--porcelain"):
        print("STOP: worktree is not clean", file=sys.stderr)
        return 2

    code_sha = _git("rev-parse", "HEAD")
    preflight = load_approved_preflight(args.preflight)
    verify_cli_help_still_bound(preflight)

    if not args.execute_provider_reads:
        print("PROVIDER_READS_EXECUTED=0")
        print("OWNER_APPROVAL_REQUIRED=YES")
        print(f"EXPECTED_OWNER_APPROVAL_ID={EXPECTED_OWNER_APPROVAL_ID}")
        print(f"EXPECTED_PREFLIGHT_HASH={EXPECTED_PREFLIGHT_HASH}")
        print("STOP: --execute-provider-reads was not supplied")
        return 2

    if args.owner_approval_id != EXPECTED_OWNER_APPROVAL_ID:
        print("STOP: owner approval id mismatch", file=sys.stderr)
        return 2
    if args.approve_preflight_hash != EXPECTED_PREFLIGHT_HASH:
        print("STOP: approved preflight hash mismatch", file=sys.stderr)
        return 2

    for value in (
        args.authorization_output,
        args.receipts_output,
        args.result_output,
        args.raw_dir,
    ):
        if Path(value).exists():
            print(f"STOP: output already exists: {value}", file=sys.stderr)
            return 2

    authorization = build_authorization_artifact(
        code_commit_sha=code_sha,
        preflight=preflight,
        owner_approval_id=args.owner_approval_id,
        approved_preflight_hash=args.approve_preflight_hash,
    )
    write_authorization_artifact_exclusive(args.authorization_output, authorization)

    print("=== AUTHORIZATION FROZEN ===")
    print(json.dumps(authorization, ensure_ascii=False, sort_keys=True, indent=2))
    print("=== BEGINNING APPROVED READ-ONLY ZONE ===")

    result = execute_provider_reads(
        code_commit_sha=code_sha,
        preflight=preflight,
        authorization=authorization,
        receipt_path=args.receipts_output,
        result_path=args.result_output,
        raw_dir=args.raw_dir,
    )

    print("=== READ-ONLY ZONE FINISHED ===")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    print(f"RESULT_PATH={args.result_output}")
    print(f"AUTHORIZATION_PATH={args.authorization_output}")
    print(f"RECEIPTS_PATH={args.receipts_output}")
    print(f"RAW_DIR={args.raw_dir}")
    print(f"PROVIDER_DISPATCH_ATTEMPTS={result['provider_dispatch_attempts']}")
    print(f"PROVIDER_READS={result['provider_reads']}")
    print(f"AUTHORIZATION_CONSUMED={str(result['authorization_consumed']).lower()}")
    print("MODEL_CALLS=0")
    print("BROKER_WRITES=0")
    print("ALPACA_ORDERS=0")
    print("LIVE_MONEY=PROHIBITED")

    if result["status"].endswith("_COMPLETE"):
        return 0
    if result["status"].endswith("_PARTIAL"):
        return 3
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
