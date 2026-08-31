from __future__ import annotations
import argparse,json,subprocess,sys
from pathlib import Path
from aic.council.post_research_reopen_initial_recovery_resume_v01 import execute_paid_recovery_resume
def read(p): return json.loads(Path(p).read_text())
def git(*a): return subprocess.run(['git',*a],check=True,capture_output=True,text=True).stdout.strip()
def transport():
 from aic.research.runtime import StdlibResponsesTransport,load_openai_api_key
 c,k=StdlibResponsesTransport(),load_openai_api_key(); return lambda p:c.post(payload=p,api_key=k)
def main():
 p=argparse.ArgumentParser(); p.add_argument('--execute-paid-recovery-resume',action='store_true');p.add_argument('--owner-approval',type=Path,required=False); a=p.parse_args()
 try:
  out=execute_paid_recovery_resume(execute_paid_recovery_resume=a.execute_paid_recovery_resume,branch=git('branch','--show-current'),code_head=git('rev-parse','HEAD'),worktree_clean=not bool(git('status','--porcelain')),cost=read('.aic-runtime/b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json'),readiness=read('.aic-runtime/b4_post_research_reopen_initial_recovery_resume_readiness_zero_call_v0_2.json'),reconciliation=read('.aic-runtime/b4_post_research_reopen_initial_recovery_captured_response_reconciliation_v0_1.json'),approval=read(a.owner_approval) if a.owner_approval else None,ledger_path=Path('.aic-runtime/b4_post_research_reopen_initial_recovery_resume_paid_dispatch_ledger_v0_1.json'),raw_dir=Path('.aic-runtime/b4_post_research_reopen_initial_recovery_resume_paid_raw_responses_v0_1'),result_path=Path('.aic-runtime/b4_post_research_reopen_initial_recovery_resume_council_freeze_v0_1.json'),transport_factory=transport); print(json.dumps(out));return 0
 except Exception as e: print(f'B4_RESUME_STOP: {e}',file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
