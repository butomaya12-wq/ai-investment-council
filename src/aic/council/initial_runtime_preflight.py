from __future__ import annotations

from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256

from .model_policy import (
    INITIAL_MODEL_LADDER,
    OUTPUT_TOKEN_BUDGET_VERSION,
    STAGE_MAX_OUTPUT_TOKENS,
    CouncilModelStage,
)
from .model_selection import InitialSelectedModelAuthority


INITIAL_RUNTIME_REQUEST_PREFLIGHT_VERSION = (
    "B4_INITIAL_RUNTIME_REQUEST_PREFLIGHT_ARTIFACT_v0_1"
)
INITIAL_RUNTIME_REQUEST_PREFLIGHT_RUN_CLASS = (
    "B4_LOCAL_ZERO_CALL_SELECTED_INITIAL_RUNTIME_REQUEST_PREFLIGHT"
)
SOURCE_REQUEST_PREFLIGHT_VERSION = "B4_INITIAL_REQUEST_PREFLIGHT_ARTIFACT_v0_1"
SOURCE_REQUEST_PREFLIGHT_STATUS = "READY_FOR_INITIAL_STAGE_MODEL_EVAL_COST_PREFLIGHT"
RUNTIME_REQUEST_PREFLIGHT_STATUS = "READY_FOR_B4_INITIAL_RUNTIME_COST_PREFLIGHT"
EXPECTED_LOGICAL_CALLS = 9
EXPECTED_SOURCE_VARIANTS = 36

_STAGE_BY_LANE = {
    "BULL": "BULL_INITIAL",
    "BEAR": "BEAR_INITIAL",
    "RED_TEAM": "RED_TEAM_INITIAL",
}
_LANE_ORDER = ("BULL", "BEAR", "RED_TEAM")


class InitialRuntimePreflightError(ValueError):
    pass


def _sha256_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise InitialRuntimePreflightError(f"{field_name} must be sha256")
    if any(ch not in "0123456789abcdef" for ch in value):
        raise InitialRuntimePreflightError(f"{field_name} must be lowercase sha256")
    return value


def _verify_artifact_hash(payload: Mapping[str, Any]) -> str:
    actual = _sha256_text(payload.get("artifact_hash"), field_name="artifact_hash")
    expected = canonical_sha256(payload, exclude_fields=("artifact_hash",))
    if actual != expected:
        raise InitialRuntimePreflightError("source request preflight artifact_hash mismatch")
    return actual


def _verify_code_commit_sha(value: str) -> str:
    if not isinstance(value, str) or len(value) != 40:
        raise InitialRuntimePreflightError("code_commit_sha must be exact git SHA")
    if any(ch not in "0123456789abcdef" for ch in value):
        raise InitialRuntimePreflightError("code_commit_sha must be lowercase hex")
    return value


def _expected_variant_order(candidate_order: tuple[str, str, str]) -> tuple[tuple[str, str, str], ...]:
    keys = tuple(item.candidate_key for item in INITIAL_MODEL_LADDER)
    return tuple(
        (candidate, lane, key)
        for candidate in candidate_order
        for lane in _LANE_ORDER
        for key in keys
    )


def _verify_source_variant(
    item: Mapping[str, Any],
    *,
    candidate_order: tuple[str, str, str],
) -> None:
    candidate = item.get("candidate")
    lane = item.get("lane")
    candidate_key = item.get("model_candidate_key")
    if candidate not in candidate_order:
        raise InitialRuntimePreflightError("source request variant candidate outside frozen order")
    if lane not in _LANE_ORDER:
        raise InitialRuntimePreflightError("source request variant lane invalid")
    if item.get("stage") != _STAGE_BY_LANE[lane]:
        raise InitialRuntimePreflightError("source request variant stage/lane mismatch")
    ladder = {candidate.candidate_key: candidate for candidate in INITIAL_MODEL_LADDER}
    frozen = ladder.get(candidate_key)
    if frozen is None:
        raise InitialRuntimePreflightError("source request variant model candidate outside ladder")
    if item.get("model") != frozen.model or item.get("reasoning_effort") != frozen.reasoning_effort:
        raise InitialRuntimePreflightError("source request variant model configuration drift")

    model_input_hash = _sha256_text(
        item.get("model_input_hash"), field_name="model_input_hash"
    )
    _sha256_text(item.get("request_hash"), field_name="request_hash")
    _sha256_text(item.get("schema_hash"), field_name="schema_hash")
    _sha256_text(item.get("semantic_schema_hash"), field_name="semantic_schema_hash")

    expected_ref = (
        f"B4_INITIAL_{candidate}_{lane}_{candidate_key}_{model_input_hash[:12]}"
    )
    if item.get("model_run_ref") != expected_ref:
        raise InitialRuntimePreflightError("source request variant model_run_ref drift")
    if item.get("logical_call") != f"{candidate}:{lane}":
        raise InitialRuntimePreflightError("source request variant logical_call drift")

    request_bytes = item.get("request_body_utf8_bytes")
    if type(request_bytes) is not int or request_bytes <= 0:
        raise InitialRuntimePreflightError("source request variant byte count invalid")
    expected_output_cap = STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.INITIAL]
    if item.get("max_output_tokens") != expected_output_cap:
        raise InitialRuntimePreflightError("source request variant output-token cap drift")
    if item.get("store") is not False:
        raise InitialRuntimePreflightError("source request variant requires store=false")
    if item.get("tools") != []:
        raise InitialRuntimePreflightError("source request variant requires tools=[]")
    if item.get("parallel_tool_calls") is not False:
        raise InitialRuntimePreflightError(
            "source request variant requires parallel_tool_calls=false"
        )
    if item.get("truncation") != "disabled":
        raise InitialRuntimePreflightError("source request variant requires truncation=disabled")
    if item.get("strict_json_schema") is not True:
        raise InitialRuntimePreflightError("source request variant requires strict JSON schema")


