from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import subprocess
import sys
from pathlib import Path

from aic.domain.canonical import canonical_sha256
from aic.research.reopen_judge_residual_external_read_preflight_v01 import (
    ResidualExternalReadPreflightError,
    build_preflight,
    verify_preflight,
)


DEFAULT_PLAN = Path(".aic-runtime/b3_research_reopen_residual_external_read_plan_zero_call_v0_1.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b3_research_reopen_residual_external_read_preflight_zero_call_v0_1.json")


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _read(path: Path, *, label: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResidualExternalReadPreflightError(f"unable to read {label}") from exc
    if not isinstance(payload, dict):
        raise ResidualExternalReadPreflightError(f"{label} root must be an object")
    return payload


def _write_exclusive(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb", closefd=True) as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _now_utc_second_precision() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    try:
        head = _git("rev-parse", "HEAD")
        if _git("branch", "--show-current") != "hackathon/alpaca-2026":
            raise ResidualExternalReadPreflightError("runner requires hackathon/alpaca-2026 branch")
        if _git("status", "--porcelain"):
            raise ResidualExternalReadPreflightError("runner requires clean worktree")
        if DEFAULT_OUTPUT.exists():
            raise ResidualExternalReadPreflightError("residual external-read preflight output already exists")

        plan = _read(DEFAULT_PLAN, label="residual external-read plan")
        artifact = build_preflight(
            plan=plan,
            code_commit_sha=head,
            reopen_cutoff_utc=_now_utc_second_precision(),
        )
        artifact["output_path"] = str(DEFAULT_OUTPUT)
        artifact["artifact_hash"] = canonical_sha256(artifact, exclude_fields=("artifact_hash",))
        verify_preflight(artifact, expected_code_commit_sha=head)
        _write_exclusive(DEFAULT_OUTPUT, artifact)

        print(json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=2))
        print("MODEL_CALLS=0")
        print("PROVIDER_READS=0")
        print("BROKER_WRITES=0")
        print("ALPACA_ORDERS=0")
        print("COST_USD=0")
        print("LIVE_MONEY=PROHIBITED")
        return 0
    except (ResidualExternalReadPreflightError, subprocess.CalledProcessError, OSError) as exc:
        print(
            json.dumps(
                {
                    "status": "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_PREFLIGHT_ZERO_CALL_BLOCKED",
                    "error_class": exc.__class__.__name__,
                    "error": str(exc),
                    "model_calls": 0,
                    "provider_reads": 0,
                    "broker_writes": 0,
                    "alpaca_orders": 0,
                    "cost_usd": "0",
                    "live_money": "PROHIBITED",
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
