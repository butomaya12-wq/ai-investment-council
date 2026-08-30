from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from aic.research.reopen_pagination_preflight import (
    ReopenPaginationPreflightError,
    build_zero_call_preflight_artifact,
)


DEFAULT_S00 = Path(".aic-runtime/b3_research_reopen_s00_v0_1.json")
DEFAULT_PLANNER = Path(".aic-runtime/b3_planner_batch.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b3_reopen_pagination_zero_call_v0_1.json")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Zero-call B3 reopen pagination preflight. Reads only local artifacts and Alpaca CLI help."
    )
    parser.add_argument("--s00", type=Path, default=DEFAULT_S00)
    parser.add_argument("--planner", type=Path, default=DEFAULT_PLANNER)
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
        raise ReopenPaginationPreflightError("unable to inspect git repository state")
    return completed.stdout.decode("utf-8").strip()


def main() -> int:
    args = _args()
    try:
        code_commit_sha = _git_output("rev-parse", "HEAD")
        if _git_output("status", "--porcelain"):
            raise ReopenPaginationPreflightError("worktree must be clean for zero-call pagination preflight")
        artifact = build_zero_call_preflight_artifact(
            code_commit_sha=code_commit_sha,
            s00_path=args.s00,
            planner_path=args.planner,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.output.exists():
            try:
                existing = json.loads(args.output.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ReopenPaginationPreflightError("existing zero-call preflight artifact is unreadable") from exc
            if existing != artifact:
                raise ReopenPaginationPreflightError("existing zero-call preflight artifact differs from rebuilt artifact")
        else:
            args.output.write_text(
                json.dumps(artifact, indent=2, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
        print(json.dumps({**artifact, "output_path": str(args.output)}, indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
