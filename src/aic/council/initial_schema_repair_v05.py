from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256

from . import bounded_request as bounded_request_module
from . import request as request_module
from .initial_schema_repair_v04 import (
    assert_initial_schema_repair_v04,
    build_initial_output_schema_v04,
    build_initial_request_v04,
    promotion_contract_branch_count,
    prompt_authority_unchanged as _prompt_authority_unchanged_v04,
)
from .model_policy import CouncilModelCandidate, CouncilModelStage, STAGE_MAX_OUTPUT_TOKENS
from .models import CouncilInputBundle, CouncilLane
from .request import CouncilRequestEnvelope, CouncilRequestStage


INITIAL_SCHEMA_REPAIR_VERSION = "B4_INITIAL_STAGE_CLAIM_TYPE_SCHEMA_REPAIR_v0_5"
INITIAL_SCHEMA_VERSION = "P-B4-PROMPTS-v0.2:INITIAL_OUTPUT_SCHEMA_REPAIR_v0.5"
PROMOTION_SEMANTICS_CONTRACT_VERSION = "B4_INITIAL_SCHEMA_PROMOTION_SEMANTICS_v0_2"
INITIAL_STAGE_CLAIM_TYPE_CONTRACT_VERSION = "B4_INITIAL_STAGE_CLAIM_TYPE_AUTHORITY_v0_1"

INITIAL_ALLOWED_CLAIM_TYPES = (
    "ARGUMENT",
    "CHALLENGE",
    "ASSUMPTION",
    "FALSIFIER",
    "INTEGRITY_FINDING",
)
JUDGE_ONLY_CLAIM_TYPES = ("DECISION_BASIS",)

_SCHEMA_NAME_BY_STAGE = {
    CouncilRequestStage.BULL_INITIAL: "b4_bull_initial_v0_5",
    CouncilRequestStage.BEAR_INITIAL: "b4_bear_initial_v0_5",
    CouncilRequestStage.RED_TEAM_INITIAL: "b4_red_team_initial_v0_5",
}

