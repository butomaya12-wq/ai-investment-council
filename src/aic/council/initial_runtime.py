from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import json
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import COUNCIL_OPINION_V1, MATERIAL_CLAIM_V1
from aic.research.runtime import parse_responses_payload

from .bounded_request import assert_bounded_request_invariants, build_bounded_initial_request
from .initial_runtime_cost import verify_initial_runtime_cost_preflight
from .initial_runtime_preflight import (
    EXPECTED_LOGICAL_CALLS,
    verify_initial_runtime_request_preflight,
)
from .model_input import InitialCouncilModelInput
from .model_policy import CouncilModelStage
from .model_selection import InitialSelectedModelAuthority
from .models import CouncilInputFreezeArtifact, CouncilLane
from .promotion import InitialOpinionPromotionResult, promote_initial_council_opinion
from .proposal import InitialCouncilOpinionProposal
from .request import CouncilRequestEnvelope, CouncilRequestStage, parse_council_responses_payload


INITIAL_RUNTIME_VERSION = "B4_INITIAL_PRODUCTION_RUNTIME_v0_1"
INITIAL_COUNCIL_FREEZE_ARTIFACT_VERSION = "B4_INITIAL_COUNCIL_FREEZE_ARTIFACT_v0_1"
INITIAL_COUNCIL_FREEZE_RUN_CLASS = "B4_REAL_SELECTED_MODEL_INITIAL_COUNCIL"
INITIAL_COUNCIL_FROZEN_STATUS = "INITIAL_COUNCIL_FROZEN"
INITIAL_COUNCIL_BLOCKED_STATUS = "BLOCKED_INITIAL_COUNCIL_NOT_FROZEN"

_LANE_STAGE = (
    (CouncilLane.BULL, CouncilRequestStage.BULL_INITIAL),
    (CouncilLane.BEAR, CouncilRequestStage.BEAR_INITIAL),
    (CouncilLane.RED_TEAM, CouncilRequestStage.RED_TEAM_INITIAL),
)


class InitialRuntimeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class InitialRuntimePlanItem:
    dispatch_index: int
    candidate_id: str
    lane: CouncilLane
    stage: CouncilRequestStage
    bundle: Any
    model_input: InitialCouncilModelInput
    request: CouncilRequestEnvelope
    request_body_utf8_bytes: int


@dataclass(frozen=True, slots=True)
class InitialProcessedResponse:
    candidate_id: str
    lane: CouncilLane
    stage: CouncilRequestStage
    request_hash: str
    model_run_ref: str
    response_id: str
    effective_model: str
    latency_ms: int
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    reasoning_tokens: int
    actual_cost_usd: Decimal
    output_hash: str
    structured_output: Mapping[str, Any]
    structured_output_hash: str
    material_claims: tuple[object, ...]
    claim_metadata: tuple[object, ...]
    council_opinion: object
    council_opinion_hash: str
    validator_results: tuple[Mapping[str, str], ...]


