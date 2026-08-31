from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from aic.research.reopen_judge_cr4_to_cr6_repair_runner_dry_v01 import (
    CR4ToCR6RepairRunnerDryError,
    build_dry,
    verify_preflight,
)


DEFAULT_PREFLIGHT = Path(
    ".aic-runtime/b3_research_reopen_cr4_to_cr6_repair_preflight_zero_call_v0_3.json"
)
DEFAULT_DRY = Path(
    ".aic-runtime/b3_research_reopen_cr4_to_cr6_repair_runner_dry_v0_1.json"
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
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _read(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CR4ToCR6RepairRunnerDryError("unable to read V03 preflight artifact") from exc
    if not isinstance(payload, dict):
        raise CR4ToCR6RepairRunnerDryError("V03 preflight root must be object")
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
        head = _git("rev-parse", "HEAD")
        if _git("branch", "--show-current") != "hackathon/alpaca-2026":
            raise CR4ToCR6RepairRunnerDryError(
                "runner dry requires hackathon/alpaca-2026 branch"
            )
        if _git("status", "--porcelain"):
            raise CR4ToCR6RepairRunnerDryError("runner dry requires clean worktree")

        if DEFAULT_DRY.exists():
            raise CR4ToCR6RepairRunnerDryError("runner dry output already exists")
        if (
            DEFAULT_AUTH.exists()
            or DEFAULT_JOURNAL.exists()
            or DEFAULT_RAW_DIR.exists()
            or DEFAULT_RESULT.exists()
        ):
            raise CR4ToCR6RepairRunnerDryError(
                "production evidence unexpectedly exists before runner dry"
            )

        preflight = _read(DEFAULT_PREFLIGHT)
        verify_preflight(preflight)
        dry = build_dry(preflight=preflight, code_commit_sha=head)
        _write_exclusive(DEFAULT_DRY, dry)

        print(json.dumps(dry, ensure_ascii=False, sort_keys=True, indent=2))
        print("PROVIDER_READS=0")
        print("MODEL_CALLS=0")
        print("MODEL_SYNTHESIS_CALLS=0")
        print("BROKER_WRITES=0")
        print("ALPACA_ORDERS=0")
        print("COST_USD=0")
        print("LIVE_MONEY=PROHIBITED")
        return 0
    except (
        CR4ToCR6RepairRunnerDryError,
        subprocess.CalledProcessError,
        OSError,
        ValueError,
    ) as exc:
        print("RUNNER_DRY_STATUS=BLOCKED")
        print("RUNNER_DRY_FAILURE_REASON=" + str(exc))
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
