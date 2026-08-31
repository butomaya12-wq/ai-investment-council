from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from aic.research.reopen_judge_residual_external_read_continuation_plan_v01 import (
    ResidualExternalReadContinuationPlanError,
    build_plan,
)


DEFAULT_RECONCILIATION = Path(
    ".aic-runtime/"
    "b3_research_reopen_durable_provider_read_failure_reconciliation_zero_call_v0_2.json"
)
DEFAULT_OUTPUT = Path(
    ".aic-runtime/"
    "b3_research_reopen_residual_external_read_continuation_plan_zero_call_v0_1.json"
)


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResidualExternalReadContinuationPlanError(
            "unable to read reconciliation V02 artifact"
        ) from exc
    if not isinstance(payload, dict):
        raise ResidualExternalReadContinuationPlanError(
            "reconciliation V02 root must be object"
        )
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
    parser.add_argument(
        "--reconciliation", type=Path, default=DEFAULT_RECONCILIATION
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        head = _git("rev-parse", "HEAD")
        if _git("branch", "--show-current") != "hackathon/alpaca-2026":
            raise ResidualExternalReadContinuationPlanError(
                "continuation plan requires hackathon/alpaca-2026 branch"
            )
        if _git("status", "--porcelain"):
            raise ResidualExternalReadContinuationPlanError(
                "continuation plan requires clean worktree"
            )
        if args.output.exists():
            raise ResidualExternalReadContinuationPlanError(
                "continuation plan output already exists; do not delete or rerun"
            )
        reconciliation = _read_json(args.reconciliation)
        artifact = build_plan(
            reconciliation=reconciliation,
            code_commit_sha=head,
        )
        _write_exclusive(args.output, artifact)
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
        ResidualExternalReadContinuationPlanError,
        subprocess.CalledProcessError,
        OSError,
    ) as exc:
        print(f"ResidualExternalReadContinuationPlanError: {exc}")
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
