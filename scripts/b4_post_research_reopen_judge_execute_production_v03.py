"""Explicit future paid v0.3 Judge entrypoint; never invoked by zero-call work."""
from __future__ import annotations
import argparse, json, subprocess
from pathlib import Path
from aic.council import post_research_reopen_judge_current_v03 as judge
from aic.research.runtime import StdlibResponsesTransport, load_openai_api_key
ROOT=Path(".aic-runtime")
def _read(path: Path) -> dict:
    value=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value,dict): raise SystemExit(f"STOP: object required: {path}")
    return value
def _git(*args: str)->str: return subprocess.run(["git",*args],check=True,capture_output=True,text=True).stdout.strip()
def main()->int:
    p=argparse.ArgumentParser();p.add_argument("--execute-paid-judge",action="store_true");p.add_argument("--owner-approval",type=Path,required=True);p.add_argument("--preflight",type=Path,default=ROOT/"b4_post_research_reopen_current_judge_preflight_zero_call_v0_3.json");p.add_argument("--readiness",type=Path,default=ROOT/"b4_post_research_reopen_current_judge_readiness_zero_call_v0_3.json");p.add_argument("--ledger",type=Path,default=ROOT/"b4_post_research_reopen_current_judge_paid_dispatch_ledger_v0_3.json");p.add_argument("--raw",type=Path,default=ROOT/"b4_post_research_reopen_current_judge_raw_response_v0_3.json");p.add_argument("--result",type=Path,default=ROOT/"b4_post_research_reopen_current_judge_council_freeze_v0_3.json");a=p.parse_args()
    if not a.execute_paid_judge: raise SystemExit("STOP: --execute-paid-judge is required")
    rd=lambda name:_read(ROOT/name); closure,residual,gaps,initial,cost,rebuttal,selection,evaluation,pricing,old=(rd("b3_research_reopen_final_competition_closure_zero_call_v0_1.json"),rd("b3_research_reopen_residual_external_read_plan_zero_call_v0_1.json"),rd("b3_reopen_remaining_gaps_closure_zero_call_v0_2.json"),rd("b4_post_research_reopen_initial_recovery_resume_council_freeze_v0_1.json"),rd("b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json"),rd("b4_post_research_reopen_rebuttal_council_freeze_v0_1.json"),rd("b4_judge_selected_model_authority_v0_1.json"),rd("b4_judge_model_eval_v0_1.json"),_read(Path("config/event/openai_text_pricing_2026_08_30.json")),rd("b4_reopen_judge_production_request_preflight_v0_2.json"))
    receipts=[json.loads(line) for line in (ROOT/"b4_judge_model_eval_paid_receipts_v0_1.jsonl").read_text(encoding="utf-8").splitlines() if line]; head=_git("rev-parse","HEAD");entry=judge.build_entry(code_commit_sha=head,closure=closure,residual_plan=residual,remaining_gaps_closure=gaps,initial_freeze=initial,initial_cost=cost,rebuttal_freeze=rebuttal);context=judge.build_context(entry=entry,closure=closure,residual_plan=residual,remaining_gaps_closure=gaps,initial_cost=cost,initial_freeze=initial,rebuttal_freeze=rebuttal,selection=selection); inputs=dict(entry=entry,context=context,closure=closure,residual_plan=residual,remaining_gaps_closure=gaps,initial_freeze=initial,initial_cost=cost,rebuttal_freeze=rebuttal,selection=selection,eval_artifact=evaluation,receipts=receipts,pricing=pricing,historical_request_hashes=[old["request_hash"],"8eedd3e58e95d97bf7e29e368fe199c3c681f17876feeabb22e6fbd8cc1c20d7","72bb6db79203a54b20b47452acfbd3de4fc42385ced6330ac8f75cf18419c628"])
    def factory():
        key=load_openai_api_key(); transport=StdlibResponsesTransport(); return lambda payload:transport.post(payload=payload,api_key=key)
    result=judge.execute_paid(execute_paid_judge=True,branch=_git("branch","--show-current"),code_commit_sha=head,worktree_clean=not bool(_git("status","--porcelain")),preflight=_read(a.preflight),readiness=_read(a.readiness),approval=_read(a.owner_approval),ledger_path=a.ledger,raw_path=a.raw,result_path=a.result,transport_factory=factory,**inputs);print(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2));return 0
if __name__ == "__main__": raise SystemExit(main())
