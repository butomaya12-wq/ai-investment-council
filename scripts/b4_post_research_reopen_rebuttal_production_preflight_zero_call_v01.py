"""Create only the current-lineage Rebuttal preflight and readiness evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

from aic.council import post_research_reopen_rebuttal_production_v01 as rebuttal


ROOT = Path(".aic-runtime")


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"STOP: object required: {path}")
    return value


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def _write_exclusive(path: Path, payload: dict) -> None:
    if path.exists():
        raise SystemExit(f"STOP: exclusive output exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--initial-freeze", type=Path, default=ROOT / "b4_post_research_reopen_initial_recovery_resume_council_freeze_v0_1.json")
    parser.add_argument("--initial-cost", type=Path, default=ROOT / "b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json")
    parser.add_argument("--pricing", type=Path, default=Path("config/event/openai_text_pricing_2026_08_30.json"))
    parser.add_argument("--selection", type=Path, default=ROOT / "b4_rebuttal_selected_model_authority_v0_2.json")
    parser.add_argument("--eval", type=Path, default=ROOT / "b4_rebuttal_model_eval_v0_1.json")
    parser.add_argument("--receipts", type=Path, default=ROOT / "b4_rebuttal_model_eval_paid_receipts_v0_1.jsonl")
    parser.add_argument("--historical-preflight", type=Path, default=ROOT / "b4_reopen_rebuttal_production_cost_preflight_zero_call_v0_2.json")
    parser.add_argument("--preflight-output", type=Path, default=ROOT / "b4_post_research_reopen_rebuttal_production_preflight_zero_call_v0_1.json")
    parser.add_argument("--readiness-output", type=Path, default=ROOT / "b4_post_research_reopen_rebuttal_production_readiness_zero_call_v0_1.json")
    args = parser.parse_args()
    if _git("branch", "--show-current") != "hackathon/alpaca-2026" or _git("status", "--porcelain"):
        raise SystemExit("STOP: exact clean hackathon/alpaca-2026 checkout required")
    initial_freeze, initial_cost, pricing, selection, eval_artifact = (_read(path) for path in (args.initial_freeze, args.initial_cost, args.pricing, args.selection, args.eval))
    receipts = [json.loads(line) for line in args.receipts.read_text(encoding="utf-8").splitlines() if line]
    historical = _read(args.historical_preflight)
    historical_hashes = [row["request_hash"] for row in historical.get("request_rows", []) if isinstance(row, dict)]
    head = _git("rev-parse", "HEAD")
    preflight = rebuttal.build_current_rebuttal_preflight(code_commit_sha=head, initial_freeze=initial_freeze, initial_cost=initial_cost, pricing=pricing, selection_authority=selection, eval_artifact=eval_artifact, receipts=receipts, historical_request_hashes=historical_hashes)
    readiness = rebuttal.build_final_rebuttal_readiness(code_commit_sha=head, preflight=preflight, initial_freeze=initial_freeze, initial_cost=initial_cost, pricing=pricing, selection_authority=selection, eval_artifact=eval_artifact, receipts=receipts, historical_request_hashes=historical_hashes)
    _write_exclusive(args.preflight_output, preflight); _write_exclusive(args.readiness_output, readiness)
    print(json.dumps({"preflight_hash": preflight["artifact_hash"], "readiness_hash": readiness["artifact_hash"]}, sort_keys=True))
    print("MODEL_CALLS=0\nPROVIDER_READS=0\nBROKER_WRITES=0\nALPACA_ORDERS=0\nCOST_USD_THIS_STEP=0\nLIVE_MONEY=PROHIBITED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
