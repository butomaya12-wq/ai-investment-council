from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import MATERIAL_CLAIM_V1

from .initial_runtime import InitialRuntimePlanItem, build_initial_runtime_plan
from .model_input import InitialCouncilModelInput
from .model_selection import InitialSelectedModelAuthority
from .models import (
    CouncilInputFreezeArtifact,
    CouncilMateriality,
    CouncilSupportStatus,
)
from .promotion import CouncilPromotionError, promote_initial_council_opinion
from .proposal import InitialCouncilOpinionProposal


DIAGNOSIS_ARTIFACT_VERSION = "B4_INITIAL_RUNTIME_BLOCK_DIAGNOSIS_ARTIFACT_v0_1"
DIAGNOSIS_RUN_CLASS = "B4_INITIAL_RUNTIME_ZERO_CALL_REPLAY_DIAGNOSIS"
EXPECTED_BLOCKED_STATUS = "BLOCKED_INITIAL_COUNCIL_NOT_FROZEN"
EXPECTED_RECEIPT_VERSION = "B4_INITIAL_RUNTIME_PAID_CALL_RECEIPT_v0_2"
EXPECTED_BILLING_CONTRACT_VERSION = "B4_INITIAL_RUNTIME_CACHE_WRITE_BILLING_v0_1"


class InitialRuntimeDiagnosisError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ClaimViolation:
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


