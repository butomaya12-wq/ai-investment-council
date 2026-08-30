from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256

from . import bounded_request as bounded_request_module
from . import request as request_module
from .model_policy import CouncilModelCandidate, CouncilModelStage, STAGE_MAX_OUTPUT_TOKENS
from .models import CouncilInputBundle, CouncilLane
from .request import CouncilRequestEnvelope, CouncilRequestStage


INITIAL_SCHEMA_REPAIR_VERSION = "B4_INITIAL_PROMOTABLE_SUPPORT_SCHEMA_REPAIR_v0_3"
INITIAL_SCHEMA_VERSION = "P-B4-PROMPTS-v0.2:INITIAL_OUTPUT_SCHEMA_REPAIR_v0.3"

_SCHEMA_NAME_BY_STAGE = {
    CouncilRequestStage.BULL_INITIAL: "b4_bull_initial_v0_3",
    CouncilRequestStage.BEAR_INITIAL: "b4_bear_initial_v0_3",
    CouncilRequestStage.RED_TEAM_INITIAL: "b4_red_team_initial_v0_3",
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


def _claim_object(schema: Mapping[str, Any]) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for node in _walk_dicts(schema):
        properties = node.get("properties")
        if isinstance(properties, dict) and _CLAIM_FIELDS.issubset(set(properties)):
            matches.append(node)
    if len(matches) != 1:
        raise ValueError("Initial schema repair requires exactly one ProposedCouncilClaim object")
    return matches[0]


def _repair_union(schema: Mapping[str, Any]) -> dict[str, Any]:
    repaired = deepcopy(dict(schema))
    claim = _claim_object(repaired)
    original = deepcopy(claim)

    material = deepcopy(original)
    material_props = material["properties"]
    material_props["materiality"] = {"type": "string", "const": "MATERIAL"}
    material_props["support_status"] = {"type": "string", "const": "SUPPORTED"}

    supporting = deepcopy(original)
    supporting_props = supporting["properties"]
    supporting_props["materiality"] = {"type": "string", "const": "SUPPORTING"}
    supporting_props["support_status"] = {
        "type": "string",
        "enum": ["SUPPORTED", "CONFLICTED", "INSUFFICIENT"],
    }

    claim.clear()
    claim["anyOf"] = [material, supporting]
    assert_initial_schema_repair(repaired)
    return repaired


def _repair_union_nodes(schema: Mapping[str, Any]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for node in _walk_dicts(schema):
        branches = node.get("anyOf")
        if not isinstance(branches, list) or len(branches) != 2:
            continue
        if all(
            isinstance(branch, dict)
            and isinstance(branch.get("properties"), dict)
            and _CLAIM_FIELDS.issubset(set(branch["properties"]))
            for branch in branches
        ):
            matches.append(node)
    return matches


def assert_initial_schema_repair(schema: Mapping[str, Any]) -> None:
    matches = _repair_union_nodes(schema)
    if len(matches) != 1:
        raise ValueError("Initial schema must contain exactly one promotable-support claim union")
    branches = matches[0]["anyOf"]
    branch_by_materiality: dict[str, Mapping[str, Any]] = {}
    for branch in branches:
        properties = branch["properties"]
        materiality = properties.get("materiality")
        if not isinstance(materiality, Mapping):
            raise ValueError("Initial schema repair materiality branch missing")
        value = materiality.get("const")
        if not isinstance(value, str):
            raise ValueError("Initial schema repair materiality branch must use const")
        branch_by_materiality[value] = branch

    if set(branch_by_materiality) != {"MATERIAL", "SUPPORTING"}:
        raise ValueError("Initial schema repair must cover MATERIAL and SUPPORTING exactly")

    material_support = branch_by_materiality["MATERIAL"]["properties"]["support_status"]
    if material_support != {"type": "string", "const": "SUPPORTED"}:
        raise ValueError("MATERIAL claims must be schema-constrained to SUPPORTED")

    supporting_support = branch_by_materiality["SUPPORTING"]["properties"]["support_status"]
    if supporting_support != {
        "type": "string",
        "enum": ["SUPPORTED", "CONFLICTED", "INSUFFICIENT"],
    }:
        raise ValueError("SUPPORTING claims must preserve all bounded support states")


def build_initial_output_schema_v03(
    *,
    bundle: CouncilInputBundle,
    lane: CouncilLane,
    model_run_ref: str,
    allowed_data_gap_refs: tuple[str, ...],
) -> dict[str, Any]:
    legacy = request_module.build_initial_output_schema(
        bundle=bundle,
        lane=lane,
        model_run_ref=model_run_ref,
        allowed_data_gap_refs=allowed_data_gap_refs,
    )
    return _repair_union(legacy)


def _rebuild_request(
    request: CouncilRequestEnvelope,
    *,
    payload: Mapping[str, Any],
    schema_version: str,
) -> CouncilRequestEnvelope:
    body = {
        "request_version": request.request_version,
        "prompt_contract_version": request.prompt_contract_version,
        "stage": request.stage.value,
        "prompt_version": request.prompt_version,
        "prompt_hash": request.prompt_hash,
        "schema_version": schema_version,
        "input_hash": request.input_hash,
        "model_candidate_key": request.model_candidate_key,
        "request_payload": dict(payload),
    }
    rebuilt = CouncilRequestEnvelope(**body, request_hash=canonical_sha256(body))
    request_module.assert_request_invariants(rebuilt)
    schema = rebuilt.request_payload["text"]["format"]["schema"]
    assert_initial_schema_repair(schema)
    return rebuilt


def build_initial_request_v03(
    *,
    stage: CouncilRequestStage,
    model_candidate: CouncilModelCandidate,
    bundle: CouncilInputBundle,
    model_run_ref: str,
    model_input: Mapping[str, Any],
    allowed_data_gap_refs: tuple[str, ...] = (),
) -> CouncilRequestEnvelope:
    if stage not in _SCHEMA_NAME_BY_STAGE:
        raise ValueError("Initial schema repair requires Bull/Bear/Red-Team stage")
    legacy = request_module.build_initial_request(
        stage=stage,
        model_candidate=model_candidate,
        bundle=bundle,
        model_run_ref=model_run_ref,
        model_input=model_input,
        allowed_data_gap_refs=allowed_data_gap_refs,
    )
    payload = deepcopy(dict(legacy.request_payload))
    fmt = payload["text"]["format"]
    fmt["schema"] = _repair_union(fmt["schema"])
    fmt["name"] = _SCHEMA_NAME_BY_STAGE[stage]
    return _rebuild_request(
        legacy,
        payload=payload,
        schema_version=INITIAL_SCHEMA_VERSION,
    )


def build_bounded_initial_request_v03(
    *,
    stage: CouncilRequestStage,
    model_candidate: CouncilModelCandidate,
    bundle: CouncilInputBundle,
    model_run_ref: str,
    model_input: Mapping[str, Any],
    allowed_data_gap_refs: tuple[str, ...] = (),
) -> CouncilRequestEnvelope:
    unbounded = build_initial_request_v03(
        stage=stage,
        model_candidate=model_candidate,
        bundle=bundle,
        model_run_ref=model_run_ref,
        model_input=model_input,
        allowed_data_gap_refs=allowed_data_gap_refs,
    )
    payload = deepcopy(dict(unbounded.request_payload))
    payload["max_output_tokens"] = STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.INITIAL]
    bounded = _rebuild_request(
        unbounded,
        payload=payload,
        schema_version=INITIAL_SCHEMA_VERSION,
    )
    bounded_request_module.assert_bounded_request_invariants(bounded)
    return bounded


def prompt_authority_unchanged(
    legacy: CouncilRequestEnvelope,
    repaired: CouncilRequestEnvelope,
) -> bool:
    return (
        legacy.prompt_contract_version == repaired.prompt_contract_version
        and legacy.prompt_version == repaired.prompt_version
        and legacy.prompt_hash == repaired.prompt_hash
        and legacy.request_payload.get("instructions")
        == repaired.request_payload.get("instructions")
        and legacy.input_hash == repaired.input_hash
    )
