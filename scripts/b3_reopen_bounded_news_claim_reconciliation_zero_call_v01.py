from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from aic.research.reopen_claim_reconciliation import (
    PASS_STATUS,
    ReopenClaimReconciliationError,
    build_claim_reconciliation,
)


DEFAULT_BOUNDED_REVIEW = Path(".aic-runtime/b3_reopen_bounded_news_zero_call_v0_1.json")
DEFAULT_B3_RECONCILIATION = Path(".aic-runtime/b3_selected_model_reconciliation.json")
DEFAULT_B4_INPUT_FREEZE = Path(".aic-runtime/b4_council_input_freeze.json")
DEFAULT_INITIAL_FREEZE = Path(".aic-runtime/b4_initial_council_freeze_v0_5.json")
DEFAULT_REBUTTAL_FREEZE = Path(".aic-runtime/b4_rebuttal_council_freeze_v0_1.json")
DEFAULT_JUDGE_RESULT = Path(".aic-runtime/b4_judge_production_result_v0_1.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b3_reopen_bounded_news_claim_reconciliation_zero_call_v0_1.json")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Reconcile the closed bounded-news source gap through immutable B3/B4/Judge "
            "claim lineage using local artifacts only. No provider/model/broker surface exists."
        )
    )
    parser.add_argument("--bounded-review", type=Path, default=DEFAULT_BOUNDED_REVIEW)
    parser.add_argument("--b3-reconciliation", type=Path, default=DEFAULT_B3_RECONCILIATION)
    parser.add_argument("--b4-input-freeze", type=Path, default=DEFAULT_B4_INPUT_FREEZE)
    parser.add_argument("--initial-freeze", type=Path, default=DEFAULT_INITIAL_FREEZE)
    parser.add_argument("--rebuttal-freeze", type=Path, default=DEFAULT_REBUTTAL_FREEZE)
    parser.add_argument("--judge-result", type=Path, default=DEFAULT_JUDGE_RESULT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _git_head() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReopenClaimReconciliationError("unable to resolve local git HEAD") from exc
    head = result.stdout.strip()
    if len(head) != 40 or any(ch not in "0123456789abcdef" for ch in head):
        raise ReopenClaimReconciliationError("local git HEAD is not canonical SHA")
    return head


def _atomic_write(path: Path, payload: dict) -> None:
    if path.exists():
        raise ReopenClaimReconciliationError(f"refusing to overwrite existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        temporary.write_text(data, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _public_summary(payload: dict, *, output: Path) -> dict:
    return {
        "artifact_version": payload["artifact_version"],
        "status": payload["status"],
        "code_commit_sha": payload["code_commit_sha"],
        "source_bounded_news_review_hash": payload["source_bounded_news_review_hash"],
        "source_b3_selected_model_reconciliation_hash": payload["source_b3_selected_model_reconciliation_hash"],
        "source_production_judge_result_hash": payload["source_production_judge_result_hash"],
        "superseded_source_gap_ref": payload["superseded_source_gap_ref"],
        "closure_evidence_ref": payload["closure_evidence_ref"],
        "candidate_reconciliations": payload["candidate_reconciliations"],
        "legacy_unknown_ref_occurrence_counts": payload["legacy_unknown_ref_occurrence_counts"],
        "claim_reconciliation": payload["claim_reconciliation"],
        "closed_reopen_reason_codes": payload["closed_reopen_reason_codes"],
        "remaining_reopen_reason_codes": payload["remaining_reopen_reason_codes"],
        "news_gap_closed": payload["news_gap_closed"],
        "overall_research_reopen_complete": payload["overall_research_reopen_complete"],
        "legacy_frozen_artifacts_mutated": payload["legacy_frozen_artifacts_mutated"],
        "final_decision_created": payload["final_decision_created"],
        "b5_handoff_created": payload["b5_handoff_created"],
        "new_provider_reads": payload["new_provider_reads"],
        "model_calls": payload["model_calls"],
        "broker_writes": payload["broker_writes"],
        "alpaca_orders": payload["alpaca_orders"],
        "live_money": payload["live_money"],
        "next_gate": payload["next_gate"],
        "artifact_hash": payload["artifact_hash"],
        "output_path": str(output),
    }


def main() -> int:
    args = _args()
    try:
        artifact = build_claim_reconciliation(
            code_commit_sha=_git_head(),
            bounded_review_path=args.bounded_review,
            b3_reconciliation_path=args.b3_reconciliation,
            b4_input_freeze_path=args.b4_input_freeze,
            initial_freeze_path=args.initial_freeze,
            rebuttal_freeze_path=args.rebuttal_freeze,
            judge_result_path=args.judge_result,
        )
        if artifact.get("status") != PASS_STATUS:
            raise ReopenClaimReconciliationError("claim reconciliation did not produce PASS")
        _atomic_write(args.output, artifact)
    except ReopenClaimReconciliationError as exc:
        print(
            json.dumps(
                {
                    "status": "B3_REOPEN_BOUNDED_NEWS_CLAIM_RECONCILIATION_ZERO_CALL_BLOCKED",
                    "error_class": type(exc).__name__,
                    "error": str(exc),
                    "new_provider_reads": 0,
                    "model_calls": 0,
                    "broker_writes": 0,
                    "alpaca_orders": 0,
                    "live_money": "PROHIBITED",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 3

    print(json.dumps(_public_summary(artifact, output=args.output), indent=2, sort_keys=True))
    print("MODEL_CALLS=0")
    print("PROVIDER_READS=0")
    print("BROKER_WRITES=0")
    print("ALPACA_ORDERS=0")
    print("LIVE_MONEY=PROHIBITED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
