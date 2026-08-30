from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from aic.council.reopen_lifecycle_plan import load_and_build_b4_reopen_lifecycle_plan


DEFAULT_OVERLAY = Path(".aic-runtime/b4_reopen_input_overlay_zero_call_v0_1.json")
DEFAULT_INITIAL_AUTHORITY = Path("config/event/b4_initial_selected_model_v1.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b4_reopen_lifecycle_plan_zero_call_v0_1.json")
EXPECTED_BRANCH = "hackathon/alpaca-2026"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--overlay", type=Path, default=DEFAULT_OVERLAY)
    parser.add_argument("--initial-selected-model-authority", type=Path, default=DEFAULT_INITIAL_AUTHORITY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    branch = _git("branch", "--show-current")
    if branch != EXPECTED_BRANCH:
        raise SystemExit(f"STOP: expected branch {EXPECTED_BRANCH}, got {branch}")
    if _git("status", "--porcelain"):
        raise SystemExit("STOP: worktree must be clean")
    if args.output.exists():
        raise SystemExit(f"STOP: output already exists: {args.output}")
    for path in (args.overlay, args.initial_selected_model_authority):
        if not path.is_file():
            raise SystemExit(f"STOP: required immutable input missing: {path}")

    head = _git("rev-parse", "HEAD")
    artifact = load_and_build_b4_reopen_lifecycle_plan(
        code_commit_sha=head,
        overlay_path=args.overlay,
        initial_selected_model_authority_path=args.initial_selected_model_authority,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"OUTPUT_PATH={args.output}")
    print("MODEL_CALLS=0")
    print("PROVIDER_READS=0")
    print("BROKER_WRITES=0")
    print("ALPACA_ORDERS=0")
    print("LIVE_MONEY=PROHIBITED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
