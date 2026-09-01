"""Evaluate real current B4 production evidence against the positive INVEST gate.

Zero-call only: no model calls, provider reads, broker writes, or Alpaca orders.
Historical v0.3 artifacts are read as inputs and are never mutated.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from aic.council import post_research_reopen_judge_current_v03 as v03
from aic.council import post_research_reopen_judge_current_v04 as v04


ROOT = Path(".aic-runtime")
ALLOWED_BRANCHES = {
    "hackathon/alpaca-2026",
    "hackathon/b4-positive-invest-gate",
}
HISTORICAL_REQUEST_HASHES = [
    "8eedd3e58e95d97bf7e29e368fe199c3c681f17876feeabb22e6fbd8cc1c20d7",
    "72bb6db79203a54b20b47452acfbd3de4fc42385ced6330ac8f75cf18419c628",
]


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"STOP: object required: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise SystemExit(f"STOP: object row required: {path}")
        rows.append(value)
    return rows


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write(path: Path, value: dict) -> None:
    if path.exists():
        raise SystemExit(f"STOP: exclusive output exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, indent=2)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--gate-output",
        type=Path,
        default=ROOT / "b4_post_research_reopen_current_invest_eligibility_zero_call_v0_4.json",
    )
    parser.add_argument(
        "--entry-output",
        type=Path,
        default=ROOT / "b4_post_research_reopen_current_judge_entry_zero_call_v0_4.json",
    )
    parser.add_argument(
        "--preflight-output",
        type=Path,
        default=ROOT / "b4_post_research_reopen_current_judge_preflight_zero_call_v0_4.json",
    )
    parser.add_argument(
        "--readiness-output",
        type=Path,
        default=ROOT / "b4_post_research_reopen_current_judge_readiness_zero_call_v0_4.json",
    )
    args = parser.parse_args()

    branch = _git("branch", "--show-current")
    if branch not in ALLOWED_BRANCHES or _git("status", "--porcelain"):
        raise SystemExit(
            "STOP: exact clean hackathon branch checkout required for v0.4 zero-call evaluation"
        )

    rd = lambda name: _read(ROOT / name)
    closure = rd("b3_research_reopen_final_competition_closure_zero_call_v0_1.json")
    residual = rd("b3_research_reopen_residual_external_read_plan_zero_call_v0_1.json")
    gaps = rd("b3_reopen_remaining_gaps_closure_zero_call_v0_2.json")
    initial = rd("b4_post_research_reopen_initial_recovery_resume_council_freeze_v0_1.json")
    cost = rd("b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json")
    rebuttal = rd("b4_post_research_reopen_rebuttal_council_freeze_v0_1.json")
    selection = rd("b4_judge_selected_model_authority_v0_1.json")
    evaluation = rd("b4_judge_model_eval_v0_1.json")
    receipts = _read_jsonl(ROOT / "b4_judge_model_eval_paid_receipts_v0_1.jsonl")
    pricing = _read(Path("config/event/openai_text_pricing_2026_08_30.json"))
    old = rd("b4_reopen_judge_production_request_preflight_v0_2.json")

    selection_hash = v03.verify_selection(
        selection,
        eval_artifact=evaluation,
        receipts=receipts,
    )

    head = _git("rev-parse", "HEAD")
    source_entry = v03.build_entry(
        code_commit_sha=head,
        closure=closure,
        residual_plan=residual,
        remaining_gaps_closure=gaps,
        initial_freeze=initial,
        initial_cost=cost,
        rebuttal_freeze=rebuttal,
    )
    source_context = v03.build_context(
        entry=source_entry,
        closure=closure,
        residual_plan=residual,
        remaining_gaps_closure=gaps,
        initial_cost=cost,
        initial_freeze=initial,
        rebuttal_freeze=rebuttal,
        selection=selection,
    )
    if (
        source_context.model_input.get("source_lineage", {}).get(
            "judge_selection_authority_hash"
        )
        != selection_hash
    ):
        raise SystemExit("STOP: source context Judge selection lineage drift")

    gate = v04.build_gate(
        source_entry=source_entry,
        source_context=source_context,
    )
    entry = v04.build_entry(
        code_commit_sha=head,
        source_entry=source_entry,
        source_context=source_context,
        gate=gate,
    )
    context = v04.build_context(
        entry=entry,
        source_entry=source_entry,
        source_context=source_context,
        gate=gate,
    )
    historical = [old["request_hash"], *HISTORICAL_REQUEST_HASHES]
    preflight = v04.build_preflight(
        code_commit_sha=head,
        entry=entry,
        context=context,
        source_entry=source_entry,
        source_context=source_context,
        gate=gate,
        pricing=pricing,
        historical_request_hashes=historical,
    )
    readiness = v04.build_readiness(
        code_commit_sha=head,
        preflight=preflight,
        entry=entry,
        context=context,
        source_entry=source_entry,
        source_context=source_context,
        gate=gate,
        pricing=pricing,
        historical_request_hashes=historical,
    )

    _write(args.gate_output, gate)
    _write(args.entry_output, entry)
    _write(args.preflight_output, preflight)
    _write(args.readiness_output, readiness)

    summary = {
        "policy_version": gate["policy_version"],
        "policy_hash": gate["policy_hash"],
        "evaluation_hash": gate["artifact_hash"],
        "judge_selection_authority_hash": selection_hash,
        "candidate_results": gate["candidate_results"],
        "invest_eligible_candidates": gate["invest_eligible_candidates"],
        "invest_blocked_candidates": gate["invest_blocked_candidates"],
        "allowed_judge_outcomes": gate["allowed_judge_outcomes"],
        "judge_max_cost_usd": preflight["judge_max_cost_usd"],
        "owner_approval_required_before_paid_judge": True,
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    print("MODEL_CALLS=0")
    print("PROVIDER_READS=0")
    print("BROKER_WRITES=0")
    print("ALPACA_ORDERS=0")
    print("COST_USD_THIS_STEP=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