_CLAIM_FIELDS = {
    "claim_local_ref",
    "candidate_id",
    "lane",
    "claim_type",
    "claim_text",
    "source_material_claim_ids",
    "computed_value_ids",
    "conflict_ids",
    "claim_kind",
    "support_status",
    "materiality",
}


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _claim_union_nodes(schema: Mapping[str, Any]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for node in _walk_dicts(schema):
        branches = node.get("anyOf")
        if not isinstance(branches, list) or not branches:
            continue
        if all(
            isinstance(branch, dict)
            and isinstance(branch.get("properties"), dict)
            and _CLAIM_FIELDS.issubset(set(branch["properties"]))
            for branch in branches
        ):
            matches.append(node)
    return matches


def _restrict_initial_claim_types(schema: Mapping[str, Any]) -> dict[str, Any]:
    repaired = deepcopy(dict(schema))
    assert_initial_schema_repair_v04(repaired)
    matches = _claim_union_nodes(repaired)
    if len(matches) != 1:
        raise ValueError("Initial v0.5 repair requires exactly one claim anyOf union")

    for branch in matches[0]["anyOf"]:
        props = branch["properties"]
        props["claim_type"] = {
            "type": "string",
            "enum": list(INITIAL_ALLOWED_CLAIM_TYPES),
        }

    assert_initial_schema_repair_v05(repaired)
    return repaired


def assert_initial_schema_repair_v05(schema: Mapping[str, Any]) -> None:
    assert_initial_schema_repair_v04(schema)
    matches = _claim_union_nodes(schema)
    if len(matches) != 1:
        raise ValueError("Initial v0.5 schema must contain exactly one claim anyOf union")

    expected = {
        "type": "string",
        "enum": list(INITIAL_ALLOWED_CLAIM_TYPES),
    }
    for branch in matches[0]["anyOf"]:
        props = branch.get("properties")
        if not isinstance(props, Mapping):
            raise ValueError("Initial v0.5 claim branch properties missing")
        if props.get("claim_type") != expected:
            raise ValueError("Initial v0.5 claim_type branch does not bind Initial authority")


def initial_stage_claim_type_contract_satisfied(schema: Mapping[str, Any]) -> bool:
    try:
        assert_initial_schema_repair_v05(schema)
    except ValueError:
        return False
    return True


def build_initial_output_schema_v05(
    *,
    bundle: CouncilInputBundle,
    lane: CouncilLane,
    model_run_ref: str,
    model_input: Mapping[str, Any],
    allowed_data_gap_refs: tuple[str, ...],
) -> dict[str, Any]:
    legacy = build_initial_output_schema_v04(
        bundle=bundle,
        lane=lane,
        model_run_ref=model_run_ref,
        model_input=model_input,
        allowed_data_gap_refs=allowed_data_gap_refs,
    )
    return _restrict_initial_claim_types(legacy)


def _rebuild_request(
    request: CouncilRequestEnvelope,
    *,
    payload: Mapping[str, Any],
) -> CouncilRequestEnvelope:
    body = {
        "request_version": request.request_version,
        "prompt_contract_version": request.prompt_contract_version,
        "stage": request.stage.value,
        "prompt_version": request.prompt_version,
        "prompt_hash": request.prompt_hash,
        "schema_version": INITIAL_SCHEMA_VERSION,
        "input_hash": request.input_hash,
        "model_candidate_key": request.model_candidate_key,
        "request_payload": dict(payload),
    }
    rebuilt = CouncilRequestEnvelope(**body, request_hash=canonical_sha256(body))
    request_module.assert_request_invariants(rebuilt)
    schema = rebuilt.request_payload["text"]["format"]["schema"]
    assert_initial_schema_repair_v05(schema)
    return rebuilt


def build_initial_request_v05(
    *,
    stage: CouncilRequestStage,
    model_candidate: CouncilModelCandidate,
    bundle: CouncilInputBundle,
    model_run_ref: str,
    model_input: Mapping[str, Any],
    allowed_data_gap_refs: tuple[str, ...] = (),
) -> CouncilRequestEnvelope:
    if stage not in _SCHEMA_NAME_BY_STAGE:
        raise ValueError("Initial v0.5 schema repair requires Bull/Bear/Red-Team stage")
    legacy = build_initial_request_v04(
        stage=stage,
        model_candidate=model_candidate,
        bundle=bundle,
        model_run_ref=model_run_ref,
        model_input=model_input,
        allowed_data_gap_refs=allowed_data_gap_refs,
    )
    payload = deepcopy(dict(legacy.request_payload))
    fmt = payload["text"]["format"]
    fmt["schema"] = _restrict_initial_claim_types(fmt["schema"])
    fmt["name"] = _SCHEMA_NAME_BY_STAGE[stage]
    return _rebuild_request(legacy, payload=payload)


def build_bounded_initial_request_v05(
    *,
    stage: CouncilRequestStage,
    model_candidate: CouncilModelCandidate,
    bundle: CouncilInputBundle,
    model_run_ref: str,
    model_input: Mapping[str, Any],
    allowed_data_gap_refs: tuple[str, ...] = (),
) -> CouncilRequestEnvelope:
    unbounded = build_initial_request_v05(
        stage=stage,
        model_candidate=model_candidate,
        bundle=bundle,
        model_run_ref=model_run_ref,
        model_input=model_input,
        allowed_data_gap_refs=allowed_data_gap_refs,
    )
    payload = deepcopy(dict(unbounded.request_payload))
    payload["max_output_tokens"] = STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.INITIAL]
    bounded = _rebuild_request(unbounded, payload=payload)
    bounded_request_module.assert_bounded_request_invariants(bounded)
    return bounded


def prompt_authority_unchanged(
    legacy: CouncilRequestEnvelope,
    repaired: CouncilRequestEnvelope,
) -> bool:
    return _prompt_authority_unchanged_v04(legacy, repaired)
