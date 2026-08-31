import json,os,subprocess
from pathlib import Path
from aic.council.post_research_reopen_initial_recovery_resume_v01 import build_final_resume_readiness
def main():
 out=Path('.aic-runtime/b4_post_research_reopen_initial_recovery_resume_readiness_zero_call_v0_2.json')
 if out.exists(): raise SystemExit('exclusive output exists')
 if subprocess.run(['git','status','--porcelain'],capture_output=True,text=True).stdout: raise SystemExit('clean worktree required')
 read=lambda p:json.loads(Path(p).read_text()); a=build_final_resume_readiness(code_head=subprocess.run(['git','rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip(),cost=read('.aic-runtime/b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json'),reconciliation=read('.aic-runtime/b4_post_research_reopen_initial_recovery_captured_response_reconciliation_v0_1.json'),parity=read('.aic-runtime/b4_post_research_reopen_initial_all_nine_gap_parity_zero_call_v0_1.json')); out.write_text(json.dumps(a,sort_keys=True,indent=2)+'\n');print(a['artifact_hash'])
if __name__=='__main__':main()
