"""Current-lineage B3-closure-aware Judge preflight and fail-closed executor.

Historical Judge artifacts remain deliberately untouched.  This module is the
single place where a closed B3 requirement is distinguished from a material
decision uncertainty: the latter stays visible to the Judge but cannot create
another B3 request without a newly frozen canonical requirement.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import MATERIAL_CLAIM_V1

from .bounded_request import assert_bounded_request_invariants, build_bounded_judge_request
from .initial_runtime_cost_v02 import actual_cost_usd, runtime_cost_upper_bound_usd
from .judge_model_selection_v01 import build_judge_selected_model_authority, verify_judge_selected_model_authority
from .judge_production import _usage_counts
from .model_policy import JUDGE_MODEL_LADDER, CouncilModelStage, STAGE_MAX_OUTPUT_TOKENS
from .post_research_reopen_initial_execute_production_v01 import _external_json_value, _replace_durable, _write_exclusive, external_provider_json_sha256, frozen_initial_items
from .post_research_reopen_rebuttal_production_v01 import CURRENT_INITIAL_FREEZE_HASH, verify_current_initial_freeze
from .proposal import FrozenJudgeDecisionProposal, JudgeDecisionProposalDraft, JudgeNextDirective, JudgeOutcome
from .rebuttal_runtime_execution import validate_rebuttal_processed_record
from .request import parse_council_responses_payload


B3_HASH = "ad1f477df9094e40c26186a045c6ca5794cc1cf4c415929bb1453cd09b1e4149"
CURRENT_REBUTTAL_HASH = "18b854261c9b49c1fcd2addfd66af52fda54e71b0949416f7dc1cdfed3e8fd9e"
CURRENT_REBUTTAL_RECORD_HASHES = (
    "a498c9e31912b828fd649bb4d90e1aefcd443acd92ada2408d615db139f80575",
    "c3aa9fc835df7dcfb5649e8f18efee650c137a7933dabcbc70251f3d9515e09e",
    "cf3c12e4556d2d1501b279427d2b44b3c3f806eb681afccd057c63c122e62736",
)
CANDIDATES = ("NVDA", "MSFT", "META")
ENTRY_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_ENTRY_v0_1"
PREFLIGHT_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_PREFLIGHT_ZERO_CALL_v0_1"
READINESS_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_READINESS_ZERO_CALL_v0_1"
APPROVAL_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_OWNER_APPROVAL_v0_1"
LEDGER_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_PAID_DISPATCH_LEDGER_v0_1"
RESULT_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_COUNCIL_FREEZE_v0_1"
MODEL_RUN_REF = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_J1"


class CurrentJudgeError(RuntimeError):
    pass


def _need(ok: bool, message: str) -> None:
    if not ok:
        raise CurrentJudgeError(message)


def _hash(payload: Mapping[str, Any], field: str = "artifact_hash") -> str:
    observed = payload.get(field)
    _need(isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None, f"{field} missing")
    _need(observed == canonical_sha256(payload, exclude_fields=(field,)), f"{field} mismatch")
    return observed


def _utc(now: datetime) -> str:
    return now.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _selected() -> Any:
    rows = [row for row in JUDGE_MODEL_LADDER if row.candidate_key == "J1"]
    _need(len(rows) == 1, "J1 selection missing")
    selected = rows[0]
    _need((selected.model, selected.reasoning_effort, selected.ladder_position) == ("gpt-5.6-terra", "medium", 1), "J1 policy drift")
    _need(STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.JUDGE] == 8192, "Judge output cap drift")
    return selected


def verify_b3_final_closure(closure: Mapping[str, Any]) -> str:
    observed = _hash(closure)
    _need(observed == B3_HASH, "B3 final closure hash drift")
    exact = {
        "artifact_version": "B3_RESEARCH_REOPEN_FINAL_COMPETITION_CLOSURE_v0_1",
        "status": "B3_RESEARCH_REOPEN_CLOSED_FOR_NEW_B4_VERDICT",
        "canonical_research_reopen_closed": True,
        "remaining_canonical_reopen_requirement_ids": [],
        "additional_provider_read_required_before_b4": False,
        "research_reopen_request_satisfied_for_return_to_b4": True,
        "new_b4_verdict_required": True,
        "next_gate": "B4_POST_RESEARCH_REOPEN_VERDICT_PREFLIGHT_ZERO_CALL",
        "judge_meta_change_conditions_preserved_as_b4_decision_context": True,
        "judge_meta_change_conditions_reclassified_as_canonical_reopen_requirements": False,
    }
    for key, value in exact.items():
        _need(closure.get(key) == value, f"B3 final closure drift: {key}")
    rows = closure.get("requirement_closures")
    _need(isinstance(rows, list) and len(rows) == 3, "B3 closure rows drift")
    observed_rows = {(row.get("requirement_id"), row.get("closure_status")) for row in rows if isinstance(row, Mapping)}
    _need(observed_rows == {
        ("NVDA_CURRENT_DEVELOPMENTS_Q4", "CLOSED_DECISION_USABLE_NONEXHAUSTIVE"),
        ("MSFT_VALUATION_CONTEXT_DEPTH", "CLOSED_COMPARATIVE_CONTEXT_PRESENT_ATTRACTIVENESS_NOT_ESTABLISHED"),
        ("MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY", "CLOSED_AS_NOT_ESTABLISHED_MATERIAL_RISK"),
    }, "B3 closure semantics drift")
    return observed


def verify_current_rebuttal_freeze(payload: Mapping[str, Any]) -> str:
    observed = _hash(payload)
    _need(observed == CURRENT_REBUTTAL_HASH, "current Rebuttal freeze hash drift")
    exact = {
        "artifact_version": "B4_POST_RESEARCH_REOPEN_REBUTTAL_PRODUCTION_COUNCIL_FREEZE_v0_1",
        "status": "B4_POST_RESEARCH_REOPEN_REBUTTAL_COUNCIL_FROZEN",
        "code_commit_sha": "d41d07a328b8878eb5d2c50e053fe25fb0ab32e6",
        "rebuttal_actual_cost_usd": "1.384836", "automatic_retries": 0, "judge_authorized": False,
        "b5_handoff_created": False, "broker_writes": 0, "alpaca_orders": 0, "live_money": "PROHIBITED",
    }
    for key, value in exact.items():
        _need(payload.get(key) == value, f"current Rebuttal freeze drift: {key}")
    rows = payload.get("processed_records")
    _need(isinstance(rows, list) and len(rows) == 3, "current Rebuttal record count drift")
    _need(tuple(row.get("candidate_id") for row in rows if isinstance(row, Mapping)) == CANDIDATES, "current Rebuttal candidate order drift")
    _need(tuple(row.get("record_hash") for row in rows if isinstance(row, Mapping)) == CURRENT_REBUTTAL_RECORD_HASHES, "current Rebuttal record hashes drift")
    for row in rows:
        _need(isinstance(row, Mapping), "current Rebuttal record malformed")
        validate_rebuttal_processed_record(row)
        _need(row.get("record_hash") == canonical_sha256(row, exclude_fields=("record_hash",)), "current Rebuttal record self-hash drift")
    return observed


_CLOSED = {
    "ALPACA_NEWS_PAGINATION_INCOMPLETE": ("NVDA_CURRENT_DEVELOPMENTS_Q4", "CLOSED_DECISION_USABLE_NONEXHAUSTIVE"),
    "VALUATION_EVIDENCE_NOT_SUPPLIED": ("MSFT_VALUATION_CONTEXT_DEPTH", "CLOSED_COMPARATIVE_CONTEXT_PRESENT_ATTRACTIVENESS_NOT_ESTABLISHED"),
    "VALUATION_EVIDENCE_MISSING": ("MSFT_VALUATION_CONTEXT_DEPTH", "CLOSED_COMPARATIVE_CONTEXT_PRESENT_ATTRACTIVENESS_NOT_ESTABLISHED"),
    "VALUATION_SPECIFIC_EVIDENCE_NOT_SUPPLIED": ("MSFT_VALUATION_CONTEXT_DEPTH", "CLOSED_COMPARATIVE_CONTEXT_PRESENT_ATTRACTIVENESS_NOT_ESTABLISHED"),
}
_STALE_PREFIXES = ("META_Q4_RECENT_DEVELOPMENTS", "META_VALUATION_CONTEXT_MISSING", "META_PORTFOLIO_INTERACTION_CONTEXT_MISSING")


def _classification(raw: str) -> tuple[str, str | None, str | None]:
    if raw in _CLOSED:
        requirement, status = _CLOSED[raw]
        return "CLOSED_BUT_DECISION_CONTEXT_ONLY", requirement, status
    if raw.startswith(_STALE_PREFIXES):
        return "INVALID_OR_STALE_REOPEN_REASON", None, None
    return "UNMAPPED_REOPEN_REASON", None, None


def build_semantic_parity_audit(*, closure: Mapping[str, Any], initial_freeze: Mapping[str, Any], initial_cost: Mapping[str, Any], rebuttal_freeze: Mapping[str, Any]) -> dict[str, Any]:
    closure_hash = verify_b3_final_closure(closure)
    initial_hash = verify_current_initial_freeze(initial_freeze, initial_cost=initial_cost)
    rebuttal_hash = verify_current_rebuttal_freeze(rebuttal_freeze)
    rows: list[dict[str, Any]] = []
    for record in initial_freeze["processed_records"]:
        structured = record.get("structured_output", {})
        for raw in structured.get("material_unknown_refs", []):
            classification, requirement, status = _classification(raw)
            rows.append({"candidate_id": record["candidate_id"], "source_stage": "CURRENT_INITIAL", "source_record_hash": record["record_hash"], "raw_reason_or_ref": raw, "b3_canonical_requirement_id": requirement, "b3_final_closure_status": status, "lifecycle_classification": classification, "additional_b3_provider_read_authorized": False, "visible_to_judge_as_uncertainty": True, "may_independently_force_new_research_reopen": False})
    for record in rebuttal_freeze["processed_records"]:
        reasons = list(record.get("research_reopen_reason_codes", [])) + list(record.get("required_unknown_refs", []))
        for raw in dict.fromkeys(reasons):
            classification, requirement, status = _classification(raw)
            rows.append({"candidate_id": record["candidate_id"], "source_stage": "CURRENT_REBUTTAL", "source_record_hash": record["record_hash"], "raw_reason_or_ref": raw, "b3_canonical_requirement_id": requirement, "b3_final_closure_status": status, "lifecycle_classification": classification, "additional_b3_provider_read_authorized": False, "visible_to_judge_as_uncertainty": True, "may_independently_force_new_research_reopen": False})
    catalogue = []
    for raw in (*_CLOSED, "META_Q4_RECENT_DEVELOPMENTS*", "META_VALUATION_CONTEXT_MISSING", "META_PORTFOLIO_INTERACTION_CONTEXT_MISSING"):
        classification, requirement, status = _classification(raw.rstrip("*"))
        catalogue.append({"raw_reason_or_ref": raw, "b3_canonical_requirement_id": requirement, "b3_final_closure_status": status, "lifecycle_classification": classification})
    unmapped = [row for row in rows if row["lifecycle_classification"] == "UNMAPPED_REOPEN_REASON"]
    _need(not unmapped, "unmapped genuinely new reopen reason blocks Judge")
    out: dict[str, Any] = {"artifact_version": "B4_POST_RESEARCH_REOPEN_B3_CLOSURE_PARITY_AUDIT_v0_1", "status": "PASS_CLOSED_CONTEXT_NOT_REOPEN_AUTHORITY", "b3_final_closure_hash": closure_hash, "current_initial_freeze_hash": initial_hash, "current_rebuttal_freeze_hash": rebuttal_hash, "classification_contract": ["CANONICAL_REOPEN_REQUIREMENT_OPEN", "CLOSED_BUT_DECISION_CONTEXT_ONLY", "INVALID_OR_STALE_REOPEN_REASON", "UNMAPPED_REOPEN_REASON"], "reason_rows": rows, "catalogue": catalogue, "canonical_open_research_requirements_after_b3": [], "closed_context_reopen_parity": "PASS", "local_lifecycle_closure_parity_bug": any(row["source_stage"] == "CURRENT_REBUTTAL" and row["lifecycle_classification"] == "CLOSED_BUT_DECISION_CONTEXT_ONLY" for row in rows), "new_canonical_requirement_authority_found": False, "additional_provider_read_required": False}
    out["artifact_hash"] = canonical_sha256(out, exclude_fields=("artifact_hash",)); return out


def verify_semantic_parity_audit(payload: Mapping[str, Any], **inputs: Any) -> str:
    observed = _hash(payload); expected = build_semantic_parity_audit(**inputs)
    _need(dict(payload) == expected, "semantic parity audit drift"); return observed


def _claims(initial_freeze: Mapping[str, Any], rebuttal_freeze: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    values: list[dict[str, Any]] = []; insufficient: dict[str, list[str]] = {candidate: [] for candidate in CANDIDATES}
    for source in (initial_freeze["processed_records"], rebuttal_freeze["processed_records"]):
        for record in source:
            for raw in record.get("material_claims", []):
                claim = MATERIAL_CLAIM_V1.model_validate(raw)
                dumped = claim.model_dump(mode="json", exclude_none=False)
                values.append(dumped)
                if str(claim.support_status) == "INSUFFICIENT":
                    insufficient[claim.candidate_id].append(claim.claim_id)
    return values, insufficient


def build_current_judge_entry(*, code_commit_sha: str, closure: Mapping[str, Any], initial_freeze: Mapping[str, Any], initial_cost: Mapping[str, Any], rebuttal_freeze: Mapping[str, Any]) -> dict[str, Any]:
    _need(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha) is not None, "Judge entry requires code SHA")
    audit = build_semantic_parity_audit(closure=closure, initial_freeze=initial_freeze, initial_cost=initial_cost, rebuttal_freeze=rebuttal_freeze)
    claims, insufficient = _claims(initial_freeze, rebuttal_freeze)
    # This is an entry sufficiency rule, not a new research authority: B3 itself
    # freezes MSFT valuation and return-durability as not established material risk.
    blockers = {
        "NVDA": [],
        "MSFT": ["MSFT_VALUATION_ATTRACTIVENESS_NOT_ESTABLISHED", "MSFT_FORWARD_AI_CLOUD_RETURN_DURABILITY_NOT_ESTABLISHED", *insufficient["MSFT"]],
        "META": [],
    }
    eligible = [candidate for candidate in CANDIDATES if not blockers[candidate]]
    _need(eligible == ["NVDA", "META"], "current eligibility derivation drift")
    items = frozen_initial_items(initial_cost)
    sample = items[0].plan_item.bundle
    out: dict[str, Any] = {"artifact_version": ENTRY_VERSION, "status": "PASS_CURRENT_LINEAGE_JUDGE_ENTRY_DERIVED", "code_commit_sha": code_commit_sha, "b3_final_closure_hash": audit["b3_final_closure_hash"], "current_initial_freeze_hash": audit["current_initial_freeze_hash"], "current_rebuttal_freeze_hash": audit["current_rebuttal_freeze_hash"], "initial_processed_record_hashes": initial_freeze["processed_record_hashes"], "rebuttal_processed_record_hashes": rebuttal_freeze["processed_record_hashes"], "semantic_parity_audit_hash": audit["artifact_hash"], "canonical_open_research_requirements_after_b3": [], "decision_context_uncertainties": list(closure["b4_input_overlay"]["unresolved_uncertainties_are_decision_inputs_not_reopen_triggers"]), "candidate_sufficiency": [{"candidate_id": candidate, "invest_eligibility": "INVEST_ELIGIBLE" if candidate in eligible else "INVEST_BLOCKED", "deterministic_reason_codes": blockers[candidate]} for candidate in CANDIDATES], "invest_eligible_candidates": eligible, "invest_blocked_candidates": [candidate for candidate in CANDIDATES if candidate not in eligible], "allowed_judge_outcomes": ["INVEST", "WATCH", "ABSTAIN"] if eligible else ["WATCH", "ABSTAIN"], "watch_abstain_creates_new_b3_reopen": False, "mandate_version": sample.mandate_version, "deep_comparison_id": sample.deep_comparison_id, "council_policy_version": sample.council_policy_version, "judge_policy_version": sample.judge_policy_version, "model_policy_version": sample.model_policy_version, "material_claims": claims, "model_calls": 0, "provider_reads": 0, "broker_writes": 0, "alpaca_orders": 0, "live_money": "PROHIBITED"}
    out["artifact_hash"] = canonical_sha256(out, exclude_fields=("artifact_hash",)); return out


def verify_current_judge_entry(payload: Mapping[str, Any], **inputs: Any) -> str:
    observed = _hash(payload); expected = build_current_judge_entry(**inputs)
    _need(dict(payload) == expected, "current Judge entry drift"); return observed


def verify_historical_judge_selection(selection: Mapping[str, Any], *, eval_artifact: Mapping[str, Any], receipts: Sequence[Mapping[str, Any]]) -> str:
    rebuilt = build_judge_selected_model_authority(eval_artifact, receipts)
    _need(dict(selection) == rebuilt, "historical Judge selection replay drift")
    observed = verify_judge_selected_model_authority(selection)
    _need(selection.get("selected_candidate") == {"candidate_key": "J1", "model": "gpt-5.6-terra", "reasoning_effort": "medium", "ladder_position": 1}, "historical J1 selection drift")
    _selected(); return observed


def _request(entry: Mapping[str, Any], selection: Mapping[str, Any]) -> Any:
    selected = _selected()
    base = {"current_judge_lifecycle_contract": "B3_CLOSED_CONTEXT_ONLY_v0_1", "b3_canonical_research_reopen_closed": True, "remaining_canonical_reopen_requirement_ids": [], "judge_must_not_reopen_closed_context": True, "candidate_eligibility": entry["candidate_sufficiency"], "allowed_outcomes": entry["allowed_judge_outcomes"], "decision_context_uncertainties": entry["decision_context_uncertainties"], "material_claims": entry["material_claims"], "source_lineage": {"b3_final_closure_hash": entry["b3_final_closure_hash"], "current_initial_freeze_hash": entry["current_initial_freeze_hash"], "current_rebuttal_freeze_hash": entry["current_rebuttal_freeze_hash"], "entry_hash": entry["artifact_hash"]}}
    judge_input_hash = canonical_sha256(base); model_input = dict(base); model_input.update({"judge_input_hash": judge_input_hash, "mandate_version": entry["mandate_version"], "deep_comparison_id": entry["deep_comparison_id"], "council_policy_version": entry["council_policy_version"], "judge_policy_version": entry["judge_policy_version"], "model_policy_version": entry["model_policy_version"]})
    claim_ids = tuple(row["claim_id"] for row in entry["material_claims"])
    unknowns = tuple(entry["decision_context_uncertainties"])
    request = build_bounded_judge_request(model_candidate=selected, model_input=model_input, candidate_ids=CANDIDATES, mandate_version=entry["mandate_version"], deep_comparison_id=entry["deep_comparison_id"], judge_input_hash=judge_input_hash, council_policy_version=entry["council_policy_version"], judge_policy_version=entry["judge_policy_version"], model_policy_version=entry["model_policy_version"], model_run_ref=MODEL_RUN_REF, allowed_claim_ids=claim_ids, allowed_unknown_refs=unknowns, allowed_condition_refs=tuple(dict.fromkeys((*claim_ids, *unknowns))))
    assert_bounded_request_invariants(request)
    _need(request.request_payload.get("model") == "gpt-5.6-terra" and request.request_payload.get("reasoning") == {"effort": "medium"} and request.request_payload.get("max_output_tokens") == 8192, "current Judge request policy drift")
    return request


def build_current_judge_preflight(*, code_commit_sha: str, closure: Mapping[str, Any], initial_freeze: Mapping[str, Any], initial_cost: Mapping[str, Any], rebuttal_freeze: Mapping[str, Any], entry: Mapping[str, Any], selection: Mapping[str, Any], eval_artifact: Mapping[str, Any], receipts: Sequence[Mapping[str, Any]], pricing: Mapping[str, Any], historical_request_hashes: Sequence[str]) -> dict[str, Any]:
    entry_hash = verify_current_judge_entry(entry, code_commit_sha=code_commit_sha, closure=closure, initial_freeze=initial_freeze, initial_cost=initial_cost, rebuttal_freeze=rebuttal_freeze)
    selection_hash = verify_historical_judge_selection(selection, eval_artifact=eval_artifact, receipts=receipts)
    request = _request(entry, selection)
    _need(request.request_hash not in historical_request_hashes, "historical Judge request hash reuse")
    body_bytes = len(json.dumps(request.request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    max_cost = runtime_cost_upper_bound_usd(model="gpt-5.6-terra", input_tokens_upper_bound=body_bytes, output_tokens_upper_bound=8192, call_count=1, pricing=pricing)
    out: dict[str, Any] = {"artifact_version": PREFLIGHT_VERSION, "status": "PASS_ZERO_CALL_CURRENT_JUDGE_PREFLIGHT", "code_commit_sha": code_commit_sha, "b3_final_closure_hash": B3_HASH, "current_initial_freeze_hash": CURRENT_INITIAL_FREEZE_HASH, "current_rebuttal_freeze_hash": CURRENT_REBUTTAL_HASH, "current_judge_entry_hash": entry_hash, "historical_judge_selection_authority_hash": selection_hash, "historical_judge_selection_revalidated": True, "historical_judge_outputs_reused": False, "historical_judge_request_hashes_reused": False, "model": "gpt-5.6-terra", "reasoning_effort": "medium", "judge_selected_candidate": "J1", "new_paid_calls_planned": 1, "new_paid_call_count_ceiling": 1, "max_output_tokens": 8192, "request_hash": request.request_hash, "request_manifest_hash": canonical_sha256({"request_hashes": [request.request_hash]}), "request_payload": request.request_payload, "judge_input_hash": request.input_hash, "request_body_utf8_bytes": body_bytes, "input_tokens_upper_bound": body_bytes, "pricing_hash": _hash(pricing, "pricing_hash"), "pricing_version": pricing["pricing_version"], "judge_max_cost_usd": format(max_cost, "f"), "automatic_retries": 0, "owner_approval_required": True, "owner_approval_status": "NOT_GRANTED", "model_calls_authorized": False, "provider_reads_authorized": False, "broker_writes": 0, "alpaca_orders": 0, "live_money": "PROHIBITED", "model_calls_this_step": 0, "provider_reads_this_step": 0, "cost_usd_this_step": "0"}
    out["artifact_hash"] = canonical_sha256(out, exclude_fields=("artifact_hash",)); return out


def verify_current_judge_preflight(payload: Mapping[str, Any], **inputs: Any) -> str:
    observed = _hash(payload); expected = build_current_judge_preflight(**inputs)
    _need(dict(payload) == expected, "current Judge preflight drift"); return observed


def build_current_judge_readiness(*, code_commit_sha: str, preflight: Mapping[str, Any], **inputs: Any) -> dict[str, Any]:
    preflight_hash = verify_current_judge_preflight(preflight, code_commit_sha=code_commit_sha, **inputs)
    out: dict[str, Any] = {"artifact_version": READINESS_VERSION, "status": "PASS_ZERO_CALL_CURRENT_JUDGE_READINESS", "code_commit_sha": code_commit_sha, "source_judge_preflight_hash": preflight_hash, "B3_FINAL_CLOSURE_VERIFY": "PASS", "B3_FINAL_CLOSURE_HASH": B3_HASH, "REMAINING_CANONICAL_REOPEN_REQUIREMENTS": [], "ADDITIONAL_PROVIDER_READ_REQUIRED": False, "CURRENT_INITIAL_FREEZE_VERIFY": "PASS", "CURRENT_INITIAL_FREEZE_HASH": CURRENT_INITIAL_FREEZE_HASH, "CURRENT_REBUTTAL_FREEZE_VERIFY": "PASS", "CURRENT_REBUTTAL_FREEZE_HASH": CURRENT_REBUTTAL_HASH, "CURRENT_REBUTTAL_RECORD_COUNT": 3, "CURRENT_REBUTTAL_ACTUAL_COST_USD": "1.384836", "CLOSED_CONTEXT_REOPEN_PARITY": "PASS", "CANONICAL_OPEN_RESEARCH_REQUIREMENTS_AFTER_B3": [], "JUDGE_CURRENT_LINEAGE_ENTRY_DERIVED": "PASS", "INVEST_ELIGIBLE_CANDIDATES": inputs["entry"]["invest_eligible_candidates"], "INVEST_BLOCKED_CANDIDATES": inputs["entry"]["invest_blocked_candidates"], "ALLOWED_JUDGE_OUTCOMES": inputs["entry"]["allowed_judge_outcomes"], "WATCH_ABSTAIN_CREATES_NEW_B3_REOPEN": False, "JUDGE_SELECTED_CANDIDATE": "J1", "MODEL": "gpt-5.6-terra", "REASONING_EFFORT": "medium", "MAX_OUTPUT_TOKENS": 8192, "JUDGE_REQUEST_HASH": preflight["request_hash"], "JUDGE_REQUEST_MANIFEST_HASH": preflight["request_manifest_hash"], "JUDGE_MAX_COST_USD": preflight["judge_max_cost_usd"], "JUDGE_EXECUTOR_IMPLEMENTED": True, "EXPLICIT_PAID_FLAG_REQUIRED": True, "OWNER_APPROVAL_REQUIRED": True, "OWNER_APPROVAL_STATUS": "NOT_GRANTED", "MODEL_CALLS_AUTHORIZED": False, "AUTOMATIC_RETRIES": 0, "PARTIAL_DISPATCH_FAIL_CLOSED": True, "MODEL_CALLS_THIS_STEP": 0, "PROVIDER_READS_THIS_STEP": 0, "BROKER_WRITES": 0, "ALPACA_ORDERS": 0, "COST_USD_THIS_STEP": "0", "LIVE_MONEY": "PROHIBITED"}
    out["artifact_hash"] = canonical_sha256(out, exclude_fields=("artifact_hash",)); return out


def verify_current_judge_readiness(payload: Mapping[str, Any], *, code_commit_sha: str, preflight: Mapping[str, Any], **inputs: Any) -> str:
    observed = _hash(payload); expected = build_current_judge_readiness(code_commit_sha=code_commit_sha, preflight=preflight, **inputs)
    _need(dict(payload) == expected, "current Judge readiness drift"); return observed


def build_judge_owner_approval(*, code_commit_sha: str, readiness_hash: str, preflight: Mapping[str, Any], entry: Mapping[str, Any], owner_approval_id: str, owner_approval_at_utc: str) -> dict[str, Any]:
    out = {"artifact_version": APPROVAL_VERSION, "owner_approval_granted": True, "owner_approval_id": owner_approval_id, "owner_approval_at_utc": owner_approval_at_utc, "approved_executor_code_commit_sha": code_commit_sha, "judge_readiness_hash": readiness_hash, "b3_final_closure_hash": B3_HASH, "current_initial_freeze_hash": CURRENT_INITIAL_FREEZE_HASH, "current_rebuttal_freeze_hash": CURRENT_REBUTTAL_HASH, "semantic_parity_adjudication_hash": entry["artifact_hash"], "invest_eligible_candidates": entry["invest_eligible_candidates"], "allowed_judge_outcomes": entry["allowed_judge_outcomes"], "judge_selected_model_authority_hash": preflight["historical_judge_selection_authority_hash"], "request_hash": preflight["request_hash"], "model": "gpt-5.6-terra", "reasoning_effort": "medium", "new_paid_call_count": 1, "new_paid_call_count_ceiling": 1, "max_output_tokens": 8192, "approved_judge_max_cost_usd": preflight["judge_max_cost_usd"], "automatic_retries": 0}
    out["artifact_hash"] = canonical_sha256(out, exclude_fields=("artifact_hash",)); return out


def verify_judge_owner_approval(approval: Mapping[str, Any], *, code_commit_sha: str, readiness_hash: str, preflight: Mapping[str, Any], entry: Mapping[str, Any]) -> str:
    observed = _hash(approval); expected = build_judge_owner_approval(code_commit_sha=code_commit_sha, readiness_hash=readiness_hash, preflight=preflight, entry=entry, owner_approval_id=str(approval.get("owner_approval_id", "")), owner_approval_at_utc=str(approval.get("owner_approval_at_utc", "")))
    _need(dict(approval) == expected, "Judge owner approval drift"); return observed


def materialize_judge_owner_approval(path: Path, **kwargs: Any) -> dict[str, Any]:
    approval = build_judge_owner_approval(**kwargs); _write_exclusive(path, approval); return approval


def build_judge_raw_response_capture(*, request_hash: str, provider_response: Mapping[str, Any], dispatch_started_at_utc: str, captured_at_utc: str) -> dict[str, Any]:
    raw = _external_json_value(provider_response); _need(isinstance(raw, Mapping), "provider response must be Mapping")
    response_id = raw.get("id"); _need(response_id is None or isinstance(response_id, str), "provider response id malformed")
    out = {"capture_version": "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_RAW_PROVIDER_RESPONSE_v0_1", "request_hash": request_hash, "provider_response_id": response_id, "dispatch_started_at_utc": dispatch_started_at_utc, "captured_at_utc": captured_at_utc, "raw_response": dict(raw)}
    out["raw_response_hash"] = external_provider_json_sha256(out); return out


def verify_judge_raw_response_capture(capture: Mapping[str, Any], *, request_hash: str) -> str:
    observed = capture.get("raw_response_hash"); _need(isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None, "raw response hash missing")
    comparable = dict(capture); comparable.pop("raw_response_hash", None)
    _need(observed == external_provider_json_sha256(comparable), "raw response hash mismatch")
    _need(capture.get("capture_version") == "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_RAW_PROVIDER_RESPONSE_v0_1" and capture.get("request_hash") == request_hash and isinstance(capture.get("raw_response"), Mapping), "raw response capture drift")
    return observed


def _validate_proposal(proposal: JudgeDecisionProposalDraft, *, entry: Mapping[str, Any], request: Any) -> None:
    _need(proposal.outcome.value in entry["allowed_judge_outcomes"], "Judge outcome outside current contract")
    _need(proposal.judge_input_hash == request.input_hash and proposal.mandate_version == entry["mandate_version"] and proposal.deep_comparison_id == entry["deep_comparison_id"], "Judge proposal lineage mismatch")
    _need(proposal.execution_authority is False and proposal.research_reopen_required is False and not proposal.research_reopen_reason_codes, "current closed B3 Judge cannot request research")
    if proposal.outcome == JudgeOutcome.INVEST:
        _need(proposal.primary_candidate_id in entry["invest_eligible_candidates"] and proposal.next_directive == JudgeNextDirective.PROMOTE_FINAL_DECISION, "Judge INVEST violates eligibility")
    elif proposal.outcome == JudgeOutcome.WATCH:
        _need(proposal.next_directive == JudgeNextDirective.MONITOR, "WATCH must terminally monitor")
    else:
        _need(proposal.next_directive == JudgeNextDirective.STOP, "ABSTAIN must terminally stop")


def _process(raw: Mapping[str, Any], *, request: Any, entry: Mapping[str, Any], pricing: Mapping[str, Any]) -> tuple[dict[str, Any], Decimal]:
    """Parse only after the immutable transport capture has been fsynced."""
    call, proposal = parse_council_responses_payload(raw, request=request, latency_ms=0)
    _validate_proposal(proposal, entry=entry, request=request)
    frozen = FrozenJudgeDecisionProposal.from_draft(proposal)
    usage = _usage_counts(raw)
    cost = actual_cost_usd(raw, model="gpt-5.6-terra", pricing=pricing)
    record: dict[str, Any] = {
        "request_hash": request.request_hash,
        "response_id": call.response_id,
        "outcome": proposal.outcome.value,
        "frozen_judge_proposal": frozen.model_dump(mode="json", exclude_none=False),
        "usage": {"input_tokens": usage[0], "cached_tokens": usage[1], "cache_write_tokens": usage[2], "output_tokens": usage[3], "reasoning_tokens": usage[4]},
        "actual_cost_usd": format(cost, "f"),
    }
    record["record_hash"] = canonical_sha256(record, exclude_fields=("record_hash",))
    return record, cost


def execute_paid_judge(*, execute_paid_judge: bool, branch: str, code_commit_sha: str, worktree_clean: bool, closure: Mapping[str, Any], initial_freeze: Mapping[str, Any], initial_cost: Mapping[str, Any], rebuttal_freeze: Mapping[str, Any], entry: Mapping[str, Any], preflight: Mapping[str, Any], readiness: Mapping[str, Any], selection: Mapping[str, Any], eval_artifact: Mapping[str, Any], receipts: Sequence[Mapping[str, Any]], pricing: Mapping[str, Any], historical_request_hashes: Sequence[str], approval: Mapping[str, Any] | None, ledger_path: Path, raw_path: Path, result_path: Path, transport_factory: Callable[[], Callable[[Mapping[str, Any]], Mapping[str, Any]]], now: Callable[[], datetime] = lambda: datetime.now(UTC), process: Callable[[Mapping[str, Any]], tuple[dict[str, Any], Decimal]] | None = None) -> dict[str, Any]:
    _need(execute_paid_judge is True, "--execute-paid-judge is required"); _need(approval is not None, "exact Judge owner approval required")
    _need(branch == "hackathon/alpaca-2026" and worktree_clean and not ledger_path.exists() and not raw_path.exists() and not result_path.exists(), "Judge pre-transport gate failed")
    _need(verify_b3_final_closure(closure) == B3_HASH and closure["remaining_canonical_reopen_requirement_ids"] == [] and closure["additional_provider_read_required_before_b4"] is False, "B3 gate failed")
    readiness_hash = verify_current_judge_readiness(readiness, code_commit_sha=code_commit_sha, preflight=preflight, closure=closure, initial_freeze=initial_freeze, initial_cost=initial_cost, rebuttal_freeze=rebuttal_freeze, entry=entry, selection=selection, eval_artifact=eval_artifact, receipts=receipts, pricing=pricing, historical_request_hashes=historical_request_hashes)
    verify_current_judge_preflight(preflight, code_commit_sha=code_commit_sha, closure=closure, initial_freeze=initial_freeze, initial_cost=initial_cost, rebuttal_freeze=rebuttal_freeze, entry=entry, selection=selection, eval_artifact=eval_artifact, receipts=receipts, pricing=pricing, historical_request_hashes=historical_request_hashes)
    approval_hash = verify_judge_owner_approval(approval, code_commit_sha=code_commit_sha, readiness_hash=readiness_hash, preflight=preflight, entry=entry)
    request = _request(entry, selection)
    _need(request.request_hash == preflight["request_hash"] and request.request_payload == preflight["request_payload"], "current Judge request reconstruction drift")
    ledger: dict[str, Any] = {"ledger_version": LEDGER_VERSION, "owner_approval_hash": approval_hash, "entries": [{"dispatch_index": 1, "request_hash": preflight["request_hash"], "state": "NOT_DISPATCHED", "automatic_retry_permitted": False}]}; ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _write_exclusive(ledger_path, ledger)
    entry_ledger = ledger["entries"][0]; entry_ledger.update(state="DISPATCH_STARTED_UNKNOWN", dispatch_started_at_utc=_utc(now())); ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _replace_durable(ledger_path, ledger)
    transport = transport_factory()
    try:
        raw = transport(preflight["request_payload"])
    except Exception as exc:
        entry_ledger["stop_reason"] = f"AMBIGUOUS_PROVIDER_OUTCOME:{type(exc).__name__}"; ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _replace_durable(ledger_path, ledger); raise CurrentJudgeError("ambiguous Judge provider outcome") from exc
    _need(isinstance(raw, Mapping), "provider response must be Mapping")
    capture = build_judge_raw_response_capture(request_hash=preflight["request_hash"], provider_response=raw, dispatch_started_at_utc=entry_ledger["dispatch_started_at_utc"], captured_at_utc=_utc(now())); _write_exclusive(raw_path, capture); raw_hash = verify_judge_raw_response_capture(capture, request_hash=preflight["request_hash"])
    entry_ledger.update(raw_response_hash=raw_hash, raw_response_path=str(raw_path), response_captured_at_utc=capture["captured_at_utc"]); ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _replace_durable(ledger_path, ledger)
    try:
        processed, actual = (_process(raw, request=request, entry=entry, pricing=pricing) if process is None else process(raw))
    except Exception as exc:
        entry_ledger["stop_reason"] = f"RESPONSE_CAPTURED_BUT_NOT_ACCEPTED:{type(exc).__name__}"; ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _replace_durable(ledger_path, ledger); raise CurrentJudgeError("captured Judge response failed validation") from exc
    _need(actual <= Decimal(str(preflight["judge_max_cost_usd"])), "Judge actual cost exceeds authority")
    entry_ledger.update(state="COMPLETED", processed_record_hash=processed["record_hash"], actual_cost_usd=format(actual, "f")); ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _replace_durable(ledger_path, ledger)
    outcome = processed["outcome"]
    result = {"artifact_version": RESULT_VERSION, "status": "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_FROZEN", "code_commit_sha": code_commit_sha, "judge_readiness_hash": readiness_hash, "owner_approval_hash": approval_hash, "b3_final_closure_hash": B3_HASH, "current_judge_entry_hash": entry["artifact_hash"], "request_hash": preflight["request_hash"], "raw_response_hash": raw_hash, "processed_record": processed, "judge_actual_cost_usd": format(actual, "f"), "final_b4_decision_created": True, "b5_handoff_eligible": outcome == "INVEST", "b5_handoff_created": False, "research_reopen_created": False, "broker_writes": 0, "alpaca_orders": 0, "live_money": "PROHIBITED", "automatic_retries": 0, "ledger_hash": ledger["ledger_hash"]}; result["artifact_hash"] = canonical_sha256(result, exclude_fields=("artifact_hash",)); _write_exclusive(result_path, result); return result
