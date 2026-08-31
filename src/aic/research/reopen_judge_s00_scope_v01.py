from __future__ import annotations

import re
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import RESEARCH_REOPEN_REQUEST_V1


ARTIFACT_VERSION = "B3_RESEARCH_REOPEN_JUDGE_S00_SCOPE_v0_1"
PASS_STATUS = "B3_RESEARCH_REOPEN_S00_SCOPE_ZERO_CALL_PASS"
NEXT_GATE = "B3_RESEARCH_REOPEN_EXISTING_EVIDENCE_INVENTORY_ZERO_CALL"

EXPECTED_REOPEN_HASH = "bf6e2454c4cc871409058a35d66690e29df8103de6d0681679dd675a39746830"
EXPECTED_POSTPROCESS_HASH = "3c8cd44aa4fadd4a79491cc4a414a550964b7c297c139d044cf1abbb943a6da3"
EXPECTED_JUDGE_RESULT_HASH = "1f77c26c7198cae5c809b8c2fd36cf03dd1bfceb5a2c6e5ac505b1c4b6334090"
EXPECTED_JUDGE_PROPOSAL_HASH = "fa333d33a578502d0175f9da117772ba1e9571af7322fa5796f12ebce82ab960"
EXPECTED_REBUTTAL_FREEZE_HASH = "75f2ac76e0f4478e71447871b0b284cbe74e0e8378fb6171fff855ab1ce1ade4"
EXPECTED_JUDGE_RUN_ID = "AIC-B4-REOPEN-JUDGE-20260831T062734480149Z-5af852b4e156"
EXPECTED_REOPEN_REQUEST_ID = "B4_REOPEN_JUDGE_RESEARCH_REOPEN_REQUEST_001"
EXPECTED_REOPEN_REASONS = (
    "Q4_RECENT_DEVELOPMENTS",
    "B4_MATERIAL_CLAIM_MSFT_RED_TEAM_4f85cd62978ad094a81b",
    "B4_MATERIAL_CLAIM_MSFT_RED_TEAM_f9a27271c11b2a79ac37",
)
EXPECTED_META_CONDITION_IDS = (
    "META_CONDITION_001",
    "META_CONDITION_002",
    "META_CONDITION_003",
    "META_CONDITION_004",
)
EXPECTED_NVDA_WHY_NOT = (
    "NVDA_RESEARCH_REOPEN_REQUIRED",
    "NVDA_CURRENT_DEVELOPMENTS_NOT_DECISION_USABLE",
    "NVDA_VALUATION_AND_COMPARATIVE_CONTEXT_ABSENT",
    "NVDA_FORWARD_DURABILITY_UNRESOLVED",
)
EXPECTED_MSFT_WHY_NOT = (
    "MSFT_RESEARCH_REOPEN_REQUIRED",
    "MSFT_VALUATION_CONTEXT_INSUFFICIENT",
    "MSFT_FUTURE_MONETIZATION_AND_INVESTMENT_RETURN_UNRESOLVED",
    "MSFT_CURRENT_STRENGTH_NOT_PROOF_OF_FORWARD_DURABILITY",
)
EXPECTED_META_BLOCKERS = (
    "META_FORWARD_DEPENDENCIES_UNRESOLVED",
    "META_VALUATION_CONTEXT_NARROW",
    "META_PORTFOLIO_CONTEXT_NARROW",
)


class JudgeReopenS00ScopeError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise JudgeReopenS00ScopeError(message)


def _self_hash(payload: Mapping[str, Any], *, field: str) -> str:
    observed = payload.get(field)
    _require(isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None, f"{field} missing")
    expected = canonical_sha256(payload, exclude_fields=(field,))
    _require(observed == expected, f"{field} self-hash mismatch")
    return observed


