"""Write exclusive zero-call WATCH finalization evidence."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
from aic.domain.canonical import canonical_sha256
from aic.council import post_b4_watch_finalization_v01 as f
ROOT=Path('.aic-runtime')
def write(path,p):
 if path.exists(): raise SystemExit(f'STOP: exclusive output exists: {path}')
 path.write_text(json.dumps(p,sort_keys=True,indent=2)+'\n')
def main():
 judge=json.loads((ROOT/'b4_post_research_reopen_current_judge_council_freeze_v0_3.json').read_text()); blocked=f.blocked(judge); cost=f.provenance(judge)
 write(ROOT/'b4_post_research_reopen_watch_finalization_blocked_zero_call_v0_1.json',blocked);write(ROOT/'b4_final_valid_cycle_cost_and_provenance_zero_call_v0_1.json',cost)
 build=Path('docs/competition/BUILD_EVIDENCE.md').read_bytes(); ready=Path('docs/competition/SUBMISSION_READINESS.md').read_bytes()
 manifest={'artifact_version':'B4_WATCH_POSTPROCESS_ZERO_CALL_MANIFEST_v0_1','source_judge_freeze_hash':f.JUDGE_HASH,'final_outcome':'WATCH','canonical_final_decision_status':blocked['status'],'final_decision_hash':None,'decision_ttl_hash':None,'next_review_trigger_hash':None,'monitor_subscription_status':blocked['THESIS_MONITOR_SUBSCRIPTION_STATUS'],'monitor_subscription_hashes':[],'decision_journal_status':blocked['DECISION_JOURNAL_STATUS'],'journal_event_hashes':[],'cost_provenance_manifest_hash':cost['artifact_hash'],'build_evidence_sha256':hashlib.sha256(build).hexdigest(),'submission_readiness_sha256':hashlib.sha256(ready).hexdigest(),'B5_HANDOFF_ELIGIBLE':False,'B5_HANDOFF_CREATED':False,'BROKER_WRITES':0,'ALPACA_ORDERS':0,'LIVE_MONEY':'PROHIBITED','MODEL_CALLS_THIS_STEP':0,'PROVIDER_READS_THIS_STEP':0,'COST_USD_THIS_STEP':'0'};manifest['artifact_hash']=canonical_sha256(manifest,exclude_fields=('artifact_hash',));write(ROOT/'b4_watch_postprocess_zero_call_manifest_v0_1.json',manifest);print(manifest['artifact_hash'])
if __name__=='__main__':main()
