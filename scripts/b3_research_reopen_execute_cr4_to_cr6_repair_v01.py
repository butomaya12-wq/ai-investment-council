from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from aic.research.reopen_judge_cr4_to_cr6_repair_production_v01 import (
    BLOCKED_STATUS,
    EXPECTED_ALPACA_BINARY_SHA256,
    EXPECTED_CAPABILITY_PROBE_HASH,
    EXPECTED_REOPEN_CUTOFF_UTC,
    EXPECTED_REQUEST_MANIFEST_HASH,
    MAX_DISPATCH_ATTEMPTS,
    OWNER_APPROVAL_ID,
    CR4ToCR6RepairProductionError,
    build_authorization,
    execute_once,
    verify_installed_cli,
    verify_original_result,
    verify_preflight,
    verify_source_dry,
)


DEFAULT_PREFLIGHT = Path(
    ".aic-runtime/b3_research_reopen_cr4_to_cr6_repair_preflight_zero_call_v0_3.json"
)
DEFAULT_DRY = Path(
    ".aic-runtime/b3_research_reopen_cr4_to_cr6_repair_runner_dry_v0_1.json"
)
DEFAULT_ORIGINAL_RESULT = Path(
    ".aic-runtime/b3_research_reopen_residual_external_read_result_v0_1.json"
)
DEFAULT_AUTH = Path(
    ".aic-runtime/b3_research_reopen_cr4_to_cr6_repair_authorization_v0_1.json"
)
DEFAULT_JOURNAL = Path(
    ".aic-runtime/b3_research_reopen_cr4_to_cr6_repair_receipts_v0_1.jsonl"
)
DEFAULT_RAW_DIR = Path(
    ".aic-runtime/b3_research_reopen_cr4_to_cr6_repair_raw_v0_1"
)
DEFAULT_RESULT = Path(
    ".aic-runtime/b3_research_reopen_cr4_to_cr6_repair_result_v0_1.json"
)


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _read(path: Path, *, label: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CR4ToCR6RepairProductionError(f"unable to read {label}") from exc
    if not isinstance(payload, dict):
        raise CR4ToCR6RepairProductionError(f"{label} root must be object")
    return payload


def _write_exclusive(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb", closefd=True) as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", type=Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--runner-dry", type=Path, default=DEFAULT_DRY)
    parser.add_argument("--original-result", type=Path, default=DEFAULT_ORIGINAL_RESULT)
    parser.add_argument("--authorization-output", type=Path, default=DEFAULT_AUTH)
    parser.add_argument("--receipt-journal", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument("--raw-response-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--result-output", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--execute-provider-reads", action="store_true")
    parser.add_argument("--approve-preflight-hash")
    parser.add_argument("--approve-request-manifest-hash")
    parser.add_argument("--approve-capability-probe-hash")
    parser.add_argument("--approve-runner-dry-hash")
    parser.add_argument("--approve-alpaca-binary-sha256")
    parser.add_argument("--approve-reopen-cutoff-utc")
    parser.add_argument("--approve-max-dispatch-attempts", type=int)
    parser.add_argument("--owner-approval-id")
    parser.add_argument("--owner-approval-at-utc")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if not args.execute_provider_reads:
            raise CR4ToCR6RepairProductionError(
                "production runner requires --execute-provider-reads"
            )

        head = _git("rev-parse", "HEAD")
        if _git("branch", "--show-current") != "hackathon/alpaca-2026":
            raise CR4ToCR6RepairProductionError(
                "runner requires hackathon/alpaca-2026 branch"
            )
        if _git("status", "--porcelain"):
            raise CR4ToCR6RepairProductionError("runner requires clean worktree")

        for path in (
            args.authorization_output,
            args.receipt_journal,
            args.result_output,
            args.raw_response_dir,
        ):
            if path.exists():
                raise CR4ToCR6RepairProductionError(
                    f"production evidence already exists: {path}; do not delete or rerun"
                )

        preflight = _read(args.preflight, label="CR4-to-CR6 repair preflight")
        dry = _read(args.runner_dry, label="CR4-to-CR6 repair runner dry")
        original_result = _read(args.original_result, label="original provider result")

        preflight_hash = verify_preflight(preflight)
        dry_hash = verify_source_dry(dry)
        verify_original_result(original_result)
        cli = verify_installed_cli(preflight)

        if args.approve_preflight_hash != preflight_hash:
            raise CR4ToCR6RepairProductionError("approved preflight hash mismatch")
        if args.approve_request_manifest_hash != EXPECTED_REQUEST_MANIFEST_HASH:
            raise CR4ToCR6RepairProductionError("approved request manifest mismatch")
        if args.approve_capability_probe_hash != EXPECTED_CAPABILITY_PROBE_HASH:
            raise CR4ToCR6RepairProductionError("approved capability probe mismatch")
        if args.approve_runner_dry_hash != dry_hash:
            raise CR4ToCR6RepairProductionError("approved runner dry hash mismatch")
        if args.approve_alpaca_binary_sha256 != cli.get("alpaca_binary_sha256"):
            raise CR4ToCR6RepairProductionError("approved Alpaca binary SHA mismatch")
        if args.approve_alpaca_binary_sha256 != EXPECTED_ALPACA_BINARY_SHA256:
            raise CR4ToCR6RepairProductionError("frozen Alpaca binary SHA mismatch")
        if args.approve_reopen_cutoff_utc != EXPECTED_REOPEN_CUTOFF_UTC:
            raise CR4ToCR6RepairProductionError("approved reopen cutoff mismatch")
        if args.approve_max_dispatch_attempts != MAX_DISPATCH_ATTEMPTS:
            raise CR4ToCR6RepairProductionError("approved dispatch ceiling mismatch")
        if args.owner_approval_id != OWNER_APPROVAL_ID:
            raise CR4ToCR6RepairProductionError("owner approval id mismatch")
        if not args.owner_approval_at_utc:
            raise CR4ToCR6RepairProductionError("owner approval timestamp required")

        authorization = build_authorization(
            preflight=preflight,
            dry=dry,
            owner_approval_id=args.owner_approval_id,
            owner_approval_at_utc=args.owner_approval_at_utc,
            code_commit_sha=head,
        )
        _write_exclusive(args.authorization_output, authorization)

        result = execute_once(
            preflight=preflight,
            dry=dry,
            original_result=original_result,
            authorization=authorization,
            journal_path=args.receipt_journal,
            raw_dir=args.raw_response_dir,
        )
        _write_exclusive(args.result_output, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        print("MODEL_CALLS=0")
        print("MODEL_SYNTHESIS_CALLS=0")
        print("BROKER_WRITES=0")
        print("ALPACA_ORDERS=0")
        print("LIVE_MONEY=PROHIBITED")
        return 2 if result.get("status") == BLOCKED_STATUS else 0
    except (
        CR4ToCR6RepairProductionError,
        subprocess.CalledProcessError,
        OSError,
        ValueError,
    ) as exc:
        print("CR4_TO_CR6_REPAIR_PRODUCTION_PRE_DISPATCH_BLOCK=TRUE")
        print("REASON=" + str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
