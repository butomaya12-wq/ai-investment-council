from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from aic.research.reopen_judge_cr4_to_cr6_repair_preflight_v01 import (
    CR4ToCR6RepairPreflightError,
    build_preflight,
    verify_preflight,
)


RECONCILIATION = Path(
    ".aic-runtime/b3_research_reopen_wire_repair_v02_failure_reconciliation_zero_call_v0_1.json"
)
ORIGINAL_RESULT = Path(
    ".aic-runtime/b3_research_reopen_residual_external_read_result_v0_1.json"
)
OUTPUT = Path(
    ".aic-runtime/b3_research_reopen_cr4_to_cr6_repair_preflight_zero_call_v0_1.json"
)


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _read_json(path: Path, *, label: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CR4ToCR6RepairPreflightError(f"unable to read {label}") from exc
    if not isinstance(payload, dict):
        raise CR4ToCR6RepairPreflightError(f"{label} root must be object")
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


def main() -> int:
    try:
        if _git("branch", "--show-current") != "hackathon/alpaca-2026":
            raise CR4ToCR6RepairPreflightError(
                "runner requires hackathon/alpaca-2026 branch"
            )
        if _git("status", "--porcelain"):
            raise CR4ToCR6RepairPreflightError("runner requires clean worktree")
        if OUTPUT.exists():
            raise CR4ToCR6RepairPreflightError(
                "repair preflight output already exists; do not delete or rerun"
            )
        head = _git("rev-parse", "HEAD")
        artifact = build_preflight(
            reconciliation=_read_json(RECONCILIATION, label="V02 failure reconciliation"),
            original_result=_read_json(ORIGINAL_RESULT, label="original provider result"),
            code_commit_sha=head,
        )
        verify_preflight(artifact, expected_code_commit_sha=head)
        _write_exclusive(OUTPUT, artifact)
        print(json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=2))
        print("CLI_CAPABILITY_PROBE=PASS")
        print("PROVIDER_READS=0")
        print("MODEL_CALLS=0")
        print("MODEL_SYNTHESIS_CALLS=0")
        print("BROKER_WRITES=0")
        print("ALPACA_ORDERS=0")
        print("COST_USD=0")
        print("LIVE_MONEY=PROHIBITED")
        return 0
    except (
        CR4ToCR6RepairPreflightError,
        subprocess.CalledProcessError,
        OSError,
        ValueError,
    ) as exc:
        print(f"CR4ToCR6RepairPreflightError: {exc}")
        print("CLI_CAPABILITY_PROBE=FAIL")
        print("PROVIDER_READS=0")
        print("MODEL_CALLS=0")
        print("MODEL_SYNTHESIS_CALLS=0")
        print("BROKER_WRITES=0")
        print("ALPACA_ORDERS=0")
        print("COST_USD=0")
        print("LIVE_MONEY=PROHIBITED")
        return 2


if __name__ == "__main__":
    sys.exit(main())
