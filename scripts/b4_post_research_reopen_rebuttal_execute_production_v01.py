"""Explicit paid-entrypoint; do not use without a newly materialized approval."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from aic.council import post_research_reopen_rebuttal_production_v01 as rebuttal
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
    parser.add_argument("--execute-paid-rebuttal", action="store_true")
    parser.add_argument("--owner-approval", type=Path, required=True)
    parser.add_argument("--initial-freeze", type=Path, default=ROOT / "b4_post_research_reopen_initial_recovery_resume_council_freeze_v0_1.json")
    parser.add_argument("--initial-cost", type=Path, default=ROOT / "b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json")
    parser.add_argument("--pricing", type=Path, default=Path("config/event/openai_text_pricing_2026_08_30.json"))
    parser.add_argument("--selection", type=Path, default=ROOT / "b4_rebuttal_selected_model_authority_v0_2.json")
    parser.add_argument("--eval", type=Path, default=ROOT / "b4_rebuttal_model_eval_v0_1.json")
    parser.add_argument("--receipts", type=Path, default=ROOT / "b4_rebuttal_model_eval_paid_receipts_v0_1.jsonl")
    parser.add_argument("--historical-preflight", type=Path, default=ROOT / "b4_reopen_rebuttal_production_cost_preflight_zero_call_v0_2.json")
    parser.add_argument("--preflight", type=Path, default=ROOT / "b4_post_research_reopen_rebuttal_production_preflight_zero_call_v0_1.json")
    parser.add_argument("--readiness", type=Path, default=ROOT / "b4_post_research_reopen_rebuttal_production_readiness_zero_call_v0_1.json")
    parser.add_argument("--ledger", type=Path, default=ROOT / "b4_post_research_reopen_rebuttal_paid_dispatch_ledger_v0_1.json")
    parser.add_argument("--raw-dir", type=Path, default=ROOT / "b4_post_research_reopen_rebuttal_paid_raw_responses_v0_1")
    parser.add_argument("--result", type=Path, default=ROOT / "b4_post_research_reopen_rebuttal_council_freeze_v0_1.json")
    args = parser.parse_args()
    if not args.execute_paid_rebuttal:
        raise SystemExit("STOP: --execute-paid-rebuttal is required")
    initial_freeze, initial_cost, pricing, selection, eval_artifact, preflight, readiness, approval = (_read(path) for path in (args.initial_freeze, args.initial_cost, args.pricing, args.selection, args.eval, args.preflight, args.readiness, args.owner_approval))
    receipts = [json.loads(line) for line in args.receipts.read_text(encoding="utf-8").splitlines() if line]
    historical = _read(args.historical_preflight)
    historical_hashes = [row["request_hash"] for row in historical.get("request_rows", []) if isinstance(row, dict)]
    def factory():
        api_key = load_openai_api_key(); transport = StdlibResponsesTransport()
        return lambda payload: transport.post(payload=payload, api_key=api_key)
    result = rebuttal.execute_paid_rebuttal(execute_paid_rebuttal=True, branch=_git("branch", "--show-current"), code_commit_sha=_git("rev-parse", "HEAD"), worktree_clean=not bool(_git("status", "--porcelain")), preflight=preflight, readiness=readiness, initial_freeze=initial_freeze, initial_cost=initial_cost, pricing=pricing, selection_authority=selection, eval_artifact=eval_artifact, receipts=receipts, historical_request_hashes=historical_hashes, approval=approval, ledger_path=args.ledger, raw_dir=args.raw_dir, result_path=args.result, transport_factory=factory)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
