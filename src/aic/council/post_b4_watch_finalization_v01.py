"""Zero-call finalization evidence for the frozen current-lineage WATCH verdict."""
from __future__ import annotations
from decimal import Decimal
from typing import Any, Mapping
import re
from aic.domain.canonical import canonical_sha256

JUDGE_HASH="e3eac844b71bea54b22b5a4f14825f2bffd756cae16d3fd0cd5f66704d7bed49"
JUDGE_HEAD="ae94645c573ea0efe86f7ee3a8f3665b010d75a9"
INITIAL_HASH="9138746e122b494e3a2eb84695b98870299145d5d806d2aa9da62ecb010cd394"
REBUTTAL_HASH="18b854261c9b49c1fcd2addfd66af52fda54e71b0949416f7dc1cdfed3e8fd9e"
B3_HASH="ad1f477df9094e40c26186a045c6ca5794cc1cf4c415929bb1453cd09b1e4149"
class WatchFinalizationError(RuntimeError): pass
def need(ok: bool,msg: str)->None:
 if not ok: raise WatchFinalizationError(msg)
def hash_of(p:Mapping[str,Any],field="artifact_hash")->str:
 v=p.get(field);need(isinstance(v,str) and re.fullmatch(r"[0-9a-f]{64}",v) is not None and v==canonical_sha256(p,exclude_fields=(field,)),f"{field} mismatch");return v
def verify_judge(p:Mapping[str,Any])->dict[str,Any]:
 need(hash_of(p)==JUDGE_HASH and p.get("artifact_version")=="B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_COUNCIL_FREEZE_v0_3" and p.get("status")=="B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_FROZEN" and p.get("code_commit_sha")==JUDGE_HEAD,"Judge freeze identity drift")
 r=p.get("processed_record");need(isinstance(r,Mapping) and r.get("record_hash")==canonical_sha256(r,exclude_fields=("record_hash",)),"Judge record hash drift")
 d=r.get("frozen_judge_proposal",{}).get("draft",{});need(isinstance(d,Mapping) and r.get("outcome")=="WATCH" and r.get("next_directive")=="MONITOR" and d.get("primary_candidate_id") is None and d.get("watch_candidate_ids")==["NVDA","MSFT","META"] and d.get("evidence_status")=="PARTIAL" and d.get("execution_authority") is False and d.get("research_reopen_required") is False and d.get("research_reopen_reason_codes")==[],"Judge WATCH semantics drift")
 for k,v in {"final_b4_decision_created":True,"b5_handoff_eligible":False,"b5_handoff_created":False,"research_reopen_created":False,"automatic_retries":0,"broker_writes":0,"alpaca_orders":0,"live_money":"PROHIBITED","actual_cost_usd":"0.138086"}.items():need(p.get(k)==v,f"Judge invariant drift: {k}")
 need(p.get("raw_response_hash")=="2ca884031d8f69f44eec931235f6ddd89cb66a1be5b8b3c44fb58e5f70f16e81" and p.get("ledger_hash")=="d53e31f0f600a285110f30660129b9479ac4d22ff8c567742342ece8ff4a501f" and r.get("record_hash")=="00e4cecc720a5c64faeaffb954d5f8976e9b34b3922a9ba5e870b0c4bb7f5f4f","Judge bindings drift")
 return dict(d)
def blocked(judge:Mapping[str,Any])->dict[str,Any]:
 d=verify_judge(judge);p={"artifact_version":"B4_POST_RESEARCH_REOPEN_WATCH_FINALIZATION_BLOCKED_ZERO_CALL_v0_1","status":"CANONICAL_FINAL_DECISION_PROMOTION_BLOCKED_ZERO_CALL","source_judge_freeze_hash":JUDGE_HASH,"source_judge_proposal_hash":judge["processed_record"]["frozen_judge_proposal"]["judge_proposal_hash"],"missing_authority_fields":["DECISION_DRAFT_B4_v0_4.created_at"],"blocking_reason":"FINAL_DECISION_V1 FD-V01 requires canonical-identical source DECISION_DRAFT_B4_v0_4.created_at; no such authoritative source exists in the current lineage.","B4_WATCH_VERDICT_IS_STILL_FROZEN":True,"B5_HANDOFF_ELIGIBLE":False,"THESIS_MONITOR_SUBSCRIPTION_STATUS":"BLOCKED_MISSING_CANONICAL_FINAL_DECISION","DECISION_JOURNAL_STATUS":"BLOCKED_NO_AUTHORITATIVE_EXISTING_EVENT_CHAIN","model_calls_this_step":0,"provider_reads_this_step":0,"broker_writes":0,"alpaca_orders":0,"cost_usd_this_step":"0","live_money":"PROHIBITED","preserved_watch_condition_ids":[x["condition_id"] for x in d["what_would_change_decision"]]};p["artifact_hash"]=canonical_sha256(p,exclude_fields=("artifact_hash",));return p
def provenance(judge:Mapping[str,Any])->dict[str,Any]:
 verify_judge(judge);p={"artifact_version":"B4_FINAL_VALID_CYCLE_COST_AND_PROVENANCE_ZERO_CALL_v0_1","B3_FINAL_CLOSURE_HASH":B3_HASH,"INITIAL_FREEZE_HASH":INITIAL_HASH,"REBUTTAL_FREEZE_HASH":REBUTTAL_HASH,"JUDGE_FREEZE_HASH":JUDGE_HASH,"INITIAL_VALID_CURRENT_ACTUAL_COST_USD":"1.566666","REBUTTAL_ACTUAL_COST_USD":"1.384836","JUDGE_ACTUAL_COST_USD":"0.138086","FINAL_VALID_B4_PRODUCTION_CYCLE_KNOWN_ACTUAL_COST_USD":"3.089588","HISTORICAL_LOST_INITIAL_CALL_ACTUAL_COST_USD":"UNKNOWN","HISTORICAL_LOST_INITIAL_CALL_CONSERVATIVE_MAX_USD":"0.636487","CURRENT_PRODUCTION_LINEAGE_TIGHTER_CONSERVATIVE_MAX_USD":"3.726075","TOTAL_PROJECT_SPEND_USD":"NOT_COMPUTED_BY_THIS_ARTIFACT","model_eval_costs_included":False,"b3_research_costs_included":False,"cost_usd_this_step":"0","model_calls_this_step":0,"provider_reads_this_step":0,"broker_writes":0,"alpaca_orders":0};need(sum(map(Decimal,[p["INITIAL_VALID_CURRENT_ACTUAL_COST_USD"],p["REBUTTAL_ACTUAL_COST_USD"],p["JUDGE_ACTUAL_COST_USD"]]))==Decimal(p["FINAL_VALID_B4_PRODUCTION_CYCLE_KNOWN_ACTUAL_COST_USD"]),"cost sum drift");p["artifact_hash"]=canonical_sha256(p,exclude_fields=("artifact_hash",));return p
