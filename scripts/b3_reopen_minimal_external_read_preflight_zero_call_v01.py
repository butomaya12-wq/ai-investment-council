from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from aic.research.reopen_minimal_external_preflight import (
    build_minimal_external_read_preflight,
)


DEFAULT_PRIMITIVES = Path(
    ".aic-runtime/b3_reopen_local_valuation_and_portfolio_primitives_zero_call_v0_1.json"
)
DEFAULT_OUTPUT = Path(
    ".aic-runtime/b3_reopen_minimal_external_read_preflight_zero_call_v0_1.json"
)


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--primitives", type=Path, default=DEFAULT_PRIMITIVES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if branch != "hackathon/alpaca-2026":
        raise RuntimeError("minimal external-read preflight requires hackathon/alpaca-2026")
    if _git("status", "--porcelain"):
        raise RuntimeError("worktree must be clean before minimal external-read preflight")
    head = _git("rev-parse", "HEAD")
    if args.output.exists():
        raise RuntimeError("minimal external-read preflight output already exists")

    artifact = build_minimal_external_read_preflight(
        code_commit_sha=head,
        primitives_path=args.primitives,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(artifact, ensure_ascii=False, sort_keys=True, indent=2))
    print(f"OUTPUT_PATH={args.output}")
    print("MODEL_CALLS=0")
    print("PROVIDER_READS=0")
    print("BROKER_WRITES=0")
    print("ALPACA_ORDERS=0")
    print("LIVE_MONEY=PROHIBITED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