def verify_source_initial_request_preflight(payload: Mapping[str, Any]) -> str:
    artifact_hash = _verify_artifact_hash(payload)
    if payload.get("artifact_version") != SOURCE_REQUEST_PREFLIGHT_VERSION:
        raise InitialRuntimePreflightError("unexpected source Initial request preflight version")
    if payload.get("status") != SOURCE_REQUEST_PREFLIGHT_STATUS:
        raise InitialRuntimePreflightError("source Initial request preflight is not ready")
    if payload.get("logical_call_count") != EXPECTED_LOGICAL_CALLS:
        raise InitialRuntimePreflightError("source Initial logical-call count drift")
    if payload.get("request_variant_count") != EXPECTED_SOURCE_VARIANTS:
        raise InitialRuntimePreflightError("source Initial request-variant count drift")
    if payload.get("initial_model_ladder_count") != len(INITIAL_MODEL_LADDER):
        raise InitialRuntimePreflightError("source Initial model-ladder count drift")
    if payload.get("output_token_budget_version") != OUTPUT_TOKEN_BUDGET_VERSION:
        raise InitialRuntimePreflightError("source Initial output-token budget version drift")
    for field in ("model_calls", "provider_reads", "broker_writes", "alpaca_orders"):
        if payload.get(field) != 0:
            raise InitialRuntimePreflightError(f"source Initial {field} must be zero")
    if payload.get("live_money") != "PROHIBITED":
        raise InitialRuntimePreflightError("source Initial live-money invariant drift")

    raw_order = payload.get("candidate_order")
    if not isinstance(raw_order, list) or len(raw_order) != 3:
        raise InitialRuntimePreflightError("source Initial candidate order invalid")
    if any(not isinstance(item, str) or not item for item in raw_order):
        raise InitialRuntimePreflightError("source Initial candidate identity invalid")
    candidate_order = tuple(raw_order)
    if len(set(candidate_order)) != 3:
        raise InitialRuntimePreflightError("source Initial candidates must be distinct")

    variants = payload.get("request_variants")
    if not isinstance(variants, list) or len(variants) != EXPECTED_SOURCE_VARIANTS:
        raise InitialRuntimePreflightError("source Initial request variants missing")
    observed_order: list[tuple[str, str, str]] = []
    for raw in variants:
        if not isinstance(raw, Mapping):
            raise InitialRuntimePreflightError("source Initial request variant must be object")
        _verify_source_variant(raw, candidate_order=candidate_order)  # type: ignore[arg-type]
        observed_order.append(
            (raw["candidate"], raw["lane"], raw["model_candidate_key"])
        )
    if tuple(observed_order) != _expected_variant_order(candidate_order):  # type: ignore[arg-type]
        raise InitialRuntimePreflightError("source Initial request variant order drift")
    return artifact_hash


def build_initial_runtime_request_preflight(
    source_request_preflight: Mapping[str, Any],
    *,
    authority: InitialSelectedModelAuthority,
    code_commit_sha: str,
) -> dict[str, Any]:
    source_hash = verify_source_initial_request_preflight(source_request_preflight)
    code_commit_sha = _verify_code_commit_sha(code_commit_sha)

    selected = authority.selected_candidate
    if selected.stage is not CouncilModelStage.INITIAL:
        raise InitialRuntimePreflightError("selected authority is not for Initial stage")
    if selected not in INITIAL_MODEL_LADDER:
        raise InitialRuntimePreflightError("selected authority candidate outside frozen Initial ladder")

    variants = source_request_preflight["request_variants"]
    selected_variants = [
        dict(item)
        for item in variants
        if isinstance(item, Mapping)
        and item.get("model_candidate_key") == selected.candidate_key
    ]
    if len(selected_variants) != EXPECTED_LOGICAL_CALLS:
        raise InitialRuntimePreflightError("selected Initial request count must be exactly nine")

    raw_order = source_request_preflight["candidate_order"]
    candidate_order = tuple(raw_order)
    expected_selected_order = tuple(
        (candidate, lane, selected.candidate_key)
        for candidate in candidate_order
        for lane in _LANE_ORDER
    )
    observed_selected_order = tuple(
        (item["candidate"], item["lane"], item["model_candidate_key"])
        for item in selected_variants
    )
    if observed_selected_order != expected_selected_order:
        raise InitialRuntimePreflightError("selected Initial request order drift")
    if any(item["model"] != selected.model for item in selected_variants):
        raise InitialRuntimePreflightError("selected Initial request model drift")
    if any(
        item["reasoning_effort"] != selected.reasoning_effort
        for item in selected_variants
    ):
        raise InitialRuntimePreflightError("selected Initial reasoning effort drift")

    request_manifest_hash = canonical_sha256(
        {"selected_request_variants": selected_variants}
    )
    artifact: dict[str, Any] = {
        "artifact_version": INITIAL_RUNTIME_REQUEST_PREFLIGHT_VERSION,
        "run_class": INITIAL_RUNTIME_REQUEST_PREFLIGHT_RUN_CLASS,
        "status": RUNTIME_REQUEST_PREFLIGHT_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_request_preflight_artifact_hash": source_hash,
        "b4_input_freeze_artifact_hash": source_request_preflight[
            "b4_input_freeze_artifact_hash"
        ],
        "b3_reconciliation_artifact_hash": source_request_preflight[
            "b3_reconciliation_artifact_hash"
        ],
        "b2_handoff_hash": source_request_preflight["b2_handoff_hash"],
        "mandate_version": source_request_preflight["mandate_version"],
        "selected_model_authority_version": authority.artifact_version,
        "selected_model_authority_selection_hash": authority.selection_hash,
        "selected_model_eval_artifact_hash": authority.model_eval_artifact_hash,
        "selected_candidate": {
            "candidate_key": selected.candidate_key,
            "stage": selected.stage.value,
            "model": selected.model,
            "reasoning_effort": selected.reasoning_effort,
            "ladder_position": selected.ladder_position,
        },
        "candidate_order": list(candidate_order),
        "logical_call_count": EXPECTED_LOGICAL_CALLS,
        "planned_paid_calls_max": EXPECTED_LOGICAL_CALLS,
        "automatic_repair_calls_authorized": False,
        "output_token_budget_version": OUTPUT_TOKEN_BUDGET_VERSION,
        "max_output_tokens_per_call": STAGE_MAX_OUTPUT_TOKENS[
            CouncilModelStage.INITIAL
        ],
        "selected_request_variants": selected_variants,
        "request_manifest_hash": request_manifest_hash,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_initial_runtime_request_preflight(payload: Mapping[str, Any]) -> str:
    actual = _sha256_text(payload.get("artifact_hash"), field_name="artifact_hash")
    if actual != canonical_sha256(payload, exclude_fields=("artifact_hash",)):
        raise InitialRuntimePreflightError("Initial runtime request preflight artifact_hash mismatch")
    if payload.get("artifact_version") != INITIAL_RUNTIME_REQUEST_PREFLIGHT_VERSION:
        raise InitialRuntimePreflightError("unexpected Initial runtime request preflight version")
    if payload.get("run_class") != INITIAL_RUNTIME_REQUEST_PREFLIGHT_RUN_CLASS:
        raise InitialRuntimePreflightError("unexpected Initial runtime request preflight run class")
    if payload.get("status") != RUNTIME_REQUEST_PREFLIGHT_STATUS:
        raise InitialRuntimePreflightError("Initial runtime request preflight is not ready")
    _verify_code_commit_sha(payload.get("code_commit_sha"))
    if payload.get("logical_call_count") != EXPECTED_LOGICAL_CALLS:
        raise InitialRuntimePreflightError("Initial runtime logical-call count drift")
    if payload.get("planned_paid_calls_max") != EXPECTED_LOGICAL_CALLS:
        raise InitialRuntimePreflightError("Initial runtime paid-call ceiling drift")
    if payload.get("automatic_repair_calls_authorized") is not False:
        raise InitialRuntimePreflightError("Initial runtime must not auto-authorize repair calls")
    variants = payload.get("selected_request_variants")
    if not isinstance(variants, list) or len(variants) != EXPECTED_LOGICAL_CALLS:
        raise InitialRuntimePreflightError("Initial runtime selected request variants missing")
    expected_manifest = canonical_sha256({"selected_request_variants": variants})
    if payload.get("request_manifest_hash") != expected_manifest:
        raise InitialRuntimePreflightError("Initial runtime request manifest hash mismatch")
    for field in ("model_calls", "provider_reads", "broker_writes", "alpaca_orders"):
        if payload.get(field) != 0:
            raise InitialRuntimePreflightError(f"Initial runtime {field} must be zero")
    if payload.get("live_money") != "PROHIBITED":
        raise InitialRuntimePreflightError("Initial runtime live-money invariant drift")
    return actual
