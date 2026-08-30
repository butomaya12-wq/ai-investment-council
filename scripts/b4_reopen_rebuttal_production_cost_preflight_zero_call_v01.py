from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from aic.council.reopen_rebuttal_production_cost_preflight import (
    load_and_build_b4_reopen_rebuttal_production_cost_preflight,
)


DEFAULT_RECOVERED_INITIAL = Path(
    ".aic-runtime/b4_reopen_initial_council_freeze_recovered_v0_2.json"
)
DEFAULT_LIFECYCLE = Path(".aic-runtime/b4_reopen_lifecycle_plan_zero_call_v0_2.json")
DEFAULT_INITIAL_COST = Path(".aic-runtime/b4_reopen_production_cost_preflight_zero_call_v0_1.json")
DEFAULT_OVERLAY = Path(".aic-runtime/b4_reopen_input_overlay_zero_call_v0_1.json")
DEFAULT_CLOSURE = Path(".aic-runtime/b3_reopen_remaining_gaps_closure_zero_call_v0_2.json")
DEFAULT_FREEZE = Path(".aic-runtime/b4_council_input_freeze.json")
DEFAULT_RECONCILIATION = Path(".aic-runtime/b3_selected_model_reconciliation.json")
DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_INITIAL_AUTHORITY = Path("config/event/b4_initial_selected_model_v1.json")
DEFAULT_PRICING = Path("config/event/openai_text_pricing_2026_08_30.json")
DEFAULT_OUTPUT = Path(
    ".aic-runtime/b4_reopen_rebuttal_production_cost_preflight_zero_call_v0_1.json"
)
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
    parser.add_argument("--recovered-initial-freeze", type=Path, default=DEFAULT_RECOVERED_INITIAL)
    parser.add_argument("--lifecycle", type=Path, default=DEFAULT_LIFECYCLE)
    parser.add_argument("--initial-cost-preflight", type=Path, default=DEFAULT_INITIAL_COST)
    parser.add_argument("--overlay", type=Path, default=DEFAULT_OVERLAY)
    parser.add_argument("--closure", type=Path, default=DEFAULT_CLOSURE)
    parser.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--reconciliation", type=Path, default=DEFAULT_RECONCILIATION)
    parser.add_argument("--handoff", type=Path, default=DEFAULT_HANDOFF)
    parser.add_argument("--initial-authority", type=Path, default=DEFAULT_INITIAL_AUTHORITY)
    parser.add_argument("--pricing", type=Path, default=DEFAULT_PRICING)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    branch = _git("branch", "--show-current")
    if branch != EXPECTED_BRANCH:
        raise SystemExit(f"STOP: expected branch {EXPECTED_BRANCH}, got {branch}")
    if _git("status", "--porcelain"):
        raise SystemExit("STOP: worktree must be clean")
    if args.output.exists():
        raise SystemExit(f"STOP: output already exists: {args.output}")

    required = (
        args.recovered_initial_freeze,
        args.lifecycle,
        args.initial_cost_preflight,
        args.overlay,
        args.closure,
        args.freeze,
        args.reconciliation,
        args.handoff,
        args.initial_authority,
        args.pricing,
    )
    for path in required:
        if not path.is_file():
            raise SystemExit(f"STOP: required immutable input missing: {path}")

    head = _git("rev-parse", "HEAD")
    artifact = load_and_build_b4_reopen_rebuttal_production_cost_preflight(
        code_commit_sha=head,
        recovered_initial_freeze_path=args.recovered_initial_freeze,
        lifecycle_path=args.lifecycle,
        initial_cost_preflight_path=args.initial_cost_preflight,
        overlay_path=args.overlay,
        closure_path=args.closure,
        freeze_path=args.freeze,
        reconciliation_path=args.reconciliation,
        handoff_path=args.handoff,
        initial_authority_path=args.initial_authority,
        pricing_path=args.pricing,
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
