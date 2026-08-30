from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import MATERIAL_CLAIM_V1

from . import bounded_request as bounded_request_module
from . import request as request_module
from .model_policy import CouncilModelCandidate, CouncilModelStage, STAGE_MAX_OUTPUT_TOKENS
from .models import CouncilInputBundle, CouncilLane
from .request import CouncilRequestEnvelope, CouncilRequestStage


INITIAL_SCHEMA_REPAIR_VERSION = "B4_INITIAL_PROMOTION_SEMANTICS_SCHEMA_REPAIR_v0_4"
INITIAL_SCHEMA_VERSION = "P-B4-PROMPTS-v0.2:INITIAL_OUTPUT_SCHEMA_REPAIR_v0.4"
PROMOTION_SEMANTICS_CONTRACT_VERSION = "B4_INITIAL_SCHEMA_PROMOTION_SEMANTICS_v0_1"

_SCHEMA_NAME_BY_STAGE = {
    CouncilRequestStage.BULL_INITIAL: "b4_bull_initial_v0_4",
    CouncilRequestStage.BEAR_INITIAL: "b4_bear_initial_v0_4",
    CouncilRequestStage.RED_TEAM_INITIAL: "b4_red_team_initial_v0_4",
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

_FORBIDDEN_COMPOSITION_KEYS = {
    "allOf",
    "not",
    "dependentRequired",
    "dependentSchemas",
    "if",
    "then",
    "else",
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
        raise ValueError("Initial v0.4 repair requires exactly one ProposedCouncilClaim object")
    return matches[0]


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


def _enum_value(value: object) -> str:
    raw = getattr(value, "value", value)
    if not isinstance(raw, str):
        raise ValueError("claim enum value must serialize to string")
    return raw


def _fact_source_ids(
    model_input: Mapping[str, Any],
    *,
    bundle: CouncilInputBundle,
) -> tuple[str, ...]:
    raw_claims = model_input.get("material_claims")
    if not isinstance(raw_claims, (list, tuple)):
        raise ValueError("Initial v0.4 repair requires frozen model_input.material_claims")

    claims = []
    for raw in raw_claims:
        if not isinstance(raw, Mapping):
            raise ValueError("Initial v0.4 model input MaterialClaim must be object")
        claims.append(MATERIAL_CLAIM_V1.model_validate(dict(raw)))

    observed_ids = tuple(claim.claim_id for claim in claims)
    if observed_ids != bundle.allowed_material_claim_ids:
        raise ValueError("Initial v0.4 MaterialClaim order/allowlist differs from frozen bundle")
    if any(claim.candidate_id != bundle.candidate_id for claim in claims):
        raise ValueError("Initial v0.4 MaterialClaim candidate differs from frozen bundle")

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
        raise ValueError("schema enum must be non-empty")
    prop.clear()
    prop.update({"type": "string", "enum": list(values)})


def _restrict_string_array(prop: dict[str, Any], allowed: tuple[str, ...]) -> None:
    prop.clear()
    prop["type"] = "array"
    prop["items"] = {"type": "string"}
    if allowed:
        prop["items"]["enum"] = list(allowed)
    else:
        prop["maxItems"] = 0


def _require_nonempty(prop: dict[str, Any]) -> None:
    if prop.get("type") != "array":
        raise ValueError("promotion provenance property must remain an array")
    if prop.get("maxItems") == 0:
        raise ValueError("cannot require provenance from an empty frozen allowlist")
    prop["minItems"] = 1


def _branch(
    original: Mapping[str, Any],
    *,
    kind: str,
    materiality: str,
    support_statuses: tuple[str, ...],
    provenance_field: str,
    fact_source_ids: tuple[str, ...],
) -> dict[str, Any]:
    branch = deepcopy(dict(original))
    props = branch["properties"]

    if kind == "FACT_RESTATEMENT":
        _set_const(props["claim_kind"], "FACT_RESTATEMENT")
        _restrict_string_array(props["source_material_claim_ids"], fact_source_ids)
    elif kind == "NON_FACT":
        _set_enum(props["claim_kind"], ("INFERENCE", "PROCESS_FINDING"))
    else:
        raise ValueError("unknown Initial v0.4 claim-kind branch")

    _set_const(props["materiality"], materiality)
    if len(support_statuses) == 1:
        _set_const(props["support_status"], support_statuses[0])
    else:
        _set_enum(props["support_status"], support_statuses)

    _require_nonempty(props[provenance_field])
    return branch


def _repair_union(
    schema: Mapping[str, Any],
    *,
    fact_source_ids: tuple[str, ...],
    bundle: CouncilInputBundle,
) -> dict[str, Any]:
    repaired = deepcopy(dict(schema))
    claim = _claim_object(repaired)
    original = deepcopy(claim)

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
                _branch(
                    original,
                    kind="FACT_RESTATEMENT",
                    materiality=materiality,
                    support_statuses=support_statuses,
                    provenance_field=provenance_field,
                    fact_source_ids=fact_source_ids,
                )
            )
        for provenance_field in non_fact_provenance:
            branches.append(
                _branch(
                    original,
                    kind="NON_FACT",
                    materiality=materiality,
                    support_statuses=support_statuses,
                    provenance_field=provenance_field,
                    fact_source_ids=fact_source_ids,
                )
            )

    if not branches:
        raise ValueError("Initial v0.4 schema has no promotable provenance branch")

    claim.clear()
    claim["anyOf"] = branches
    assert_initial_schema_repair_v04(
        repaired,
        fact_source_ids=fact_source_ids,
    )
    return repaired


def _required_provenance_fields(branch: Mapping[str, Any]) -> tuple[str, ...]:
    props = branch.get("properties")
    if not isinstance(props, Mapping):
        raise ValueError("claim branch properties missing")
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


def assert_initial_schema_repair_v04(
    schema: Mapping[str, Any],
    *,
    fact_source_ids: tuple[str, ...] | None = None,
) -> None:
    for node in _walk_dicts(schema):
        forbidden = _FORBIDDEN_COMPOSITION_KEYS.intersection(node)
        if forbidden:
            raise ValueError(
                "Initial v0.4 schema uses unsupported Structured Outputs composition: "
                + ", ".join(sorted(forbidden))
            )

    matches = _claim_union_nodes(schema)
    if len(matches) != 1:
        raise ValueError("Initial v0.4 schema must contain exactly one claim anyOf union")

    branches = matches[0]["anyOf"]
    if not isinstance(branches, list) or not branches:
        raise ValueError("Initial v0.4 claim union is empty")

    observed_kinds: set[str] = set()
    observed_materialities: set[str] = set()
    for branch in branches:
        props = branch["properties"]
        materiality = props.get("materiality")
        support = props.get("support_status")
        kind = props.get("claim_kind")
        if not isinstance(materiality, Mapping) or not isinstance(support, Mapping):
            raise ValueError("Initial v0.4 materiality/support branch missing")

        materiality_value = materiality.get("const")
        if materiality_value not in {"MATERIAL", "SUPPORTING"}:
            raise ValueError("Initial v0.4 materiality branch invalid")
        observed_materialities.add(materiality_value)

        if materiality_value == "MATERIAL":
            if support != {"type": "string", "const": "SUPPORTED"}:
                raise ValueError("Initial v0.4 MATERIAL branch must require SUPPORTED")
        else:
            if support != {
                "type": "string",
                "enum": ["SUPPORTED", "CONFLICTED", "INSUFFICIENT"],
            }:
                raise ValueError("Initial v0.4 SUPPORTING branch must preserve bounded support states")

        if not isinstance(kind, Mapping):
            raise ValueError("Initial v0.4 claim-kind branch missing")
        if kind.get("const") == "FACT_RESTATEMENT":
            observed_kinds.add("FACT_RESTATEMENT")
            required = _required_provenance_fields(branch)
            if len(required) != 1 or required[0] not in {
                "source_material_claim_ids",
                "computed_value_ids",
            }:
                raise ValueError("FACT_RESTATEMENT branch must require source or computed provenance")
            source_prop = props["source_material_claim_ids"]
            if fact_source_ids is not None:
                expected = {"type": "array", "items": {"type": "string"}}
                if fact_source_ids:
                    expected["items"]["enum"] = list(fact_source_ids)
                else:
                    expected["maxItems"] = 0
                if required[0] == "source_material_claim_ids":
                    expected["minItems"] = 1
                if source_prop != expected:
                    raise ValueError("FACT_RESTATEMENT source refs are not restricted to frozen FACT claims")
        elif kind.get("enum") == ["INFERENCE", "PROCESS_FINDING"]:
            observed_kinds.update({"INFERENCE", "PROCESS_FINDING"})
            required = _required_provenance_fields(branch)
            if len(required) != 1 or required[0] not in {
                "source_material_claim_ids",
                "computed_value_ids",
                "conflict_ids",
            }:
                raise ValueError("non-fact branch must require frozen provenance")
        else:
            raise ValueError("Initial v0.4 claim-kind discriminator invalid")

    if observed_materialities != {"MATERIAL", "SUPPORTING"}:
        raise ValueError("Initial v0.4 schema does not cover both materiality states")
    if not {"INFERENCE", "PROCESS_FINDING"}.issubset(observed_kinds):
        raise ValueError("Initial v0.4 schema does not cover both non-fact claim kinds")


def promotion_contract_branch_count(schema: Mapping[str, Any]) -> int:
    matches = _claim_union_nodes(schema)
    if len(matches) != 1:
        raise ValueError("Initial v0.4 schema claim union missing")
    return len(matches[0]["anyOf"])


def build_initial_output_schema_v04(
    *,
    bundle: CouncilInputBundle,
    lane: CouncilLane,
    model_run_ref: str,
    model_input: Mapping[str, Any],
    allowed_data_gap_refs: tuple[str, ...],
) -> dict[str, Any]:
    legacy = request_module.build_initial_output_schema(
        bundle=bundle,
        lane=lane,
        model_run_ref=model_run_ref,
        allowed_data_gap_refs=allowed_data_gap_refs,
    )
    fact_ids = _fact_source_ids(model_input, bundle=bundle)
    return _repair_union(
        legacy,
        fact_source_ids=fact_ids,
        bundle=bundle,
    )


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
    assert_initial_schema_repair_v04(schema)
    return rebuilt


def build_initial_request_v04(
    *,
    stage: CouncilRequestStage,
    model_candidate: CouncilModelCandidate,
    bundle: CouncilInputBundle,
    model_run_ref: str,
    model_input: Mapping[str, Any],
    allowed_data_gap_refs: tuple[str, ...] = (),
) -> CouncilRequestEnvelope:
    if stage not in _SCHEMA_NAME_BY_STAGE:
        raise ValueError("Initial v0.4 schema repair requires Bull/Bear/Red-Team stage")
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
    fact_ids = _fact_source_ids(model_input, bundle=bundle)
    fmt["schema"] = _repair_union(
        fmt["schema"],
        fact_source_ids=fact_ids,
        bundle=bundle,
    )
    fmt["name"] = _SCHEMA_NAME_BY_STAGE[stage]
    rebuilt = _rebuild_request(
        legacy,
        payload=payload,
        schema_version=INITIAL_SCHEMA_VERSION,
    )
    assert_initial_schema_repair_v04(
        rebuilt.request_payload["text"]["format"]["schema"],
        fact_source_ids=fact_ids,
    )
    return rebuilt


def build_bounded_initial_request_v04(
    *,
    stage: CouncilRequestStage,
    model_candidate: CouncilModelCandidate,
    bundle: CouncilInputBundle,
    model_run_ref: str,
    model_input: Mapping[str, Any],
    allowed_data_gap_refs: tuple[str, ...] = (),
) -> CouncilRequestEnvelope:
    unbounded = build_initial_request_v04(
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
    fact_ids = _fact_source_ids(model_input, bundle=bundle)
    assert_initial_schema_repair_v04(
        bounded.request_payload["text"]["format"]["schema"],
        fact_source_ids=fact_ids,
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
