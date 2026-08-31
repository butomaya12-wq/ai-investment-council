from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from aic.domain.canonical import canonical_sha256
from aic.research.reopen_judge_residual_external_read_continuation_runtime_v01 import (
    BLOCKED_STATUS,
    EXPECTED_CONTINUATION_MANIFEST_HASH,
    EXPECTED_CONTINUATION_PREFLIGHT_HASH,
    EXPECTED_ORIGINAL_PREFLIGHT_HASH,
    EXPECTED_ORIGINAL_RESULT_HASH,
    EXPECTED_REOPEN_CUTOFF_UTC,
    MAX_DISPATCH_ATTEMPTS,
    RESULT_VERSION,
    ResidualExternalReadContinuationRuntimeError,
    build_authorization,
    build_dry,
    execute_once,
    verify_continuation_preflight,
    verify_dry,
    verify_original_preflight,
    verify_original_result,
)


DEFAULT_CONTINUATION_PREFLIGHT = Path(
    ".aic-runtime/b3_research_reopen_residual_external_read_continuation_preflight_zero_call_v0_1.json"
)
DEFAULT_ORIGINAL_PREFLIGHT = Path(
    ".aic-runtime/b3_research_reopen_residual_external_read_preflight_zero_call_v0_1.json"
)
DEFAULT_ORIGINAL_RESULT = Path(
    ".aic-runtime/b3_research_reopen_residual_external_read_result_v0_1.json"
)
DEFAULT_DRY = Path(
    ".aic-runtime/b3_research_reopen_residual_external_read_continuation_runner_dry_v0_1.json"
)
DEFAULT_AUTH = Path(
    ".aic-runtime/b3_research_reopen_residual_external_read_continuation_authorization_v0_1.json"
)
DEFAULT_JOURNAL = Path(
    ".aic-runtime/b3_research_reopen_residual_external_read_continuation_receipts_v0_1.jsonl"
)
DEFAULT_RESULT = Path(
    ".aic-runtime/b3_research_reopen_residual_external_read_continuation_result_v0_1.json"
)


