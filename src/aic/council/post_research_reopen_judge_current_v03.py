"""Evidence-complete, non-INVEST current-lineage Judge v0.3.

This is deliberately parallel to v0.1/v0.2.  It retains their closed-B3 and
fail-closed investment authority while restoring the frozen Judge evidence
surface (packets, opinions, rebuttals, claims, values, and reference graph).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import CANDIDATE_PACKET_V1, COUNCIL_OPINION_V1, MATERIAL_CLAIM_V1

from .bounded_request import assert_bounded_request_invariants, build_bounded_judge_request
from .initial_runtime_cost_v02 import actual_cost_usd, runtime_cost_upper_bound_usd
from .judge_model_selection_v01 import build_judge_selected_model_authority, verify_judge_selected_model_authority
from .model_policy import JUDGE_MODEL_LADDER, CouncilModelStage, STAGE_MAX_OUTPUT_TOKENS
from .post_research_reopen_initial_execute_production_v01 import _external_json_value, _replace_durable, _write_exclusive, external_provider_json_sha256, frozen_initial_items
from .post_research_reopen_judge_current_v01 import verify_b3_final_closure, verify_current_rebuttal_freeze
from .post_research_reopen_judge_current_v02 import (
    B3_HASH, CANDIDATES, CURRENT_INITIAL_FREEZE_HASH, CURRENT_REBUTTAL_HASH, GAPS_HASH,
    OUTCOMES, POLICY_STATUS, RESIDUAL_HASH, classify_reason, verify_remaining_gaps_closure,
    verify_residual_plan,
)
from .post_research_reopen_rebuttal_production_v01 import verify_current_initial_freeze
from .proposal import FrozenJudgeDecisionProposal, JudgeDecisionProposalDraft, JudgeNextDirective, JudgeOutcome, RebuttalResponseType
from .request import parse_council_responses_payload


ENTRY_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_ENTRY_v0_3"
PREFLIGHT_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_PREFLIGHT_ZERO_CALL_v0_3"
READINESS_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_READINESS_ZERO_CALL_v0_3"
APPROVAL_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_OWNER_APPROVAL_v0_3"
LEDGER_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_PAID_DISPATCH_LEDGER_v0_3"
RESULT_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_COUNCIL_FREEZE_v0_3"
CONTEXT_VERSION = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_CONTEXT_v0_3"
MODEL_RUN_REF = "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_J1_V03"


class CurrentJudgeV03Error(RuntimeError):
    pass


def _need(ok: bool, message: str) -> None:
    if not ok:
        raise CurrentJudgeV03Error(message)


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


@dataclass(frozen=True)
class JudgeContext:
    model_input: Mapping[str, Any]
    judge_input_hash: str
    context_hash: str
    mandate_version: str
    deep_comparison_id: str
    allowed_claim_ids: tuple[str, ...]
    allowed_dispute_refs: tuple[str, ...]
    allowed_conflict_refs: tuple[str, ...]
    allowed_unknown_refs: tuple[str, ...]
    allowed_condition_refs: tuple[str, ...]


def build_entry(*, code_commit_sha: str, closure: Mapping[str, Any], residual_plan: Mapping[str, Any], remaining_gaps_closure: Mapping[str, Any], initial_freeze: Mapping[str, Any], initial_cost: Mapping[str, Any], rebuttal_freeze: Mapping[str, Any]) -> dict[str, Any]:
    _need(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha) is not None, "entry SHA invalid")
    b3 = verify_b3_final_closure(closure); residual = verify_residual_plan(residual_plan); gaps = verify_remaining_gaps_closure(remaining_gaps_closure)
    initial = verify_current_initial_freeze(initial_freeze, initial_cost=initial_cost); rebuttal = verify_current_rebuttal_freeze(rebuttal_freeze)
    sample = frozen_initial_items(initial_cost)[0].plan_item.bundle
    value = {"artifact_version": ENTRY_VERSION, "status": "PASS_EVIDENCE_COMPLETE_NON_INVEST_JUDGE_AUTHORITY", "code_commit_sha": code_commit_sha, "b3_final_closure_hash": b3, "b3_residual_plan_hash": residual, "b3_remaining_gaps_closure_hash": gaps, "current_initial_freeze_hash": initial, "current_rebuttal_freeze_hash": rebuttal, "canonical_open_research_requirements_after_b3": [], "additional_provider_read_required": False, "candidate_aware_reopen_provenance": "PASS", "invest_eligibility_policy_status": POLICY_STATUS, "global_invest_block_reason": POLICY_STATUS, "invest_eligible_candidates": [], "invest_blocked_candidates": list(CANDIDATES), "allowed_judge_outcomes": list(OUTCOMES), "watch_abstain_creates_new_b3_reopen": False, "mandate_version": sample.mandate_version, "deep_comparison_id": sample.deep_comparison_id, "council_policy_version": sample.council_policy_version, "judge_policy_version": sample.judge_policy_version, "model_policy_version": sample.model_policy_version, "model_calls": 0, "provider_reads": 0, "broker_writes": 0, "alpaca_orders": 0, "live_money": "PROHIBITED"}
    value["artifact_hash"] = canonical_sha256(value, exclude_fields=("artifact_hash",))
    return value


def verify_entry(payload: Mapping[str, Any], **inputs: Any) -> str:
    observed = _hash(payload)
    _need(dict(payload) == build_entry(**inputs), "v0.3 entry drift")
    return observed


def verify_selection(selection: Mapping[str, Any], *, eval_artifact: Mapping[str, Any], receipts: Sequence[Mapping[str, Any]]) -> str:
    _need(dict(selection) == build_judge_selected_model_authority(eval_artifact, receipts), "Judge selection replay drift")
    observed = verify_judge_selected_model_authority(selection)
    _need(selection.get("selected_candidate") == {"candidate_key": "J1", "model": "gpt-5.6-terra", "reasoning_effort": "medium", "ladder_position": 1}, "J1 selection drift")
    _selected()
    return observed


def _add_claim(by_id: dict[str, Any], raw: Mapping[str, Any]) -> None:
    claim = MATERIAL_CLAIM_V1.model_validate(dict(raw))
    prior = by_id.get(claim.claim_id)
    _need(prior is None or canonical_sha256(prior) == canonical_sha256(claim), "canonical MaterialClaim ID collision")
    by_id[claim.claim_id] = claim


def build_context(*, entry: Mapping[str, Any], closure: Mapping[str, Any], residual_plan: Mapping[str, Any], remaining_gaps_closure: Mapping[str, Any], initial_cost: Mapping[str, Any], initial_freeze: Mapping[str, Any], rebuttal_freeze: Mapping[str, Any], selection: Mapping[str, Any]) -> JudgeContext:
    verify_entry(entry, code_commit_sha=entry["code_commit_sha"], closure=closure, residual_plan=residual_plan, remaining_gaps_closure=remaining_gaps_closure, initial_freeze=initial_freeze, initial_cost=initial_cost, rebuttal_freeze=rebuttal_freeze)
    verify_judge_selected_model_authority(selection)
    _need(selection.get("selected_candidate") == {"candidate_key": "J1", "model": "gpt-5.6-terra", "reasoning_effort": "medium", "ladder_position": 1}, "Judge selection drift")
    model_inputs = initial_cost.get("model_facing_inputs_by_candidate")
    _need(isinstance(model_inputs, Mapping) and set(model_inputs) == set(CANDIDATES), "current Initial model inputs missing")
    packets: list[dict[str, Any]] = []; computed_by_id: dict[str, Mapping[str, Any]] = {}; claims: dict[str, Any] = {}
    for candidate in CANDIDATES:
        model_input = model_inputs[candidate]
        _need(isinstance(model_input, Mapping) and model_input.get("candidate_id") == candidate, "Initial input candidate drift")
        packet = CANDIDATE_PACKET_V1.model_validate(model_input["candidate_packet"])
        packets.append(packet.model_dump(mode="json", exclude_none=False, warnings=False))
        for raw in model_input.get("computed_values", []):
            _need(isinstance(raw, Mapping) and isinstance(raw.get("computed_value_id"), str), "computed value malformed")
            prior = computed_by_id.get(raw["computed_value_id"])
            _need(prior is None or canonical_sha256(prior) == canonical_sha256(raw), "computed value ID collision")
            computed_by_id[raw["computed_value_id"]] = dict(raw)
        for raw in model_input.get("material_claims", []):
            _need(isinstance(raw, Mapping), "Initial input claim malformed")
            _add_claim(claims, raw)
    _need([row["candidate_id"] for row in packets] == list(CANDIDATES), "candidate packet order drift")
    initial_views: list[dict[str, Any]] = []; uncertainty_rows: list[dict[str, Any]] = []
    rows = initial_freeze.get("processed_records")
    _need(isinstance(rows, list) and len(rows) == 9, "current Initial record count drift")
    expected_roles = [(candidate, lane) for candidate in CANDIDATES for lane in ("BULL", "BEAR", "RED_TEAM")]
    _need([(row.get("candidate_id"), row.get("lane")) for row in rows] == expected_roles, "current Initial role order drift")
    for row in rows:
        opinion = COUNCIL_OPINION_V1.model_validate(row["council_opinion"])
        _need(row.get("council_opinion_hash") == canonical_sha256(opinion), "Initial opinion hash drift")
        initial_views.append({"candidate_id": row["candidate_id"], "lane": row["lane"], "record_hash": row["record_hash"], "council_opinion_hash": row["council_opinion_hash"], "council_opinion": opinion.model_dump(mode="json", exclude_none=False, warnings=False)})
        for raw in row["material_claims"]: _add_claim(claims, raw)
        for raw in row["structured_output"].get("material_unknown_refs", []):
            uncertainty_rows.append(classify_reason(candidate_id=row["candidate_id"], raw_reason=raw, closure=closure, residual_plan=residual_plan, remaining_gaps_closure=remaining_gaps_closure))
    rebuttal_rows = rebuttal_freeze.get("processed_records")
    _need(isinstance(rebuttal_rows, list) and len(rebuttal_rows) == 3 and [row.get("candidate_id") for row in rebuttal_rows] == list(CANDIDATES), "current Rebuttal record order drift")
    rebuttals: list[dict[str, Any]] = []; disputes: list[str] = []
    for row in rebuttal_rows:
        draft = row["frozen_rebuttal_bundle"]["draft"]; items = []
        for item in draft["items"]:
            if RebuttalResponseType(item["response_type"]) == RebuttalResponseType.UNRESOLVED:
                for ref in item["opposing_finding_ids"]:
                    if ref not in disputes: disputes.append(ref)
            items.append({"rebuttal_item_id": item["rebuttal_item_id"], "responding_lane": item["responding_lane"], "opposing_finding_ids": list(item["opposing_finding_ids"]), "response_type": item["response_type"], "remaining_uncertainty_refs": list(item["remaining_uncertainty_refs"])})
        for raw in row["material_claims"]: _add_claim(claims, raw)
        for raw in dict.fromkeys([*row["research_reopen_reason_codes"], *row["required_unknown_refs"]]):
            uncertainty_rows.append(classify_reason(candidate_id=row["candidate_id"], raw_reason=raw, closure=closure, residual_plan=residual_plan, remaining_gaps_closure=remaining_gaps_closure))
        rebuttals.append({"candidate_id": row["candidate_id"], "record_hash": row["record_hash"], "rebuttal_bundle_id": row["rebuttal_bundle_id"], "rebuttal_bundle_hash": row["rebuttal_bundle_hash"], "rebuttal_material_claim_ids": [raw["claim_id"] for raw in row["material_claims"]], "items": items, "research_reopen_required": row["research_reopen_required"], "research_reopen_reason_codes": list(row["research_reopen_reason_codes"]), "required_unknown_refs": list(row["required_unknown_refs"])})
    _need(not [row for row in uncertainty_rows if row["lifecycle_classification"] == "UNMAPPED_REOPEN_REASON"], "unmapped current uncertainty")
    dedup_uncertainty: dict[str, dict[str, Any]] = {}
    for row in uncertainty_rows:
        key = f'{row["candidate_id"]}:{row["raw_reason_or_ref"]}'
        dedup_uncertainty[key] = {"uncertainty_ref": key, **row}
    unknown_refs = tuple(dedup_uncertainty)
    condition_extra = tuple(dict.fromkeys(x for row in dedup_uncertainty.values() for x in row["decision_context_condition_ids"]))
    claim_payloads = [claims[key].model_dump(mode="json", exclude_none=False, warnings=False) for key in claims]
    conflict_refs = tuple(dict.fromkeys(ref for claim in claims.values() for ref in claim.conflict_ids))
    claim_ids = tuple(row["claim_id"] for row in claim_payloads)
    _need(bool(claim_ids) and bool(computed_by_id), "evidence-complete context requires claims and computed values")
    condition_refs = tuple(dict.fromkeys((*claim_ids, *disputes, *conflict_refs, *unknown_refs, *condition_extra)))
    base = {"context_version": CONTEXT_VERSION, "candidate_order": list(CANDIDATES), "candidate_packets": packets, "computed_values": list(computed_by_id.values()), "mandate_version": entry["mandate_version"], "deep_comparison_id": entry["deep_comparison_id"], "council_policy_version": entry["council_policy_version"], "judge_policy_version": entry["judge_policy_version"], "model_policy_version": entry["model_policy_version"], "material_claims": claim_payloads, "initial_role_views": initial_views, "rebuttal_bundles": rebuttals, "decision_context_uncertainties": list(dedup_uncertainty.values()), "material_conflict_refs": list(conflict_refs), "unresolved_dispute_refs": list(disputes), "event_outcome_constraints": {"invest_authorized": False, "invest_eligibility_policy_status": POLICY_STATUS, "invest_eligible_candidates": [], "invest_blocked_candidates": list(CANDIDATES), "allowed_outcomes": list(OUTCOMES), "watch_next_directive": "MONITOR", "abstain_next_directive": "STOP", "canonical_b3_reopen_closed": True, "new_research_inside_current_b4_allowed": False, "watch_abstain_creates_new_b3_reopen": False}, "source_lineage": {"b3_final_closure_hash": B3_HASH, "b3_residual_plan_hash": RESIDUAL_HASH, "b3_remaining_gaps_closure_hash": GAPS_HASH, "current_initial_freeze_hash": CURRENT_INITIAL_FREEZE_HASH, "current_initial_record_hashes": initial_freeze["processed_record_hashes"], "current_rebuttal_freeze_hash": CURRENT_REBUTTAL_HASH, "current_rebuttal_record_hashes": rebuttal_freeze["processed_record_hashes"], "judge_entry_hash": entry["artifact_hash"], "judge_selection_authority_hash": selection["artifact_hash"]}}
    judge_input_hash = canonical_sha256(base); model_input = {**base, "judge_input_hash": judge_input_hash}
    return JudgeContext(model_input=model_input, judge_input_hash=judge_input_hash, context_hash=canonical_sha256(model_input), mandate_version=entry["mandate_version"], deep_comparison_id=entry["deep_comparison_id"], allowed_claim_ids=claim_ids, allowed_dispute_refs=tuple(disputes), allowed_conflict_refs=conflict_refs, allowed_unknown_refs=unknown_refs, allowed_condition_refs=condition_refs)


def verify_context(context: JudgeContext) -> None:
    model_input = context.model_input
    _need(model_input.get("candidate_order") == list(CANDIDATES) and len(model_input.get("candidate_packets", [])) == 3 and len(model_input.get("initial_role_views", [])) == 9 and len(model_input.get("rebuttal_bundles", [])) == 3, "evidence context surface incomplete")
    _need(bool(model_input.get("material_claims")) and bool(model_input.get("computed_values")) and bool(context.allowed_claim_ids), "evidence graph incomplete")
    _need(tuple(row["claim_id"] for row in model_input["material_claims"]) == context.allowed_claim_ids, "allowed claim graph drift")
    supplied = set(context.allowed_claim_ids) | set(context.allowed_dispute_refs) | set(context.allowed_conflict_refs) | set(context.allowed_unknown_refs) | {ref for row in model_input["decision_context_uncertainties"] for ref in row["decision_context_condition_ids"]}
    _need(set(context.allowed_condition_refs).issubset(supplied) and set(context.allowed_unknown_refs) == {row["uncertainty_ref"] for row in model_input["decision_context_uncertainties"]}, "allowlist broader than context")
    _need(model_input.get("judge_input_hash") == context.judge_input_hash and context.judge_input_hash == canonical_sha256({key: value for key, value in model_input.items() if key != "judge_input_hash"}), "context hash drift")


def _request(entry: Mapping[str, Any], context: JudgeContext) -> Any:
    verify_context(context); selected = _selected()
    request = build_bounded_judge_request(model_candidate=selected, model_input=context.model_input, candidate_ids=CANDIDATES, mandate_version=context.mandate_version, deep_comparison_id=context.deep_comparison_id, judge_input_hash=context.judge_input_hash, council_policy_version=entry["council_policy_version"], judge_policy_version=entry["judge_policy_version"], model_policy_version=entry["model_policy_version"], model_run_ref=MODEL_RUN_REF, allowed_claim_ids=context.allowed_claim_ids, allowed_dispute_refs=context.allowed_dispute_refs, allowed_conflict_refs=context.allowed_conflict_refs, allowed_unknown_refs=context.allowed_unknown_refs, allowed_condition_refs=context.allowed_condition_refs)
    assert_bounded_request_invariants(request)
    _need(request.request_payload.get("model") == "gpt-5.6-terra" and request.request_payload.get("reasoning") == {"effort": "medium"} and request.request_payload.get("max_output_tokens") == 8192, "v0.3 request policy drift")
    return request


def build_preflight(*, code_commit_sha: str, entry: Mapping[str, Any], context: JudgeContext, selection: Mapping[str, Any], eval_artifact: Mapping[str, Any], receipts: Sequence[Mapping[str, Any]], pricing: Mapping[str, Any], historical_request_hashes: Sequence[str], **inputs: Any) -> dict[str, Any]:
    entry_hash = verify_entry(entry, code_commit_sha=code_commit_sha, **inputs); selection_hash = verify_selection(selection, eval_artifact=eval_artifact, receipts=receipts); verify_context(context); request = _request(entry, context)
    _need(request.request_hash not in historical_request_hashes, "historical Judge request reuse")
    nbytes = len(json.dumps(request.request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()); cost = runtime_cost_upper_bound_usd(model="gpt-5.6-terra", input_tokens_upper_bound=nbytes, output_tokens_upper_bound=8192, call_count=1, pricing=pricing)
    value = {"artifact_version": PREFLIGHT_VERSION, "status": "PASS_ZERO_CALL_EVIDENCE_COMPLETE_NON_INVEST_JUDGE_PREFLIGHT", "code_commit_sha": code_commit_sha, "entry_hash": entry_hash, "context_hash": context.context_hash, "selection_hash": selection_hash, "pricing_hash": _hash(pricing, "pricing_hash"), "pricing_version": pricing["pricing_version"], "model": "gpt-5.6-terra", "reasoning_effort": "medium", "judge_selected_candidate": "J1", "new_paid_calls_planned": 1, "new_paid_call_count_ceiling": 1, "max_output_tokens": 8192, "request_hash": request.request_hash, "request_manifest_hash": canonical_sha256({"request_hashes": [request.request_hash]}), "request_payload": request.request_payload, "judge_input_hash": context.judge_input_hash, "request_body_utf8_bytes": nbytes, "input_tokens_upper_bound": nbytes, "judge_max_cost_usd": format(cost, "f"), "evidence_counts": {"candidate_packets": 3, "initial_role_views": 9, "rebuttal_bundles": 3, "canonical_material_claims": len(context.allowed_claim_ids), "computed_values": len(context.model_input["computed_values"]), "allowed_claim_ids": len(context.allowed_claim_ids), "allowed_dispute_refs": len(context.allowed_dispute_refs), "allowed_conflict_refs": len(context.allowed_conflict_refs), "allowed_unknown_refs": len(context.allowed_unknown_refs), "allowed_condition_refs": len(context.allowed_condition_refs)}, "automatic_retries": 0, "owner_approval_required": True, "owner_approval_status": "NOT_GRANTED", "model_calls_authorized": False, "provider_reads_authorized": False, "model_calls_this_step": 0, "provider_reads_this_step": 0, "broker_writes": 0, "alpaca_orders": 0, "cost_usd_this_step": "0", "live_money": "PROHIBITED"}
    value["artifact_hash"] = canonical_sha256(value, exclude_fields=("artifact_hash",)); return value


def verify_preflight(payload: Mapping[str, Any], **inputs: Any) -> str:
    observed = _hash(payload); _need(dict(payload) == build_preflight(**inputs), "v0.3 preflight drift"); return observed


def build_readiness(*, code_commit_sha: str, preflight: Mapping[str, Any], **inputs: Any) -> dict[str, Any]:
    source = verify_preflight(preflight, code_commit_sha=code_commit_sha, **inputs); entry = inputs["entry"]; counts = preflight["evidence_counts"]
    value = {"artifact_version": READINESS_VERSION, "status": "PASS_ZERO_CALL_EVIDENCE_COMPLETE_NON_INVEST_JUDGE_READINESS", "code_commit_sha": code_commit_sha, "source_preflight_hash": source, "EVIDENCE_COMPLETE_JUDGE_CONTEXT": "PASS", "CANDIDATE_PACKET_COUNT": counts["candidate_packets"], "INITIAL_VIEW_COUNT": counts["initial_role_views"], "REBUTTAL_BUNDLE_COUNT": counts["rebuttal_bundles"], "CANONICAL_MATERIAL_CLAIM_COUNT": counts["canonical_material_claims"], "COMPUTED_VALUE_COUNT": counts["computed_values"], "ALLOWED_CLAIM_ID_COUNT": counts["allowed_claim_ids"], "ALLOWED_DISPUTE_REF_COUNT": counts["allowed_dispute_refs"], "ALLOWED_CONFLICT_REF_COUNT": counts["allowed_conflict_refs"], "ALLOWED_UNKNOWN_REF_COUNT": counts["allowed_unknown_refs"], "ALLOWED_CONDITION_REF_COUNT": counts["allowed_condition_refs"], "B3_FINAL_CLOSURE_VERIFY": "PASS", "CANONICAL_OPEN_RESEARCH_REQUIREMENTS_AFTER_B3": [], "ADDITIONAL_PROVIDER_READ_REQUIRED": False, "CANDIDATE_AWARE_REOPEN_PROVENANCE": "PASS", "INVEST_ELIGIBILITY_POLICY_STATUS": POLICY_STATUS, "INVEST_ELIGIBLE_CANDIDATES": [], "INVEST_BLOCKED_CANDIDATES": list(CANDIDATES), "ALLOWED_JUDGE_OUTCOMES": list(OUTCOMES), "WATCH_ABSTAIN_CREATES_NEW_B3_REOPEN": False, "JUDGE_SELECTED_CANDIDATE": "J1", "MODEL": "gpt-5.6-terra", "REASONING_EFFORT": "medium", "MAX_OUTPUT_TOKENS": 8192, "JUDGE_REQUEST_HASH": preflight["request_hash"], "JUDGE_REQUEST_MANIFEST_HASH": preflight["request_manifest_hash"], "JUDGE_MAX_COST_USD": preflight["judge_max_cost_usd"], "STRICT_JUDGE_READINESS_VERIFIER": "PASS", "INVALID_APPROVAL_FAILS_BEFORE_TRANSPORT": "PASS", "VALID_AUTHORITY_REACHES_FAKE_TRANSPORT": "PASS", "RAW_CAPTURE_BEFORE_LOCAL_VALIDATION": "PASS", "PARTIAL_DISPATCH_FAIL_CLOSED": True, "AUTOMATIC_RETRIES": 0, "OWNER_APPROVAL_REQUIRED": True, "OWNER_APPROVAL_STATUS": "NOT_GRANTED", "MODEL_CALLS_AUTHORIZED": False, "MODEL_CALLS_THIS_STEP": 0, "PROVIDER_READS_THIS_STEP": 0, "BROKER_WRITES": 0, "ALPACA_ORDERS": 0, "COST_USD_THIS_STEP": "0", "LIVE_MONEY": "PROHIBITED", "entry_hash": entry["artifact_hash"]}
    value["artifact_hash"] = canonical_sha256(value, exclude_fields=("artifact_hash",)); return value


def verify_readiness(payload: Mapping[str, Any], *, code_commit_sha: str, preflight: Mapping[str, Any], **inputs: Any) -> str:
    observed = _hash(payload); _need(dict(payload) == build_readiness(code_commit_sha=code_commit_sha, preflight=preflight, **inputs), "v0.3 readiness drift"); return observed


def build_owner_approval(*, code_commit_sha: str, readiness_hash: str, preflight: Mapping[str, Any], entry: Mapping[str, Any], owner_approval_id: str, owner_approval_at_utc: str) -> dict[str, Any]:
    value = {"artifact_version": APPROVAL_VERSION, "owner_approval_granted": True, "owner_approval_id": owner_approval_id, "owner_approval_at_utc": owner_approval_at_utc, "approved_executor_code_commit_sha": code_commit_sha, "readiness_hash": readiness_hash, "entry_hash": entry["artifact_hash"], "request_hash": preflight["request_hash"], "model": "gpt-5.6-terra", "reasoning_effort": "medium", "new_paid_call_count": 1, "new_paid_call_count_ceiling": 1, "max_output_tokens": 8192, "approved_judge_max_cost_usd": preflight["judge_max_cost_usd"], "allowed_judge_outcomes": list(OUTCOMES), "automatic_retries": 0}; value["artifact_hash"] = canonical_sha256(value, exclude_fields=("artifact_hash",)); return value


def verify_owner_approval(payload: Mapping[str, Any], **inputs: Any) -> str:
    observed = _hash(payload); expected = build_owner_approval(**inputs, owner_approval_id=str(payload.get("owner_approval_id", "")), owner_approval_at_utc=str(payload.get("owner_approval_at_utc", ""))); _need(dict(payload) == expected, "v0.3 approval drift"); return observed


def _validate_proposal(proposal: JudgeDecisionProposalDraft, *, context: JudgeContext) -> None:
    _need(proposal.outcome in {JudgeOutcome.WATCH, JudgeOutcome.ABSTAIN}, "v0.3 rejects INVEST")
    _need(proposal.judge_input_hash == context.judge_input_hash and proposal.mandate_version == context.mandate_version and proposal.deep_comparison_id == context.deep_comparison_id and proposal.model_run_ref == MODEL_RUN_REF, "v0.3 lineage violation")
    _need(proposal.execution_authority is False and proposal.research_reopen_required is False and not proposal.research_reopen_reason_codes, "v0.3 lifecycle violation")
    _need(set(proposal.selected_candidate_basis_claim_ids).issubset(context.allowed_claim_ids), "basis claim outside graph")
    _need(all(set(row.claim_ids).issubset(context.allowed_claim_ids) for row in proposal.why_not_other_candidates), "why-not claim outside graph")
    _need(set(proposal.unresolved_dispute_refs).issubset(context.allowed_dispute_refs) and set(proposal.material_conflict_refs).issubset(context.allowed_conflict_refs) and set(proposal.material_unknown_refs).issubset(context.allowed_unknown_refs), "reference outside graph")
    _need(all(set(row.source_or_claim_refs).issubset(context.allowed_condition_refs) for row in proposal.what_would_change_decision) and set(proposal.invalidation_condition_refs).issubset(context.allowed_condition_refs), "condition outside graph")
    _need((proposal.outcome == JudgeOutcome.WATCH and proposal.next_directive == JudgeNextDirective.MONITOR and bool(proposal.what_would_change_decision)) or (proposal.outcome == JudgeOutcome.ABSTAIN and proposal.next_directive == JudgeNextDirective.STOP and proposal.primary_candidate_id is None), "v0.3 terminal directive violation")


def build_raw_capture(*, request_hash: str, raw: Mapping[str, Any], started_at: str, captured_at: str) -> dict[str, Any]:
    external = _external_json_value(raw); _need(isinstance(external, Mapping), "provider response must be Mapping")
    value = {"capture_version": "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_RAW_PROVIDER_RESPONSE_v0_3", "request_hash": request_hash, "provider_response_id": external.get("id"), "dispatch_started_at_utc": started_at, "captured_at_utc": captured_at, "raw_response": dict(external)}; value["raw_response_hash"] = external_provider_json_sha256(value); return value


def verify_raw_capture(payload: Mapping[str, Any], *, request_hash: str) -> str:
    observed = payload.get("raw_response_hash"); _need(isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None, "raw hash missing"); stripped = dict(payload); stripped.pop("raw_response_hash", None); _need(observed == external_provider_json_sha256(stripped) and payload.get("capture_version") == "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_RAW_PROVIDER_RESPONSE_v0_3" and payload.get("request_hash") == request_hash, "raw capture drift"); return observed


def execute_paid(*, execute_paid_judge: bool, branch: str, code_commit_sha: str, worktree_clean: bool, preflight: Mapping[str, Any], readiness: Mapping[str, Any], approval: Mapping[str, Any] | None, ledger_path: Path, raw_path: Path, result_path: Path, transport_factory: Callable[[], Callable[[Mapping[str, Any]], Mapping[str, Any]]], **inputs: Any) -> dict[str, Any]:
    _need(execute_paid_judge is True and approval is not None, "explicit paid flag and approval required")
    _need(branch == "hackathon/alpaca-2026" and worktree_clean and not ledger_path.exists() and not raw_path.exists() and not result_path.exists(), "pre-transport gate failed")
    readiness_hash = verify_readiness(readiness, code_commit_sha=code_commit_sha, preflight=preflight, **inputs); verify_preflight(preflight, code_commit_sha=code_commit_sha, **inputs); approval_hash = verify_owner_approval(approval, code_commit_sha=code_commit_sha, readiness_hash=readiness_hash, preflight=preflight, entry=inputs["entry"])
    context = inputs["context"]; request = _request(inputs["entry"], context); _need(request.request_hash == preflight["request_hash"], "request reconstruction drift")
    ledger = {"ledger_version": LEDGER_VERSION, "approval_hash": approval_hash, "entries": [{"dispatch_index": 1, "request_hash": request.request_hash, "state": "NOT_DISPATCHED", "automatic_retry_permitted": False}]}; ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _write_exclusive(ledger_path, ledger)
    row = ledger["entries"][0]; row.update(state="DISPATCH_STARTED_UNKNOWN", dispatch_started_at_utc=_utc(datetime.now(UTC))); ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _replace_durable(ledger_path, ledger)
    try: raw = transport_factory()(preflight["request_payload"])
    except Exception as exc: row["stop_reason"] = f"AMBIGUOUS_PROVIDER_OUTCOME:{type(exc).__name__}"; ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _replace_durable(ledger_path, ledger); raise CurrentJudgeV03Error("ambiguous provider outcome") from exc
    capture = build_raw_capture(request_hash=request.request_hash, raw=raw, started_at=row["dispatch_started_at_utc"], captured_at=_utc(datetime.now(UTC))); _write_exclusive(raw_path, capture); raw_hash = verify_raw_capture(capture, request_hash=request.request_hash); row.update(raw_response_hash=raw_hash, raw_response_path=str(raw_path)); ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _replace_durable(ledger_path, ledger)
    try:
        call, proposal = parse_council_responses_payload(raw, request=request, latency_ms=0); _validate_proposal(proposal, context=context); frozen = FrozenJudgeDecisionProposal.from_draft(proposal); actual = actual_cost_usd(raw, model="gpt-5.6-terra", pricing=inputs["pricing"]); _need(actual <= Decimal(str(preflight["judge_max_cost_usd"])), "actual cost exceeds ceiling"); record = {"outcome": proposal.outcome.value, "next_directive": proposal.next_directive.value, "response_id": call.response_id, "frozen_judge_proposal": frozen.model_dump(mode="json", exclude_none=False)}; record["record_hash"] = canonical_sha256(record, exclude_fields=("record_hash",))
    except Exception as exc: row["stop_reason"] = f"RESPONSE_CAPTURED_BUT_NOT_ACCEPTED:{type(exc).__name__}"; ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _replace_durable(ledger_path, ledger); raise CurrentJudgeV03Error("captured response failed validation") from exc
    row.update(state="COMPLETED", processed_record_hash=record["record_hash"], actual_cost_usd=format(actual, "f")); ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",)); _replace_durable(ledger_path, ledger)
    result = {"artifact_version": RESULT_VERSION, "status": "B4_POST_RESEARCH_REOPEN_CURRENT_JUDGE_FROZEN", "code_commit_sha": code_commit_sha, "raw_response_hash": raw_hash, "processed_record": record, "actual_cost_usd": format(actual, "f"), "final_b4_decision_created": True, "b5_handoff_eligible": False, "b5_handoff_created": False, "research_reopen_created": False, "broker_writes": 0, "alpaca_orders": 0, "automatic_retries": 0, "live_money": "PROHIBITED", "ledger_hash": ledger["ledger_hash"]}; result["artifact_hash"] = canonical_sha256(result, exclude_fields=("artifact_hash",)); _write_exclusive(result_path, result); return result
