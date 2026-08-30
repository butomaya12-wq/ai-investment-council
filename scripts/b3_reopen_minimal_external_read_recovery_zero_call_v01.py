from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from aic.research.reopen_minimal_external_recovery import (
    build_recovery_artifact,
    write_recovery_artifact_exclusive,
)


EXPECTED_BRANCH = "hackathon/alpaca-2026"


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Zero-call recovery of already captured B3 minimal external-read responses."
    )
    parser.add_argument(
        "--blocked-result",
        default=".aic-runtime/b3_reopen_minimal_external_read_result_v0_1.json",
    )
    parser.add_argument(
        "--authorization",
        default=".aic-runtime/b3_reopen_minimal_external_read_authorization_v0_1.json",
    )
    parser.add_argument(
        "--receipts",
        default=".aic-runtime/b3_reopen_minimal_external_read_receipts_v0_1.jsonl",
    )
    parser.add_argument(
        "--raw-dir",
        default=".aic-runtime/b3_reopen_minimal_external_read_raw_v0_1",
    )
    parser.add_argument(
        "--output",
        default=".aic-runtime/b3_reopen_minimal_external_read_recovery_zero_call_v0_1.json",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    branch = _git("branch", "--show-current")
    if branch != EXPECTED_BRANCH:
        print(f"STOP: expected branch {EXPECTED_BRANCH}, got {branch}", file=sys.stderr)
        return 2
    if _git("status", "--porcelain"):
        print("STOP: worktree is not clean", file=sys.stderr)
        return 2

    output = Path(args.output)
    if output.exists():
        print(f"STOP: recovery output already exists: {output}", file=sys.stderr)
        return 2

    for path in (args.blocked_result, args.authorization, args.receipts, args.raw_dir):
        if not Path(path).exists():
            print(f"STOP: required local capture artifact missing: {path}", file=sys.stderr)
            return 2

    code_sha = _git("rev-parse", "HEAD")
    artifact = build_recovery_artifact(
        code_commit_sha=code_sha,
        blocked_result_path=args.blocked_result,
        authorization_path=args.authorization,
        receipts_path=args.receipts,
        raw_dir=args.raw_dir,
    )
    write_recovery_artifact_exclusive(output, artifact)

    print(json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=2))
    print(f"OUTPUT_PATH={output}")
    print("NEW_PROVIDER_DISPATCH_ATTEMPTS=0")
    print("NEW_PROVIDER_READS=0")
    print("MODEL_CALLS=0")
    print("BROKER_WRITES=0")
    print("ALPACA_ORDERS=0")
    print("LIVE_MONEY=PROHIBITED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