def verify_reopen_request(payload: Mapping[str, Any]) -> str:
    model = RESEARCH_REOPEN_REQUEST_V1.model_validate(dict(payload))
    normalized = model.model_dump(mode="json", exclude_none=False, warnings=False)
    _require(normalized["request_hash"] == EXPECTED_REOPEN_HASH, "reopen request hash drift")
    _require(normalized["reopen_request_id"] == EXPECTED_REOPEN_REQUEST_ID, "reopen request id drift")
    _require(normalized["parent_run_id"] == EXPECTED_JUDGE_RUN_ID, "reopen parent Judge run drift")
    _require(normalized["parent_decision_id"] is None, "reopen request must not invent FinalDecision")
    _require(normalized["trigger_bundle_id"] is None, "reopen request must not invent trigger bundle")
    _require(tuple(normalized["reason_codes"]) == EXPECTED_REOPEN_REASONS, "reopen reason-code drift")
    _require(tuple(normalized["source_ref_ids"]) == EXPECTED_REOPEN_REASONS, "reopen source-ref drift")
    _require(normalized["requested_at"] == "2026-08-31T06:27:54.582282Z", "reopen requested_at drift")
    _require(normalized["new_run_start_state"] == "S00", "reopen must start at S00")
    _require(normalized["request_hash"] == canonical_sha256(normalized, exclude_fields=("request_hash",)), "reopen canonical self-hash drift")
    return normalized["request_hash"]


def verify_postprocess(payload: Mapping[str, Any]) -> str:
    observed = _self_hash(payload, field="artifact_hash")
    _require(observed == EXPECTED_POSTPROCESS_HASH, "postprocess hash drift")
    _require(payload.get("artifact_version") == "B4_REOPEN_JUDGE_PROPOSAL_POSTPROCESS_v0_1", "postprocess version drift")
    _require(payload.get("status") == "B4_REOPEN_JUDGE_RESEARCH_REOPEN_REQUEST_PERSISTED", "postprocess status drift")
    _require(payload.get("source_judge_result_artifact_hash") == EXPECTED_JUDGE_RESULT_HASH, "postprocess Judge lineage drift")
    _require(payload.get("source_judge_proposal_hash") == EXPECTED_JUDGE_PROPOSAL_HASH, "postprocess proposal lineage drift")
    _require(payload.get("source_judge_run_id") == EXPECTED_JUDGE_RUN_ID, "postprocess Judge run drift")
    _require(payload.get("source_outcome") == "WATCH", "postprocess source outcome drift")
    _require(payload.get("source_primary_candidate_id") == "META", "postprocess primary candidate drift")
    _require(payload.get("research_reopen_request_count") == 1, "postprocess must persist exactly one reopen request")
    _require(payload.get("research_reopen_request_hash") == EXPECTED_REOPEN_HASH, "postprocess reopen hash drift")
    raw_reopen = payload.get("research_reopen_request")
    _require(isinstance(raw_reopen, Mapping), "postprocess canonical reopen request missing")
    verify_reopen_request(raw_reopen)
    _require(payload.get("new_run_start_state") == "S00", "postprocess S00 drift")
    _require(payload.get("next_gate") == "B3_RESEARCH_REOPEN_S00_SCOPE_ZERO_CALL", "postprocess next gate drift")
    _require(payload.get("research_run_started") is False, "research run already started unexpectedly")
    _require(payload.get("final_decision_created") is False, "FinalDecision already exists unexpectedly")
    _require(payload.get("b5_handoff_created") is False, "B5 handoff already exists unexpectedly")
    _require(payload.get("execution_authority") is False, "postprocess cannot grant execution authority")
    _require(payload.get("paid_model_calls_authorized") is False, "postprocess cannot authorize model calls")
    _require(payload.get("provider_reads_authorized") is False, "postprocess cannot authorize provider reads")
    _require(payload.get("model_calls") == 0 and payload.get("provider_reads") == 0, "postprocess zero-call counters drift")
    _require(payload.get("broker_writes") == 0 and payload.get("alpaca_orders") == 0, "postprocess broker/order drift")
    _require(payload.get("live_money") == "PROHIBITED", "live money must remain prohibited")
    return observed


