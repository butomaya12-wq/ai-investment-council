from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from aic.research.reopen_judge_durable_provider_read_failure_reconciliation_v01 import (
    DurableProviderReadFailureReconciliationError,
)
from aic.research.reopen_judge_durable_provider_read_failure_reconciliation_v02 import (
    DurableProviderReadFailureReconciliationV02Error,
    build_reconciliation_v02,
)


DEFAULT_LOCAL_REPLAY = Path(".aic-runtime/b3_research_reopen_local_replay_zero_call_v0_1.json")
DEFAULT_AUTHORIZATION = Path(".aic-runtime/b3_research_reopen_residual_external_read_authorization_v0_1.json")
DEFAULT_JOURNAL = Path(".aic-runtime/b3_research_reopen_residual_external_read_receipts_v0_1.jsonl")
DEFAULT_RESULT = Path(".aic-runtime/b3_research_reopen_residual_external_read_result_v0_1.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b3_research_reopen_durable_provider_read_failure_reconciliation_zero_call_v0_2.json")


def _git(*args: str) -> str:
    completed = subprocess.run(["git", *args], check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def _read_json(path: Path, *, label: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DurableProviderReadFailureReconciliationV02Error(
            f"unable to read {label}"
        ) from exc
    if not isinstance(payload, dict):
        raise DurableProviderReadFailureReconciliationV02Error(
            f"{label} root must be object"
        )
    return payload


def _read_jsonl(path: Path, *, label: str) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise DurableProviderReadFailureReconciliationV02Error(
            f"unable to read {label}"
        ) from exc
    rows: list[dict] = []
    for index, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DurableProviderReadFailureReconciliationV02Error(
                f"{label} line {index} is invalid JSON"
            ) from exc
        if not isinstance(row, dict):
            raise DurableProviderReadFailureReconciliationV02Error(
                f"{label} line {index} root must be object"
            )
        rows.append(row)
    return rows


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
    parser.add_argument("--local-replay", type=Path, default=DEFAULT_LOCAL_REPLAY)
    parser.add_argument("--authorization", type=Path, default=DEFAULT_AUTHORIZATION)
    parser.add_argument("--receipt-journal", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        head = _git("rev-parse", "HEAD")
        if _git("branch", "--show-current") != "hackathon/alpaca-2026":
            raise DurableProviderReadFailureReconciliationV02Error(
                "reconciliation V02 requires hackathon/alpaca-2026 branch"
            )
        if _git("status", "--porcelain"):
            raise DurableProviderReadFailureReconciliationV02Error(
                "reconciliation V02 requires clean worktree"
            )
        if args.output.exists():
            raise DurableProviderReadFailureReconciliationV02Error(
                "reconciliation V02 output already exists; do not delete or rerun"
            )

        local_replay = _read_json(args.local_replay, label="local replay")
        authorization = _read_json(args.authorization, label="authorization")
        journal_rows = _read_jsonl(args.receipt_journal, label="receipt journal")
        result = _read_json(args.result, label="provider read result")

        artifact = build_reconciliation_v02(
            local_replay=local_replay,
            authorization=authorization,
            journal_rows=journal_rows,
            result=result,
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
        DurableProviderReadFailureReconciliationV02Error,
        DurableProviderReadFailureReconciliationError,
        subprocess.CalledProcessError,
        OSError,
    ) as exc:
        print(f"DurableProviderReadFailureReconciliationV02Error: {exc}")
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
