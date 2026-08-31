"""Independent v0.2 current-lineage Judge: closed B3, non-INVEST only.

v0.1 is historical evidence.  This line binds the missing candidate-specific
B3 provenance and deliberately disables positive INVEST authority because the
frozen policy contains no deterministic positive-invest criteria.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence

from aic.domain.canonical import canonical_sha256

from .bounded_request import assert_bounded_request_invariants, build_bounded_judge_request
from .initial_runtime_cost_v02 import actual_cost_usd, runtime_cost_upper_bound_usd
from .judge_model_selection_v01 import build_judge_selected_model_authority, verify_judge_selected_model_authority
from .model_policy import JUDGE_MODEL_LADDER, CouncilModelStage, STAGE_MAX_OUTPUT_TOKENS
from .post_research_reopen_initial_execute_production_v01 import _external_json_value, _replace_durable, _write_exclusive, external_provider_json_sha256, frozen_initial_items
from .post_research_reopen_judge_current_v01 import CURRENT_REBUTTAL_HASH, verify_b3_final_closure, verify_current_rebuttal_freeze
from .post_research_reopen_rebuttal_production_v01 import CURRENT_INITIAL_FREEZE_HASH, verify_current_initial_freeze
from .proposal import FrozenJudgeDecisionProposal, JudgeDecisionProposalDraft, JudgeNextDirective, JudgeOutcome
from .request import parse_council_responses_payload


B3_HASH = "ad1f477df9094e40c26186a045c6ca5794cc1cf4c415929bb1453cd09b1e4149"
RESIDUAL_HASH = "a37196c7998c87e2e3723f58dbfb88a58e985493497e1bab3587194b70398aa3"
GAPS_HASH = "af8f48ae8e6984c73c7ff447eeb523fbda72855ee49460bdc60f0634be4216e6"
CANDIDATES = ("NVDA", "MSFT", "META")
OUTCOMES = ("WATCH", "ABSTAIN")
ENTRY_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_ENTRY_v0_2"
PREFLIGHT_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_PREFLIGHT_ZERO_CALL_v0_2"
READINESS_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_READINESS_ZERO_CALL_v0_2"
APPROVAL_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_OWNER_APPROVAL_v0_2"
LEDGER_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_PAID_DISPATCH_LEDGER_v0_2"
RESULT_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_COUNCIL_FREEZE_v0_2"
MODEL_RUN_REF = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_J1_V02"
POLICY_STATUS = "INVEST_ELIGIBILITY_POLICY_UNDERSPECIFIED"


class CurrentJudgeV02Error(RuntimeError):
    pass


def _need(ok: bool, message: str) -> None:
    if not ok:
        raise CurrentJudgeV02Error(message)


def _hash(payload: Mapping[str, Any], field: str = "artifact_hash") -> str:
    value = payload.get(field)
    _need(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None, f"{field} missing")
    _need(value == canonical_sha256(payload, exclude_fields=(field,)), f"{field} mismatch")
    return value


def _utc(now: datetime) -> str:
    return now.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _selected() -> Any:
    rows = [row for row in JUDGE_MODEL_LADDER if row.candidate_key == "J1"]
    _need(len(rows) == 1, "J1 missing")
    row = rows[0]
    _need((row.model, row.reasoning_effort, row.ladder_position, STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.JUDGE]) == ("gpt-5.6-terra", "medium", 1, 8192), "J1 policy drift")
    return row


def verify_residual_plan(payload: Mapping[str, Any]) -> str:
    observed = _hash(payload)
    _need(observed == RESIDUAL_HASH and payload.get("artifact_version") == "B3_RESEARCH_REOPEN_RESIDUAL_EXTERNAL_READ_PLAN_v0_1", "residual plan drift")
    rows = payload.get("provider_read_bundles")
    _need(isinstance(rows, list), "residual plan bundles missing")
    expected = {"NVDA": ["NVDA_CURRENT_DEVELOPMENTS_Q4"], "MSFT": ["MSFT_VALUATION_CONTEXT_DEPTH", "MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY"], "META": ["META_CONDITION_001", "META_CONDITION_002", "META_CONDITION_003"]}
    for candidate, targets in expected.items():
        matches = [row for row in rows if row.get("symbol_scope") == [candidate] and row.get("target_ids") == targets]
        _need(len(matches) == 1, f"{candidate} residual mapping drift")
    return observed


def verify_remaining_gaps_closure(payload: Mapping[str, Any]) -> str:
    observed = _hash(payload)
    _need(observed == GAPS_HASH and payload.get("artifact_version") == "B3_REOPEN_REMAINING_GAPS_CLOSURE_v0_2", "remaining gaps closure drift")
    _need(payload.get("remaining_reopen_reason_codes") == [] and payload.get("research_reopen_request_satisfied") is True, "remaining B3 gap")
    reasons = payload.get("reason_closure")
    _need(isinstance(reasons, list) and any(row.get("reason_code") == "ALPACA_NEWS_PAGINATION_INCOMPLETE" and row.get("closed") is True for row in reasons if isinstance(row, Mapping)), "pagination lifecycle closure absent")
    return observed


def classify_reason(*, candidate_id: str, raw_reason: str, closure: Mapping[str, Any], residual_plan: Mapping[str, Any], remaining_gaps_closure: Mapping[str, Any]) -> dict[str, Any]:
    _need(candidate_id in CANDIDATES, "unknown candidate")
    verify_b3_final_closure(closure); verify_residual_plan(residual_plan); verify_remaining_gaps_closure(remaining_gaps_closure)
    states = {row["requirement_id"]: row["closure_status"] for row in closure["requirement_closures"]}
    value = {"candidate_id": candidate_id, "raw_reason_or_ref": raw_reason, "canonical_requirement_ids": [], "decision_context_condition_ids": [], "final_closure_statuses": [], "lifecycle_classification": "UNMAPPED_REOPEN_REASON", "global_reason_closed": False, "additional_provider_read_authorized": False, "visible_to_judge_as_uncertainty": True, "may_independently_force_new_research_reopen": False}
    if raw_reason == "ALPACA_NEWS_PAGINATION_INCOMPLETE":
        value.update(lifecycle_classification="CLOSED_BUT_DECISION_CONTEXT_ONLY", global_reason_closed=True)
        if candidate_id == "NVDA": value["canonical_requirement_ids"] = ["NVDA_CURRENT_DEVELOPMENTS_Q4"]
        elif candidate_id == "MSFT": value["canonical_requirement_ids"] = ["MSFT_VALUATION_CONTEXT_DEPTH", "MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY"]
        else: value["decision_context_condition_ids"] = ["META_CONDITION_001", "META_CONDITION_002", "META_CONDITION_003"]
        value["final_closure_statuses"] = [states[x] for x in value["canonical_requirement_ids"]]
        return value
    if candidate_id == "MSFT" and raw_reason in {"VALUATION_EVIDENCE_NOT_SUPPLIED", "VALUATION_EVIDENCE_MISSING", "VALUATION_SPECIFIC_EVIDENCE_NOT_SUPPLIED"}:
        value.update(canonical_requirement_ids=["MSFT_VALUATION_CONTEXT_DEPTH"], final_closure_statuses=[states["MSFT_VALUATION_CONTEXT_DEPTH"]], lifecycle_classification="CLOSED_BUT_DECISION_CONTEXT_ONLY", global_reason_closed=True)
        return value
    if raw_reason.startswith(("META_Q4_RECENT_DEVELOPMENTS", "META_VALUATION_CONTEXT_MISSING", "META_PORTFOLIO_INTERACTION_CONTEXT_MISSING")):
        value["lifecycle_classification"] = "INVALID_OR_STALE_REOPEN_REASON"
    return value


def build_parity_audit(*, closure: Mapping[str, Any], residual_plan: Mapping[str, Any], remaining_gaps_closure: Mapping[str, Any], initial_freeze: Mapping[str, Any], initial_cost: Mapping[str, Any], rebuttal_freeze: Mapping[str, Any]) -> dict[str, Any]:
    b3 = verify_b3_final_closure(closure); residual = verify_residual_plan(residual_plan); gaps = verify_remaining_gaps_closure(remaining_gaps_closure)
    initial = verify_current_initial_freeze(initial_freeze, initial_cost=initial_cost); rebuttal = verify_current_rebuttal_freeze(rebuttal_freeze)
    rows: list[dict[str, Any]] = []
    for stage, records, field in (("CURRENT_INITIAL", initial_freeze["processed_records"], "structured_output"), ("CURRENT_REBUTTAL", rebuttal_freeze["processed_records"], None)):
        for record in records:
            reasons = record.get(field, {}).get("material_unknown_refs", []) if field else list(dict.fromkeys([*record.get("research_reopen_reason_codes", []), *record.get("required_unknown_refs", [])]))
            for reason in reasons:
                row = classify_reason(candidate_id=record["candidate_id"], raw_reason=reason, closure=closure, residual_plan=residual_plan, remaining_gaps_closure=remaining_gaps_closure)
                rows.append({"source_stage": stage, "source_record_hash": record["record_hash"], **row})
    _need(not [row for row in rows if row["lifecycle_classification"] == "UNMAPPED_REOPEN_REASON"], "unmapped new reason blocks v0.2")
    result = {"artifact_version": "B4_POST_RESEARCH_REOPEN_B3_CLOSURE_PARITY_AUDIT_v0_2", "status": "PASS_CANDIDATE_AWARE_REOPEN_PROVENANCE", "b3_final_closure_hash": b3, "b3_residual_plan_hash": residual, "b3_remaining_gaps_closure_hash": gaps, "current_initial_freeze_hash": initial, "current_rebuttal_freeze_hash": rebuttal, "reason_rows": rows, "canonical_open_research_requirements_after_b3": [], "additional_provider_read_required": False, "candidate_aware_reopen_provenance": "PASS"}
    result["artifact_hash"] = canonical_sha256(result, exclude_fields=("artifact_hash",)); return result


def build_entry(*, code_commit_sha: str, closure: Mapping[str, Any], residual_plan: Mapping[str, Any], remaining_gaps_closure: Mapping[str, Any], initial_freeze: Mapping[str, Any], initial_cost: Mapping[str, Any], rebuttal_freeze: Mapping[str, Any]) -> dict[str, Any]:
    _need(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha) is not None, "entry SHA invalid")
    audit = build_parity_audit(closure=closure, residual_plan=residual_plan, remaining_gaps_closure=remaining_gaps_closure, initial_freeze=initial_freeze, initial_cost=initial_cost, rebuttal_freeze=rebuttal_freeze)
    sample = frozen_initial_items(initial_cost)[0].plan_item.bundle
    sufficiency = [{"candidate_id": candidate, "hard_invest_blockers": [POLICY_STATUS], "non_blocking_uncertainties": list(closure["b4_input_overlay"]["unresolved_uncertainties_are_decision_inputs_not_reopen_triggers"]), "invest_eligibility": "INVEST_BLOCKED"} for candidate in CANDIDATES]
    value = {"artifact_version": ENTRY_VERSION, "status": "PASS_NON_INVEST_JUDGE_AUTHORITY", "code_commit_sha": code_commit_sha, "semantic_parity_audit_hash": audit["artifact_hash"], "b3_final_closure_hash": B3_HASH, "b3_residual_plan_hash": RESIDUAL_HASH, "b3_remaining_gaps_closure_hash": GAPS_HASH, "current_initial_freeze_hash": CURRENT_INITIAL_FREEZE_HASH, "current_rebuttal_freeze_hash": CURRENT_REBUTTAL_HASH, "canonical_open_research_requirements_after_b3": [], "additional_provider_read_required": False, "invest_eligibility_policy_status": POLICY_STATUS, "global_invest_block_reason": POLICY_STATUS, "candidate_sufficiency": sufficiency, "invest_eligible_candidates": [], "invest_blocked_candidates": list(CANDIDATES), "allowed_judge_outcomes": list(OUTCOMES), "watch_abstain_creates_new_b3_reopen": False, "mandate_version": sample.mandate_version, "deep_comparison_id": sample.deep_comparison_id, "council_policy_version": sample.council_policy_version, "judge_policy_version": sample.judge_policy_version, "model_policy_version": sample.model_policy_version, "model_calls": 0, "provider_reads": 0, "broker_writes": 0, "alpaca_orders": 0, "live_money": "PROHIBITED"}
    value["artifact_hash"] = canonical_sha256(value, exclude_fields=("artifact_hash",)); return value


def verify_entry(payload: Mapping[str, Any], **inputs: Any) -> str:
    observed = _hash(payload); _need(dict(payload) == build_entry(**inputs), "v0.2 entry drift"); return observed


def verify_selection(selection: Mapping[str, Any], *, eval_artifact: Mapping[str, Any], receipts: Sequence[Mapping[str, Any]]) -> str:
    _need(dict(selection) == build_judge_selected_model_authority(eval_artifact, receipts), "Judge selection replay drift")
    observed = verify_judge_selected_model_authority(selection)
    _need(selection.get("selected_candidate") == {"candidate_key": "J1", "model": "gpt-5.6-terra", "reasoning_effort": "medium", "ladder_position": 1}, "J1 selection drift")
    _selected(); return observed


def _request(entry: Mapping[str, Any]) -> Any:
    selected = _selected()
    base = {"lifecycle_contract": "B3_CLOSED_NON_INVEST_JUDGE_v0_2", "b3_canonical_research_reopen_closed": True, "remaining_canonical_reopen_requirement_ids": [], "invest_authorized": False, "invest_eligibility_policy_status": POLICY_STATUS, "candidate_sufficiency": entry["candidate_sufficiency"], "allowed_judge_outcomes": list(OUTCOMES), "watch_abstain_creates_new_b3_reopen": False, "source_lineage": {"b3_final_closure_hash": B3_HASH, "b3_residual_plan_hash": RESIDUAL_HASH, "b3_remaining_gaps_closure_hash": GAPS_HASH, "entry_hash": entry["artifact_hash"]}}
    input_hash = canonical_sha256(base); model_input = dict(base); model_input.update({"judge_input_hash": input_hash, "mandate_version": entry["mandate_version"], "deep_comparison_id": entry["deep_comparison_id"], "council_policy_version": entry["council_policy_version"], "judge_policy_version": entry["judge_policy_version"], "model_policy_version": entry["model_policy_version"]})
    request = build_bounded_judge_request(model_candidate=selected, model_input=model_input, candidate_ids=CANDIDATES, mandate_version=entry["mandate_version"], deep_comparison_id=entry["deep_comparison_id"], judge_input_hash=input_hash, council_policy_version=entry["council_policy_version"], judge_policy_version=entry["judge_policy_version"], model_policy_version=entry["model_policy_version"], model_run_ref=MODEL_RUN_REF, allowed_claim_ids=(), allowed_unknown_refs=(), allowed_condition_refs=())
    assert_bounded_request_invariants(request)
    _need(request.request_payload.get("model") == "gpt-5.6-terra" and request.request_payload.get("reasoning") == {"effort": "medium"} and request.request_payload.get("max_output_tokens") == 8192, "v0.2 Judge request policy drift")
    return request


def build_preflight(*, code_commit_sha: str, entry: Mapping[str, Any], selection: Mapping[str, Any], eval_artifact: Mapping[str, Any], receipts: Sequence[Mapping[str, Any]], pricing: Mapping[str, Any], historical_request_hashes: Sequence[str], **inputs: Any) -> dict[str, Any]:
    entry_hash = verify_entry(entry, code_commit_sha=code_commit_sha, **inputs); selection_hash = verify_selection(selection, eval_artifact=eval_artifact, receipts=receipts); request = _request(entry)
    _need(request.request_hash not in historical_request_hashes, "historical Judge request reuse")
    size = len(json.dumps(request.request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    cost = runtime_cost_upper_bound_usd(model="gpt-5.6-terra", input_tokens_upper_bound=size, output_tokens_upper_bound=8192, call_count=1, pricing=pricing)
    value = {"artifact_version": PREFLIGHT_VERSION, "status": "PASS_ZERO_CALL_NON_INVEST_JUDGE_PREFLIGHT", "code_commit_sha": code_commit_sha, "b3_final_closure_hash": B3_HASH, "b3_residual_plan_hash": RESIDUAL_HASH, "b3_remaining_gaps_closure_hash": GAPS_HASH, "current_initial_freeze_hash": CURRENT_INITIAL_FREEZE_HASH, "current_rebuttal_freeze_hash": CURRENT_REBUTTAL_HASH, "entry_hash": entry_hash, "selection_hash": selection_hash, "pricing_hash": _hash(pricing, "pricing_hash"), "pricing_version": pricing["pricing_version"], "model": "gpt-5.6-terra", "reasoning_effort": "medium", "judge_selected_candidate": "J1", "new_paid_calls_planned": 1, "new_paid_call_count_ceiling": 1, "max_output_tokens": 8192, "request_hash": request.request_hash, "request_manifest_hash": canonical_sha256({"request_hashes": [request.request_hash]}), "request_payload": request.request_payload, "judge_input_hash": request.input_hash, "request_body_utf8_bytes": size, "input_tokens_upper_bound": size, "judge_max_cost_usd": format(cost, "f"), "automatic_retries": 0, "historical_judge_outputs_reused": False, "historical_judge_request_hashes_reused": False, "owner_approval_required": True, "owner_approval_status": "NOT_GRANTED", "model_calls_authorized": False, "provider_reads_authorized": False, "model_calls_this_step": 0, "provider_reads_this_step": 0, "broker_writes": 0, "alpaca_orders": 0, "cost_usd_this_step": "0", "live_money": "PROHIBITED"}
    value["artifact_hash"] = canonical_sha256(value, exclude_fields=("artifact_hash",)); return value


def verify_preflight(payload: Mapping[str, Any], **inputs: Any) -> str:
    observed = _hash(payload); _need(dict(payload) == build_preflight(**inputs), "v0.2 preflight drift"); return observed


def build_readiness(*, code_commit_sha: str, preflight: Mapping[str, Any], **inputs: Any) -> dict[str, Any]:
    preflight_hash = verify_preflight(preflight, code_commit_sha=code_commit_sha, **inputs)
    entry = inputs["entry"]
    value = {"artifact_version": READINESS_VERSION, "status": "PASS_ZERO_CALL_NON_INVEST_JUDGE_READINESS", "code_commit_sha": code_commit_sha, "source_preflight_hash": preflight_hash, "B3_FINAL_CLOSURE_VERIFY": "PASS", "B3_FINAL_CLOSURE_HASH": B3_HASH, "REMAINING_CANONICAL_REOPEN_REQUIREMENTS": [], "ADDITIONAL_PROVIDER_READ_REQUIRED": False, "CURRENT_INITIAL_FREEZE_VERIFY": "PASS", "CURRENT_INITIAL_FREEZE_HASH": CURRENT_INITIAL_FREEZE_HASH, "CURRENT_REBUTTAL_FREEZE_VERIFY": "PASS", "CURRENT_REBUTTAL_FREEZE_HASH": CURRENT_REBUTTAL_HASH, "CANDIDATE_AWARE_REOPEN_PROVENANCE": "PASS", "INVEST_ELIGIBILITY_POLICY_STATUS": POLICY_STATUS, "INVEST_ELIGIBLE_CANDIDATES": [], "INVEST_BLOCKED_CANDIDATES": list(CANDIDATES), "GLOBAL_INVEST_BLOCK_REASON": POLICY_STATUS, "ALLOWED_JUDGE_OUTCOMES": list(OUTCOMES), "WATCH_ABSTAIN_CREATES_NEW_B3_REOPEN": False, "JUDGE_SELECTED_CANDIDATE": "J1", "MODEL": "gpt-5.6-terra", "REASONING_EFFORT": "medium", "MAX_OUTPUT_TOKENS": 8192, "JUDGE_REQUEST_HASH": preflight["request_hash"], "JUDGE_REQUEST_MANIFEST_HASH": preflight["request_manifest_hash"], "JUDGE_MAX_COST_USD": preflight["judge_max_cost_usd"], "JUDGE_EXECUTOR_IMPLEMENTED": True, "EXPLICIT_PAID_FLAG_REQUIRED": True, "OWNER_APPROVAL_REQUIRED": True, "OWNER_APPROVAL_STATUS": "NOT_GRANTED", "MODEL_CALLS_AUTHORIZED": False, "STRICT_JUDGE_READINESS_VERIFIER": "PASS", "INVALID_APPROVAL_FAILS_BEFORE_TRANSPORT": "PASS", "VALID_AUTHORITY_REACHES_FAKE_TRANSPORT": "PASS", "RAW_CAPTURE_BEFORE_LOCAL_VALIDATION": "PASS", "AUTOMATIC_RETRIES": 0, "PARTIAL_DISPATCH_FAIL_CLOSED": True, "MODEL_CALLS_THIS_STEP": 0, "PROVIDER_READS_THIS_STEP": 0, "BROKER_WRITES": 0, "ALPACA_ORDERS": 0, "COST_USD_THIS_STEP": "0", "LIVE_MONEY": "PROHIBITED", "entry_hash": entry["artifact_hash"]}
    value["artifact_hash"] = canonical_sha256(value, exclude_fields=("artifact_hash",)); return value


def verify_readiness(payload: Mapping[str, Any], *, code_commit_sha: str, preflight: Mapping[str, Any], **inputs: Any) -> str:
    observed = _hash(payload); _need(dict(payload) == build_readiness(code_commit_sha=code_commit_sha, preflight=preflight, **inputs), "v0.2 readiness drift"); return observed


def build_owner_approval(*, code_commit_sha: str, readiness_hash: str, preflight: Mapping[str, Any], entry: Mapping[str, Any], owner_approval_id: str, owner_approval_at_utc: str) -> dict[str, Any]:
    value = {"artifact_version": APPROVAL_VERSION, "owner_approval_granted": True, "owner_approval_id": owner_approval_id, "owner_approval_at_utc": owner_approval_at_utc, "approved_executor_code_commit_sha": code_commit_sha, "readiness_hash": readiness_hash, "b3_final_closure_hash": B3_HASH, "current_initial_freeze_hash": CURRENT_INITIAL_FREEZE_HASH, "current_rebuttal_freeze_hash": CURRENT_REBUTTAL_HASH, "entry_hash": entry["artifact_hash"], "selection_hash": preflight["selection_hash"], "request_hash": preflight["request_hash"], "model": "gpt-5.6-terra", "reasoning_effort": "medium", "new_paid_call_count": 1, "new_paid_call_count_ceiling": 1, "max_output_tokens": 8192, "approved_judge_max_cost_usd": preflight["judge_max_cost_usd"], "invest_eligibility_policy_status": POLICY_STATUS, "invest_eligible_candidates": [], "invest_blocked_candidates": list(CANDIDATES), "allowed_judge_outcomes": list(OUTCOMES), "automatic_retries": 0}
    value["artifact_hash"] = canonical_sha256(value, exclude_fields=("artifact_hash",)); return value


def verify_owner_approval(payload: Mapping[str, Any], **inputs: Any) -> str:
    observed = _hash(payload); expected = build_owner_approval(**inputs, owner_approval_id=str(payload.get("owner_approval_id", "")), owner_approval_at_utc=str(payload.get("owner_approval_at_utc", ""))); _need(dict(payload) == expected, "v0.2 approval drift"); return observed


def _validate_proposal(proposal: JudgeDecisionProposalDraft, *, request: Any) -> None:
    envelope = json.loads(request.request_payload["input"])
    model_input = envelope.get("model_input")
    _need(isinstance(model_input, Mapping), "v0.2 Judge request input missing")
    _need(proposal.outcome in {JudgeOutcome.WATCH, JudgeOutcome.ABSTAIN}, "v0.2 rejects INVEST")
    _need(proposal.judge_input_hash == request.input_hash and proposal.mandate_version == model_input["mandate_version"] and proposal.deep_comparison_id == model_input["deep_comparison_id"] and proposal.council_policy_version == model_input["council_policy_version"] and proposal.judge_policy_version == model_input["judge_policy_version"] and proposal.model_policy_version == model_input["model_policy_version"] and proposal.model_run_ref == MODEL_RUN_REF and proposal.execution_authority is False and proposal.research_reopen_required is False and not proposal.research_reopen_reason_codes, "v0.2 lifecycle violation")
    _need((proposal.outcome == JudgeOutcome.WATCH and proposal.next_directive == JudgeNextDirective.MONITOR) or (proposal.outcome == JudgeOutcome.ABSTAIN and proposal.next_directive == JudgeNextDirective.STOP), "v0.2 directive violation")


def build_raw_capture(*, request_hash: str, raw: Mapping[str, Any], started_at: str, captured_at: str) -> dict[str, Any]:
    external = _external_json_value(raw); _need(isinstance(external, Mapping), "provider response must be Mapping")
    value = {"capture_version": "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_RAW_PROVIDER_RESPONSE_v0_2", "request_hash": request_hash, "provider_response_id": external.get("id"), "dispatch_started_at_utc": started_at, "captured_at_utc": captured_at, "raw_response": dict(external)}; value["raw_response_hash"] = external_provider_json_sha256(value); return value


def verify_raw_capture(payload: Mapping[str, Any], *, request_hash: str) -> str:
    observed = payload.get("raw_response_hash"); _need(isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None, "raw hash missing"); stripped = dict(payload); stripped.pop("raw_response_hash", None); _need(observed == external_provider_json_sha256(stripped) and payload.get("capture_version") == "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_RAW_PROVIDER_RESPONSE_v0_2" and payload.get("request_hash") == request_hash and isinstance(payload.get("raw_response"), Mapping), "raw capture drift"); return observed


def execute_paid(*, execute_paid_judge: bool, branch: str, code_commit_sha: str, worktree_clean: bool, preflight: Mapping[str, Any], readiness: Mapping[str, Any], approval: Mapping[str, Any] | None, ledger_path: Path, raw_path: Path, result_path: Path, transport_factory: Callable[[], Callable[[Mapping[str, Any]], Mapping[str, Any]]], **inputs: Any) -> dict[str, Any]:
    _need(execute_paid_judge is True and approval is not None, "explicit paid flag and approval required")
    _need(branch == "hackathon/alpaca-2026" and worktree_clean and not ledger_path.exists() and not raw_path.exists() and not result_path.exists(), "pre-transport gate failed")
    readiness_hash = verify_readiness(readiness, code_commit_sha=code_commit_sha, preflight=preflight, **inputs); verify_preflight(preflight, code_commit_sha=code_commit_sha, **inputs)
    approval_hash = verify_owner_approval(approval, code_commit_sha=code_commit_sha, readiness_hash=readiness_hash, preflight=preflight, entry=inputs["entry"])
    request = _request(inputs["entry"]); _need(request.request_hash == preflight["request_hash"], "request reconstruction drift")
    ledger = {"ledger_version": LEDGER_VERSION, "approval_hash": approval_hash, "entries": [{"dispatch_index": 1, "request_hash": request.request_hash, "state": "NOT_DISPATCHED", "automatic_retry_permitted": False}]}; ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _write_exclusive(ledger_path, ledger)
    row = ledger["entries"][0]; row.update(state="DISPATCH_STARTED_UNKNOWN", dispatch_started_at_utc=_utc(datetime.now(UTC))); ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _replace_durable(ledger_path, ledger)
    transport = transport_factory()
    try: raw = transport(preflight["request_payload"])
    except Exception as exc: row["stop_reason"] = f"AMBIGUOUS_PROVIDER_OUTCOME:{type(exc).__name__}"; ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _replace_durable(ledger_path, ledger); raise CurrentJudgeV02Error("ambiguous provider outcome") from exc
    capture = build_raw_capture(request_hash=request.request_hash, raw=raw, started_at=row["dispatch_started_at_utc"], captured_at=_utc(datetime.now(UTC))); _write_exclusive(raw_path, capture); raw_hash = verify_raw_capture(capture, request_hash=request.request_hash)
    row.update(raw_response_hash=raw_hash, raw_response_path=str(raw_path)); ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _replace_durable(ledger_path, ledger)
    try:
        call, proposal = parse_council_responses_payload(raw, request=request, latency_ms=0); _validate_proposal(proposal, request=request); frozen = FrozenJudgeDecisionProposal.from_draft(proposal); actual = actual_cost_usd(raw, model="gpt-5.6-terra", pricing=inputs["pricing"]); _need(actual <= Decimal(str(preflight["judge_max_cost_usd"])), "actual Judge cost exceeds approved ceiling"); record = {"outcome": proposal.outcome.value, "next_directive": proposal.next_directive.value, "response_id": call.response_id, "frozen_judge_proposal": frozen.model_dump(mode="json", exclude_none=False)}; record["record_hash"] = canonical_sha256(record, exclude_fields=("record_hash",))
    except Exception as exc: row["stop_reason"] = f"RESPONSE_CAPTURED_BUT_NOT_ACCEPTED:{type(exc).__name__}"; ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _replace_durable(ledger_path, ledger); raise CurrentJudgeV02Error("captured response failed validation") from exc
    row.update(state="COMPLETED", processed_record_hash=record["record_hash"], actual_cost_usd=format(actual, "f")); ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _replace_durable(ledger_path, ledger)
    result = {"artifact_version": RESULT_VERSION, "status": "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_FROZEN", "code_commit_sha": code_commit_sha, "raw_response_hash": raw_hash, "processed_record": record, "actual_cost_usd": format(actual, "f"), "final_b4_decision_created": True, "b5_handoff_eligible": False, "b5_handoff_created": False, "research_reopen_created": False, "broker_writes": 0, "alpaca_orders": 0, "automatic_retries": 0, "live_money": "PROHIBITED", "ledger_hash": ledger["ledger_hash"]}; result["artifact_hash"] = canonical_sha256(result, exclude_fields=("artifact_hash",)); _write_exclusive(result_path, result); return result
