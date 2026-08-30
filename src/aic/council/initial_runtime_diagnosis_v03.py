from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import MATERIAL_CLAIM_V1

from . import initial_runtime as initial_runtime_module
from .initial_runtime import InitialRuntimePlanItem, build_initial_runtime_plan
from .initial_schema_repair_v03 import build_bounded_initial_request_v03
from .model_input import InitialCouncilModelInput
from .model_selection import InitialSelectedModelAuthority
from .models import CouncilClaimKind, CouncilInputFreezeArtifact
from .promotion import (
    CouncilPromotionError,
    _validate_claim_refs,
    _validate_generated_text,
    _validate_numeric_provenance,
    _validate_support_semantics,
    promote_initial_council_opinion,
)
from .proposal import InitialCouncilOpinionProposal


DIAGNOSIS_ARTIFACT_VERSION = "B4_INITIAL_RUNTIME_V03_BLOCK_DIAGNOSIS_ARTIFACT_v0_1"
DIAGNOSIS_RUN_CLASS = "B4_INITIAL_RUNTIME_V03_ZERO_CALL_PROMOTION_REPLAY_DIAGNOSIS"
EXPECTED_BLOCKED_STATUS = "BLOCKED_INITIAL_COUNCIL_NOT_FROZEN"
EXPECTED_RECEIPT_VERSION = "B4_INITIAL_RUNTIME_PAID_CALL_RECEIPT_v0_3"
EXPECTED_BILLING_CONTRACT_VERSION = "B4_INITIAL_RUNTIME_CACHE_WRITE_BILLING_v0_1"
EXPECTED_RECORDED_REASON = (
    "CouncilPromotionError: B4 inference/process finding requires frozen provenance refs"
)


class InitialRuntimeV03DiagnosisError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ProvenanceGap:
    claim_local_ref: str
    claim_type: str
    claim_kind: str
    support_status: str
    materiality: str
    source_material_claim_ids: tuple[str, ...]
    computed_value_ids: tuple[str, ...]
    conflict_ids: tuple[str, ...]
    claim_text_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim_local_ref": self.claim_local_ref,
            "claim_type": self.claim_type,
            "claim_kind": self.claim_kind,
            "support_status": self.support_status,
            "materiality": self.materiality,
            "source_material_claim_ids": list(self.source_material_claim_ids),
            "computed_value_ids": list(self.computed_value_ids),
            "conflict_ids": list(self.conflict_ids),
            "claim_text_hash": self.claim_text_hash,
        }


def missing_inference_provenance(
    proposal: InitialCouncilOpinionProposal,
) -> tuple[ProvenanceGap, ...]:
    gaps: list[ProvenanceGap] = []
    for claim in proposal.proposed_claims:
        if claim.claim_kind not in {
            CouncilClaimKind.INFERENCE,
            CouncilClaimKind.PROCESS_FINDING,
        }:
            continue
        if claim.source_material_claim_ids or claim.computed_value_ids or claim.conflict_ids:
            continue
        gaps.append(
            ProvenanceGap(
                claim_local_ref=claim.claim_local_ref,
                claim_type=claim.claim_type.value,
                claim_kind=claim.claim_kind.value,
                support_status=claim.support_status.value,
                materiality=claim.materiality.value,
                source_material_claim_ids=tuple(claim.source_material_claim_ids),
                computed_value_ids=tuple(claim.computed_value_ids),
                conflict_ids=tuple(claim.conflict_ids),
                claim_text_hash=canonical_sha256({"claim_text": claim.claim_text}),
            )
        )
    return tuple(gaps)


def _verify_hash_bound_mapping(raw: Mapping[str, Any], *, hash_field: str, label: str) -> str:
    observed = raw.get(hash_field)
    if not isinstance(observed, str) or len(observed) != 64:
        raise InitialRuntimeV03DiagnosisError(f"{label} hash missing")
    expected = canonical_sha256(raw, exclude_fields=(hash_field,))
    if observed != expected:
        raise InitialRuntimeV03DiagnosisError(f"{label} canonical hash mismatch")
    return observed


def _source_claims(model_input: InitialCouncilModelInput) -> dict[str, object]:
    result: dict[str, object] = {}
    for raw in model_input.material_claims:
        claim = MATERIAL_CLAIM_V1.model_validate(dict(raw))
        if claim.claim_id in result:
            raise InitialRuntimeV03DiagnosisError("duplicate source MaterialClaim in model input")
        result[claim.claim_id] = claim
    return result


def _computed_values(model_input: InitialCouncilModelInput) -> dict[str, str]:
    return {item.computed_value_id: item.value for item in model_input.computed_values}


