from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import MATERIAL_CLAIM_V1

from . import bounded_request as bounded_request_module
from . import request as request_module
from .model_policy import CouncilModelCandidate, CouncilModelStage, STAGE_MAX_OUTPUT_TOKENS
from .models import CouncilInputBundle, CouncilLane
from .request import CouncilRequestEnvelope


REBUTTAL_SCHEMA_REPAIR_VERSION = "B4_REBUTTAL_PROMOTION_STAGE_SCHEMA_REPAIR_v0_1"
REBUTTAL_SCHEMA_VERSION = "P-B4-PROMPTS-v0.2:REBUTTAL_BUNDLE_MODEL_DTO_REPAIR_v0.1"
REBUTTAL_PROMOTION_SEMANTICS_CONTRACT_VERSION = "B4_REBUTTAL_SCHEMA_PROMOTION_SEMANTICS_v0_1"
REBUTTAL_OPPOSING_LANE_CONTRACT_VERSION = "B4_REBUTTAL_OPPOSING_LANE_AUTHORITY_v0_1"
REBUTTAL_CLAIM_TYPE_CONTRACT_VERSION = "B4_REBUTTAL_CLAIM_TYPE_AUTHORITY_v0_1"
REBUTTAL_SCHEMA_NAME = "b4_rebuttal_bundle_v0_1_repair"

REBUTTAL_ALLOWED_CLAIM_TYPES = (
    "ARGUMENT",
    "CHALLENGE",
    "ASSUMPTION",
    "FALSIFIER",
    "INTEGRITY_FINDING",
)
JUDGE_ONLY_CLAIM_TYPES = ("DECISION_BASIS",)
REBUTTAL_LANES = (CouncilLane.BULL, CouncilLane.BEAR, CouncilLane.RED_TEAM)

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
_ITEM_FIELDS = {
    "rebuttal_item_id",
    "responding_lane",
    "opposing_finding_ids",
    "response_type",
    "response_proposed_claims",
    "remaining_uncertainty_refs",
}
_FORBIDDEN_COMPOSITION_KEYS = {
    "allOf",
    "not",
    "dependentRequired",
    "dependentSchemas",
    "if",
    "then",
    "else",
}


class RebuttalSchemaRepairError(ValueError):
    pass


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _object_with_fields(schema: Mapping[str, Any], fields: set[str], *, label: str) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for node in _walk_dicts(schema):
        props = node.get("properties")
        if isinstance(props, dict) and fields.issubset(set(props)):
            matches.append(node)
    if len(matches) != 1:
        raise RebuttalSchemaRepairError(f"Rebuttal repair requires exactly one {label} object")
    return matches[0]


def _item_union_nodes(schema: Mapping[str, Any]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for node in _walk_dicts(schema):
        branches = node.get("anyOf")
        if not isinstance(branches, list) or not branches:
            continue
        if all(
            isinstance(branch, dict)
            and isinstance(branch.get("properties"), dict)
            and _ITEM_FIELDS.issubset(set(branch["properties"]))
            for branch in branches
        ):
            matches.append(node)
    return matches


def _enum_value(value: object) -> str:
    raw = getattr(value, "value", value)
    if not isinstance(raw, str):
        raise RebuttalSchemaRepairError("claim enum value must serialize to string")
    return raw


def _candidate_model_input(model_input: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = model_input.get("candidate_model_input")
    if nested is None:
        return model_input
    if not isinstance(nested, Mapping):
        raise RebuttalSchemaRepairError("candidate_model_input must be an object")
    return nested


def _fact_source_ids(
    model_input: Mapping[str, Any],
    *,
    bundle: CouncilInputBundle,
) -> tuple[str, ...]:
    candidate_input = _candidate_model_input(model_input)
    raw_claims = candidate_input.get("material_claims")
    if not isinstance(raw_claims, (list, tuple)):
        raise RebuttalSchemaRepairError("Rebuttal repair requires frozen candidate material_claims")

    claims = []
    for raw in raw_claims:
        if not isinstance(raw, Mapping):
            raise RebuttalSchemaRepairError("Rebuttal MaterialClaim input must be an object")
        claims.append(MATERIAL_CLAIM_V1.model_validate(dict(raw)))

    observed_ids = tuple(claim.claim_id for claim in claims)
    if observed_ids != bundle.allowed_material_claim_ids:
        raise RebuttalSchemaRepairError("Rebuttal MaterialClaim order/allowlist differs from frozen bundle")
    if any(claim.candidate_id != bundle.candidate_id for claim in claims):
        raise RebuttalSchemaRepairError("Rebuttal MaterialClaim candidate differs from frozen bundle")

    return tuple(
        claim.claim_id
        for claim in claims
        if _enum_value(claim.claim_kind) == "FACT"
    )


def _set_const(prop: dict[str, Any], value: str) -> None:
    prop.clear()
    prop.update({"type": "string", "const": value})


def _set_enum(prop: dict[str, Any], values: tuple[str, ...]) -> None:
    if not values:
        raise RebuttalSchemaRepairError("schema enum must be non-empty")
    prop.clear()
    prop.update({"type": "string", "enum": list(values)})


def _restrict_string_array(
    prop: dict[str, Any],
    allowed: tuple[str, ...],
    *,
    require_nonempty: bool = False,
) -> None:
    prop.clear()
    prop["type"] = "array"
    prop["items"] = {"type": "string"}
    if allowed:
        prop["items"]["enum"] = list(allowed)
    else:
        prop["maxItems"] = 0
    if require_nonempty:
        if not allowed:
            raise RebuttalSchemaRepairError("cannot require a ref from an empty allowlist")
        prop["minItems"] = 1


def _require_nonempty(prop: dict[str, Any]) -> None:
    if prop.get("type") != "array":
        raise RebuttalSchemaRepairError("promotion provenance property must remain an array")
    if prop.get("maxItems") == 0:
        raise RebuttalSchemaRepairError("cannot require provenance from an empty frozen allowlist")
    prop["minItems"] = 1


def _claim_branch(
    original: Mapping[str, Any],
    *,
    lane: CouncilLane,
    kind: str,
    materiality: str,
    support_statuses: tuple[str, ...],
    provenance_field: str,
    fact_source_ids: tuple[str, ...],
) -> dict[str, Any]:
    branch = deepcopy(dict(original))
    props = branch["properties"]

    _set_const(props["lane"], lane.value)
    _set_enum(props["claim_type"], REBUTTAL_ALLOWED_CLAIM_TYPES)

    if kind == "FACT_RESTATEMENT":
        _set_const(props["claim_kind"], "FACT_RESTATEMENT")
        _restrict_string_array(props["source_material_claim_ids"], fact_source_ids)
    elif kind == "NON_FACT":
        _set_enum(props["claim_kind"], ("INFERENCE", "PROCESS_FINDING"))
    else:
        raise RebuttalSchemaRepairError("unknown Rebuttal claim-kind branch")

    _set_const(props["materiality"], materiality)
    if len(support_statuses) == 1:
        _set_const(props["support_status"], support_statuses[0])
    else:
        _set_enum(props["support_status"], support_statuses)

    _require_nonempty(props[provenance_field])
    return branch


def _claim_branches(
    original: Mapping[str, Any],
    *,
    lane: CouncilLane,
    fact_source_ids: tuple[str, ...],
    bundle: CouncilInputBundle,
) -> list[dict[str, Any]]:
    materiality_support = (
        ("MATERIAL", ("SUPPORTED",)),
        ("SUPPORTING", ("SUPPORTED", "CONFLICTED", "INSUFFICIENT")),
    )

    fact_provenance: list[str] = []
    if fact_source_ids:
        fact_provenance.append("source_material_claim_ids")
    if bundle.allowed_computed_value_ids:
        fact_provenance.append("computed_value_ids")

    non_fact_provenance: list[str] = []
    if bundle.allowed_material_claim_ids:
        non_fact_provenance.append("source_material_claim_ids")
    if bundle.allowed_computed_value_ids:
        non_fact_provenance.append("computed_value_ids")
    if bundle.allowed_conflict_ids:
        non_fact_provenance.append("conflict_ids")

    branches: list[dict[str, Any]] = []
    for materiality, support_statuses in materiality_support:
        for provenance_field in fact_provenance:
            branches.append(
                _claim_branch(
                    original,
                    lane=lane,
                    kind="FACT_RESTATEMENT",
                    materiality=materiality,
                    support_statuses=support_statuses,
                    provenance_field=provenance_field,
                    fact_source_ids=fact_source_ids,
                )
            )
        for provenance_field in non_fact_provenance:
            branches.append(
                _claim_branch(
                    original,
                    lane=lane,
                    kind="NON_FACT",
                    materiality=materiality,
                    support_statuses=support_statuses,
                    provenance_field=provenance_field,
                    fact_source_ids=fact_source_ids,
                )
            )
    if not branches:
        raise RebuttalSchemaRepairError("Rebuttal schema has no promotable claim branch")
    return branches


def _normalized_opposing_map(
    opposing_claim_ids_by_lane: Mapping[CouncilLane | str, tuple[str, ...]],
) -> dict[CouncilLane, tuple[str, ...]]:
    result: dict[CouncilLane, tuple[str, ...]] = {}
    for lane in REBUTTAL_LANES:
        raw = opposing_claim_ids_by_lane.get(lane)
        if raw is None:
            raw = opposing_claim_ids_by_lane.get(lane.value)
        if not isinstance(raw, tuple) or not raw or len(set(raw)) != len(raw):
            raise RebuttalSchemaRepairError(
                f"Rebuttal {lane.value} opposing finding allowlist must be a non-empty unique tuple"
            )
        if any(not isinstance(item, str) or not item or item != item.strip() for item in raw):
            raise RebuttalSchemaRepairError("Rebuttal opposing finding refs must be non-empty trimmed strings")
        result[lane] = raw
    return result


def _repair_schema(
    schema: Mapping[str, Any],
    *,
    opposing_claim_ids_by_lane: Mapping[CouncilLane | str, tuple[str, ...]],
    fact_source_ids: tuple[str, ...],
    bundle: CouncilInputBundle,
) -> dict[str, Any]:
    repaired = deepcopy(dict(schema))
    opposing = _normalized_opposing_map(opposing_claim_ids_by_lane)
    item = _object_with_fields(repaired, _ITEM_FIELDS, label="RebuttalItemDraft")
    claim = _object_with_fields(repaired, _CLAIM_FIELDS, label="ProposedCouncilClaim")
    original_item = deepcopy(item)
    original_claim = deepcopy(claim)

    item_branches: list[dict[str, Any]] = []
    for lane in REBUTTAL_LANES:
        branch = deepcopy(original_item)
        props = branch["properties"]
        _set_const(props["responding_lane"], lane.value)
        _restrict_string_array(
            props["opposing_finding_ids"],
            opposing[lane],
            require_nonempty=True,
        )
        claims_prop = props["response_proposed_claims"]
        claims_prop["items"] = {
            "anyOf": _claim_branches(
                original_claim,
                lane=lane,
                fact_source_ids=fact_source_ids,
                bundle=bundle,
            )
        }
        item_branches.append(branch)

    item.clear()
    item["anyOf"] = item_branches
    assert_rebuttal_schema_repair_v01(
        repaired,
        opposing_claim_ids_by_lane=opposing,
        fact_source_ids=fact_source_ids,
    )
    return repaired


def _required_provenance_fields(branch: Mapping[str, Any]) -> tuple[str, ...]:
    props = branch.get("properties")
    if not isinstance(props, Mapping):
        raise RebuttalSchemaRepairError("Rebuttal claim branch properties missing")
    result = []
    for field in (
        "source_material_claim_ids",
        "computed_value_ids",
        "conflict_ids",
    ):
        raw = props.get(field)
        if isinstance(raw, Mapping) and raw.get("minItems") == 1:
            result.append(field)
    return tuple(result)


def assert_rebuttal_schema_repair_v01(
    schema: Mapping[str, Any],
    *,
    opposing_claim_ids_by_lane: Mapping[CouncilLane | str, tuple[str, ...]] | None = None,
    fact_source_ids: tuple[str, ...] | None = None,
) -> None:
    for node in _walk_dicts(schema):
        forbidden = _FORBIDDEN_COMPOSITION_KEYS.intersection(node)
        if forbidden:
            raise RebuttalSchemaRepairError(
                "Rebuttal schema uses unsupported Structured Outputs composition: "
                + ", ".join(sorted(forbidden))
            )

    matches = _item_union_nodes(schema)
    if len(matches) != 1:
        raise RebuttalSchemaRepairError("Rebuttal schema must contain exactly one lane item anyOf union")
    branches = matches[0]["anyOf"]
    if not isinstance(branches, list) or len(branches) != 3:
        raise RebuttalSchemaRepairError("Rebuttal lane union must contain exactly three branches")

    expected_opposing = (
        _normalized_opposing_map(opposing_claim_ids_by_lane)
        if opposing_claim_ids_by_lane is not None
        else None
    )
    observed_lanes: set[str] = set()
    for item_branch in branches:
        props = item_branch["properties"]
        lane_prop = props.get("responding_lane")
        if not isinstance(lane_prop, Mapping):
            raise RebuttalSchemaRepairError("Rebuttal responding_lane branch missing")
        lane_value = lane_prop.get("const")
        if lane_value not in {lane.value for lane in REBUTTAL_LANES}:
            raise RebuttalSchemaRepairError("Rebuttal responding_lane discriminator invalid")
        observed_lanes.add(lane_value)
        lane = CouncilLane(lane_value)

        opposing_prop = props.get("opposing_finding_ids")
        if not isinstance(opposing_prop, Mapping) or opposing_prop.get("minItems") != 1:
            raise RebuttalSchemaRepairError("Rebuttal opposing_finding_ids must be non-empty in schema")
        if expected_opposing is not None:
            expected = {
                "type": "array",
                "items": {"type": "string", "enum": list(expected_opposing[lane])},
                "minItems": 1,
            }
            if opposing_prop != expected:
                raise RebuttalSchemaRepairError("Rebuttal opposing finding allowlist is not lane-specific")

        response_claims = props.get("response_proposed_claims")
        if not isinstance(response_claims, Mapping):
            raise RebuttalSchemaRepairError("Rebuttal response_proposed_claims schema missing")
        items = response_claims.get("items")
        if not isinstance(items, Mapping) or not isinstance(items.get("anyOf"), list):
            raise RebuttalSchemaRepairError("Rebuttal response claims must use a promotion-semantic anyOf union")
        claim_branches = items["anyOf"]
        if not claim_branches:
            raise RebuttalSchemaRepairError("Rebuttal response claim union is empty")

        observed_kinds: set[str] = set()
        observed_materialities: set[str] = set()
        for claim_branch in claim_branches:
            cprops = claim_branch.get("properties")
            if not isinstance(cprops, Mapping) or not _CLAIM_FIELDS.issubset(set(cprops)):
                raise RebuttalSchemaRepairError("Rebuttal claim branch shape invalid")
            if cprops.get("lane") != {"type": "string", "const": lane.value}:
                raise RebuttalSchemaRepairError("Rebuttal proposed claim lane is not bound to responding_lane")
            if cprops.get("claim_type") != {
                "type": "string",
                "enum": list(REBUTTAL_ALLOWED_CLAIM_TYPES),
            }:
                raise RebuttalSchemaRepairError("Rebuttal claim_type does not bind Council-stage authority")

            materiality = cprops.get("materiality")
            support = cprops.get("support_status")
            kind = cprops.get("claim_kind")
            if not isinstance(materiality, Mapping) or not isinstance(support, Mapping) or not isinstance(kind, Mapping):
                raise RebuttalSchemaRepairError("Rebuttal promotion-semantic discriminator missing")
            materiality_value = materiality.get("const")
            if materiality_value not in {"MATERIAL", "SUPPORTING"}:
                raise RebuttalSchemaRepairError("Rebuttal materiality branch invalid")
            observed_materialities.add(materiality_value)
            if materiality_value == "MATERIAL":
                if support != {"type": "string", "const": "SUPPORTED"}:
                    raise RebuttalSchemaRepairError("Rebuttal MATERIAL branch must require SUPPORTED")
            else:
                if support != {
                    "type": "string",
                    "enum": ["SUPPORTED", "CONFLICTED", "INSUFFICIENT"],
                }:
                    raise RebuttalSchemaRepairError("Rebuttal SUPPORTING branch support states drift")

            required = _required_provenance_fields(claim_branch)
            if kind.get("const") == "FACT_RESTATEMENT":
                observed_kinds.add("FACT_RESTATEMENT")
                if len(required) != 1 or required[0] not in {
                    "source_material_claim_ids",
                    "computed_value_ids",
                }:
                    raise RebuttalSchemaRepairError("Rebuttal FACT_RESTATEMENT must require source or computed provenance")
                if fact_source_ids is not None:
                    source_prop = cprops["source_material_claim_ids"]
                    expected_source: dict[str, Any] = {
                        "type": "array",
                        "items": {"type": "string"},
                    }
                    if fact_source_ids:
                        expected_source["items"]["enum"] = list(fact_source_ids)
                    else:
                        expected_source["maxItems"] = 0
                    if required[0] == "source_material_claim_ids":
                        expected_source["minItems"] = 1
                    if source_prop != expected_source:
                        raise RebuttalSchemaRepairError("Rebuttal FACT_RESTATEMENT source refs are not FACT-only")
            elif kind.get("enum") == ["INFERENCE", "PROCESS_FINDING"]:
                observed_kinds.update({"INFERENCE", "PROCESS_FINDING"})
                if len(required) != 1 or required[0] not in {
                    "source_material_claim_ids",
                    "computed_value_ids",
                    "conflict_ids",
                }:
                    raise RebuttalSchemaRepairError("Rebuttal inference/process branch must require frozen provenance")
            else:
                raise RebuttalSchemaRepairError("Rebuttal claim-kind discriminator invalid")

        if observed_materialities != {"MATERIAL", "SUPPORTING"}:
            raise RebuttalSchemaRepairError("Rebuttal claim union must cover both materiality states")
        if not {"INFERENCE", "PROCESS_FINDING"}.issubset(observed_kinds):
            raise RebuttalSchemaRepairError("Rebuttal claim union must cover both non-fact claim kinds")

    if observed_lanes != {lane.value for lane in REBUTTAL_LANES}:
        raise RebuttalSchemaRepairError("Rebuttal schema does not cover Bull/Bear/Red-Team branches")


def build_rebuttal_output_schema_v01(
    *,
    bundle: CouncilInputBundle,
    model_input: Mapping[str, Any],
    initial_opinion_ids: tuple[str, ...],
    initial_opinion_hashes: tuple[str, ...],
    opposing_claim_ids_by_lane: Mapping[CouncilLane | str, tuple[str, ...]],
    allowed_uncertainty_refs: tuple[str, ...] = (),
) -> dict[str, Any]:
    opposing = _normalized_opposing_map(opposing_claim_ids_by_lane)
    all_opposing = tuple(dict.fromkeys(ref for lane in REBUTTAL_LANES for ref in opposing[lane]))
    legacy = request_module.build_rebuttal_output_schema(
        bundle=bundle,
        initial_opinion_ids=initial_opinion_ids,
        initial_opinion_hashes=initial_opinion_hashes,
        allowed_opposing_claim_ids=all_opposing,
        allowed_source_material_claim_ids=bundle.allowed_material_claim_ids,
        allowed_uncertainty_refs=allowed_uncertainty_refs,
    )
    fact_ids = _fact_source_ids(model_input, bundle=bundle)
    return _repair_schema(
        legacy,
        opposing_claim_ids_by_lane=opposing,
        fact_source_ids=fact_ids,
        bundle=bundle,
    )


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
        "schema_version": REBUTTAL_SCHEMA_VERSION,
        "input_hash": request.input_hash,
        "model_candidate_key": request.model_candidate_key,
        "request_payload": dict(payload),
    }
    rebuilt = CouncilRequestEnvelope(**body, request_hash=canonical_sha256(body))
    request_module.assert_request_invariants(rebuilt)
    schema = rebuilt.request_payload["text"]["format"]["schema"]
    assert_rebuttal_schema_repair_v01(schema)
    return rebuilt


def build_rebuttal_request_v01(
    *,
    model_candidate: CouncilModelCandidate,
    bundle: CouncilInputBundle,
    model_input: Mapping[str, Any],
    initial_opinion_ids: tuple[str, ...],
    initial_opinion_hashes: tuple[str, ...],
    opposing_claim_ids_by_lane: Mapping[CouncilLane | str, tuple[str, ...]],
    allowed_uncertainty_refs: tuple[str, ...] = (),
) -> CouncilRequestEnvelope:
    opposing = _normalized_opposing_map(opposing_claim_ids_by_lane)
    all_opposing = tuple(dict.fromkeys(ref for lane in REBUTTAL_LANES for ref in opposing[lane]))
    legacy = request_module.build_rebuttal_request(
        model_candidate=model_candidate,
        bundle=bundle,
        model_input=model_input,
        initial_opinion_ids=initial_opinion_ids,
        initial_opinion_hashes=initial_opinion_hashes,
        allowed_opposing_claim_ids=all_opposing,
        allowed_source_material_claim_ids=bundle.allowed_material_claim_ids,
        allowed_uncertainty_refs=allowed_uncertainty_refs,
    )
    payload = deepcopy(dict(legacy.request_payload))
    fmt = payload["text"]["format"]
    fact_ids = _fact_source_ids(model_input, bundle=bundle)
    fmt["schema"] = _repair_schema(
        fmt["schema"],
        opposing_claim_ids_by_lane=opposing,
        fact_source_ids=fact_ids,
        bundle=bundle,
    )
    fmt["name"] = REBUTTAL_SCHEMA_NAME
    return _rebuild_request(legacy, payload=payload)


def build_bounded_rebuttal_request_v01(
    *,
    model_candidate: CouncilModelCandidate,
    bundle: CouncilInputBundle,
    model_input: Mapping[str, Any],
    initial_opinion_ids: tuple[str, ...],
    initial_opinion_hashes: tuple[str, ...],
    opposing_claim_ids_by_lane: Mapping[CouncilLane | str, tuple[str, ...]],
    allowed_uncertainty_refs: tuple[str, ...] = (),
) -> CouncilRequestEnvelope:
    unbounded = build_rebuttal_request_v01(
        model_candidate=model_candidate,
        bundle=bundle,
        model_input=model_input,
        initial_opinion_ids=initial_opinion_ids,
        initial_opinion_hashes=initial_opinion_hashes,
        opposing_claim_ids_by_lane=opposing_claim_ids_by_lane,
        allowed_uncertainty_refs=allowed_uncertainty_refs,
    )
    payload = deepcopy(dict(unbounded.request_payload))
    payload["max_output_tokens"] = STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.REBUTTAL]
    bounded = _rebuild_request(unbounded, payload=payload)
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
        and legacy.input_hash == repaired.input_hash
        and legacy.model_candidate_key == repaired.model_candidate_key
        and legacy.request_payload.get("model") == repaired.request_payload.get("model")
        and legacy.request_payload.get("reasoning") == repaired.request_payload.get("reasoning")
        and legacy.request_payload.get("instructions") == repaired.request_payload.get("instructions")
        and legacy.request_payload.get("input") == repaired.request_payload.get("input")
        and legacy.request_payload.get("store") == repaired.request_payload.get("store")
        and legacy.request_payload.get("tools") == repaired.request_payload.get("tools")
        and legacy.request_payload.get("parallel_tool_calls")
        == repaired.request_payload.get("parallel_tool_calls")
        and legacy.request_payload.get("truncation") == repaired.request_payload.get("truncation")
    )
