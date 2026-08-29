from __future__ import annotations

import json
from enum import StrEnum
from typing import Any, Mapping, TypeVar

from pydantic import model_validator

from aic.domain.canonical import canonical_sha256
from aic.research.runtime import ResponsesProtocolError, parse_responses_payload

from .model_policy import (
    API_INVARIANTS,
    INITIAL_MODEL_LADDER,
    JUDGE_MODEL_LADDER,
    MODEL_LADDERS,
    REBUTTAL_MODEL_LADDER,
    CouncilModelCandidate,
    CouncilModelStage,
)
from .models import B4Model, CouncilInputBundle, CouncilLane
from .prompts import (
    BEAR_INITIAL_INSTRUCTIONS,
    BEAR_INITIAL_PROMPT_VERSION,
    BULL_INITIAL_INSTRUCTIONS,
    BULL_INITIAL_PROMPT_VERSION,
    JUDGE_INSTRUCTIONS,
    JUDGE_PROMPT_VERSION,
    PROMPT_CONTRACT_VERSION,
    RED_TEAM_INITIAL_INSTRUCTIONS,
    RED_TEAM_INITIAL_PROMPT_VERSION,
    REBUTTAL_INSTRUCTIONS,
    REBUTTAL_PROMPT_VERSION,
    bear_initial_prompt_hash,
    bull_initial_prompt_hash,
    judge_prompt_hash,
    red_team_initial_prompt_hash,
    rebuttal_prompt_hash,
)
from .proposal import (
    InitialCouncilOpinionProposal,
    JudgeDecisionProposalDraft,
    RebuttalBundleDraft,
)


REQUEST_VERSION = "B4_RESPONSES_REQUEST_v0_1"
INITIAL_SCHEMA_VERSION = "P-B4-PROMPTS-v0.2:INITIAL_OUTPUT_SCHEMA"
REBUTTAL_SCHEMA_VERSION = "P-B4-PROMPTS-v0.2:REBUTTAL_BUNDLE_MODEL_DTO"
JUDGE_SCHEMA_VERSION = "P-B4-PROMPTS-v0.2:JUDGE_DECISION_PROPOSAL_MODEL_DTO"
UNTRUSTED_COUNCIL_DATA_MARKER = "UNTRUSTED_COUNCIL_DATA"


class CouncilRequestStage(StrEnum):
    BULL_INITIAL = "BULL_INITIAL"
    BEAR_INITIAL = "BEAR_INITIAL"
    RED_TEAM_INITIAL = "RED_TEAM_INITIAL"
    REBUTTAL = "REBUTTAL"
    JUDGE = "JUDGE"


_INITIAL_STAGE_TO_LANE = {
    CouncilRequestStage.BULL_INITIAL: CouncilLane.BULL,
    CouncilRequestStage.BEAR_INITIAL: CouncilLane.BEAR,
    CouncilRequestStage.RED_TEAM_INITIAL: CouncilLane.RED_TEAM,
}
_STAGE_TO_MODEL_STAGE = {
    CouncilRequestStage.BULL_INITIAL: CouncilModelStage.INITIAL,
    CouncilRequestStage.BEAR_INITIAL: CouncilModelStage.INITIAL,
    CouncilRequestStage.RED_TEAM_INITIAL: CouncilModelStage.INITIAL,
    CouncilRequestStage.REBUTTAL: CouncilModelStage.REBUTTAL,
    CouncilRequestStage.JUDGE: CouncilModelStage.JUDGE,
}
_STAGE_PROMPT = {
    CouncilRequestStage.BULL_INITIAL: (
        BULL_INITIAL_PROMPT_VERSION,
        bull_initial_prompt_hash,
        BULL_INITIAL_INSTRUCTIONS,
        INITIAL_SCHEMA_VERSION,
        "b4_bull_initial_v0_2",
    ),
    CouncilRequestStage.BEAR_INITIAL: (
        BEAR_INITIAL_PROMPT_VERSION,
        bear_initial_prompt_hash,
        BEAR_INITIAL_INSTRUCTIONS,
        INITIAL_SCHEMA_VERSION,
        "b4_bear_initial_v0_2",
    ),
    CouncilRequestStage.RED_TEAM_INITIAL: (
        RED_TEAM_INITIAL_PROMPT_VERSION,
        red_team_initial_prompt_hash,
        RED_TEAM_INITIAL_INSTRUCTIONS,
        INITIAL_SCHEMA_VERSION,
        "b4_red_team_initial_v0_2",
    ),
    CouncilRequestStage.REBUTTAL: (
        REBUTTAL_PROMPT_VERSION,
        rebuttal_prompt_hash,
        REBUTTAL_INSTRUCTIONS,
        REBUTTAL_SCHEMA_VERSION,
        "b4_rebuttal_v0_2",
    ),
    CouncilRequestStage.JUDGE: (
        JUDGE_PROMPT_VERSION,
        judge_prompt_hash,
        JUDGE_INSTRUCTIONS,
        JUDGE_SCHEMA_VERSION,
        "b4_judge_decision_proposal_v0_2",
    ),
}

_FORBIDDEN_CREDENTIAL_KEYS = (
    "api_key",
    "apikey",
    "secret",
    "password",
    "authorization",
    "bearer",
    "credential",
    "apca_api",
    "openai_api",
)
_FORBIDDEN_MODEL_PROPERTIES = {
    "claim_id",
    "council_claim_id",
    "decision_lifecycle_policy_ref",
    "decision_ttl",
    "duration",
    "next_review_trigger",
    "trigger_at",
    "trigger_at_utc",
    "risk_policy_ref",
    "sizing_policy_ref",
    "risk_result",
    "approval",
    "approval_envelope",
    "order",
    "broker_command",
}


class CouncilRequestError(ValueError):
    pass


class CouncilRequestEnvelope(B4Model):
    request_version: str
    prompt_contract_version: str
    stage: CouncilRequestStage
    prompt_version: str
    prompt_hash: str
    schema_version: str
    input_hash: str
    model_candidate_key: str
    request_payload: Mapping[str, Any]
    request_hash: str

    @model_validator(mode="after")
    def _hash(self):
        expected = canonical_sha256(
            {
                "request_version": self.request_version,
                "prompt_contract_version": self.prompt_contract_version,
                "stage": self.stage.value,
                "prompt_version": self.prompt_version,
                "prompt_hash": self.prompt_hash,
                "schema_version": self.schema_version,
                "input_hash": self.input_hash,
                "model_candidate_key": self.model_candidate_key,
                "request_payload": self.request_payload,
            }
        )
        if self.request_hash != expected:
            raise ValueError("request_hash does not bind B4 Responses request")
        return self


def _openai_strict_schema(node: Any) -> Any:
    if isinstance(node, list):
        return [_openai_strict_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in {"default", "title"}:
            continue
        out[key] = _openai_strict_schema(value)
    if out.get("type") == "object":
        properties = out.get("properties")
        if isinstance(properties, dict):
            out["required"] = list(properties.keys())
            out["additionalProperties"] = False
    return out


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _object_with_properties(schema: Mapping[str, Any], required_fields: set[str]) -> dict[str, Any]:
    matches = []
    for node in _walk_dicts(schema):
        properties = node.get("properties")
        if isinstance(properties, dict) and required_fields.issubset(set(properties)):
            matches.append(node)
    if len(matches) != 1:
        raise CouncilRequestError(
            "strict schema object lookup is ambiguous for fields: " + ",".join(sorted(required_fields))
        )
    return matches[0]


def _set_const(prop: dict[str, Any], value: str) -> None:
    prop.clear()
    prop.update({"type": "string", "const": value})


def _set_nullable_candidate(prop: dict[str, Any], candidates: tuple[str, ...]) -> None:
    prop.clear()
    prop.update(
        {
            "anyOf": [
                {"type": "string", "enum": list(candidates)},
                {"type": "null"},
            ]
        }
    )


def _restrict_string_array(prop: dict[str, Any], allowed: tuple[str, ...], *, exact_count: int | None = None) -> None:
    prop.clear()
    prop["type"] = "array"
    prop["items"] = {"type": "string"}
    if allowed:
        prop["items"]["enum"] = list(allowed)
    else:
        prop["maxItems"] = 0
    if exact_count is not None:
        prop["minItems"] = exact_count
        prop["maxItems"] = exact_count


def _all_property_names(schema: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for node in _walk_dicts(schema):
        properties = node.get("properties")
        if isinstance(properties, dict):
            names.update(properties)
    return names


def _assert_model_schema_authority(schema: Mapping[str, Any], *, stage: CouncilRequestStage) -> None:
    names = _all_property_names(schema)
    forbidden = names & _FORBIDDEN_MODEL_PROPERTIES
    if forbidden:
        raise CouncilRequestError("forbidden model-authority fields in B4 schema: " + ", ".join(sorted(forbidden)))
    if "claim_local_ref" not in names and stage != CouncilRequestStage.JUDGE:
        raise CouncilRequestError("Council/rebuttal model schema must expose response-local claim_local_ref")
    if "bundle_hash" in names:
        raise CouncilRequestError("raw rebuttal model schema must not assign application bundle_hash")
    if "judge_proposal_hash" in names:
        raise CouncilRequestError("raw Judge model schema must not assign application judge_proposal_hash")


def _proposed_claim_object(schema: Mapping[str, Any]) -> dict[str, Any]:
    return _object_with_properties(
        schema,
        {
            "claim_local_ref",
            "candidate_id",
            "lane",
            "source_material_claim_ids",
            "computed_value_ids",
            "conflict_ids",
        },
    )


def build_initial_output_schema(
    *,
    bundle: CouncilInputBundle,
    lane: CouncilLane,
    model_run_ref: str,
    allowed_data_gap_refs: tuple[str, ...],
) -> dict[str, Any]:
    if lane not in {CouncilLane.BULL, CouncilLane.BEAR, CouncilLane.RED_TEAM}:
        raise CouncilRequestError("initial output schema requires Bull/Bear/Red-Team lane")
    schema = _openai_strict_schema(
        InitialCouncilOpinionProposal.model_json_schema(mode="validation")
    )
    root = _object_with_properties(
        schema,
        {
            "opinion_id",
            "candidate_id",
            "lane",
            "council_input_bundle_hash",
            "candidate_packet_hash",
            "proposed_claims",
            "role_boundary_status",
        },
    )
    props = root["properties"]
    for field_name, value in (
        ("candidate_id", bundle.candidate_id),
        ("lane", lane.value),
        ("council_input_bundle_hash", bundle.bundle_hash),
        ("candidate_packet_hash", bundle.candidate_packet_hash),
        ("mandate_version", bundle.mandate_version),
        ("council_policy_version", bundle.council_policy_version),
        ("model_policy_version", bundle.model_policy_version),
        ("model_run_ref", model_run_ref),
    ):
        _set_const(props[field_name], value)
    _restrict_string_array(props["material_unknown_refs"], allowed_data_gap_refs)
    _restrict_string_array(props["material_conflict_refs"], bundle.allowed_conflict_ids)

    claim = _proposed_claim_object(schema)
    cprops = claim["properties"]
    _set_const(cprops["candidate_id"], bundle.candidate_id)
    _set_const(cprops["lane"], lane.value)
    _restrict_string_array(cprops["source_material_claim_ids"], bundle.allowed_material_claim_ids)
    _restrict_string_array(cprops["computed_value_ids"], bundle.allowed_computed_value_ids)
    _restrict_string_array(cprops["conflict_ids"], bundle.allowed_conflict_ids)
    _assert_model_schema_authority(schema, stage=CouncilRequestStage.BULL_INITIAL)
    return schema


def build_rebuttal_output_schema(
    *,
    bundle: CouncilInputBundle,
    initial_opinion_ids: tuple[str, ...],
    initial_opinion_hashes: tuple[str, ...],
    allowed_opposing_claim_ids: tuple[str, ...],
    allowed_source_material_claim_ids: tuple[str, ...],
    allowed_uncertainty_refs: tuple[str, ...],
) -> dict[str, Any]:
    if len(initial_opinion_ids) != 3 or len(set(initial_opinion_ids)) != 3:
        raise CouncilRequestError("rebuttal schema requires exactly three unique initial opinion IDs")
    if len(initial_opinion_hashes) != 3 or len(set(initial_opinion_hashes)) != 3:
        raise CouncilRequestError("rebuttal schema requires exactly three unique initial opinion hashes")
    schema = _openai_strict_schema(RebuttalBundleDraft.model_json_schema(mode="validation"))
    root = _object_with_properties(
        schema,
        {"rebuttal_bundle_id", "candidate_id", "council_input_bundle_hash", "initial_opinion_ids", "items"},
    )
    props = root["properties"]
    _set_const(props["candidate_id"], bundle.candidate_id)
    _set_const(props["council_input_bundle_hash"], bundle.bundle_hash)
    _restrict_string_array(props["initial_opinion_ids"], initial_opinion_ids, exact_count=3)
    _restrict_string_array(props["initial_opinion_hashes"], initial_opinion_hashes, exact_count=3)
    props["items"]["minItems"] = 3
    props["items"]["maxItems"] = 3

    item = _object_with_properties(
        schema,
        {"rebuttal_item_id", "responding_lane", "opposing_finding_ids", "response_proposed_claims"},
    )
    _restrict_string_array(item["properties"]["opposing_finding_ids"], allowed_opposing_claim_ids)
    _restrict_string_array(item["properties"]["remaining_uncertainty_refs"], allowed_uncertainty_refs)

    claim = _proposed_claim_object(schema)
    cprops = claim["properties"]
    _set_const(cprops["candidate_id"], bundle.candidate_id)
    _restrict_string_array(cprops["source_material_claim_ids"], allowed_source_material_claim_ids)
    _restrict_string_array(cprops["computed_value_ids"], bundle.allowed_computed_value_ids)
    _restrict_string_array(cprops["conflict_ids"], bundle.allowed_conflict_ids)
    _assert_model_schema_authority(schema, stage=CouncilRequestStage.REBUTTAL)
    return schema


def build_judge_output_schema(
    *,
    candidate_ids: tuple[str, ...],
    mandate_version: str,
    deep_comparison_id: str,
    judge_input_hash: str,
    council_policy_version: str,
    judge_policy_version: str,
    model_policy_version: str,
    model_run_ref: str,
    allowed_claim_ids: tuple[str, ...],
    allowed_dispute_refs: tuple[str, ...],
    allowed_conflict_refs: tuple[str, ...],
    allowed_unknown_refs: tuple[str, ...],
    allowed_condition_refs: tuple[str, ...],
) -> dict[str, Any]:
    if len(candidate_ids) != 3 or len(set(candidate_ids)) != 3:
        raise CouncilRequestError("Judge schema requires exactly three unique candidate IDs")
    schema = _openai_strict_schema(JudgeDecisionProposalDraft.model_json_schema(mode="validation"))
    root = _object_with_properties(
        schema,
        {"b4_decision_id", "outcome", "primary_candidate_id", "watch_candidate_ids", "judge_input_hash"},
    )
    props = root["properties"]
    _set_nullable_candidate(props["primary_candidate_id"], candidate_ids)
    _restrict_string_array(props["watch_candidate_ids"], candidate_ids)
    for field_name, value in (
        ("mandate_version", mandate_version),
        ("deep_comparison_id", deep_comparison_id),
        ("judge_input_hash", judge_input_hash),
        ("council_policy_version", council_policy_version),
        ("judge_policy_version", judge_policy_version),
        ("model_policy_version", model_policy_version),
        ("model_run_ref", model_run_ref),
    ):
        _set_const(props[field_name], value)
    _restrict_string_array(props["selected_candidate_basis_claim_ids"], allowed_claim_ids)
    _restrict_string_array(props["unresolved_dispute_refs"], allowed_dispute_refs)
    _restrict_string_array(props["material_conflict_refs"], allowed_conflict_refs)
    _restrict_string_array(props["material_unknown_refs"], allowed_unknown_refs)
    _restrict_string_array(props["invalidation_condition_refs"], allowed_condition_refs)

    why_not = _object_with_properties(schema, {"candidate_id", "claim_ids", "reason_codes"})
    why_not["properties"]["candidate_id"] = {"type": "string", "enum": list(candidate_ids)}
    _restrict_string_array(why_not["properties"]["claim_ids"], allowed_claim_ids)

    condition = _object_with_properties(schema, {"condition_id", "condition_text", "source_or_claim_refs"})
    _restrict_string_array(
        condition["properties"]["source_or_claim_refs"],
        tuple(dict.fromkeys((*allowed_claim_ids, *allowed_condition_refs))),
    )
    _assert_model_schema_authority(schema, stage=CouncilRequestStage.JUDGE)
    return schema


def _scan_credential_keys(value: Any, *, path: str = "$MODEL_INPUT") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key).lower()
            if any(marker in key_text for marker in _FORBIDDEN_CREDENTIAL_KEYS):
                raise CouncilRequestError(f"credential-like key is forbidden in B4 model input: {path}.{key}")
            _scan_credential_keys(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _scan_credential_keys(child, path=f"{path}[{index}]")


def _validate_model_candidate(stage: CouncilRequestStage, candidate: CouncilModelCandidate) -> None:
    expected_stage = _STAGE_TO_MODEL_STAGE[stage]
    if candidate.stage != expected_stage:
        raise CouncilRequestError("model candidate stage does not match B4 request stage")
    if candidate not in MODEL_LADDERS[expected_stage]:
        raise CouncilRequestError("model candidate is outside frozen B4 model ladder")


def _request_envelope(
    *,
    stage: CouncilRequestStage,
    model_candidate: CouncilModelCandidate,
    model_input: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> CouncilRequestEnvelope:
    _validate_model_candidate(stage, model_candidate)
    _scan_credential_keys(model_input)
    prompt_version, prompt_hash_fn, instructions, schema_version, schema_name = _STAGE_PROMPT[stage]
    input_envelope = {
        "content_trust": UNTRUSTED_COUNCIL_DATA_MARKER,
        "stage": stage.value,
        "model_input": model_input,
    }
    input_hash = canonical_sha256(input_envelope)
    request_payload = {
        "model": model_candidate.model,
        "reasoning": {"effort": model_candidate.reasoning_effort},
        "instructions": instructions,
        "input": json.dumps(
            input_envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "store": API_INVARIANTS.store,
        "tools": [],
        "parallel_tool_calls": False,
        "truncation": "disabled",
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": schema,
            }
        },
    }
    body = {
        "request_version": REQUEST_VERSION,
        "prompt_contract_version": PROMPT_CONTRACT_VERSION,
        "stage": stage.value,
        "prompt_version": prompt_version,
        "prompt_hash": prompt_hash_fn(),
        "schema_version": schema_version,
        "input_hash": input_hash,
        "model_candidate_key": model_candidate.candidate_key,
        "request_payload": request_payload,
    }
    return CouncilRequestEnvelope(**body, request_hash=canonical_sha256(body))


def build_initial_request(
    *,
    stage: CouncilRequestStage,
    model_candidate: CouncilModelCandidate,
    bundle: CouncilInputBundle,
    model_run_ref: str,
    model_input: Mapping[str, Any],
    allowed_data_gap_refs: tuple[str, ...] = (),
) -> CouncilRequestEnvelope:
    lane = _INITIAL_STAGE_TO_LANE.get(stage)
    if lane is None:
        raise CouncilRequestError("initial request stage must be Bull/Bear/Red-Team")
    schema = build_initial_output_schema(
        bundle=bundle,
        lane=lane,
        model_run_ref=model_run_ref,
        allowed_data_gap_refs=allowed_data_gap_refs,
    )
    return _request_envelope(
        stage=stage,
        model_candidate=model_candidate,
        model_input=model_input,
        schema=schema,
    )


def build_rebuttal_request(
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
    schema = build_rebuttal_output_schema(
        bundle=bundle,
        initial_opinion_ids=initial_opinion_ids,
        initial_opinion_hashes=initial_opinion_hashes,
        allowed_opposing_claim_ids=allowed_opposing_claim_ids,
        allowed_source_material_claim_ids=allowed_source_material_claim_ids,
        allowed_uncertainty_refs=allowed_uncertainty_refs,
    )
    return _request_envelope(
        stage=CouncilRequestStage.REBUTTAL,
        model_candidate=model_candidate,
        model_input=model_input,
        schema=schema,
    )


def build_judge_request(
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
    schema = build_judge_output_schema(
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
    return _request_envelope(
        stage=CouncilRequestStage.JUDGE,
        model_candidate=model_candidate,
        model_input=model_input,
        schema=schema,
    )


def assert_request_invariants(request: CouncilRequestEnvelope) -> None:
    payload = request.request_payload
    if payload.get("store") is not False:
        raise CouncilRequestError("B4 Responses request requires store=false")
    if payload.get("tools") != []:
        raise CouncilRequestError("B4 Responses request requires tools=[]")
    if payload.get("parallel_tool_calls") is not False:
        raise CouncilRequestError("B4 Responses request requires parallel_tool_calls=false")
    if payload.get("truncation") != "disabled":
        raise CouncilRequestError("B4 Responses request requires truncation=disabled")
    text = payload.get("text")
    if not isinstance(text, Mapping):
        raise CouncilRequestError("B4 Responses request requires text configuration")
    fmt = text.get("format")
    if not isinstance(fmt, Mapping):
        raise CouncilRequestError("B4 Responses request requires structured output format")
    if fmt.get("type") != "json_schema" or fmt.get("strict") is not True:
        raise CouncilRequestError("B4 Responses request requires strict json_schema output")
    schema = fmt.get("schema")
    if not isinstance(schema, Mapping):
        raise CouncilRequestError("B4 Responses request requires JSON schema object")
    _assert_model_schema_authority(schema, stage=request.stage)


_OutputT = TypeVar("_OutputT", InitialCouncilOpinionProposal, RebuttalBundleDraft, JudgeDecisionProposalDraft)


def parse_stage_output_text(output_text: str, *, stage: CouncilRequestStage) -> _OutputT:
    if not isinstance(output_text, str) or not output_text.strip():
        raise CouncilRequestError("B4 output_text must be non-empty")
    try:
        raw = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise CouncilRequestError("B4 structured output is not valid JSON") from exc
    if stage in _INITIAL_STAGE_TO_LANE:
        return InitialCouncilOpinionProposal.model_validate(raw)  # type: ignore[return-value]
    if stage == CouncilRequestStage.REBUTTAL:
        return RebuttalBundleDraft.model_validate(raw)  # type: ignore[return-value]
    if stage == CouncilRequestStage.JUDGE:
        return JudgeDecisionProposalDraft.model_validate(raw)  # type: ignore[return-value]
    raise CouncilRequestError("unknown B4 request stage")


def parse_council_responses_payload(
    payload: Mapping[str, Any],
    *,
    request: CouncilRequestEnvelope,
    latency_ms: int = 0,
):
    """Zero-I/O protocol parser reusing the B3-proven Responses payload guard."""
    assert_request_invariants(request)
    requested_model = request.request_payload.get("model")
    if not isinstance(requested_model, str) or not requested_model:
        raise CouncilRequestError("B4 requested model missing")
    try:
        call = parse_responses_payload(
            payload,
            requested_model=requested_model,
            latency_ms=latency_ms,
        )
    except ResponsesProtocolError as exc:
        raise CouncilRequestError(str(exc)) from exc
    draft = parse_stage_output_text(call.output_text, stage=request.stage)
    return call, draft


def frozen_ladder_surface() -> dict[str, tuple[str, ...]]:
    return {
        "INITIAL": tuple(item.candidate_key for item in INITIAL_MODEL_LADDER),
        "REBUTTAL": tuple(item.candidate_key for item in REBUTTAL_MODEL_LADDER),
        "JUDGE": tuple(item.candidate_key for item in JUDGE_MODEL_LADDER),
    }
