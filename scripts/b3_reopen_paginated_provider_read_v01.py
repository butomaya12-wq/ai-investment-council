from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from aic.domain.canonical import canonical_sha256
from aic.research.reopen_paginated_read import (
    BLOCKED_STATUS,
    PARTIAL_STATUS,
    ReopenPaginatedReadError,
    execute_paginated_provider_reads,
    load_approved_preflight,
    load_read_authority,
    summarize_result,
    verify_cli_help_still_bound,
)


DEFAULT_AUTHORITY = Path("config/event/b3_reopen_paginated_read_authority_v1.json")
DEFAULT_PREFLIGHT = Path(".aic-runtime/b3_reopen_pagination_zero_call_v0_1.json")
DEFAULT_AUTHORIZATION = Path(".aic-runtime/b3_reopen_paginated_provider_read_authorization_v0_1.json")
DEFAULT_RECEIPTS = Path(".aic-runtime/b3_reopen_paginated_provider_read_receipts_v0_1.jsonl")
DEFAULT_RESULT = Path(".aic-runtime/b3_reopen_paginated_provider_read_result_v0_1.json")
EXPECTED_BRANCH = "hackathon/alpaca-2026"
EXPECTED_REMOTE_FRAGMENT = "butomaya12-wq/ai-investment-council"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Execute the owner-authorized B3 reopen Alpaca News pagination read. "
            "Bounded, read-only, CLI paper profile, no models and no trading writes."
        )
    )
    parser.add_argument("--authority", type=Path, default=DEFAULT_AUTHORITY)
    parser.add_argument("--preflight", type=Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--authorization-output", type=Path, default=DEFAULT_AUTHORIZATION)
    parser.add_argument("--receipts-output", type=Path, default=DEFAULT_RECEIPTS)
    parser.add_argument("--result-output", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--execute-provider-read", action="store_true")
    parser.add_argument("--approve-preflight-hash")
    parser.add_argument("--owner-approval-id")
    parser.add_argument("--approve-max-provider-reads", type=int)
    return parser.parse_args()


def _git_output(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise ReopenPaginatedReadError("unable to inspect git repository state")
    return completed.stdout.decode("utf-8").strip()


def _pre_dispatch_checks(args: argparse.Namespace):
    code_commit_sha = _git_output("rev-parse", "HEAD")
    if _git_output("status", "--porcelain"):
        raise ReopenPaginatedReadError("worktree must be clean before provider-read execution")
    branch = _git_output("branch", "--show-current")
    if branch != EXPECTED_BRANCH:
        raise ReopenPaginatedReadError("provider-read execution requires hackathon/alpaca-2026 branch")
    remote = _git_output("remote", "get-url", "origin")
    if EXPECTED_REMOTE_FRAGMENT not in remote:
        raise ReopenPaginatedReadError("provider-read execution repository remote drift")

    authority = load_read_authority(args.authority)
    preflight = load_approved_preflight(args.preflight, authority=authority)
    cli = verify_cli_help_still_bound(preflight)

    for path in (args.authorization_output, args.receipts_output, args.result_output):
        if path.exists():
            raise ReopenPaginatedReadError(f"provider-read evidence already exists: {path}")

    authority_hash = canonical_sha256(authority.model_dump(mode="json", exclude_none=False))
    dry = {
        "status": "B3_REOPEN_PAGINATED_PROVIDER_READ_PRE_DISPATCH_PASS",
        "code_commit_sha": code_commit_sha,
        "branch": branch,
        "authority_hash": authority_hash,
        "owner_approval_id": authority.owner_approval_id,
        "source_zero_call_preflight_hash": preflight["artifact_hash"],
        "approved_candidate_ids": list(authority.approved_candidate_ids),
        "approved_auth_mode": authority.approved_auth_mode,
        "approved_max_pages_per_candidate": authority.approved_max_pages_per_candidate,
        "approved_provider_dispatch_attempts_max": authority.approved_provider_dispatch_attempts_max,
        "authorization_consumption_rule": authority.authorization_consumption_rule,
        "automatic_retries": 0,
        "rerun_authorized": False,
        "alpaca_cli_path": cli["alpaca_cli_path"],
        "alpaca_news_help_sha256": cli["alpaca_news_help_sha256"],
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    return code_commit_sha, authority, preflight, dry


def main() -> int:
    args = _args()
    try:
        code_commit_sha, authority, preflight, dry = _pre_dispatch_checks(args)
        print(json.dumps(dry, indent=2, ensure_ascii=False))

        if not args.execute_provider_read:
            print("EXECUTION=NOT_REQUESTED")
            return 0

        if args.approve_preflight_hash != authority.source_zero_call_preflight_hash:
            raise ReopenPaginatedReadError("explicit approved preflight hash mismatch")
        if args.owner_approval_id != authority.owner_approval_id:
            raise ReopenPaginatedReadError("explicit owner approval id mismatch")
        if args.approve_max_provider_reads != authority.approved_provider_dispatch_attempts_max:
            raise ReopenPaginatedReadError("explicit provider-read ceiling mismatch")

        print("=== AUTHORIZED B3 REOPEN PAGINATED ALPACA NEWS READ ===")
        result = execute_paginated_provider_reads(
            authority=authority,
            preflight=preflight,
            code_commit_sha=code_commit_sha,
            authorization_path=args.authorization_output,
            receipts_path=args.receipts_output,
            result_path=args.result_output,
        )
        print(json.dumps(summarize_result(result), indent=2, ensure_ascii=False))
        print(f"RESULT_PATH={args.result_output}")
        print(f"AUTHORIZATION_PATH={args.authorization_output}")
        print(f"RECEIPTS_PATH={args.receipts_output}")
        if result["status"] == BLOCKED_STATUS:
            return 3
        if result["status"] == PARTIAL_STATUS:
            return 4
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
