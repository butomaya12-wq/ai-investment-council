from __future__ import annotations

from typing import Any, Mapping, Sequence

from aic.domain.canonical import canonical_sha256

from . import initial_runtime as initial_runtime_module
from .initial_runtime import InitialRuntimePlanItem, build_initial_runtime_plan
from .initial_runtime_diagnosis_v03 import (
    _claim_rule_scan,
    _computed_values,
    _plan_item_for_receipt,
    _source_claims,
    _verify_hash_bound_mapping,
)
from .initial_schema_repair_v04 import build_bounded_initial_request_v04
from .model_input import InitialCouncilModelInput
from .model_selection import InitialSelectedModelAuthority
from .models import CouncilClaimType, CouncilInputFreezeArtifact, CouncilLane
from .promotion import CouncilPromotionError, promote_initial_council_opinion
from .proposal import InitialCouncilOpinionProposal, validate_initial_proposal_lineage


DIAGNOSIS_ARTIFACT_VERSION = "B4_INITIAL_RUNTIME_V04_BLOCK_DIAGNOSIS_ARTIFACT_v0_1"
DIAGNOSIS_RUN_CLASS = "B4_INITIAL_RUNTIME_V04_ZERO_CALL_STAGE_CONTRACT_REPLAY_DIAGNOSIS"
EXPECTED_BLOCKED_STATUS = "BLOCKED_INITIAL_COUNCIL_NOT_FROZEN"
EXPECTED_RECEIPT_VERSION = "B4_INITIAL_RUNTIME_PAID_CALL_RECEIPT_v0_4"
EXPECTED_BILLING_CONTRACT_VERSION = "B4_INITIAL_RUNTIME_CACHE_WRITE_BILLING_v0_1"
EXPECTED_RECORDED_REASON = (
    "CouncilPromotionError: DECISION_BASIS is Judge-only and forbidden in initial opinions"
)


class InitialRuntimeV04DiagnosisError(ValueError):
    pass


