"""Explicit paid current-lineage Judge entrypoint; inert without its flag/approval."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from aic.council import post_research_reopen_judge_current_v01 as judge
from aic.research.runtime import StdlibResponsesTransport, load_openai_api_key

ROOT = Path(".aic-runtime")


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"STOP: object required: {path}")
    return value


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-paid-judge", action="store_true")
    parser.add_argument("--owner-approval", type=Path, required=True)
    parser.add_argument("--b3-closure", type=Path, default=ROOT / "b3_research_reopen_final_competition_closure_zero_call_v0_1.json")
    parser.add_argument("--initial-freeze", type=Path, default=ROOT / "b4_post_research_reopen_initial_recovery_resume_council_freeze_v0_1.json")
    parser.add_argument("--initial-cost", type=Path, default=ROOT / "b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json")
    parser.add_argument("--rebuttal-freeze", type=Path, default=ROOT / "b4_post_research_reopen_rebuttal_council_freeze_v0_1.json")
    parser.add_argument("--preflight", type=Path, default=ROOT / "b4_post_research_reopen_current_judge_preflight_zero_call_v0_1.json")
    parser.add_argument("--readiness", type=Path, default=ROOT / "b4_post_research_reopen_current_judge_readiness_zero_call_v0_1.json")
    parser.add_argument("--selection", type=Path, default=ROOT / "b4_judge_selected_model_authority_v0_1.json")
    parser.add_argument("--eval", type=Path, default=ROOT / "b4_judge_model_eval_v0_1.json")
    parser.add_argument("--receipts", type=Path, default=ROOT / "b4_judge_model_eval_paid_receipts_v0_1.jsonl")
    parser.add_argument("--pricing", type=Path, default=Path("config/event/openai_text_pricing_2026_08_30.json"))
    parser.add_argument("--historical-preflight", type=Path, default=ROOT / "b4_reopen_judge_production_request_preflight_v0_2.json")
    parser.add_argument("--ledger", type=Path, default=ROOT / "b4_post_research_reopen_current_judge_paid_dispatch_ledger_v0_1.json")
    parser.add_argument("--raw", type=Path, default=ROOT / "b4_post_research_reopen_current_judge_raw_response_v0_1.json")
    parser.add_argument("--result", type=Path, default=ROOT / "b4_post_research_reopen_current_judge_council_freeze_v0_1.json")
    args = parser.parse_args()
    if not args.execute_paid_judge:
        raise SystemExit("STOP: --execute-paid-judge is required")
    closure, initial, cost, rebuttal, preflight, readiness, selection, evaluation, pricing, approval, old = (_read(path) for path in (args.b3_closure, args.initial_freeze, args.initial_cost, args.rebuttal_freeze, args.preflight, args.readiness, args.selection, args.eval, args.pricing, args.owner_approval, args.historical_preflight))
    receipts = [json.loads(line) for line in args.receipts.read_text(encoding="utf-8").splitlines() if line]
    entry = judge.build_current_judge_entry(code_commit_sha=_git("rev-parse", "HEAD"), closure=closure, initial_freeze=initial, initial_cost=cost, rebuttal_freeze=rebuttal)
    def factory():
        key = load_openai_api_key(); transport = StdlibResponsesTransport()
        return lambda payload: transport.post(payload=payload, api_key=key)
    result = judge.execute_paid_judge(execute_paid_judge=True, branch=_git("branch", "--show-current"), code_commit_sha=_git("rev-parse", "HEAD"), worktree_clean=not bool(_git("status", "--porcelain")), closure=closure, initial_freeze=initial, initial_cost=cost, rebuttal_freeze=rebuttal, entry=entry, preflight=preflight, readiness=readiness, selection=selection, eval_artifact=evaluation, receipts=receipts, pricing=pricing, historical_request_hashes=[old["request_hash"]], approval=approval, ledger_path=args.ledger, raw_path=args.raw, result_path=args.result, transport_factory=factory)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
