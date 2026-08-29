from __future__ import annotations

from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256

from .model_policy import CouncilModelCandidate, CouncilModelStage, STAGE_MAX_OUTPUT_TOKENS
from .models import CouncilInputBundle
from .request import (
    CouncilRequestEnvelope,
    CouncilRequestError,
    CouncilRequestStage,
    assert_request_invariants,
    build_initial_request,
    build_judge_request,
    build_rebuttal_request,
)


BOUNDED_REQUEST_VERSION = "B4_BOUNDED_RESPONSES_REQUEST_v0_1"

_STAGE_TO_MODEL_STAGE = {
    CouncilRequestStage.BULL_INITIAL: CouncilModelStage.INITIAL,
    CouncilRequestStage.BEAR_INITIAL: CouncilModelStage.INITIAL,
    CouncilRequestStage.RED_TEAM_INITIAL: CouncilModelStage.INITIAL,
    CouncilRequestStage.REBUTTAL: CouncilModelStage.REBUTTAL,
    CouncilRequestStage.JUDGE: CouncilModelStage.JUDGE,
}


def _apply_output_bound(request: CouncilRequestEnvelope) -> CouncilRequestEnvelope:
    model_stage = _STAGE_TO_MODEL_STAGE[request.stage]
    payload = dict(request.request_payload)
    payload["max_output_tokens"] = STAGE_MAX_OUTPUT_TOKENS[model_stage]
    body = {
        "request_version": request.request_version,
        "prompt_contract_version": request.prompt_contract_version,
        "stage": request.stage.value,
        "prompt_version": request.prompt_version,
        "prompt_hash": request.prompt_hash,
        "schema_version": request.schema_version,
        "input_hash": request.input_hash,
        "model_candidate_key": request.model_candidate_key,
        "request_payload": payload,
    }
    bounded = CouncilRequestEnvelope(**body, request_hash=canonical_sha256(body))
    assert_bounded_request_invariants(bounded)
    return bounded


def assert_bounded_request_invariants(request: CouncilRequestEnvelope) -> None:
    assert_request_invariants(request)
    model_stage = _STAGE_TO_MODEL_STAGE[request.stage]
    expected = STAGE_MAX_OUTPUT_TOKENS[model_stage]
    actual = request.request_payload.get("max_output_tokens")
    if actual != expected:
        raise CouncilRequestError(
            f"B4 {model_stage.value} request requires max_output_tokens={expected}"
        )


def build_bounded_initial_request(
    *,
    stage: CouncilRequestStage,
    model_candidate: CouncilModelCandidate,
    bundle: CouncilInputBundle,
    model_run_ref: str,
    model_input: Mapping[str, Any],
    allowed_data_gap_refs: tuple[str, ...] = (),
) -> CouncilRequestEnvelope:
    return _apply_output_bound(
        build_initial_request(
            stage=stage,
            model_candidate=model_candidate,
            bundle=bundle,
            model_run_ref=model_run_ref,
            model_input=model_input,
            allowed_data_gap_refs=allowed_data_gap_refs,
        )
    )


def build_bounded_rebuttal_request(
    *,
    model_candidate: CouncilModelCandidate,
    bundle: CouncilInputBundle,
    model_input: Mapping[str, Any],
    initial_opinion_ids: tuple[str, ...],
    initial_opinion_hashes: tuple[str, ...],
    allowed_opposing_claim_ids: tuple[str, ...],
    allowed_source_material_claim_ids: tuple[str, ...],
    allowed_uncertainty_refs: tuple[str, ...] = (),
) -> CouncilRequestEnvelope:
    return _apply_output_bound(
        build_rebuttal_request(
            model_candidate=model_candidate,
            bundle=bundle,
            model_input=model_input,
            initial_opinion_ids=initial_opinion_ids,
            initial_opinion_hashes=initial_opinion_hashes,
            allowed_opposing_claim_ids=allowed_opposing_claim_ids,
            allowed_source_material_claim_ids=allowed_source_material_claim_ids,
            allowed_uncertainty_refs=allowed_uncertainty_refs,
        )
    )


def build_bounded_judge_request(
    *,
    model_candidate: CouncilModelCandidate,
    model_input: Mapping[str, Any],
    candidate_ids: tuple[str, ...],
    mandate_version: str,
    deep_comparison_id: str,
    judge_input_hash: str,
    council_policy_version: str,
    judge_policy_version: str,
    model_policy_version: str,
    model_run_ref: str,
    allowed_claim_ids: tuple[str, ...],
    allowed_dispute_refs: tuple[str, ...] = (),
    allowed_conflict_refs: tuple[str, ...] = (),
    allowed_unknown_refs: tuple[str, ...] = (),
    allowed_condition_refs: tuple[str, ...] = (),
) -> CouncilRequestEnvelope:
    return _apply_output_bound(
        build_judge_request(
            model_candidate=model_candidate,
            model_input=model_input,
            candidate_ids=candidate_ids,
            mandate_version=mandate_version,
            deep_comparison_id=deep_comparison_id,
            judge_input_hash=judge_input_hash,
            council_policy_version=council_policy_version,
            judge_policy_version=judge_policy_version,
            model_policy_version=model_policy_version,
            model_run_ref=model_run_ref,
            allowed_claim_ids=allowed_claim_ids,
            allowed_dispute_refs=allowed_dispute_refs,
            allowed_conflict_refs=allowed_conflict_refs,
            allowed_unknown_refs=allowed_unknown_refs,
            allowed_condition_refs=allowed_condition_refs,
        )
    )