def _why_not_map(proposal: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = proposal.get("why_not_other_candidates")
    _require(isinstance(rows, list), "Judge why_not_other_candidates missing")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        _require(isinstance(row, Mapping) and isinstance(row.get("candidate_id"), str), "Judge why-not row malformed")
        candidate = str(row["candidate_id"])
        _require(candidate not in result, "duplicate Judge why-not candidate")
        result[candidate] = row
    _require(tuple(result) == ("NVDA", "MSFT"), "Judge why-not candidate order drift")
    return result


def verify_judge_result(payload: Mapping[str, Any]) -> str:
    observed = _self_hash(payload, field="artifact_hash")
    _require(observed == EXPECTED_JUDGE_RESULT_HASH, "Judge result hash drift")
    _require(payload.get("artifact_version") == "B4_REOPEN_JUDGE_PRODUCTION_RESULT_v0_2", "Judge result version drift")
    _require(payload.get("status") == "B4_REOPEN_JUDGE_PROPOSAL_FROZEN", "Judge result is not frozen")
    _require(payload.get("run_id") == EXPECTED_JUDGE_RUN_ID, "Judge run id drift")
    _require(payload.get("outcome") == "WATCH" and payload.get("primary_candidate_id") == "META", "Judge WATCH/META drift")
    _require(payload.get("research_reopen_required") is True, "Judge reopen requirement lost")
    _require(tuple(payload.get("research_reopen_reason_codes") or ()) == EXPECTED_REOPEN_REASONS, "Judge reopen reasons drift")
    _require(payload.get("judge_proposal_hash") == EXPECTED_JUDGE_PROPOSAL_HASH, "Judge proposal hash drift")
    proposal = payload.get("judge_proposal")
    _require(isinstance(proposal, Mapping), "Judge proposal missing")
    _require(canonical_sha256(proposal) == EXPECTED_JUDGE_PROPOSAL_HASH, "Judge proposal canonical hash drift")
    why_not = _why_not_map(proposal)
    _require(tuple(why_not["NVDA"].get("reason_codes") or ()) == EXPECTED_NVDA_WHY_NOT, "NVDA why-not reasons drift")
    _require(tuple(why_not["MSFT"].get("reason_codes") or ()) == EXPECTED_MSFT_WHY_NOT, "MSFT why-not reasons drift")
    blockers = proposal.get("blocking_reason_codes")
    _require(isinstance(blockers, list), "Judge blockers missing")
    for blocker in EXPECTED_META_BLOCKERS:
        _require(blocker in blockers, f"Judge lost blocker {blocker}")
    conditions = proposal.get("what_would_change_decision")
    _require(isinstance(conditions, list) and len(conditions) == 4, "Judge META change-condition count drift")
    _require(tuple(row.get("condition_id") for row in conditions if isinstance(row, Mapping)) == EXPECTED_META_CONDITION_IDS, "Judge META condition ids drift")
    for row in conditions:
        _require(isinstance(row, Mapping), "Judge META condition malformed")
        _require(isinstance(row.get("condition_text"), str) and bool(str(row.get("condition_text")).strip()), "Judge META condition text missing")
        refs = row.get("source_or_claim_refs")
        _require(isinstance(refs, list) and bool(refs) and all(isinstance(x, str) and x for x in refs), "Judge META condition refs missing")
    _require(payload.get("judge_authorization_consumed") is True, "Judge authority must remain consumed")
    _require(payload.get("rerun_authorized") is False, "Judge rerun must remain forbidden")
    _require(payload.get("final_decision_created") is False and payload.get("b5_handoff_created") is False, "Judge unexpectedly created FinalDecision/B5")
    _require(payload.get("execution_authority") is False, "Judge result cannot grant execution authority")
    _require(payload.get("broker_writes") == 0 and payload.get("alpaca_orders") == 0, "Judge broker/order drift")
    _require(payload.get("live_money") == "PROHIBITED", "live money must remain prohibited")
    return observed


def _claim_map(rebuttal: Mapping[str, Any], candidate: str) -> dict[str, Mapping[str, Any]]:
    rows = rebuttal.get("processed_records")
    _require(isinstance(rows, list), "Rebuttal processed records missing")
    target = next((row for row in rows if isinstance(row, Mapping) and row.get("candidate_id") == candidate), None)
    _require(isinstance(target, Mapping), f"Rebuttal candidate {candidate} missing")
    claims = target.get("material_claims")
    _require(isinstance(claims, list), f"Rebuttal {candidate} material claims missing")
    result: dict[str, Mapping[str, Any]] = {}
    for claim in claims:
        _require(isinstance(claim, Mapping) and isinstance(claim.get("claim_id"), str), f"Rebuttal {candidate} claim malformed")
        claim_id = str(claim["claim_id"])
        _require(claim_id not in result, f"duplicate Rebuttal claim {claim_id}")
        result[claim_id] = claim
    return result


def verify_rebuttal_freeze(payload: Mapping[str, Any]) -> tuple[str, Mapping[str, Any], Mapping[str, Any]]:
    observed = _self_hash(payload, field="artifact_hash")
    _require(observed == EXPECTED_REBUTTAL_FREEZE_HASH, "Rebuttal freeze hash drift")
    _require(payload.get("artifact_version") == "B4_REOPEN_REBUTTAL_COUNCIL_FREEZE_v0_3", "Rebuttal freeze version drift")
    _require(payload.get("status") == "B4_REOPEN_REBUTTAL_COUNCIL_FROZEN", "Rebuttal not frozen")
    _require(payload.get("candidate_order") == ["NVDA", "MSFT", "META"], "Rebuttal candidate order drift")
    _require(payload.get("research_reopen_required_candidates") == ["NVDA", "MSFT"], "Rebuttal reopen candidate drift")
    reasons = payload.get("research_reopen_reason_codes_by_candidate")
    _require(isinstance(reasons, Mapping), "Rebuttal reopen reason map missing")
    _require(reasons.get("NVDA") == [EXPECTED_REOPEN_REASONS[0]], "NVDA reopen reason drift")
    _require(reasons.get("MSFT") == [EXPECTED_REOPEN_REASONS[1], EXPECTED_REOPEN_REASONS[2]], "MSFT reopen reason drift")
    claims = _claim_map(payload, "MSFT")
    valuation = claims.get(EXPECTED_REOPEN_REASONS[1])
    durability = claims.get(EXPECTED_REOPEN_REASONS[2])
    _require(isinstance(valuation, Mapping), "MSFT valuation reopen claim missing")
    _require(isinstance(durability, Mapping), "MSFT durability reopen claim missing")
    _require(valuation.get("candidate_id") == "MSFT" and valuation.get("category") == "INTEGRITY_FINDING", "MSFT valuation claim shape drift")
    _require(valuation.get("support_status") == "SUPPORTED" and valuation.get("materiality") == "MATERIAL", "MSFT valuation claim status drift")
    _require(durability.get("candidate_id") == "MSFT" and durability.get("category") == "ASSUMPTION", "MSFT durability claim shape drift")
    _require(durability.get("support_status") == "SUPPORTED" and durability.get("materiality") == "MATERIAL", "MSFT durability claim status drift")
    _require(payload.get("rebuttal_rerun_authorized") is False, "Rebuttal rerun must remain forbidden")
    _require(payload.get("broker_writes") == 0 and payload.get("alpaca_orders") == 0, "Rebuttal broker/order drift")
    _require(payload.get("live_money") == "PROHIBITED", "live money must remain prohibited")
    return observed, valuation, durability


def _copy_condition(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "condition_id": row["condition_id"],
        "condition_text": row["condition_text"],
        "source_or_claim_refs": list(row["source_or_claim_refs"]),
        "scope_role": "JUDGE_CHANGE_CONDITION_FOR_EXECUTABLE_INVEST",
        "canonical_reopen_reason": False,
        "provider_read_authorized": False,
        "model_call_authorized": False,
    }


def build_scope_artifact(
    *,
    reopen_request: Mapping[str, Any],
    postprocess: Mapping[str, Any],
    judge_result: Mapping[str, Any],
    rebuttal_freeze: Mapping[str, Any],
    code_commit_sha: str,
) -> dict[str, Any]:
    _require(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha) is not None, "scope code SHA must be lowercase git SHA")
    reopen_hash = verify_reopen_request(reopen_request)
    postprocess_hash = verify_postprocess(postprocess)
    judge_hash = verify_judge_result(judge_result)
    rebuttal_hash, valuation_claim, durability_claim = verify_rebuttal_freeze(rebuttal_freeze)

    proposal = judge_result["judge_proposal"]
    conditions = proposal["what_would_change_decision"]

    requirements = [
        {
            "requirement_id": "NVDA_CURRENT_DEVELOPMENTS_Q4",
            "candidate_id": "NVDA",
            "source_ref_id": EXPECTED_REOPEN_REASONS[0],
            "problem": "Current-developments coverage is not decision-usable; pagination closure alone is insufficient.",
            "source_judge_reason_codes": list(EXPECTED_NVDA_WHY_NOT),
            "resolution_class": "EXTERNAL_READ_REQUIRED_AFTER_LOCAL_INVENTORY",
            "preferred_existing_capability": "BOUNDED_ALPACA_NEWS_REOPEN",
            "local_review_first": True,
            "provider_read_authorized": False,
            "model_call_authorized": False,
        },
        {
            "requirement_id": "MSFT_VALUATION_CONTEXT_DEPTH",
            "candidate_id": "MSFT",
            "source_ref_id": EXPECTED_REOPEN_REASONS[1],
            "frozen_claim_text": valuation_claim["claim_text"],
            "frozen_evidence_ids": list(valuation_claim.get("evidence_ids") or []),
            "resolution_class": "LOCAL_DETERMINISTIC_REVIEW_FIRST_THEN_EXTERNAL_IF_STILL_NEEDED",
            "preferred_existing_capability": "LOCAL_VALUATION_PRIMITIVE_REPLAY",
            "local_review_first": True,
            "provider_read_authorized": False,
            "model_call_authorized": False,
        },
        {
            "requirement_id": "MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY",
            "candidate_id": "MSFT",
            "source_ref_id": EXPECTED_REOPEN_REASONS[2],
            "frozen_claim_text": durability_claim["claim_text"],
            "frozen_evidence_ids": list(durability_claim.get("evidence_ids") or []),
            "frozen_computed_value_ids": list(durability_claim.get("computed_value_ids") or []),
            "resolution_class": "EXTERNAL_READ_REQUIRED_THEN_BOUNDED_SYNTHESIS_IF_NEEDED",
            "preferred_existing_capability": "BOUNDED_CURRENT_DEVELOPMENTS_AND_PRIMARY_SOURCE_REVIEW",
            "local_review_first": True,
            "provider_read_authorized": False,
            "model_call_authorized": False,
        },
    ]

    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_research_reopen_request_hash": reopen_hash,
        "source_postprocess_artifact_hash": postprocess_hash,
        "source_judge_result_artifact_hash": judge_hash,
        "source_judge_proposal_hash": EXPECTED_JUDGE_PROPOSAL_HASH,
        "source_rebuttal_freeze_artifact_hash": rebuttal_hash,
        "source_judge_run_id": EXPECTED_JUDGE_RUN_ID,
        "new_linked_run_start_state": "S00",
        "canonical_reopen_reason_codes": list(EXPECTED_REOPEN_REASONS),
        "canonical_reopen_requirement_count": 3,
        "canonical_reopen_requirements": requirements,
        "judge_change_conditions_for_executable_invest": [_copy_condition(row) for row in conditions],
        "judge_change_condition_count": 4,
        "scope_separation_rule": "CANONICAL_REOPEN_REQUIREMENTS_ARE_NORMATIVE_FOR_CURRENT_REOPEN; META_CHANGE_CONDITIONS_ARE_ADDITIONAL_EXIT_TO_B5_EVIDENCE_TARGETS_AND_DO_NOT_REWRITE_CANONICAL_REOPEN_REASONS",
        "planned_current_developments_candidate_symbols": ["NVDA", "MSFT", "META"],
        "provider_efficiency_note": "Use existing bounded symbol-level Alpaca news capability after zero-call inventory; do not add a new retrieval framework.",
        "minimal_evidence_sequence": [
            "LOCAL_EXISTING_EVIDENCE_AND_PRIMITIVE_REPLAY",
            "DETERMINE_RESIDUAL_GAPS_ZERO_CALL",
            "BOUNDED_PROVIDER_READ_PREFLIGHT_IF_RESIDUAL_GAPS_REQUIRE_EXTERNAL_DATA",
            "BOUNDED_PROVIDER_READS_ONLY_AFTER_EXPLICIT_READ_AUTHORITY",
            "FREEZE_NEW_EVIDENCE_AND_RECONCILE_CLAIMS",
            "MODEL_SYNTHESIS_ONLY_AFTER_ZERO_CALL_COST_PREFLIGHT_AND_EXPLICIT_OWNER_APPROVAL_IF_STILL_NEEDED",
            "RETURN_TO_B4_ONLY_AFTER_RESEARCH_REOPEN_IS_DURABLY_CLOSED_OR_FAIL_CLOSED",
        ],
        "broad_b3_rerun_authorized": False,
        "research_run_started": False,
        "provider_reads_authorized": False,
        "model_calls_authorized": False,
        "judge_rerun_authorized": False,
        "rebuttal_rerun_authorized": False,
        "final_decision_created": False,
        "b5_handoff_created": False,
        "execution_authority": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_scope_artifact(payload: Mapping[str, Any], *, expected_code_commit_sha: str) -> str:
    observed = _self_hash(payload, field="artifact_hash")
    _require(payload.get("artifact_version") == ARTIFACT_VERSION, "scope version drift")
    _require(payload.get("status") == PASS_STATUS, "scope status drift")
    _require(payload.get("code_commit_sha") == expected_code_commit_sha, "scope code SHA drift")
    _require(payload.get("source_research_reopen_request_hash") == EXPECTED_REOPEN_HASH, "scope reopen lineage drift")
    _require(payload.get("source_postprocess_artifact_hash") == EXPECTED_POSTPROCESS_HASH, "scope postprocess lineage drift")
    _require(payload.get("source_judge_result_artifact_hash") == EXPECTED_JUDGE_RESULT_HASH, "scope Judge lineage drift")
    _require(payload.get("source_judge_proposal_hash") == EXPECTED_JUDGE_PROPOSAL_HASH, "scope Judge proposal drift")
    _require(payload.get("source_rebuttal_freeze_artifact_hash") == EXPECTED_REBUTTAL_FREEZE_HASH, "scope Rebuttal lineage drift")
    _require(payload.get("canonical_reopen_reason_codes") == list(EXPECTED_REOPEN_REASONS), "scope canonical reopen reason drift")
    _require(payload.get("canonical_reopen_requirement_count") == 3, "scope canonical requirement count drift")
    rows = payload.get("canonical_reopen_requirements")
    _require(isinstance(rows, list) and [row.get("requirement_id") for row in rows if isinstance(row, Mapping)] == [
        "NVDA_CURRENT_DEVELOPMENTS_Q4",
        "MSFT_VALUATION_CONTEXT_DEPTH",
        "MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY",
    ], "scope canonical requirement ids drift")
    conditions = payload.get("judge_change_conditions_for_executable_invest")
    _require(isinstance(conditions, list) and tuple(row.get("condition_id") for row in conditions if isinstance(row, Mapping)) == EXPECTED_META_CONDITION_IDS, "scope META condition drift")
    _require(payload.get("judge_change_condition_count") == 4, "scope META condition count drift")
    for row in [*rows, *conditions]:
        _require(isinstance(row, Mapping), "scope row malformed")
        _require(row.get("provider_read_authorized") is False, "scope row cannot authorize provider read")
        _require(row.get("model_call_authorized") is False, "scope row cannot authorize model call")
    _require(payload.get("broad_b3_rerun_authorized") is False, "broad B3 rerun must remain forbidden")
    _require(payload.get("research_run_started") is False, "S00 scope cannot start research")
    _require(payload.get("provider_reads_authorized") is False and payload.get("model_calls_authorized") is False, "S00 scope cannot authorize calls")
    _require(payload.get("judge_rerun_authorized") is False and payload.get("rebuttal_rerun_authorized") is False, "B4 reruns must remain forbidden")
    _require(payload.get("final_decision_created") is False and payload.get("b5_handoff_created") is False, "S00 scope cannot create FinalDecision/B5")
    _require(payload.get("execution_authority") is False, "S00 scope cannot grant execution authority")
    _require(payload.get("model_calls") == 0 and payload.get("provider_reads") == 0, "S00 scope must be zero-call")
    _require(payload.get("broker_writes") == 0 and payload.get("alpaca_orders") == 0, "S00 scope broker/order drift")
    _require(payload.get("live_money") == "PROHIBITED", "live money must remain prohibited")
    _require(payload.get("next_gate") == NEXT_GATE, "scope next gate drift")
    return observed
