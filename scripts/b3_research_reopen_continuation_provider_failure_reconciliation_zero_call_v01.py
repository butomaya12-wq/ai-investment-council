from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from aic.research.reopen_judge_continuation_provider_failure_reconciliation_v01 import (
    ContinuationProviderFailureReconciliationError,
    build_reconciliation,
    read_journal,
    read_json,
)


AUTH = Path(".aic-runtime/b3_research_reopen_residual_external_read_continuation_authorization_v0_1.json")
JOURNAL = Path(".aic-runtime/b3_research_reopen_residual_external_read_continuation_receipts_v0_1.jsonl")
RESULT = Path(".aic-runtime/b3_research_reopen_residual_external_read_continuation_result_v0_1.json")
OUTPUT = Path(".aic-runtime/b3_research_reopen_continuation_provider_failure_reconciliation_zero_call_v0_1.json")


def _git(*args: str) -> str:
    completed = subprocess.run(["git", *args], check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def _write_exclusive(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb", closefd=True) as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    try:
        if _git("branch", "--show-current") != "hackathon/alpaca-2026":
            raise ContinuationProviderFailureReconciliationError("runner requires hackathon/alpaca-2026 branch")
        if _git("status", "--porcelain"):
            raise ContinuationProviderFailureReconciliationError("runner requires clean worktree")
        if OUTPUT.exists():
            raise ContinuationProviderFailureReconciliationError("reconciliation output already exists; do not delete or rerun")
        artifact = build_reconciliation(
            authorization=read_json(AUTH, label="continuation authorization"),
            result=read_json(RESULT, label="continuation result"),
            journal_rows=read_journal(JOURNAL),
            code_commit_sha=_git("rev-parse", "HEAD"),
        )
        _write_exclusive(OUTPUT, artifact)
        print(json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=2))
        print("MODEL_CALLS=0")
        print("MODEL_SYNTHESIS_CALLS=0")
        print("PROVIDER_READS=0")
        print("BROKER_WRITES=0")
        print("ALPACA_ORDERS=0")
        print("COST_USD=0")
        print("LIVE_MONEY=PROHIBITED")
        return 0
    except (ContinuationProviderFailureReconciliationError, subprocess.CalledProcessError, OSError) as exc:
        print(f"ContinuationProviderFailureReconciliationError: {exc}")
        print("MODEL_CALLS=0")
        print("MODEL_SYNTHESIS_CALLS=0")
        print("PROVIDER_READS=0")
        print("BROKER_WRITES=0")
        print("ALPACA_ORDERS=0")
        print("COST_USD=0")
        print("LIVE_MONEY=PROHIBITED")
        return 2


if __name__ == "__main__":
    sys.exit(main())
