from __future__ import annotations
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from aic.council import post_research_reopen_initial_recovery_resume_v01 as r
from aic.council.post_research_reopen_initial_execute_production_v01 import frozen_initial_items

COST=Path('.aic-runtime/b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json')
LEDGER=Path('.aic-runtime/b4_post_research_reopen_initial_recovery_paid_dispatch_ledger_v0_1.json')
RAW=Path('.aic-runtime/b4_post_research_reopen_initial_recovery_paid_raw_responses_v0_1/01-02a5559a11d587ef27f74389e783b960f75bdf610f83a4e0e554504d2a07c232.json')
def test_frozen_schema_derivation_and_all_nine_parity():
 c=json.loads(COST.read_text()); items=frozen_initial_items(c)
 assert r.derive_initial_allowed_data_gap_refs_from_frozen_request(items[0].plan_item.request)==('ALPACA_NEWS_PAGINATION_INCOMPLETE',)
 p=r.all_nine_gap_parity(c); assert p['processor_schema_gap_allowlist_parity']=='PASS' and len(p['rows'])==9
def test_unbounded_gap_schema_fails_closed():
 c=json.loads(COST.read_text()); request=frozen_initial_items(c)[0].plan_item.request
 bad=SimpleNamespace(request_payload={'text':{'format':{'schema':{'properties':{'material_unknown_refs':{'type':'array','items':{'type':'string'}}}}}}})
 with pytest.raises(Exception,match='unbounded'): r.derive_initial_allowed_data_gap_refs_from_frozen_request(bad)
def test_saved_capture_replays_and_resume_excludes_request_one():
 c=json.loads(COST.read_text()); ledger=json.loads(LEDGER.read_text()); capture=json.loads(RAW.read_text()); recon=r.reconcile_captured_response(pre_repair_head='29e9e2c0f9fc490b36c0547a1bd953ec38d5bcad',final_head='a'*40,cost=c,recovery_ledger=ledger,recovery_ledger_file_sha256=r.file_sha256(LEDGER),capture=capture,capture_file_sha256=r.file_sha256(RAW)); parity=r.all_nine_gap_parity(c); ready=r.build_resume_readiness(code_head='a'*40,cost=c,reconciliation=recon,parity=parity)
 assert recon['request_1_local_replay']=='PASS' and recon['request_1_resend_allowed'] is False
 assert ready['resume_request_indices']==[2,3,4,5,6,7,8,9] and ready['resume_request_1_included'] is False and ready['new_resume_max_cost_usd']=='5.089556'

def test_approval_verifier_returns_actual_hash_and_strict_readiness_recomputes():
 c=json.loads(COST.read_text()); ledger=json.loads(LEDGER.read_text()); capture=json.loads(RAW.read_text()); recon=r.reconcile_captured_response(pre_repair_head='29e9e2c0f9fc490b36c0547a1bd953ec38d5bcad',final_head='a'*40,cost=c,recovery_ledger=ledger,recovery_ledger_file_sha256=r.file_sha256(LEDGER),capture=capture,capture_file_sha256=r.file_sha256(RAW)); parity=r.all_nine_gap_parity(c); ready=r.build_final_resume_readiness(code_head='a'*40,cost=c,reconciliation=recon,parity=parity); approval=r.build_resume_owner_approval(code_head='a'*40,readiness_hash=ready['artifact_hash'],cost=c,owner_approval_id='OWNER',owner_approval_at_utc='2026-08-31T00:00:00Z')
 assert r.verify_resume_owner_approval(approval,code_head='a'*40,readiness_hash=ready['artifact_hash'],cost=c)==approval['artifact_hash']
 assert r.verify_final_resume_readiness(ready,code_head='a'*40,cost=c,reconciliation=recon,parity=parity)==ready['artifact_hash']
 with pytest.raises(Exception): r.verify_resume_owner_approval(approval,code_head='a'*40,readiness_hash='0'*64,cost=c)
 tampered=dict(ready); tampered['new_paid_calls_planned']=7; tampered['artifact_hash']=r.canonical_sha256(tampered,exclude_fields=('artifact_hash',))
 with pytest.raises(Exception,match='semantic'): r.verify_final_resume_readiness(tampered,code_head='a'*40,cost=c,reconciliation=recon,parity=parity)

def _authority():
 c=json.loads(COST.read_text()); ledger=json.loads(LEDGER.read_text()); capture=json.loads(RAW.read_text()); recon=r.reconcile_captured_response(pre_repair_head='29e9e2c0f9fc490b36c0547a1bd953ec38d5bcad',final_head='a'*40,cost=c,recovery_ledger=ledger,recovery_ledger_file_sha256=r.file_sha256(LEDGER),capture=capture,capture_file_sha256=r.file_sha256(RAW)); parity=r.all_nine_gap_parity(c); ready=r.build_final_resume_readiness(code_head='a'*40,cost=c,reconciliation=recon,parity=parity); approval=r.build_resume_owner_approval(code_head='a'*40,readiness_hash=ready['artifact_hash'],cost=c,owner_approval_id='OWNER',owner_approval_at_utc='2026-08-31T00:00:00Z'); return c,recon,parity,ready,approval

def test_invalid_approval_fails_before_transport_without_side_effects(tmp_path):
 c,recon,parity,ready,approval=_authority(); approval['new_paid_call_count']=7; approval['artifact_hash']=r.canonical_sha256(approval,exclude_fields=('artifact_hash',)); calls=0
 def factory():
  nonlocal calls; calls+=1; return lambda _: {}
 with pytest.raises(Exception,match='approval'):
  r.execute_paid_recovery_resume(execute_paid_recovery_resume=True,branch='hackathon/alpaca-2026',code_head='a'*40,worktree_clean=True,cost=c,readiness=ready,reconciliation=recon,parity=parity,approval=approval,ledger_path=tmp_path/'ledger',raw_dir=tmp_path/'raw',result_path=tmp_path/'result',transport_factory=factory)
 assert calls==0 and not (tmp_path/'ledger').exists() and not (tmp_path/'raw').exists() and not (tmp_path/'result').exists()

def test_valid_authority_reaches_first_fake_transport_for_original_request_two(tmp_path):
 c,recon,parity,ready,approval=_authority(); sent=[]; factories=0
 def factory():
  nonlocal factories; factories+=1
  def send(payload): sent.append(payload); raise TimeoutError('fake')
  return send
 with pytest.raises(Exception,match='ambiguous'):
  r.execute_paid_recovery_resume(execute_paid_recovery_resume=True,branch='hackathon/alpaca-2026',code_head='a'*40,worktree_clean=True,cost=c,readiness=ready,reconciliation=recon,parity=parity,approval=approval,ledger_path=tmp_path/'ledger',raw_dir=tmp_path/'raw',result_path=tmp_path/'result',transport_factory=factory)
 assert factories==1 and len(sent)==1 and sent[0]==c['initial_requests'][1]['request_payload'] and sent[0]!=c['initial_requests'][0]['request_payload']