def _git(*args: str) -> str:
    completed = subprocess.run(["git", *args], check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def _read(path: Path, *, label: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResidualExternalReadContinuationRuntimeError(f"unable to read {label}") from exc
    if not isinstance(payload, dict):
        raise ResidualExternalReadContinuationRuntimeError(f"{label} root must be object")
    return payload


def _write_exclusive(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb", closefd=True) as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _dispatch_count(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("event_type") == "PROVIDER_DISPATCH_ATTEMPT":
            count += 1
    return count


def _blocked(*, preflight_hash: str, auth_hash: str | None, reason: str, dispatch_count: int) -> dict:
    artifact = {
        "artifact_version": RESULT_VERSION,
        "status": BLOCKED_STATUS,
        "source_continuation_preflight_hash": preflight_hash,
        "source_original_preflight_hash": EXPECTED_ORIGINAL_PREFLIGHT_HASH,
        "source_original_result_hash": EXPECTED_ORIGINAL_RESULT_HASH,
        "authorization_artifact_hash": auth_hash,
        "continuation_request_manifest_hash": EXPECTED_CONTINUATION_MANIFEST_HASH,
        "reopen_cutoff_utc": EXPECTED_REOPEN_CUTOFF_UTC,
        "provider_dispatch_attempts": dispatch_count,
        "provider_dispatch_attempts_max": MAX_DISPATCH_ATTEMPTS,
        "failure_reason": reason,
        "automatic_retries": 0,
        "automatic_followup_reads": 0,
        "model_calls": 0,
        "model_synthesis_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": (
            "ZERO_CALL_DURABLE_CONTINUATION_PROVIDER_READ_FAILURE_RECONCILIATION"
            if dispatch_count
            else "FIX_CONTINUATION_PRE_DISPATCH_BLOCKER_ZERO_CALL"
        ),
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--continuation-preflight", type=Path, default=DEFAULT_CONTINUATION_PREFLIGHT)
    parser.add_argument("--original-preflight", type=Path, default=DEFAULT_ORIGINAL_PREFLIGHT)
    parser.add_argument("--original-result", type=Path, default=DEFAULT_ORIGINAL_RESULT)
    parser.add_argument("--runner-dry", type=Path, default=DEFAULT_DRY)
    parser.add_argument("--authorization-output", type=Path, default=DEFAULT_AUTH)
    parser.add_argument("--receipt-journal", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument("--result-output", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--execute-provider-reads", action="store_true")
    parser.add_argument("--approve-continuation-preflight-hash")
    parser.add_argument("--approve-continuation-request-manifest-hash")
    parser.add_argument("--approve-runner-dry-hash")
    parser.add_argument("--approve-reopen-cutoff-utc")
    parser.add_argument("--approve-max-dispatch-attempts", type=int)
    parser.add_argument("--owner-approval-id")
    parser.add_argument("--owner-approval-at-utc")
    return parser


def main() -> int:
    args = _parser().parse_args()
    preflight_hash = ""
    auth_hash: str | None = None
    try:
        head = _git("rev-parse", "HEAD")
        if _git("branch", "--show-current") != "hackathon/alpaca-2026":
            raise ResidualExternalReadContinuationRuntimeError(
                "runner requires hackathon/alpaca-2026 branch"
            )
        if _git("status", "--porcelain"):
            raise ResidualExternalReadContinuationRuntimeError("runner requires clean worktree")

        continuation_preflight = _read(args.continuation_preflight, label="continuation preflight")
        original_preflight = _read(args.original_preflight, label="original preflight")
        original_result = _read(args.original_result, label="original provider result")
        preflight_hash = verify_continuation_preflight(continuation_preflight)
        verify_original_preflight(original_preflight)
        verify_original_result(original_result)

        if not args.execute_provider_reads:
            if args.runner_dry.exists():
                raise ResidualExternalReadContinuationRuntimeError(
                    "continuation runner dry output already exists"
                )
            dry = build_dry(
                continuation_preflight=continuation_preflight,
                original_preflight=original_preflight,
                original_result=original_result,
                code_commit_sha=head,
            )
            _write_exclusive(args.runner_dry, dry)
            print(json.dumps(dry, ensure_ascii=False, sort_keys=True, indent=2))
            print("MODEL_CALLS=0")
            print("MODEL_SYNTHESIS_CALLS=0")
            print("PROVIDER_READS=0")
            print("BROKER_WRITES=0")
            print("ALPACA_ORDERS=0")
            print("COST_USD=0")
            print("LIVE_MONEY=PROHIBITED")
            return 0

        if args.authorization_output.exists() or args.receipt_journal.exists() or args.result_output.exists():
            raise ResidualExternalReadContinuationRuntimeError(
                "continuation production evidence already exists; do not delete or rerun"
            )
        dry = _read(args.runner_dry, label="continuation runner dry")
        dry_hash = verify_dry(dry, expected_code_commit_sha=head)
        if args.approve_continuation_preflight_hash != EXPECTED_CONTINUATION_PREFLIGHT_HASH:
            raise ResidualExternalReadContinuationRuntimeError("approved continuation preflight hash mismatch")
        if args.approve_continuation_request_manifest_hash != EXPECTED_CONTINUATION_MANIFEST_HASH:
            raise ResidualExternalReadContinuationRuntimeError("approved continuation request manifest mismatch")
        if args.approve_runner_dry_hash != dry_hash:
            raise ResidualExternalReadContinuationRuntimeError("approved runner dry hash mismatch")
        if args.approve_reopen_cutoff_utc != EXPECTED_REOPEN_CUTOFF_UTC:
            raise ResidualExternalReadContinuationRuntimeError("approved reopen cutoff mismatch")
        if args.approve_max_dispatch_attempts != MAX_DISPATCH_ATTEMPTS:
            raise ResidualExternalReadContinuationRuntimeError("approved dispatch ceiling mismatch")
        if not args.owner_approval_id or not args.owner_approval_at_utc:
            raise ResidualExternalReadContinuationRuntimeError("explicit owner approval metadata required")

        authorization = build_authorization(
            dry=dry,
            owner_approval_id=args.owner_approval_id,
            owner_approval_at_utc=args.owner_approval_at_utc,
            code_commit_sha=head,
        )
        auth_hash = str(authorization["artifact_hash"])
        _write_exclusive(args.authorization_output, authorization)
        result = execute_once(
            continuation_preflight=continuation_preflight,
            original_preflight=original_preflight,
            original_result=original_result,
            authorization=authorization,
            journal_path=args.receipt_journal,
        )
        _write_exclusive(args.result_output, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if result.get("status") != BLOCKED_STATUS else 2
    except (ResidualExternalReadContinuationRuntimeError, subprocess.CalledProcessError, OSError, ValueError) as exc:
        count = _dispatch_count(args.receipt_journal)
        blocked = _blocked(
            preflight_hash=preflight_hash or EXPECTED_CONTINUATION_PREFLIGHT_HASH,
            auth_hash=auth_hash,
            reason=str(exc),
            dispatch_count=count,
        )
        if args.execute_provider_reads and not args.result_output.exists():
            try:
                _write_exclusive(args.result_output, blocked)
            except OSError:
                pass
        print(json.dumps(blocked, ensure_ascii=False, sort_keys=True, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
