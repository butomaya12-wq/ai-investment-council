"""Zero-call reconciliation and fail-closed eight-call resume support."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import hashlib
from pathlib import Path
import re
from time import perf_counter_ns
from typing import Any, Callable, Mapping, Sequence

from aic.domain.canonical import canonical_sha256

from . import post_research_reopen_initial_production_dispatch_v01 as dispatch
from .initial_runtime_cost_v02 import actual_cost_usd, load_initial_runtime_pricing
from .post_research_reopen_initial_execute_production_v01 import (
    PostResearchInitialExecutionError, _replace_durable, _write_exclusive,
    build_raw_response_capture, frozen_initial_items, verify_raw_response_capture,
)
from .reopen_initial_runtime import (
    derive_initial_allowed_data_gap_refs_from_frozen_request,
    process_reopen_initial_provider_response,
)

RECON_VERSION = "B4_POST_RESEARCH_REOPEN_INITIAL_RECOVERY_CAPTURED_RESPONSE_RECONCILIATION_v0_1"
RECON_STATUS = "B4_POST_RESEARCH_REOPEN_INITIAL_RECOVERY_CAPTURED_RESPONSE_RECONCILIATION_PASS"
RESUME_READINESS_VERSION = "B4_POST_RESEARCH_REOPEN_INITIAL_RECOVERY_RESUME_READINESS_ZERO_CALL_v0_1"
RESUME_APPROVAL_VERSION = "B4_POST_RESEARCH_REOPEN_INITIAL_RECOVERY_RESUME_OWNER_APPROVAL_v0_1"
RESUME_LEDGER_VERSION = "B4_POST_RESEARCH_REOPEN_INITIAL_RECOVERY_RESUME_PAID_DISPATCH_LEDGER_v0_1"
RESUME_RESULT_VERSION = "B4_POST_RESEARCH_REOPEN_INITIAL_RECOVERY_RESUME_COUNCIL_FREEZE_v0_1"
RECOVERY_LEDGER_HASH = "7750e6781477285a7ae4dbde05938e53d4cff47303a7df942a080aa21126d6db"
RECOVERY_APPROVAL_HASH = "108f249a9fc899c38829fd5741685fec2296182fac0e280d421862854c7a8a0f"
RECOVERY_READINESS_HASH = "33e90e606b45f2cb9f34da92b271d7e2529d5bd4f71cd9cfc01d4c5169f38b10"
CAPTURE_HASH = "78165385c30af8ee5b728d392dab90a8a35988e745f1e94823dd90abd61968fb"
RESUME_MAX_COST = Decimal("5.089556")

class RecoveryResumeError(RuntimeError): pass
def _need(ok: bool, message: str) -> None:
    if not ok: raise RecoveryResumeError(message)
def _hash(payload: Mapping[str, Any], field: str = "artifact_hash") -> str:
    value = payload.get(field); _need(isinstance(value,str) and value == canonical_sha256(payload,exclude_fields=(field,)), f"{field} mismatch"); return value
def file_sha256(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def _utc(now: datetime) -> str: return now.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00","Z")

def all_nine_gap_parity(cost: Mapping[str, Any]) -> dict[str, Any]:
    items = frozen_initial_items(cost); rows=[]
    for frozen in items:
        item=frozen.plan_item; allowed=derive_initial_allowed_data_gap_refs_from_frozen_request(item.request)
        model_gaps=item.model_input.get("data_gap_refs")
        _need(isinstance(model_gaps,(list,tuple)) and all(isinstance(x,str) for x in model_gaps), "frozen model-input gaps malformed")
        parity=tuple(model_gaps)==allowed
        _need(parity, f"request {item.dispatch_index} schema/processor gap parity failure")
        rows.append({"dispatch_index":item.dispatch_index,"candidate":item.candidate_id,"lane":item.lane.value,"request_hash":item.request.request_hash,"schema_allowed_material_unknown_refs":list(allowed),"model_input_data_gap_refs":list(model_gaps),"processor_derived_allowed_data_gap_refs":list(allowed),"parity_status":"PASS"})
    out={"artifact_version":"B4_POST_RESEARCH_REOPEN_INITIAL_ALL_NINE_GAP_PARITY_v0_1","source_cost_preflight_hash":dispatch.EXPECTED_PREFLIGHT_HASH,"rows":rows,"processor_schema_gap_allowlist_parity":"PASS","model_calls_this_step":0,"provider_reads_this_step":0}
    out["artifact_hash"]=canonical_sha256(out,exclude_fields=("artifact_hash",)); return out

def reconcile_captured_response(*, pre_repair_head: str, final_head: str, cost: Mapping[str,Any], recovery_ledger: Mapping[str,Any], recovery_ledger_file_sha256: str, capture: Mapping[str,Any], capture_file_sha256: str) -> dict[str,Any]:
    _need(pre_repair_head=="29e9e2c0f9fc490b36c0547a1bd953ec38d5bcad", "pre-repair HEAD drift")
    _need(recovery_ledger.get("ledger_hash")==RECOVERY_LEDGER_HASH and recovery_ledger.get("recovery_owner_approval_hash")==RECOVERY_APPROVAL_HASH, "failed recovery ledger drift")
    entries=recovery_ledger.get("entries"); _need(isinstance(entries,list) and len(entries)==9, "failed recovery entries drift")
    first=entries[0]; _need(isinstance(first,Mapping) and first.get("state")==dispatch.DISPATCH_STARTED_UNKNOWN and first.get("stop_reason")=="RESPONSE_CAPTURED_BUT_NOT_ACCEPTED:CouncilPromotionError", "failed recovery request #1 drift")
    items=frozen_initial_items(cost); item=items[0].plan_item
    _need(capture.get("raw_response_hash")==CAPTURE_HASH, "capture hash drift")
    verify_raw_response_capture(capture,request_hash=item.request.request_hash)
    raw=capture.get("raw_response"); _need(isinstance(raw,Mapping) and raw.get("id")=="resp_0d8088512d1bac50016a95b4d29f0087d287fd9cdc43990771" and raw.get("status")=="completed" and raw.get("model")==dispatch.EXPECTED_MODEL, "saved provider response identity drift")
    allowed=derive_initial_allowed_data_gap_refs_from_frozen_request(item.request)
    pricing=load_initial_runtime_pricing()
    record=process_reopen_initial_provider_response(item,raw_response=raw,latency_ms=19000,frozen_at=datetime.fromisoformat(str(capture["captured_at_utc"]).replace("Z","+00:00")),pricing=pricing)
    proposal=record["structured_output"]; proposal_refs=proposal.get("material_unknown_refs")
    _need(isinstance(proposal_refs,list) and set(proposal_refs).issubset(set(allowed)), "proposal refs outside frozen schema")
    artifact={"artifact_version":RECON_VERSION,"status":RECON_STATUS,"pre_repair_code_head":pre_repair_head,"final_repaired_code_head":final_head,"recovery_owner_approval_hash":RECOVERY_APPROVAL_HASH,"recovery_readiness_hash":RECOVERY_READINESS_HASH,"pre_repair_recovery_ledger_hash":RECOVERY_LEDGER_HASH,"recovery_ledger_file_sha256":recovery_ledger_file_sha256,"request_hash":item.request.request_hash,"raw_response_capture_hash":CAPTURE_HASH,"raw_response_file_sha256":capture_file_sha256,"provider_response_id":raw["id"],"original_stop_reason":first["stop_reason"],"frozen_schema_allowed_refs":list(allowed),"proposal_refs":proposal_refs,"proposal_refs_outside_frozen_schema":[],"validator_fix_version":"FROZEN_REQUEST_SCHEMA_GAP_ALLOWLIST_v0_1","processed_record":record,"processed_record_hash":record["record_hash"],"actual_cost_usd":record["actual_cost_usd"],"request_1_provider_call_already_completed":True,"request_1_raw_response_durable":True,"request_1_local_replay":"PASS","request_1_fresh_output_recovered":True,"request_1_resend_required":False,"request_1_resend_allowed":False,"model_calls_for_reconciliation":0,"provider_reads_for_reconciliation":0}
    artifact["artifact_hash"]=canonical_sha256(artifact,exclude_fields=("artifact_hash",)); return artifact

def build_resume_readiness(*, code_head: str, cost: Mapping[str,Any], reconciliation: Mapping[str,Any], parity: Mapping[str,Any]) -> dict[str,Any]:
    _need(_hash(reconciliation)==reconciliation.get("artifact_hash") and reconciliation.get("status")==RECON_STATUS, "reconciliation not PASS")
    _need(_hash(parity)==parity.get("artifact_hash") and parity.get("processor_schema_gap_allowlist_parity")=="PASS", "all-nine parity not PASS")
    items=frozen_initial_items(cost); remaining=items[1:]; _need(len(remaining)==8, "resume count drift")
    total=sum((Decimal(str(x.row["estimated_max_cost_usd"])) for x in remaining),Decimal("0")); _need(total==RESUME_MAX_COST,"resume frozen cost drift")
    out={"artifact_version":RESUME_READINESS_VERSION,"status":"B4_POST_RESEARCH_REOPEN_INITIAL_RECOVERY_RESUME_READINESS_ZERO_CALL_PASS","code_commit_sha":code_head,"captured_response_1_reconciliation":"PASS","captured_response_1_reconciliation_hash":reconciliation["artifact_hash"],"captured_response_1_resend_allowed":False,"processor_schema_gap_allowlist_parity":"PASS","all_nine_gap_parity_hash":parity["artifact_hash"],"fresh_initial_records_already_recovered":1,"new_paid_calls_planned":8,"resume_request_indices":[2,3,4,5,6,7,8,9],"resume_request_hashes":[x.plan_item.request.request_hash for x in remaining],"resume_request_1_included":False,"new_resume_max_cost_usd":"5.089556","model":dispatch.EXPECTED_MODEL,"reasoning_effort":dispatch.EXPECTED_REASONING_EFFORT,"max_output_tokens_per_call":4096,"owner_approval_required":True,"owner_approval_status":"NOT_GRANTED","model_calls_authorized":False,"automatic_retries":0,"partial_dispatch_fail_closed":True,"model_calls_this_step":0,"provider_reads_this_step":0,"broker_writes":0,"alpaca_orders":0,"cost_usd_this_step":"0","total_lineage_actual_cost_usd":"UNKNOWN","total_lineage_conservative_max_usd":"6.362530"}
    out["artifact_hash"]=canonical_sha256(out,exclude_fields=("artifact_hash",)); return out