def _plan_item_for_receipt(
    plan: Sequence[InitialRuntimePlanItem],
    receipt: Mapping[str, Any],
) -> InitialRuntimePlanItem:
    dispatch_index = receipt.get("dispatch_index")
    if type(dispatch_index) is not int:
        raise InitialRuntimeV03DiagnosisError("receipt dispatch_index missing")
    matches = [item for item in plan if item.dispatch_index == dispatch_index]
    if len(matches) != 1:
        raise InitialRuntimeV03DiagnosisError(
            "receipt dispatch_index does not identify one repaired frozen plan item"
        )
    item = matches[0]
    if receipt.get("candidate_id") != item.candidate_id:
        raise InitialRuntimeV03DiagnosisError("receipt candidate differs from repaired frozen plan")
    if receipt.get("lane") != item.lane.value:
        raise InitialRuntimeV03DiagnosisError("receipt lane differs from repaired frozen plan")
    if receipt.get("request_hash") != item.request.request_hash:
        raise InitialRuntimeV03DiagnosisError("receipt request hash differs from repaired frozen plan")
    return item


def _claim_rule_scan(
    proposal: InitialCouncilOpinionProposal,
    *,
    item: InitialRuntimePlanItem,
) -> list[dict[str, Any]]:
    source_by_id = _source_claims(item.model_input)
    computed = _computed_values(item.model_input)
    results: list[dict[str, Any]] = []

    for claim in proposal.proposed_claims:
        failures: list[dict[str, str]] = []
        parents: tuple[object, ...] | None = None

        try:
            _validate_generated_text(claim)
        except CouncilPromotionError as exc:
            failures.append({"rule": "generated_text", "error": str(exc)})

        try:
            parents = _validate_claim_refs(
                claim,
                bundle=item.bundle,
                source_by_id=source_by_id,
                computed_value_values=computed,
            )
        except CouncilPromotionError as exc:
            failures.append({"rule": "claim_refs", "error": str(exc)})

        if parents is not None:
            try:
                _validate_support_semantics(claim, parents=parents)
            except CouncilPromotionError as exc:
                failures.append({"rule": "support_semantics", "error": str(exc)})

            try:
                _validate_numeric_provenance(
                    claim,
                    parents=parents,
                    computed_value_values=computed,
                )
            except CouncilPromotionError as exc:
                failures.append({"rule": "numeric_provenance", "error": str(exc)})

        results.append(
            {
                "claim_local_ref": claim.claim_local_ref,
                "claim_type": claim.claim_type.value,
                "claim_kind": claim.claim_kind.value,
                "support_status": claim.support_status.value,
                "materiality": claim.materiality.value,
                "source_material_claim_ids": list(claim.source_material_claim_ids),
                "computed_value_ids": list(claim.computed_value_ids),
                "conflict_ids": list(claim.conflict_ids),
                "claim_text_hash": canonical_sha256({"claim_text": claim.claim_text}),
                "promotion_rule_failure_count": len(failures),
                "promotion_rule_failures": failures,
            }
        )
    return results


