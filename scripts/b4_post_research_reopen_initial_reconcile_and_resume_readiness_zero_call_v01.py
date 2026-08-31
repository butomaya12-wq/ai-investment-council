from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
from aic.council.post_research_reopen_initial_recovery_resume_v01 import all_nine_gap_parity, reconcile_captured_response, build_resume_readiness, file_sha256, RecoveryResumeError

COST=Path('.aic-runtime/b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json')
LEDGER=Path('.aic-runtime/b4_post_research_reopen_initial_recovery_paid_dispatch_ledger_v0_1.json')
RAW=Path('.aic-runtime/b4_post_research_reopen_initial_recovery_paid_raw_responses_v0_1/01-02a5559a11d587ef27f74389e783b960f75bdf610f83a4e0e554504d2a07c232.json')
RECON=Path('.aic-runtime/b4_post_research_reopen_initial_recovery_captured_response_reconciliation_v0_1.json')
PARITY=Path('.aic-runtime/b4_post_research_reopen_initial_all_nine_gap_parity_zero_call_v0_1.json')
OUT=Path('.aic-runtime/b4_post_research_reopen_initial_recovery_resume_readiness_zero_call_v0_1.json')
def git(*a): return subprocess.run(['git',*a],check=True,capture_output=True,text=True).stdout.strip()
def read(p): return json.loads(p.read_text())
def write(p,x):
    if p.exists(): raise RecoveryResumeError(f'exclusive output exists: {p}')
    p.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f: json.dump(x,f,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False); f.write('\n'); f.flush(); os.fsync(f.fileno())
def main():
  try:
    for k in ('OPENAI_API_KEY','APCA_API_KEY_ID','APCA_API_SECRET_KEY','ALPACA_API_KEY','ALPACA_API_SECRET'): os.environ[k]=''
    if git('branch','--show-current')!='hackathon/alpaca-2026' or git('status','--porcelain'): raise RecoveryResumeError('requires clean target branch')
    cost,ledger,capture=read(COST),read(LEDGER),read(RAW); head=git('rev-parse','HEAD')
    parity=all_nine_gap_parity(cost); recon=reconcile_captured_response(pre_repair_head='29e9e2c0f9fc490b36c0547a1bd953ec38d5bcad',final_head=head,cost=cost,recovery_ledger=ledger,recovery_ledger_file_sha256=file_sha256(LEDGER),capture=capture,capture_file_sha256=file_sha256(RAW)); readiness=build_resume_readiness(code_head=head,cost=cost,reconciliation=recon,parity=parity)
    write(PARITY,parity); write(RECON,recon); write(OUT,readiness)
    print(f'RECONCILIATION_HASH={recon["artifact_hash"]}\nRESUME_READINESS_HASH={readiness["artifact_hash"]}\nMODEL_CALLS_THIS_STEP=0\nPROVIDER_READS_THIS_STEP=0\nBROKER_WRITES=0\nALPACA_ORDERS=0\nCOST_USD_THIS_STEP=0')
    return 0
  except (RecoveryResumeError,OSError,ValueError,subprocess.CalledProcessError) as e:
    print(f'B4_RECOVERY_RESUME_ZERO_CALL_STOP: {e}',file=sys.stderr); return 2
if __name__=='__main__': sys.exit(main())