def request_body_utf8_bytes(payload: Mapping[str, Any]) -> int:
    return len(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _runtime_variant_map(runtime_preflight: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    variants = runtime_preflight.get("selected_request_variants")
    if not isinstance(variants, list) or len(variants) != EXPECTED_LOGICAL_CALLS:
        raise InitialRuntimeError("Initial runtime preflight does not contain exact nine selected requests")
    result: dict[tuple[str, str], Mapping[str, Any]] = {}
    for raw in variants:
        if not isinstance(raw, Mapping):
            raise InitialRuntimeError("Initial runtime selected request record must be object")
        candidate = raw.get("candidate")
        lane = raw.get("lane")
        if not isinstance(candidate, str) or not isinstance(lane, str):
            raise InitialRuntimeError("Initial runtime selected request identity missing")
        key = (candidate, lane)
        if key in result:
            raise InitialRuntimeError("Initial runtime selected request identity duplicated")
        result[key] = raw
    return result


def build_initial_runtime_plan(
    *,
    freeze: CouncilInputFreezeArtifact,
    model_inputs: tuple[InitialCouncilModelInput, InitialCouncilModelInput, InitialCouncilModelInput],
    runtime_preflight: Mapping[str, Any],
    authority: InitialSelectedModelAuthority,
) -> tuple[InitialRuntimePlanItem, ...]:
    verify_initial_runtime_request_preflight(runtime_preflight)
    if runtime_preflight.get("b4_input_freeze_artifact_hash") != freeze.artifact_hash:
        raise InitialRuntimeError("Initial runtime preflight does not bind supplied B4 input freeze")
    if runtime_preflight.get("selected_model_authority_selection_hash") != authority.selection_hash:
        raise InitialRuntimeError("Initial runtime preflight does not bind selected-model authority")
    if runtime_preflight.get("selected_model_eval_artifact_hash") != authority.model_eval_artifact_hash:
        raise InitialRuntimeError("Initial runtime preflight model-eval authority mismatch")
    if authority.selected_candidate.stage is not CouncilModelStage.INITIAL:
        raise InitialRuntimeError("selected-model authority is not Initial stage")
    if tuple(item.candidate_id for item in model_inputs) != freeze.candidate_order:
        raise InitialRuntimeError("Initial runtime model-input candidate order drift")
    if tuple(runtime_preflight.get("candidate_order", ())) != freeze.candidate_order:
        raise InitialRuntimeError("Initial runtime preflight candidate order drift")

    frozen_by_candidate = {bundle.candidate_id: bundle for bundle in freeze.bundles}
    variant_by_key = _runtime_variant_map(runtime_preflight)
    selected = authority.selected_candidate
    plan: list[InitialRuntimePlanItem] = []
    dispatch_index = 0

    for model_input in model_inputs:
        bundle = frozen_by_candidate.get(model_input.candidate_id)
        if bundle is None:
            raise InitialRuntimeError("Initial runtime model input has no frozen bundle")
        model_input_payload = model_input.model_dump(mode="json", exclude_none=False)
        for lane, stage in _LANE_STAGE:
            dispatch_index += 1
            frozen_variant = variant_by_key.get((model_input.candidate_id, lane.value))
            if frozen_variant is None:
                raise InitialRuntimeError("Initial runtime preflight selected request missing")
            model_run_ref = frozen_variant.get("model_run_ref")
            if not isinstance(model_run_ref, str) or not model_run_ref:
                raise InitialRuntimeError("Initial runtime preflight model_run_ref missing")
            request = build_bounded_initial_request(
                stage=stage,
                model_candidate=selected,
                bundle=bundle,
                model_run_ref=model_run_ref,
                model_input=model_input_payload,
                allowed_data_gap_refs=model_input.data_gap_refs,
            )
            assert_bounded_request_invariants(request)
            byte_count = request_body_utf8_bytes(request.request_payload)
            if request.request_hash != frozen_variant.get("request_hash"):
                raise InitialRuntimeError("reconstructed Initial request hash differs from zero-call preflight")
            if byte_count != frozen_variant.get("request_body_utf8_bytes"):
                raise InitialRuntimeError("reconstructed Initial request byte size differs from zero-call preflight")
            if request.request_payload.get("model") != selected.model:
                raise InitialRuntimeError("reconstructed Initial request model differs from authority")
            reasoning = request.request_payload.get("reasoning")
            if not isinstance(reasoning, Mapping) or reasoning.get("effort") != selected.reasoning_effort:
                raise InitialRuntimeError("reconstructed Initial reasoning effort differs from authority")
            plan.append(
                InitialRuntimePlanItem(
                    dispatch_index=dispatch_index,
                    candidate_id=model_input.candidate_id,
                    lane=lane,
                    stage=stage,
                    bundle=bundle,
                    model_input=model_input,
                    request=request,
                    request_body_utf8_bytes=byte_count,
                )
            )

    if len(plan) != EXPECTED_LOGICAL_CALLS:
        raise InitialRuntimeError("Initial runtime plan must contain exactly nine calls")
    observed = tuple((item.candidate_id, item.lane.value) for item in plan)
    expected = tuple(
        (candidate, lane.value)
        for candidate in freeze.candidate_order
        for lane, _ in _LANE_STAGE
    )
    if observed != expected:
        raise InitialRuntimeError("Initial runtime plan order drift")
    return tuple(plan)


def _usage_counts(raw: Mapping[str, Any]) -> tuple[int, int, int, int]:
    usage = raw.get("usage")
    if not isinstance(usage, Mapping):
        return 0, 0, 0, 0
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    input_details = usage.get("input_tokens_details")
    output_details = usage.get("output_tokens_details")
    cached_tokens = input_details.get("cached_tokens") if isinstance(input_details, Mapping) else 0
    reasoning_tokens = output_details.get("reasoning_tokens") if isinstance(output_details, Mapping) else 0
    values = (input_tokens, cached_tokens, output_tokens, reasoning_tokens)
    normalized = tuple(value if type(value) is int and value >= 0 else 0 for value in values)
    return normalized  # type: ignore[return-value]


def actual_cost_usd(
    raw: Mapping[str, Any],
    *,
    model: str,
    pricing: Mapping[str, Any],
) -> Decimal:
    models = pricing.get("models")
    if not isinstance(models, Mapping) or not isinstance(models.get(model), Mapping):
        raise InitialRuntimeError("Initial runtime pricing does not cover selected model")
    record = models[model]
    input_tokens, cached_tokens, output_tokens, _ = _usage_counts(raw)
    cached_tokens = min(cached_tokens, input_tokens)
    uncached_tokens = input_tokens - cached_tokens
    try:
        input_rate = Decimal(str(record.get("input")))
        cached_rate = Decimal(str(record.get("cached_input")))
        output_rate = Decimal(str(record.get("output")))
    except Exception as exc:
        raise InitialRuntimeError("Initial runtime pricing record is invalid") from exc
    if any(not rate.is_finite() or rate < 0 for rate in (input_rate, cached_rate, output_rate)):
        raise InitialRuntimeError("Initial runtime pricing rate invalid")
    return (
        Decimal(uncached_tokens) * input_rate
        + Decimal(cached_tokens) * cached_rate
        + Decimal(output_tokens) * output_rate
    ) / Decimal(1_000_000)


def _source_claims(model_input: InitialCouncilModelInput) -> dict[str, object]:
    result: dict[str, object] = {}
    for raw in model_input.material_claims:
        claim = MATERIAL_CLAIM_V1.model_validate(dict(raw))
        if claim.claim_id in result:
            raise InitialRuntimeError("Initial runtime model input contains duplicate source claim")
        result[claim.claim_id] = claim
    return result


def _computed_values(model_input: InitialCouncilModelInput) -> dict[str, str]:
    return {item.computed_value_id: item.value for item in model_input.computed_values}


def process_initial_provider_response(
    item: InitialRuntimePlanItem,
    *,
    raw_response: Mapping[str, Any],
    latency_ms: int,
    frozen_at: datetime,
    pricing: Mapping[str, Any],
) -> InitialProcessedResponse:
    call, proposal = parse_council_responses_payload(
        raw_response,
        request=item.request,
        latency_ms=latency_ms,
    )
    if not isinstance(proposal, InitialCouncilOpinionProposal):
        raise InitialRuntimeError("Initial production response produced wrong DTO type")
    if not proposal.proposed_claims:
        raise InitialRuntimeError("Initial production response contains no structured Council claims")
    promotion: InitialOpinionPromotionResult = promote_initial_council_opinion(
        proposal,
        bundle=item.bundle,
        expected_lane=item.lane,
        source_claims=_source_claims(item.model_input),
        computed_value_values=_computed_values(item.model_input),
        allowed_data_gap_refs=item.model_input.data_gap_refs,
        required_data_gap_refs=item.model_input.data_gap_refs,
        frozen_at=frozen_at,
    )
    structured = proposal.model_dump(mode="json", exclude_none=False)
    opinion = COUNCIL_OPINION_V1.model_validate(promotion.council_opinion)
    input_tokens, cached_tokens, output_tokens, reasoning_tokens = _usage_counts(raw_response)
    model = item.request.request_payload.get("model")
    if not isinstance(model, str) or not model:
        raise InitialRuntimeError("Initial runtime request model missing")
    return InitialProcessedResponse(
        candidate_id=item.candidate_id,
        lane=item.lane,
        stage=item.stage,
        request_hash=item.request.request_hash,
        model_run_ref=proposal.model_run_ref,
        response_id=call.response_id,
        effective_model=call.effective_model,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        cached_tokens=cached_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        actual_cost_usd=actual_cost_usd(raw_response, model=model, pricing=pricing),
        output_hash=call.output_hash,
        structured_output=structured,
        structured_output_hash=canonical_sha256(structured),
        material_claims=promotion.material_claims,
        claim_metadata=promotion.claim_metadata,
        council_opinion=opinion,
        council_opinion_hash=canonical_sha256(opinion),
        validator_results=promotion.validator_results,
    )


def processed_response_record(result: InitialProcessedResponse) -> dict[str, Any]:
    record: dict[str, Any] = {
        "candidate_id": result.candidate_id,
        "lane": result.lane.value,
        "stage": result.stage.value,
        "request_hash": result.request_hash,
        "model_run_ref": result.model_run_ref,
        "response_id": result.response_id,
        "effective_model": result.effective_model,
        "latency_ms": result.latency_ms,
        "input_tokens": result.input_tokens,
        "cached_tokens": result.cached_tokens,
        "output_tokens": result.output_tokens,
        "reasoning_tokens": result.reasoning_tokens,
        "actual_cost_usd": str(result.actual_cost_usd),
        "output_hash": result.output_hash,
        "structured_output": dict(result.structured_output),
        "structured_output_hash": result.structured_output_hash,
        "material_claims": [
            claim.model_dump(mode="json", exclude_none=False, warnings=False)
            for claim in result.material_claims
        ],
        "claim_metadata": [
            item.model_dump(mode="json", exclude_none=False)
            for item in result.claim_metadata
        ],
        "council_opinion": result.council_opinion.model_dump(
            mode="json", exclude_none=False, warnings=False
        ),
        "council_opinion_hash": result.council_opinion_hash,
        "validator_results": [dict(item) for item in result.validator_results],
    }
    record["record_hash"] = canonical_sha256(record)
    return record


def _validate_processed_record(raw: Mapping[str, Any]) -> None:
    record_hash = raw.get("record_hash")
    if not isinstance(record_hash, str) or record_hash != canonical_sha256(raw, exclude_fields=("record_hash",)):
        raise InitialRuntimeError("Initial processed record hash mismatch")
    structured = raw.get("structured_output")
    if not isinstance(structured, Mapping) or raw.get("structured_output_hash") != canonical_sha256(structured):
        raise InitialRuntimeError("Initial processed structured output hash mismatch")
    claims = raw.get("material_claims")
    if not isinstance(claims, list) or not claims:
        raise InitialRuntimeError("Initial processed record requires promoted claims")
    for claim in claims:
        MATERIAL_CLAIM_V1.model_validate(claim)
    opinion_raw = raw.get("council_opinion")
    if not isinstance(opinion_raw, Mapping):
        raise InitialRuntimeError("Initial processed record CouncilOpinion missing")
    opinion = COUNCIL_OPINION_V1.model_validate(opinion_raw)
    if raw.get("council_opinion_hash") != canonical_sha256(opinion):
        raise InitialRuntimeError("Initial processed CouncilOpinion hash mismatch")
    if opinion.candidate_id != raw.get("candidate_id") or opinion.lane != raw.get("lane"):
        raise InitialRuntimeError("Initial processed CouncilOpinion identity drift")


def build_initial_council_freeze_artifact(
    *,
    processed_records: tuple[Mapping[str, Any], ...],
    freeze: CouncilInputFreezeArtifact,
    runtime_preflight: Mapping[str, Any],
    cost_preflight: Mapping[str, Any],
    authority: InitialSelectedModelAuthority,
    run_id: str,
    paid_authorization_artifact_hash: str,
    receipt_manifest_hash: str,
    actual_cost_usd_total: Decimal,
) -> dict[str, Any]:
    runtime_hash = verify_initial_runtime_request_preflight(runtime_preflight)
    cost_hash = verify_initial_runtime_cost_preflight(cost_preflight)
    if cost_preflight.get("runtime_request_preflight_artifact_hash") != runtime_hash:
        raise InitialRuntimeError("Initial runtime cost artifact does not bind request preflight")
    if runtime_preflight.get("b4_input_freeze_artifact_hash") != freeze.artifact_hash:
        raise InitialRuntimeError("Initial runtime freeze artifact input lineage mismatch")
    if runtime_preflight.get("selected_model_authority_selection_hash") != authority.selection_hash:
        raise InitialRuntimeError("Initial runtime freeze selected-model authority mismatch")
    if len(processed_records) != EXPECTED_LOGICAL_CALLS:
        raise InitialRuntimeError("INITIAL_COUNCIL_FROZEN requires exactly nine processed opinions")

    expected_identity = tuple(
        (candidate, lane.value)
        for candidate in freeze.candidate_order
        for lane, _ in _LANE_STAGE
    )
    observed_identity: list[tuple[str, str]] = []
    opinion_ids: list[str] = []
    opinion_hashes: list[str] = []
    for raw in processed_records:
        _validate_processed_record(raw)
        candidate = raw.get("candidate_id")
        lane = raw.get("lane")
        if not isinstance(candidate, str) or not isinstance(lane, str):
            raise InitialRuntimeError("Initial processed record identity missing")
        observed_identity.append((candidate, lane))
        opinion = raw["council_opinion"]
        opinion_id = opinion.get("opinion_id") if isinstance(opinion, Mapping) else None
        if not isinstance(opinion_id, str) or not opinion_id:
            raise InitialRuntimeError("Initial processed CouncilOpinion ID missing")
        opinion_ids.append(opinion_id)
        opinion_hashes.append(raw["council_opinion_hash"])
    if tuple(observed_identity) != expected_identity:
        raise InitialRuntimeError("INITIAL_COUNCIL_FROZEN opinion order/coverage drift")
    if len(set(opinion_ids)) != EXPECTED_LOGICAL_CALLS:
        raise InitialRuntimeError("INITIAL_COUNCIL_FROZEN requires nine unique opinion IDs")
    if len(set(opinion_hashes)) != EXPECTED_LOGICAL_CALLS:
        raise InitialRuntimeError("INITIAL_COUNCIL_FROZEN requires nine unique opinion hashes")

    artifact: dict[str, Any] = {
        "artifact_version": INITIAL_COUNCIL_FREEZE_ARTIFACT_VERSION,
        "runtime_version": INITIAL_RUNTIME_VERSION,
        "run_class": INITIAL_COUNCIL_FREEZE_RUN_CLASS,
        "status": INITIAL_COUNCIL_FROZEN_STATUS,
        "run_id": run_id,
        "code_commit_sha": runtime_preflight["code_commit_sha"],
        "b4_input_freeze_artifact_hash": freeze.artifact_hash,
        "runtime_request_preflight_artifact_hash": runtime_hash,
        "runtime_cost_preflight_artifact_hash": cost_hash,
        "selected_model_authority_selection_hash": authority.selection_hash,
        "selected_model_eval_artifact_hash": authority.model_eval_artifact_hash,
        "paid_authorization_artifact_hash": paid_authorization_artifact_hash,
        "selected_candidate": dict(runtime_preflight["selected_candidate"]),
        "candidate_order": list(freeze.candidate_order),
        "initial_opinion_count": EXPECTED_LOGICAL_CALLS,
        "initial_opinion_ids": opinion_ids,
        "initial_opinion_hashes": opinion_hashes,
        "processed_records": [dict(item) for item in processed_records],
        "dispatch_attempts": EXPECTED_LOGICAL_CALLS,
        "model_calls": EXPECTED_LOGICAL_CALLS,
        "automatic_repair_calls": 0,
        "rebuttal_model_calls": 0,
        "judge_model_calls": 0,
        "actual_cost_usd": str(actual_cost_usd_total),
        "cost_receipt_status": "COMPLETE",
        "receipt_manifest_hash": receipt_manifest_hash,
        "initial_freeze_barrier": True,
        "rebuttal_authorized": False,
        "judge_authorized": False,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact
