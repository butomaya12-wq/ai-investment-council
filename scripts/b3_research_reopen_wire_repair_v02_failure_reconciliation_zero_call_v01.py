from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from aic.research.reopen_judge_wire_repair_v02_failure_reconciliation_v01 import (
    WireRepairV02FailureReconciliationError,
    build_reconciliation,
    read_journal,
    read_json,
)


AUTH = Path(
    ".aic-runtime/b3_research_reopen_continuation_wire_repair_authorization_v0_2.json"
)
JOURNAL = Path(
    ".aic-runtime/b3_research_reopen_continuation_wire_repair_receipts_v0_2.jsonl"
)
RAW_DIR = Path(
    ".aic-runtime/b3_research_reopen_continuation_wire_repair_raw_v0_2"
)
RESULT = Path(
    ".aic-runtime/b3_research_reopen_continuation_wire_repair_result_v0_2.json"
)
ORIGINAL_PREFLIGHT = Path(
    ".aic-runtime/b3_research_reopen_residual_external_read_preflight_zero_call_v0_1.json"
)
OUTPUT = Path(
    ".aic-runtime/b3_research_reopen_wire_repair_v02_failure_reconciliation_zero_call_v0_1.json"
)


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


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


def main() -> int:
    try:
        if _git("branch", "--show-current") != "hackathon/alpaca-2026":
            raise WireRepairV02FailureReconciliationError(
                "runner requires hackathon/alpaca-2026 branch"
            )
        if _git("status", "--porcelain"):
            raise WireRepairV02FailureReconciliationError(
                "runner requires clean worktree"
            )
        if OUTPUT.exists():
            raise WireRepairV02FailureReconciliationError(
                "reconciliation output already exists; do not delete or rerun"
            )
        if not RAW_DIR.is_dir():
            raise WireRepairV02FailureReconciliationError(
                "V02 raw-response directory missing"
            )

        artifact = build_reconciliation(
            authorization=read_json(AUTH, label="V02 authorization"),
            result=read_json(RESULT, label="V02 result"),
            original_preflight=read_json(
                ORIGINAL_PREFLIGHT,
                label="original residual-read preflight",
            ),
            journal_rows=read_journal(JOURNAL),
            raw_dir=RAW_DIR,
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
    except (
        WireRepairV02FailureReconciliationError,
        subprocess.CalledProcessError,
        OSError,
        ValueError,
    ) as exc:
        print(f"WireRepairV02FailureReconciliationError: {exc}")
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
