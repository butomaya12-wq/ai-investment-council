from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from aic.research.reopen_judge_residual_external_read_continuation_preflight_v01 import (
    ResidualExternalReadContinuationPreflightError,
    build_preflight,
)


DEFAULT_PLAN = Path(
    ".aic-runtime/"
    "b3_research_reopen_residual_external_read_continuation_plan_zero_call_v0_1.json"
)
DEFAULT_ORIGINAL_PREFLIGHT = Path(
    ".aic-runtime/"
    "b3_research_reopen_residual_external_read_preflight_zero_call_v0_1.json"
)
DEFAULT_OUTPUT = Path(
    ".aic-runtime/"
    "b3_research_reopen_residual_external_read_continuation_preflight_zero_call_v0_1.json"
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
        raise ResidualExternalReadContinuationPreflightError(
            f"unable to read {label}"
        ) from exc
    if not isinstance(payload, dict):
        raise ResidualExternalReadContinuationPreflightError(
            f"{label} root must be object"
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
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument(
        "--original-preflight",
        type=Path,
        default=DEFAULT_ORIGINAL_PREFLIGHT,
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        head = _git("rev-parse", "HEAD")
        if _git("branch", "--show-current") != "hackathon/alpaca-2026":
            raise ResidualExternalReadContinuationPreflightError(
                "continuation preflight requires hackathon/alpaca-2026 branch"
            )
        if _git("status", "--porcelain"):
            raise ResidualExternalReadContinuationPreflightError(
                "continuation preflight requires clean worktree"
            )
        if args.output.exists():
            raise ResidualExternalReadContinuationPreflightError(
                "continuation preflight output already exists; do not delete or rerun"
            )

        plan = _read_json(args.plan, label="continuation plan")
        original_preflight = _read_json(
            args.original_preflight,
            label="original preflight",
        )
        artifact = build_preflight(
            plan=plan,
            original_preflight=original_preflight,
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
        ResidualExternalReadContinuationPreflightError,
        subprocess.CalledProcessError,
        OSError,
    ) as exc:
        print(f"ResidualExternalReadContinuationPreflightError: {exc}")
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