def diagnose_blocked_initial_runtime_v03(
    *,
    blocked_artifact: Mapping[str, Any],
    receipts: Sequence[Mapping[str, Any]],
    freeze: CouncilInputFreezeArtifact,
    model_inputs: tuple[InitialCouncilModelInput, InitialCouncilModelInput, InitialCouncilModelInput],
    runtime_preflight: Mapping[str, Any],
    authority: InitialSelectedModelAuthority,
) -> dict[str, Any]:
    blocked_hash = _verify_hash_bound_mapping(
        blocked_artifact,
        hash_field="artifact_hash",
        label="v0.3 blocked artifact",
    )
    if blocked_artifact.get("status") != EXPECTED_BLOCKED_STATUS:
        raise InitialRuntimeV03DiagnosisError("diagnosis requires blocked v0.3 Initial artifact")
    if blocked_artifact.get("initial_freeze_barrier") is not False:
        raise InitialRuntimeV03DiagnosisError("blocked artifact unexpectedly crossed Initial freeze barrier")
    if blocked_artifact.get("rebuttal_authorized") is not False:
        raise InitialRuntimeV03DiagnosisError("blocked artifact unexpectedly authorizes Rebuttal")
    if blocked_artifact.get("judge_authorized") is not False:
        raise InitialRuntimeV03DiagnosisError("blocked artifact unexpectedly authorizes Judge")
    if blocked_artifact.get("automatic_repair_calls") != 0:
        raise InitialRuntimeV03DiagnosisError("blocked run contains automatic repair calls")
    if blocked_artifact.get("broker_writes") != 0 or blocked_artifact.get("alpaca_orders") != 0:
        raise InitialRuntimeV03DiagnosisError("blocked run contains broker/order side effect")
    if blocked_artifact.get("live_money") != "PROHIBITED":
        raise InitialRuntimeV03DiagnosisError("blocked run live-money invariant drift")

    dispatch_attempts = blocked_artifact.get("dispatch_attempts")
    model_calls = blocked_artifact.get("model_calls")
    if type(dispatch_attempts) is not int or type(model_calls) is not int:
        raise InitialRuntimeV03DiagnosisError("blocked run counters missing")
    if dispatch_attempts != model_calls:
        raise InitialRuntimeV03DiagnosisError("blocked run dispatch/model-call counters differ")
    if len(receipts) != dispatch_attempts:
        raise InitialRuntimeV03DiagnosisError("receipt count differs from blocked dispatch count")

    original_builder = initial_runtime_module.build_bounded_initial_request
    initial_runtime_module.build_bounded_initial_request = build_bounded_initial_request_v03
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
    total_missing_provenance = 0
    total_rule_failures = 0
    exact_promotion_error_reproduced = False

    for receipt in receipts:
        receipt_hash = _verify_hash_bound_mapping(
            receipt,
            hash_field="receipt_hash",
            label="v0.3 paid call receipt",
        )
        if receipt.get("receipt_version") != EXPECTED_RECEIPT_VERSION:
            raise InitialRuntimeV03DiagnosisError("receipt version is not v0.3 replay-capable contract")
        if receipt.get("billing_contract_version") != EXPECTED_BILLING_CONTRACT_VERSION:
            raise InitialRuntimeV03DiagnosisError("receipt billing contract mismatch")
        if receipt.get("provider_response_received") is not True:
            raise InitialRuntimeV03DiagnosisError("receipt lacks completed provider response")
        if receipt.get("cost_receipt_status") != "COMPLETE":
            raise InitialRuntimeV03DiagnosisError("receipt cost evidence is incomplete")
        if receipt.get("semantic_replay_status") != "COMPLETE":
            raise InitialRuntimeV03DiagnosisError("receipt structured output is not replay-complete")
        if receipt.get("automatic_repair_attempted") is not False:
            raise InitialRuntimeV03DiagnosisError("receipt indicates automatic repair")
        if receipt.get("broker_writes") != 0 or receipt.get("alpaca_orders") != 0:
            raise InitialRuntimeV03DiagnosisError("receipt contains broker/order side effect")
        if receipt.get("live_money") != "PROHIBITED":
            raise InitialRuntimeV03DiagnosisError("receipt live-money invariant drift")

        structured = receipt.get("structured_output")
        if not isinstance(structured, Mapping):
            raise InitialRuntimeV03DiagnosisError("receipt structured_output missing")
        if receipt.get("structured_output_hash") != canonical_sha256(structured):
            raise InitialRuntimeV03DiagnosisError("receipt structured_output hash mismatch")

        proposal = InitialCouncilOpinionProposal.model_validate(dict(structured))
        item = _plan_item_for_receipt(plan, receipt)
        gaps = missing_inference_provenance(proposal)
        total_missing_provenance += len(gaps)
        rule_scan = _claim_rule_scan(proposal, item=item)
        total_rule_failures += sum(row["promotion_rule_failure_count"] for row in rule_scan)

        promotion_error: str | None = None
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
            if promotion_error == EXPECTED_RECORDED_REASON:
                exact_promotion_error_reproduced = True
        else:
            raise InitialRuntimeV03DiagnosisError(
                "saved v0.3 structured output unexpectedly promotes successfully during replay"
            )

        replay_records.append(
            {
                "dispatch_index": item.dispatch_index,
                "candidate_id": item.candidate_id,
                "lane": item.lane.value,
                "request_hash": item.request.request_hash,
                "receipt_hash": receipt_hash,
                "structured_output_hash": receipt["structured_output_hash"],
                "proposal_parse_status": "PASS",
                "promotion_replay_status": "BLOCKED_AS_RECORDED",
                "promotion_error": promotion_error,
                "missing_inference_provenance_count": len(gaps),
                "missing_inference_provenance_claims": [gap.as_dict() for gap in gaps],
                "promotion_rule_scan": rule_scan,
            }
        )

    recorded_reason = blocked_artifact.get("blocked_reason")
    recorded_exact_reason = recorded_reason == EXPECTED_RECORDED_REASON
    contract_gap_signal = (
        recorded_exact_reason
        and exact_promotion_error_reproduced
        and total_missing_provenance > 0
    )

    artifact: dict[str, Any] = {
        "artifact_version": DIAGNOSIS_ARTIFACT_VERSION,
        "run_class": DIAGNOSIS_RUN_CLASS,
        "status": "PASS_ZERO_CALL_V03_PROMOTION_REPLAY_DIAGNOSIS",
        "source_blocked_artifact_hash": blocked_hash,
        "source_run_id": blocked_artifact.get("run_id"),
        "code_commit_sha": runtime_preflight.get("code_commit_sha"),
        "runtime_request_preflight_artifact_hash": runtime_preflight.get("artifact_hash"),
        "selected_candidate": dict(runtime_preflight.get("selected_candidate", {})),
        "dispatch_attempts": dispatch_attempts,
        "model_calls_in_source_run": model_calls,
        "model_calls_performed_by_diagnosis": 0,
        "provider_reads_performed_by_diagnosis": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "recorded_blocked_reason": recorded_reason,
        "exact_promotion_error_reproduced": exact_promotion_error_reproduced,
        "missing_inference_provenance_count": total_missing_provenance,
        "promotion_rule_failure_count": total_rule_failures,
        "contract_gap_signal": contract_gap_signal,
        "replay_records": replay_records,
        "rerun_authorized": False,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact
