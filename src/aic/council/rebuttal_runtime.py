from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from aic.domain.canonical import canonical_sha256

from .initial_runtime import request_body_utf8_bytes
from .model_policy import REBUTTAL_MODEL_LADDER, CouncilModelCandidate
from .models import CouncilInputFreezeArtifact, CouncilLane
from .rebuttal_model_selection_v02 import verify_rebuttal_selected_model_authority_v02
from .rebuttal_runtime_preflight import (
    EXPECTED_SELECTED,
    EXPECTED_SELECTION_HASH,
    verify_rebuttal_runtime_request_preflight,
)
from .rebuttal_schema_repair_v01 import build_bounded_rebuttal_request_v01
from .request import CouncilRequestEnvelope


REBUTTAL_RUNTIME_VERSION = "B4_REBUTTAL_PRODUCTION_RUNTIME_v0_1"
EXPECTED_PRODUCTION_CALLS = 3


class RebuttalRuntimeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RebuttalRuntimePlanItem:
    dispatch_index: int
    candidate_id: str
    context_hash: str
    bundle: Any
    model_input: Mapping[str, Any]
    initial_opinion_ids: tuple[str, ...]
    initial_opinion_hashes: tuple[str, ...]
    opposing_claim_ids_by_lane: Mapping[CouncilLane, tuple[str, ...]]
    allowed_uncertainty_refs: tuple[str, ...]
    required_unknown_refs: tuple[str, ...]
    request: CouncilRequestEnvelope
    request_body_utf8_bytes: int


def _selected_model() -> CouncilModelCandidate:
    matches = [candidate for candidate in REBUTTAL_MODEL_LADDER if candidate.candidate_key == "R3"]
    if len(matches) != 1:
        raise RebuttalRuntimeError("frozen Rebuttal ladder does not contain unique R3")
    candidate = matches[0]
    if {
        "candidate_key": candidate.candidate_key,
        "model": candidate.model,
        "reasoning_effort": candidate.reasoning_effort,
        "ladder_position": candidate.ladder_position,
    } != EXPECTED_SELECTED:
        raise RebuttalRuntimeError("frozen R3 configuration drift")
    return candidate


def _tuple_map(raw: Mapping[str, Any]) -> dict[CouncilLane, tuple[str, ...]]:
    result: dict[CouncilLane, tuple[str, ...]] = {}
    for lane in (CouncilLane.BULL, CouncilLane.BEAR, CouncilLane.RED_TEAM):
        values = raw.get(lane.value)
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise RebuttalRuntimeError("Rebuttal opposing-claim map malformed")
        result[lane] = tuple(values)
    return result


def _variant_map(preflight: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    variants = preflight.get("selected_request_variants")
    if not isinstance(variants, list) or len(variants) != EXPECTED_PRODUCTION_CALLS:
        raise RebuttalRuntimeError("runtime preflight must contain exactly three selected requests")
    result: dict[str, Mapping[str, Any]] = {}
    for raw in variants:
        if not isinstance(raw, Mapping):
            raise RebuttalRuntimeError("runtime selected request record malformed")
        candidate_id = raw.get("candidate")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise RebuttalRuntimeError("runtime selected request candidate missing")
        if candidate_id in result:
            raise RebuttalRuntimeError("runtime selected request candidate duplicated")
        result[candidate_id] = raw
    return result


def build_rebuttal_runtime_plan(
    *,
    freeze: CouncilInputFreezeArtifact,
    contexts: Sequence[Mapping[str, Any]],
    runtime_preflight: Mapping[str, Any],
    selection_authority: Mapping[str, Any],
) -> tuple[RebuttalRuntimePlanItem, ...]:
    verify_rebuttal_runtime_request_preflight(runtime_preflight)
    selection_hash = verify_rebuttal_selected_model_authority_v02(selection_authority)
    if selection_hash != EXPECTED_SELECTION_HASH:
        raise RebuttalRuntimeError("runtime selection authority hash drift")
    if selection_authority.get("selected_candidate") != EXPECTED_SELECTED:
        raise RebuttalRuntimeError("runtime selection authority is not frozen R3")
    if runtime_preflight.get("selected_model_authority_selection_hash") != selection_hash:
        raise RebuttalRuntimeError("runtime preflight does not bind selection authority")
    if runtime_preflight.get("b4_input_freeze_artifact_hash") not in (None, freeze.artifact_hash):
        raise RebuttalRuntimeError("runtime preflight B4 input freeze drift")
    if tuple(runtime_preflight.get("candidate_order", ())) != freeze.candidate_order:
        raise RebuttalRuntimeError("runtime candidate order differs from input freeze")
    if len(contexts) != EXPECTED_PRODUCTION_CALLS:
        raise RebuttalRuntimeError("runtime requires exactly three frozen candidate contexts")
    if tuple(context.get("candidate_id") for context in contexts) != freeze.candidate_order:
        raise RebuttalRuntimeError("runtime context candidate order drift")

    bundle_by_candidate = {bundle.candidate_id: bundle for bundle in freeze.bundles}
    variants = _variant_map(runtime_preflight)
    selected_model = _selected_model()
    plan: list[RebuttalRuntimePlanItem] = []

    for index, context in enumerate(contexts, start=1):
        candidate_id = context.get("candidate_id")
        if not isinstance(candidate_id, str):
            raise RebuttalRuntimeError("runtime context candidate missing")
        context_hash = context.get("context_hash")
        if not isinstance(context_hash, str) or context_hash != canonical_sha256(
            context, exclude_fields=("context_hash",)
        ):
            raise RebuttalRuntimeError(f"{candidate_id} runtime context hash mismatch")
        bundle = bundle_by_candidate.get(candidate_id)
        if bundle is None:
            raise RebuttalRuntimeError("runtime context has no frozen B4 input bundle")
        model_input = context.get("model_input")
        opposing_raw = context.get("opposing_claim_ids_by_lane")
        if not isinstance(model_input, Mapping) or not isinstance(opposing_raw, Mapping):
            raise RebuttalRuntimeError("runtime context model input/opposing map missing")
        initial_ids = tuple(context.get("initial_opinion_ids", ()))
        initial_hashes = tuple(context.get("initial_opinion_hashes", ()))
        uncertainties = tuple(context.get("allowed_uncertainty_refs", ()))
        required_unknowns = tuple(context.get("required_unknown_refs", ()))
        if len(initial_ids) != 3 or len(initial_hashes) != 3:
            raise RebuttalRuntimeError("runtime context requires three frozen Initial opinions")
        if not all(isinstance(value, str) for value in (*initial_ids, *initial_hashes, *uncertainties, *required_unknowns)):
            raise RebuttalRuntimeError("runtime context contains malformed string refs")
        opposing = _tuple_map(opposing_raw)
        request = build_bounded_rebuttal_request_v01(
            model_candidate=selected_model,
            bundle=bundle,
            model_input=model_input,
            initial_opinion_ids=initial_ids,
            initial_opinion_hashes=initial_hashes,
            opposing_claim_ids_by_lane=opposing,
            allowed_uncertainty_refs=uncertainties,
        )
        byte_count = request_body_utf8_bytes(request.request_payload)
        frozen = variants.get(candidate_id)
        if frozen is None:
            raise RebuttalRuntimeError(f"{candidate_id} selected runtime request missing")
        if request.request_hash != frozen.get("request_hash"):
            raise RebuttalRuntimeError(
                f"{candidate_id} reconstructed request hash differs from zero-call preflight"
            )
        if byte_count != frozen.get("request_body_utf8_bytes"):
            raise RebuttalRuntimeError(
                f"{candidate_id} reconstructed request byte count differs from zero-call preflight"
            )
        if request.request_payload.get("model") != selected_model.model:
            raise RebuttalRuntimeError("runtime request model differs from R3 authority")
        reasoning = request.request_payload.get("reasoning")
        if not isinstance(reasoning, Mapping) or reasoning.get("effort") != selected_model.reasoning_effort:
            raise RebuttalRuntimeError("runtime request reasoning effort differs from R3 authority")
        plan.append(
            RebuttalRuntimePlanItem(
                dispatch_index=index,
                candidate_id=candidate_id,
                context_hash=context_hash,
                bundle=bundle,
                model_input=model_input,
                initial_opinion_ids=initial_ids,
                initial_opinion_hashes=initial_hashes,
                opposing_claim_ids_by_lane=opposing,
                allowed_uncertainty_refs=uncertainties,
                required_unknown_refs=required_unknowns,
                request=request,
                request_body_utf8_bytes=byte_count,
            )
        )

    if len(plan) != EXPECTED_PRODUCTION_CALLS:
        raise RebuttalRuntimeError("runtime plan must contain exactly three calls")
    if tuple(item.candidate_id for item in plan) != freeze.candidate_order:
        raise RebuttalRuntimeError("runtime plan order drift")
    return tuple(plan)
