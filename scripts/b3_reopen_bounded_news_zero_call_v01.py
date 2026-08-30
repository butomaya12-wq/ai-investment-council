from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from aic.research.reopen_bounded_news_review import (
    ReopenBoundedNewsReviewError,
    build_bounded_news_review,
)


DEFAULT_PLANNER = Path(".aic-runtime/b3_planner_batch.json")
DEFAULT_RETRIEVAL = Path(".aic-runtime/b3_retrieval_batch.json")
DEFAULT_S00 = Path(".aic-runtime/b3_research_reopen_s00_v0_1.json")
DEFAULT_BLOCKED_RESULT = Path(".aic-runtime/b3_reopen_paginated_provider_read_result_v0_1.json")
DEFAULT_BLOCKED_RECEIPTS = Path(".aic-runtime/b3_reopen_paginated_provider_read_receipts_v0_1.jsonl")
DEFAULT_OUTPUT = Path(".aic-runtime/b3_reopen_bounded_news_zero_call_v0_1.json")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Zero-call review of the frozen B3 bounded Alpaca News need. "
            "Uses only durable local artifacts; performs no provider or model calls."
        )
    )
    parser.add_argument("--planner", type=Path, default=DEFAULT_PLANNER)
    parser.add_argument("--retrieval", type=Path, default=DEFAULT_RETRIEVAL)
    parser.add_argument("--s00", type=Path, default=DEFAULT_S00)
    parser.add_argument("--blocked-result", type=Path, default=DEFAULT_BLOCKED_RESULT)
    parser.add_argument("--blocked-receipts", type=Path, default=DEFAULT_BLOCKED_RECEIPTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _git_output(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise ReopenBoundedNewsReviewError("unable to inspect git repository state")
    return completed.stdout.decode("utf-8").strip()


def main() -> int:
    args = _args()
    try:
        code_commit_sha = _git_output("rev-parse", "HEAD")
        branch = _git_output("branch", "--show-current")
        if branch != "hackathon/alpaca-2026":
            raise ReopenBoundedNewsReviewError("bounded-news review requires hackathon/alpaca-2026")
        if _git_output("status", "--porcelain"):
            raise ReopenBoundedNewsReviewError("worktree must be clean for bounded-news review")
        artifact = build_bounded_news_review(
            code_commit_sha=code_commit_sha,
            planner_path=args.planner,
            retrieval_path=args.retrieval,
            s00_path=args.s00,
            blocked_result_path=args.blocked_result,
            blocked_receipts_path=args.blocked_receipts,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.output.exists():
            try:
                existing = json.loads(args.output.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ReopenBoundedNewsReviewError("existing bounded-news artifact is unreadable") from exc
            if existing != artifact:
                raise ReopenBoundedNewsReviewError("existing bounded-news artifact differs from deterministic replay")
        else:
            args.output.write_text(
                json.dumps(artifact, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        print(json.dumps({**artifact, "output_path": str(args.output)}, indent=2, ensure_ascii=False))
        print("MODEL_CALLS=0")
        print("PROVIDER_READS=0")
        print("BROKER_WRITES=0")
        print("ALPACA_ORDERS=0")
        print("LIVE_MONEY=PROHIBITED")
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
