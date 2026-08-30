from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from aic.research.reopen_remaining_gaps_evidence_plan import (
    RemainingGapEvidencePlanError,
    build_remaining_gaps_evidence_plan,
)


DEFAULT_SCOPE = Path(".aic-runtime/b3_reopen_remaining_gaps_scope_zero_call_v0_1.json")
DEFAULT_CLAIM_RECON = Path(".aic-runtime/b3_reopen_bounded_news_claim_reconciliation_zero_call_v0_1.json")
DEFAULT_JUDGE = Path(".aic-runtime/b4_judge_production_result_v0_1.json")
DEFAULT_RETRIEVAL = Path(".aic-runtime/b3_retrieval_batch.json")
DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_RUNTIME_ROOT = Path(".aic-runtime")
DEFAULT_OUTPUT = Path(".aic-runtime/b3_reopen_remaining_gaps_evidence_plan_zero_call_v0_1.json")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the B3 reopen valuation/portfolio evidence plan with zero provider/model calls.")
    parser.add_argument("--scope", type=Path, default=DEFAULT_SCOPE)
    parser.add_argument("--claim-reconciliation", type=Path, default=DEFAULT_CLAIM_RECON)
    parser.add_argument("--judge-result", type=Path, default=DEFAULT_JUDGE)
    parser.add_argument("--retrieval", type=Path, default=DEFAULT_RETRIEVAL)
    parser.add_argument("--handoff", type=Path, default=DEFAULT_HANDOFF)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "OPENAI_API_KEY": "", "APCA_API_KEY_ID": "", "APCA_API_SECRET_KEY": ""},
    )
    return result.stdout.strip()


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise RemainingGapEvidencePlanError(f"output already exists: {path}")
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


def main() -> int:
    args = _args()
    try:
        artifact = build_remaining_gaps_evidence_plan(
            code_commit_sha=_head(),
            scope_path=args.scope,
            claim_reconciliation_path=args.claim_reconciliation,
            judge_result_path=args.judge_result,
            retrieval_path=args.retrieval,
            handoff_path=args.handoff,
            runtime_root=args.runtime_root,
        )
        _atomic_write(args.output, artifact)
    except (RemainingGapEvidencePlanError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    public = {
        "artifact_version": artifact["artifact_version"],
        "status": artifact["status"],
        "code_commit_sha": artifact["code_commit_sha"],
        "source_remaining_gaps_scope_hash": artifact["source_remaining_gaps_scope_hash"],
        "source_production_judge_result_hash": artifact["source_production_judge_result_hash"],
        "active_reopen_reason_codes": artifact["active_reopen_reason_codes"],
        "target_candidates": artifact["target_candidates"],
        "non_target_candidate_ids": artifact["non_target_candidate_ids"],
        "target_scopes": artifact["target_scopes"],
        "valuation_evidence_plan": artifact["valuation_evidence_plan"],
        "portfolio_interaction_evidence_plan": artifact["portfolio_interaction_evidence_plan"],
        "provider_reads_authorized": artifact["provider_reads_authorized"],
        "planned_provider_reads_at_this_gate": artifact["planned_provider_reads_at_this_gate"],
        "model_calls_authorized": artifact["model_calls_authorized"],
        "planned_model_calls_at_this_gate": artifact["planned_model_calls_at_this_gate"],
        "broker_writes": artifact["broker_writes"],
        "alpaca_orders": artifact["alpaca_orders"],
        "live_money": artifact["live_money"],
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
