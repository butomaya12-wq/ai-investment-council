from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from aic.council.reopen_input_overlay import (
    B4ReopenInputOverlayError,
    build_b4_reopen_input_overlay,
)


DEFAULT_CLOSURE = Path(".aic-runtime/b3_reopen_remaining_gaps_closure_zero_call_v0_2.json")
DEFAULT_SELECTED_B3 = Path(".aic-runtime/b3_selected_model_reconciliation.json")
DEFAULT_PRODUCTION_JUDGE = Path(".aic-runtime/b4_judge_production_result_v0_1.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b4_reopen_input_overlay_zero_call_v0_1.json")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the B4 reopen additive input overlay with zero provider/model calls.")
    parser.add_argument("--closure", type=Path, default=DEFAULT_CLOSURE)
    parser.add_argument("--selected-b3", type=Path, default=DEFAULT_SELECTED_B3)
    parser.add_argument("--production-judge", type=Path, default=DEFAULT_PRODUCTION_JUDGE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _head() -> str:
    env = {
        **os.environ,
        "OPENAI_API_KEY": "",
        "APCA_API_KEY_ID": "",
        "APCA_API_SECRET_KEY": "",
        "ALPACA_LIVE_TRADE": "",
    }
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.stdout.strip()


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise B4ReopenInputOverlayError(f"output already exists: {path}")
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def main() -> int:
    args = _args()
    try:
        artifact = build_b4_reopen_input_overlay(
            code_commit_sha=_head(),
            closure_path=args.closure,
            selected_b3_path=args.selected_b3,
            production_judge_path=args.production_judge,
        )
        _atomic_write(args.output, artifact)
    except (B4ReopenInputOverlayError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    public = {
        "artifact_version": artifact["artifact_version"],
        "status": artifact["status"],
        "code_commit_sha": artifact["code_commit_sha"],
        "source_b3_reopen_closure_hash": artifact["source_b3_reopen_closure_hash"],
        "source_selected_b3_reconciliation_hash": artifact["source_selected_b3_reconciliation_hash"],
        "source_historical_production_judge_hash": artifact["source_historical_production_judge_hash"],
        "candidate_order": artifact["candidate_order"],
        "legacy_material_claim_count": artifact["legacy_material_claim_count"],
        "supplemental_claim_count": artifact["supplemental_claim_count"],
        "effective_material_claim_count": artifact["effective_material_claim_count"],
        "effective_candidate_surfaces": artifact["effective_candidate_surfaces"],
        "effective_gap_overlay": artifact["effective_gap_overlay"],
        "historical_b4_frozen_outputs_are_historical_context_only": artifact["historical_b4_frozen_outputs_are_historical_context_only"],
        "historical_b4_frozen_outputs_reusable_as_new_model_outputs": artifact["historical_b4_frozen_outputs_reusable_as_new_model_outputs"],
        "new_b4_decision_lifecycle_required": artifact["new_b4_decision_lifecycle_required"],
        "historical_production_judge_rerun_authorized": artifact["historical_production_judge_rerun_authorized"],
        "provider_reads_authorized": artifact["provider_reads_authorized"],
        "planned_provider_reads": artifact["planned_provider_reads"],
        "model_calls_authorized": artifact["model_calls_authorized"],
        "planned_model_calls": artifact["planned_model_calls"],
        "final_decision_created": artifact["final_decision_created"],
        "b5_handoff_created": artifact["b5_handoff_created"],
        "next_gate": artifact["next_gate"],
        "artifact_hash": artifact["artifact_hash"],
        "output_path": str(args.output),
    }
    print(json.dumps(public, indent=2, sort_keys=True, ensure_ascii=False))
    print("MODEL_CALLS=0")
    print("PROVIDER_READS=0")
    print("BROKER_WRITES=0")
    print("ALPACA_ORDERS=0")
    print("LIVE_MONEY=PROHIBITED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
