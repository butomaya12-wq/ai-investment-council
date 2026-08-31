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
