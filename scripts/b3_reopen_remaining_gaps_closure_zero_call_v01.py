from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from aic.research.reopen_remaining_gaps_closure import (
    RemainingGapsClosureError,
    build_remaining_gaps_closure,
)


DEFAULT_RECOVERY = Path(".aic-runtime/b3_reopen_minimal_external_read_recovery_zero_call_v0_1.json")
DEFAULT_CLAIM_RECON = Path(".aic-runtime/b3_reopen_bounded_news_claim_reconciliation_zero_call_v0_1.json")
DEFAULT_PLAN = Path(".aic-runtime/b3_reopen_remaining_gaps_evidence_plan_zero_call_v0_1.json")
DEFAULT_SCOPE = Path(".aic-runtime/b3_reopen_remaining_gaps_scope_zero_call_v0_1.json")
DEFAULT_PRIMITIVES = Path(".aic-runtime/b3_reopen_local_valuation_and_portfolio_primitives_zero_call_v0_1.json")
DEFAULT_SELECTED = Path(".aic-runtime/b3_selected_model_reconciliation.json")
DEFAULT_JUDGE = Path(".aic-runtime/b4_judge_production_result_v0_1.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b3_reopen_remaining_gaps_closure_zero_call_v0_1.json")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Close the B3 reopen research gaps with an additive zero-call evidence overlay."
    )
    parser.add_argument("--recovery", type=Path, default=DEFAULT_RECOVERY)
    parser.add_argument("--claim-reconciliation", type=Path, default=DEFAULT_CLAIM_RECON)
    parser.add_argument("--evidence-plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--scope", type=Path, default=DEFAULT_SCOPE)
    parser.add_argument("--primitives", type=Path, default=DEFAULT_PRIMITIVES)
    parser.add_argument("--selected-reconciliation", type=Path, default=DEFAULT_SELECTED)
    parser.add_argument("--judge-result", type=Path, default=DEFAULT_JUDGE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "OPENAI_API_KEY": "",
            "APCA_API_KEY_ID": "",
            "APCA_API_SECRET_KEY": "",
            "ALPACA_LIVE_TRADE": "",
        },
    )
    return completed.stdout.strip()


def _write_exclusive(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise RemainingGapsClosureError(f"output already exists: {path}")
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


def main() -> int:
    args = _args()
    try:
        artifact = build_remaining_gaps_closure(
            code_commit_sha=_head(),
            recovery_path=args.recovery,
            claim_reconciliation_path=args.claim_reconciliation,
            evidence_plan_path=args.evidence_plan,
            scope_path=args.scope,
            primitives_path=args.primitives,
            selected_reconciliation_path=args.selected_reconciliation,
            judge_result_path=args.judge_result,
        )
        _write_exclusive(args.output, artifact)
    except (RemainingGapsClosureError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    public = {
        "artifact_version": artifact["artifact_version"],
        "status": artifact["status"],
        "code_commit_sha": artifact["code_commit_sha"],
        "source_recovery_hash": artifact["source_recovery_hash"],
        "source_claim_reconciliation_hash": artifact["source_claim_reconciliation_hash"],
        "source_selected_b3_reconciliation_hash": artifact["source_selected_b3_reconciliation_hash"],
        "source_production_judge_result_hash": artifact["source_production_judge_result_hash"],
        "source_reopen_request_id": artifact["source_reopen_request_id"],
        "source_reopen_request_hash": artifact["source_reopen_request_hash"],
        "legacy_material_claims": artifact["legacy_material_claims"],
        "legacy_frozen_artifacts_mutated": artifact["legacy_frozen_artifacts_mutated"],
        "reopen_overlay_is_additive": artifact["reopen_overlay_is_additive"],
        "supplemental_evidence_units": artifact["supplemental_evidence_units"],
        "supplemental_claims": artifact["supplemental_claims"],
        "judge_condition_closure": artifact["judge_condition_closure"],
        "all_judge_conditions_satisfied": artifact["all_judge_conditions_satisfied"],
        "closed_reopen_reason_codes": artifact["closed_reopen_reason_codes"],
        "remaining_reopen_reason_codes": artifact["remaining_reopen_reason_codes"],
        "research_reopen_request_satisfied": artifact["research_reopen_request_satisfied"],
        "overall_research_reopen_complete": artifact["overall_research_reopen_complete"],
        "historical_provider_reads_reused": artifact["historical_provider_reads_reused"],
        "new_provider_dispatch_attempts": artifact["new_provider_dispatch_attempts"],
        "new_provider_reads": artifact["new_provider_reads"],
        "model_calls": artifact["model_calls"],
        "broker_writes": artifact["broker_writes"],
        "alpaca_orders": artifact["alpaca_orders"],
        "live_money": artifact["live_money"],
        "final_decision_created": artifact["final_decision_created"],
        "b5_handoff_created": artifact["b5_handoff_created"],
        "historical_production_judge_rerun_authorized": artifact["historical_production_judge_rerun_authorized"],
        "next_gate": artifact["next_gate"],
        "artifact_hash": artifact["artifact_hash"],
        "output_path": str(args.output),
    }
    print(json.dumps(public, ensure_ascii=False, sort_keys=True, indent=2))
    print("NEW_PROVIDER_DISPATCH_ATTEMPTS=0")
    print("NEW_PROVIDER_READS=0")
    print("MODEL_CALLS=0")
    print("BROKER_WRITES=0")
    print("ALPACA_ORDERS=0")
    print("LIVE_MONEY=PROHIBITED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