def _proposal_rule_scan(
    proposal: InitialCouncilOpinionProposal,
    *,
    item: InitialRuntimePlanItem,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    proposal_failures: list[dict[str, str]] = []

    try:
        validate_initial_proposal_lineage(
            proposal,
            bundle=item.bundle,
            expected_lane=item.lane,
        )
    except Exception as exc:
        proposal_failures.append(
            {"rule": "proposal_lineage", "error": f"{type(exc).__name__}: {exc}"}
        )

    if item.lane == CouncilLane.JUDGE:
        proposal_failures.append(
            {"rule": "initial_lane", "error": "Judge claims are not initial CouncilOpinion claims"}
        )

    forbidden = [
        claim
        for claim in proposal.proposed_claims
        if claim.claim_type == CouncilClaimType.DECISION_BASIS
    ]
    if forbidden:
        proposal_failures.append(
            {
                "rule": "initial_stage_claim_type",
                "error": "DECISION_BASIS is Judge-only and forbidden in initial opinions",
            }
        )

    allowed_gaps = set(item.model_input.data_gap_refs)
    if not set(proposal.material_unknown_refs).issubset(allowed_gaps):
        proposal_failures.append(
            {
                "rule": "material_unknown_refs",
                "error": "initial opinion contains unknown/gap ref outside application allowlist",
            }
        )
    if not allowed_gaps.issubset(set(proposal.material_unknown_refs)):
        proposal_failures.append(
            {
                "rule": "required_data_gap_refs",
                "error": "application-required data gap ref may not be hidden by Council output",
            }
        )
    if not set(proposal.material_conflict_refs).issubset(
        set(item.bundle.allowed_conflict_ids)
    ):
        proposal_failures.append(
            {
                "rule": "material_conflict_refs",
                "error": "initial opinion material_conflict_refs escape frozen bundle",
            }
        )

    claim_scan = _claim_rule_scan(proposal, item=item)
    forbidden_refs = {
        claim.claim_local_ref
        for claim in forbidden
    }
    for row in claim_scan:
        row["initial_stage_claim_type_forbidden"] = (
            row["claim_local_ref"] in forbidden_refs
        )
    return claim_scan, proposal_failures


def diagnose_blocked_initial_runtime_v04(
    *,
    blocked_artifact: Mapping[str, Any],
    receipts: Sequence[Mapping[str, Any]],
    freeze: CouncilInputFreezeArtifact,
    model_inputs: tuple[
        InitialCouncilModelInput,
        InitialCouncilModelInput,
        InitialCouncilModelInput,
    ],
    runtime_preflight: Mapping[str, Any],
    authority: InitialSelectedModelAuthority,
) -> dict[str, Any]:
    blocked_hash = _verify_hash_bound_mapping(
        blocked_artifact,
        hash_field="artifact_hash",
        label="v0.4 blocked artifact",
    )
    if blocked_artifact.get("status") != EXPECTED_BLOCKED_STATUS:
        raise InitialRuntimeV04DiagnosisError("diagnosis requires blocked v0.4 Initial artifact")
    if blocked_artifact.get("initial_freeze_barrier") is not False:
        raise InitialRuntimeV04DiagnosisError("blocked artifact crossed Initial freeze barrier")
    if blocked_artifact.get("rebuttal_authorized") is not False:
        raise InitialRuntimeV04DiagnosisError("blocked artifact authorizes Rebuttal")
    if blocked_artifact.get("judge_authorized") is not False:
        raise InitialRuntimeV04DiagnosisError("blocked artifact authorizes Judge")
    if blocked_artifact.get("automatic_repair_calls") != 0:
        raise InitialRuntimeV04DiagnosisError("blocked run contains automatic repair calls")
    if blocked_artifact.get("broker_writes") != 0 or blocked_artifact.get("alpaca_orders") != 0:
        raise InitialRuntimeV04DiagnosisError("blocked run contains broker/order side effect")
    if blocked_artifact.get("live_money") != "PROHIBITED":
        raise InitialRuntimeV04DiagnosisError("blocked run live-money invariant drift")

    dispatch_attempts = blocked_artifact.get("dispatch_attempts")
    model_calls = blocked_artifact.get("model_calls")
    if type(dispatch_attempts) is not int or type(model_calls) is not int:
        raise InitialRuntimeV04DiagnosisError("blocked run counters missing")
    if dispatch_attempts != model_calls:
        raise InitialRuntimeV04DiagnosisError("blocked dispatch/model-call counters differ")
    if len(receipts) != dispatch_attempts:
        raise InitialRuntimeV04DiagnosisError("receipt count differs from blocked dispatch count")

    original_builder = initial_runtime_module.build_bounded_initial_request
    initial_runtime_module.build_bounded_initial_request = build_bounded_initial_request_v04
    try:
        plan = build_initial_runtime_plan(
            freeze=freeze,
            model_inputs=model_inputs,
            runtime_preflight=runtime_preflight,
            authority=authority,
        )
    finally:
        initial_runtime_module.build_bounded_initial_request = original_builder

    replay_records: list[dict[str, Any]] = []
    exact_promotion_error_reproduced = False
    total_forbidden = 0
    total_rule_failures = 0
    total_non_stage_failures = 0
    successful_replay_count = 0
    blocked_replay_count = 0

    for receipt in receipts:
        receipt_hash = _verify_hash_bound_mapping(
            receipt,
            hash_field="receipt_hash",
            label="v0.4 paid call receipt",
        )
        if receipt.get("receipt_version") != EXPECTED_RECEIPT_VERSION:
            raise InitialRuntimeV04DiagnosisError("receipt version is not v0.4")
        if receipt.get("billing_contract_version") != EXPECTED_BILLING_CONTRACT_VERSION:
            raise InitialRuntimeV04DiagnosisError("receipt billing contract mismatch")
        if receipt.get("provider_response_received") is not True:
            raise InitialRuntimeV04DiagnosisError("receipt lacks completed provider response")
        if receipt.get("cost_receipt_status") != "COMPLETE":
            raise InitialRuntimeV04DiagnosisError("receipt cost evidence incomplete")
        if receipt.get("semantic_replay_status") != "COMPLETE":
            raise InitialRuntimeV04DiagnosisError("receipt structured output not replay-complete")
        if receipt.get("automatic_repair_attempted") is not False:
            raise InitialRuntimeV04DiagnosisError("receipt indicates automatic repair")
        if receipt.get("broker_writes") != 0 or receipt.get("alpaca_orders") != 0:
            raise InitialRuntimeV04DiagnosisError("receipt contains broker/order side effect")
        if receipt.get("live_money") != "PROHIBITED":
            raise InitialRuntimeV04DiagnosisError("receipt live-money invariant drift")

        structured = receipt.get("structured_output")
        if not isinstance(structured, Mapping):
            raise InitialRuntimeV04DiagnosisError("receipt structured_output missing")
        if receipt.get("structured_output_hash") != canonical_sha256(structured):
            raise InitialRuntimeV04DiagnosisError("receipt structured_output hash mismatch")

        proposal = InitialCouncilOpinionProposal.model_validate(dict(structured))
        item = _plan_item_for_receipt(plan, receipt)
        claim_scan, proposal_failures = _proposal_rule_scan(proposal, item=item)
        forbidden_claims = [
            row for row in claim_scan if row["initial_stage_claim_type_forbidden"]
        ]
        total_forbidden += len(forbidden_claims)
        claim_failures = sum(row["promotion_rule_failure_count"] for row in claim_scan)
        total_rule_failures += claim_failures + len(proposal_failures)
        total_non_stage_failures += claim_failures + sum(
            1 for row in proposal_failures if row["rule"] != "initial_stage_claim_type"
        )

        promotion_error: str | None = None
        replay_status: str
        try:
            promote_initial_council_opinion(
                proposal,
                bundle=item.bundle,
                expected_lane=item.lane,
                source_claims=_source_claims(item.model_input),
                computed_value_values=_computed_values(item.model_input),
                allowed_data_gap_refs=item.model_input.data_gap_refs,
                required_data_gap_refs=item.model_input.data_gap_refs,
                frozen_at=freeze.bundles[0].created_at,
            )
        except CouncilPromotionError as exc:
            promotion_error = f"{type(exc).__name__}: {exc}"
            replay_status = "BLOCKED"
            blocked_replay_count += 1
            if promotion_error == EXPECTED_RECORDED_REASON:
                exact_promotion_error_reproduced = True
        else:
            replay_status = "PROMOTED"
            successful_replay_count += 1

        expected_validation = receipt.get("validation_status")
        if expected_validation == "PASS" and replay_status != "PROMOTED":
            raise InitialRuntimeV04DiagnosisError(
                "historically successful receipt no longer promotes in deterministic replay"
            )
        if expected_validation == "FAIL" and replay_status != "BLOCKED":
            raise InitialRuntimeV04DiagnosisError(
                "historically blocked receipt unexpectedly promotes in deterministic replay"
            )

        replay_records.append(
            {
                "dispatch_index": item.dispatch_index,
                "candidate_id": item.candidate_id,
                "lane": item.lane.value,
                "request_hash": item.request.request_hash,
                "receipt_hash": receipt_hash,
                "structured_output_hash": receipt["structured_output_hash"],
                "historical_validation_status": expected_validation,
                "promotion_replay_status": replay_status,
                "promotion_error": promotion_error,
                "initial_stage_forbidden_claim_type_count": len(forbidden_claims),
                "initial_stage_forbidden_claims": forbidden_claims,
                "proposal_rule_failures": proposal_failures,
                "promotion_rule_scan": claim_scan,
            }
        )

    recorded_reason = blocked_artifact.get("blocked_reason")
    recorded_exact_reason = recorded_reason == EXPECTED_RECORDED_REASON
    contract_gap_signal = (
        recorded_exact_reason
        and exact_promotion_error_reproduced
        and total_forbidden > 0
        and total_non_stage_failures == 0
    )

    artifact: dict[str, Any] = {
        "artifact_version": DIAGNOSIS_ARTIFACT_VERSION,
        "run_class": DIAGNOSIS_RUN_CLASS,
        "status": "PASS_ZERO_CALL_V04_STAGE_CONTRACT_REPLAY_DIAGNOSIS",
        "source_blocked_artifact_hash": blocked_hash,
        "source_run_id": blocked_artifact.get("run_id"),
        "code_commit_sha": runtime_preflight.get("code_commit_sha"),
        "runtime_request_preflight_artifact_hash": runtime_preflight.get("artifact_hash"),
        "selected_candidate": dict(runtime_preflight.get("selected_candidate", {})),
        "dispatch_attempts": dispatch_attempts,
        "model_calls_in_source_run": model_calls,
        "successful_replay_count": successful_replay_count,
        "blocked_replay_count": blocked_replay_count,
        "model_calls_performed_by_diagnosis": 0,
        "provider_reads_performed_by_diagnosis": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "recorded_blocked_reason": recorded_reason,
        "exact_promotion_error_reproduced": exact_promotion_error_reproduced,
        "initial_stage_forbidden_claim_type_count": total_forbidden,
        "promotion_rule_failure_count": total_rule_failures,
        "non_stage_promotion_rule_failure_count": total_non_stage_failures,
        "contract_gap_signal": contract_gap_signal,
        "replay_records": replay_records,
        "rerun_authorized": False,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact
