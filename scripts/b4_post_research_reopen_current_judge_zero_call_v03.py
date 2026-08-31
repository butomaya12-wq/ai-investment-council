"""Create exclusive v0.3 evidence-complete Judge zero-call artifacts."""
from __future__ import annotations
import argparse, json, subprocess
from pathlib import Path
from aic.council import post_research_reopen_judge_current_v03 as judge

ROOT = Path(".aic-runtime")
def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict): raise SystemExit(f"STOP: object required: {path}")
    return value
def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()
def _write(path: Path, value: dict) -> None:
    if path.exists(): raise SystemExit(f"STOP: exclusive output exists: {path}")
    with path.open("x", encoding="utf-8") as handle: json.dump(value, handle, sort_keys=True, indent=2); handle.write("\n")
def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--preflight-output",type=Path,default=ROOT/"b4_post_research_reopen_current_judge_preflight_zero_call_v0_3.json"); p.add_argument("--readiness-output",type=Path,default=ROOT/"b4_post_research_reopen_current_judge_readiness_zero_call_v0_3.json"); args=p.parse_args()
    if _git("branch","--show-current") != "hackathon/alpaca-2026" or _git("status","--porcelain"): raise SystemExit("STOP: exact clean hackathon/alpaca-2026 checkout required")
    rd=lambda name: _read(ROOT/name)
    closure,residual,gaps,initial,cost,rebuttal,selection,evaluation,pricing,old=(rd("b3_research_reopen_final_competition_closure_zero_call_v0_1.json"),rd("b3_research_reopen_residual_external_read_plan_zero_call_v0_1.json"),rd("b3_reopen_remaining_gaps_closure_zero_call_v0_2.json"),rd("b4_post_research_reopen_initial_recovery_resume_council_freeze_v0_1.json"),rd("b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json"),rd("b4_post_research_reopen_rebuttal_council_freeze_v0_1.json"),rd("b4_judge_selected_model_authority_v0_1.json"),rd("b4_judge_model_eval_v0_1.json"),_read(Path("config/event/openai_text_pricing_2026_08_30.json")),rd("b4_reopen_judge_production_request_preflight_v0_2.json"))
    receipts=[json.loads(line) for line in (ROOT/"b4_judge_model_eval_paid_receipts_v0_1.jsonl").read_text(encoding="utf-8").splitlines() if line]
    head=_git("rev-parse","HEAD"); entry=judge.build_entry(code_commit_sha=head,closure=closure,residual_plan=residual,remaining_gaps_closure=gaps,initial_freeze=initial,initial_cost=cost,rebuttal_freeze=rebuttal); context=judge.build_context(entry=entry,closure=closure,residual_plan=residual,remaining_gaps_closure=gaps,initial_cost=cost,initial_freeze=initial,rebuttal_freeze=rebuttal,selection=selection)
    inputs=dict(entry=entry,context=context,closure=closure,residual_plan=residual,remaining_gaps_closure=gaps,initial_freeze=initial,initial_cost=cost,rebuttal_freeze=rebuttal,selection=selection,eval_artifact=evaluation,receipts=receipts,pricing=pricing,historical_request_hashes=[old["request_hash"],"8eedd3e58e95d97bf7e29e368fe199c3c681f17876feeabb22e6fbd8cc1c20d7","72bb6db79203a54b20b47452acfbd3de4fc42385ced6330ac8f75cf18419c628"])
    preflight=judge.build_preflight(code_commit_sha=head,**inputs); readiness=judge.build_readiness(code_commit_sha=head,preflight=preflight,**inputs); _write(args.preflight_output,preflight); _write(args.readiness_output,readiness)
    print(json.dumps({"preflight_hash":preflight["artifact_hash"],"readiness_hash":readiness["artifact_hash"]},sort_keys=True)); print("MODEL_CALLS=0\nPROVIDER_READS=0\nBROKER_WRITES=0\nALPACA_ORDERS=0\nCOST_USD_THIS_STEP=0")
    return 0
if __name__ == "__main__": raise SystemExit(main())
