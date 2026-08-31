from __future__ import annotations
import json
from copy import deepcopy
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
import pytest
from aic.council import post_research_reopen_judge_current_v03 as judge
from aic.council.proposal import JudgeNextDirective, JudgeOutcome
from aic.domain.canonical import canonical_sha256

ROOT=Path('.aic-runtime'); CODE='a'*40
def rd(name): return json.loads((ROOT/name).read_text())
@lru_cache(maxsize=1)
def cached():
 c,r,g,i,co,b,s,e=(rd('b3_research_reopen_final_competition_closure_zero_call_v0_1.json'),rd('b3_research_reopen_residual_external_read_plan_zero_call_v0_1.json'),rd('b3_reopen_remaining_gaps_closure_zero_call_v0_2.json'),rd('b4_post_research_reopen_initial_recovery_resume_council_freeze_v0_1.json'),rd('b4_post_research_reopen_initial_request_cost_preflight_zero_call_v0_1.json'),rd('b4_post_research_reopen_rebuttal_council_freeze_v0_1.json'),rd('b4_judge_selected_model_authority_v0_1.json'),rd('b4_judge_model_eval_v0_1.json'))
 pr=json.loads(Path('config/event/openai_text_pricing_2026_08_30.json').read_text()); rec=[json.loads(x) for x in (ROOT/'b4_judge_model_eval_paid_receipts_v0_1.jsonl').read_text().splitlines()]; en=judge.build_entry(code_commit_sha=CODE,closure=c,residual_plan=r,remaining_gaps_closure=g,initial_freeze=i,initial_cost=co,rebuttal_freeze=b); cx=judge.build_context(entry=en,closure=c,residual_plan=r,remaining_gaps_closure=g,initial_cost=co,initial_freeze=i,rebuttal_freeze=b,selection=s); inp=dict(entry=en,context=cx,closure=c,residual_plan=r,remaining_gaps_closure=g,initial_freeze=i,initial_cost=co,rebuttal_freeze=b,selection=s,eval_artifact=e,receipts=rec,pricing=pr,historical_request_hashes=['8eedd3e58e95d97bf7e29e368fe199c3c681f17876feeabb22e6fbd8cc1c20d7','72bb6db79203a54b20b47452acfbd3de4fc42385ced6330ac8f75cf18419c628',rd('b4_reopen_judge_production_request_preflight_v0_2.json')['request_hash']]); pf=judge.build_preflight(code_commit_sha=CODE,**inp); ready=judge.build_readiness(code_commit_sha=CODE,preflight=pf,**inp); ap=judge.build_owner_approval(code_commit_sha=CODE,readiness_hash=ready['artifact_hash'],preflight=pf,entry=en,owner_approval_id='TEST',owner_approval_at_utc='2026-09-01T00:00:00Z'); return inp,pf,ready,ap
def values(): return deepcopy(cached())
def proposal(outcome,cx):
 return SimpleNamespace(outcome=outcome,next_directive=JudgeNextDirective.MONITOR if outcome==JudgeOutcome.WATCH else JudgeNextDirective.STOP,judge_input_hash=cx.judge_input_hash,mandate_version=cx.mandate_version,deep_comparison_id=cx.deep_comparison_id,model_run_ref=judge.MODEL_RUN_REF,execution_authority=False,research_reopen_required=False,research_reopen_reason_codes=(),selected_candidate_basis_claim_ids=(cx.allowed_claim_ids[0],),why_not_other_candidates=(SimpleNamespace(claim_ids=(cx.allowed_claim_ids[1],)),),unresolved_dispute_refs=(),material_conflict_refs=(),material_unknown_refs=(cx.allowed_unknown_refs[0],),what_would_change_decision=(SimpleNamespace(source_or_claim_refs=(cx.allowed_claim_ids[0],)),),invalidation_condition_refs=(cx.allowed_condition_refs[0],),primary_candidate_id=None)
def test_v03_context_restores_frozen_judge_evidence_surface():
 inp,pf,ready,_=values();cx=inp['context'];mi=cx.model_input
 assert rd('b4_post_research_reopen_current_judge_readiness_zero_call_v0_2.json')['artifact_hash']=='87c38d9eca6e6d1a4ea66903525a70cf223b522335f953bf36e9ac280f7a1b6d'
 assert mi['candidate_order']==['NVDA','MSFT','META'] and len(mi['candidate_packets'])==3 and len(mi['initial_role_views'])==9 and len(mi['rebuttal_bundles'])==3
 assert mi['material_claims'] and mi['computed_values'] and cx.allowed_claim_ids
 assert tuple(x['claim_id'] for x in mi['material_claims'])==cx.allowed_claim_ids
 assert pf['request_hash'] not in inp['historical_request_hashes'] and pf['evidence_counts']['allowed_claim_ids']==len(cx.allowed_claim_ids)
 assert judge.verify_readiness(ready,code_commit_sha=CODE,preflight=pf,**inp)==ready['artifact_hash']
@pytest.mark.parametrize('outcome',[JudgeOutcome.WATCH,JudgeOutcome.ABSTAIN])
def test_only_evidence_grounded_terminal_proposals_are_valid(outcome):
 inp,_,_,_=values();cx=inp['context'];judge._validate_proposal(proposal(outcome,cx),context=cx)
 with pytest.raises(Exception,match='rejects INVEST'): judge._validate_proposal(proposal(JudgeOutcome.INVEST,cx),context=cx)
 bad=proposal(JudgeOutcome.WATCH,cx);bad.selected_candidate_basis_claim_ids=('NOT_SUPPLIED',)
 with pytest.raises(Exception,match='basis claim outside graph'): judge._validate_proposal(bad,context=cx)
 bad=proposal(JudgeOutcome.WATCH,cx);bad.what_would_change_decision=()
 with pytest.raises(Exception,match='terminal directive'): judge._validate_proposal(bad,context=cx)
def test_invalid_approval_and_captured_invalid_response_fail_closed(tmp_path):
 inp,pf,ready,ap=values();calls=0
 def sender(_):
  nonlocal calls;calls+=1;return {'id':'bad'}
 ap['new_paid_call_count']=2;ap['artifact_hash']=canonical_sha256(ap,exclude_fields=('artifact_hash',))
 kw=dict(execute_paid_judge=True,branch='hackathon/alpaca-2026',code_commit_sha=CODE,worktree_clean=True,preflight=pf,readiness=ready,ledger_path=tmp_path/'ledger',raw_path=tmp_path/'raw',result_path=tmp_path/'result',transport_factory=lambda:sender,**inp)
 with pytest.raises(Exception,match='approval'):judge.execute_paid(approval=ap,**kw)
 assert calls==0
 _,_,_,ap=values()
 with pytest.raises(Exception,match='captured response failed validation'):judge.execute_paid(approval=ap,**kw)
 ledger=json.loads((tmp_path/'ledger').read_text());raw=json.loads((tmp_path/'raw').read_text());assert calls==1 and ledger['entries'][0]['state']=='DISPATCH_STARTED_UNKNOWN' and judge.verify_raw_capture(raw,request_hash=ledger['entries'][0]['request_hash'])==ledger['entries'][0]['raw_response_hash']
 with pytest.raises(Exception,match='pre-transport gate'):judge.execute_paid(approval=ap,**kw)
 assert calls==1