def material_support_violations(
    proposal: InitialCouncilOpinionProposal,
) -> tuple[ClaimViolation, ...]:
    violations: list[ClaimViolation] = []
    for claim in proposal.proposed_claims:
        if (
            claim.materiality == CouncilMateriality.MATERIAL
            and claim.support_status != CouncilSupportStatus.SUPPORTED
        ):
            violations.append(
                ClaimViolation(
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
    return tuple(violations)


def _verify_hash_bound_mapping(raw: Mapping[str, Any], *, hash_field: str, label: str) -> str:
    observed = raw.get(hash_field)
    if not isinstance(observed, str) or len(observed) != 64:
        raise InitialRuntimeDiagnosisError(f"{label} hash missing")
    expected = canonical_sha256(raw, exclude_fields=(hash_field,))
    if observed != expected:
        raise InitialRuntimeDiagnosisError(f"{label} canonical hash mismatch")
    return observed


def _source_claims(model_input: InitialCouncilModelInput) -> dict[str, object]:
    result: dict[str, object] = {}
    for raw in model_input.material_claims:
        claim = MATERIAL_CLAIM_V1.model_validate(dict(raw))
        if claim.claim_id in result:
            raise InitialRuntimeDiagnosisError("duplicate source MaterialClaim in model input")
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
        raise InitialRuntimeDiagnosisError("receipt dispatch_index missing")
    matches = [item for item in plan if item.dispatch_index == dispatch_index]
    if len(matches) != 1:
        raise InitialRuntimeDiagnosisError("receipt dispatch_index does not identify one frozen plan item")
    item = matches[0]
    if receipt.get("candidate_id") != item.candidate_id:
        raise InitialRuntimeDiagnosisError("receipt candidate differs from frozen plan")
    if receipt.get("lane") != item.lane.value:
        raise InitialRuntimeDiagnosisError("receipt lane differs from frozen plan")
    if receipt.get("request_hash") != item.request.request_hash:
        raise InitialRuntimeDiagnosisError("receipt request hash differs from frozen plan")
    return item


def diagnose_blocked_initial_runtime(
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
        label="blocked artifact",
    )
    if blocked_artifact.get("status") != EXPECTED_BLOCKED_STATUS:
        raise InitialRuntimeDiagnosisError("diagnosis requires blocked Initial artifact")
    if blocked_artifact.get("initial_freeze_barrier") is not False:
        raise InitialRuntimeDiagnosisError("blocked artifact unexpectedly crossed Initial freeze barrier")
    if blocked_artifact.get("rebuttal_authorized") is not False:
        raise InitialRuntimeDiagnosisError("blocked artifact unexpectedly authorizes Rebuttal")
    if blocked_artifact.get("judge_authorized") is not False:
        raise InitialRuntimeDiagnosisError("blocked artifact unexpectedly authorizes Judge")
    if blocked_artifact.get("automatic_repair_calls") != 0:
        raise InitialRuntimeDiagnosisError("blocked run contains automatic repair calls")
    if blocked_artifact.get("broker_writes") != 0 or blocked_artifact.get("alpaca_orders") != 0:
        raise InitialRuntimeDiagnosisError("blocked run contains broker/order side effect")
    if blocked_artifact.get("live_money") != "PROHIBITED":
        raise InitialRuntimeDiagnosisError("blocked run live-money invariant drift")

    dispatch_attempts = blocked_artifact.get("dispatch_attempts")
    model_calls = blocked_artifact.get("model_calls")
    if type(dispatch_attempts) is not int or type(model_calls) is not int:
        raise InitialRuntimeDiagnosisError("blocked run counters missing")
    if dispatch_attempts != model_calls:
        raise InitialRuntimeDiagnosisError("blocked run dispatch/model-call counters differ")
    if len(receipts) != dispatch_attempts:
        raise InitialRuntimeDiagnosisError("receipt count differs from blocked dispatch count")

    plan = build_initial_runtime_plan(
        freeze=freeze,
        model_inputs=model_inputs,
        runtime_preflight=runtime_preflight,
        authority=authority,
    )

    replay_records: list[dict[str, Any]] = []
    total_violations = 0
    exact_promotion_error_reproduced = False

    for receipt in receipts:
        receipt_hash = _verify_hash_bound_mapping(
            receipt,
            hash_field="receipt_hash",
            label="paid call receipt",
        )
        if receipt.get("receipt_version") != EXPECTED_RECEIPT_VERSION:
            raise InitialRuntimeDiagnosisError("receipt version is not v0.2 replay-capable contract")
        if receipt.get("billing_contract_version") != EXPECTED_BILLING_CONTRACT_VERSION:
            raise InitialRuntimeDiagnosisError("receipt billing contract mismatch")
        if receipt.get("provider_response_received") is not True:
            raise InitialRuntimeDiagnosisError("receipt lacks completed provider response")
        if receipt.get("cost_receipt_status") != "COMPLETE":
            raise InitialRuntimeDiagnosisError("receipt cost evidence is incomplete")
        if receipt.get("semantic_replay_status") != "COMPLETE":
            raise InitialRuntimeDiagnosisError("receipt structured output is not replay-complete")
        if receipt.get("automatic_repair_attempted") is not False:
            raise InitialRuntimeDiagnosisError("receipt indicates automatic repair")
        if receipt.get("broker_writes") != 0 or receipt.get("alpaca_orders") != 0:
            raise InitialRuntimeDiagnosisError("receipt contains broker/order side effect")
        if receipt.get("live_money") != "PROHIBITED":
            raise InitialRuntimeDiagnosisError("receipt live-money invariant drift")

        structured = receipt.get("structured_output")
        if not isinstance(structured, Mapping):
            raise InitialRuntimeDiagnosisError("receipt structured_output missing")
        if receipt.get("structured_output_hash") != canonical_sha256(structured):
            raise InitialRuntimeDiagnosisError("receipt structured_output hash mismatch")
        proposal = InitialCouncilOpinionProposal.model_validate(dict(structured))
        item = _plan_item_for_receipt(plan, receipt)
        if proposal.model_run_ref != item.request.request_payload["text"]["format"]["schema"]["properties"]["model_run_ref"]["const"]:
            raise InitialRuntimeDiagnosisError("replayed proposal model_run_ref differs from frozen plan")

        violations = material_support_violations(proposal)
        total_violations += len(violations)
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
            if str(exc) == "unsupported/conflicted MATERIAL B4 claim may not promote":
                exact_promotion_error_reproduced = True
        else:
            raise InitialRuntimeDiagnosisError(
                "saved structured output unexpectedly promotes successfully during blocked-run replay"
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
                "material_non_supported_claim_count": len(violations),
                "material_non_supported_claims": [item.as_dict() for item in violations],
            }
        )

    recorded_reason = blocked_artifact.get("blocked_reason")
    recorded_exact_reason = (
        recorded_reason
        == "CouncilPromotionError: unsupported/conflicted MATERIAL B4 claim may not promote"
    )
    contract_gap_signal = (
        recorded_exact_reason
        and exact_promotion_error_reproduced
        and total_violations > 0
    )

    artifact: dict[str, Any] = {
        "artifact_version": DIAGNOSIS_ARTIFACT_VERSION,
        "run_class": DIAGNOSIS_RUN_CLASS,
        "status": "PASS_ZERO_CALL_REPLAY_DIAGNOSIS",
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
        "material_non_supported_claim_count": total_violations,
        "contract_gap_signal": contract_gap_signal,
        "replay_records": replay_records,
        "rerun_authorized": False,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact
