from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256

from .eval_cost import cost_upper_bound_usd
from .initial_runtime_preflight import (
    EXPECTED_LOGICAL_CALLS,
    verify_initial_runtime_request_preflight,
)
from .model_policy import CouncilModelStage, STAGE_MAX_OUTPUT_TOKENS


INITIAL_RUNTIME_COST_PREFLIGHT_VERSION = "B4_INITIAL_RUNTIME_COST_PREFLIGHT_ARTIFACT_v0_1"
INITIAL_RUNTIME_COST_PREFLIGHT_RUN_CLASS = (
    "B4_LOCAL_ZERO_CALL_SELECTED_INITIAL_RUNTIME_COST_PREFLIGHT"
)
INITIAL_RUNTIME_COST_PREFLIGHT_STATUS = (
    "REQUIRES_OWNER_COST_APPROVAL_BEFORE_B4_INITIAL_RUNTIME"
)


class InitialRuntimeCostError(ValueError):
    pass


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite() or value < 0:
        raise InitialRuntimeCostError("cost must be finite and non-negative")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _pricing_hash(pricing: Mapping[str, Any]) -> str:
    actual = pricing.get("pricing_hash")
    if not isinstance(actual, str) or len(actual) != 64:
        raise InitialRuntimeCostError("pricing_hash missing")
    expected = canonical_sha256(pricing, exclude_fields=("pricing_hash",))
    if actual != expected:
        raise InitialRuntimeCostError("pricing_hash mismatch")
    return actual


def build_initial_runtime_cost_preflight(
    runtime_preflight: Mapping[str, Any],
    *,
    pricing: Mapping[str, Any],
) -> dict[str, Any]:
    runtime_hash = verify_initial_runtime_request_preflight(runtime_preflight)
    pricing_hash = _pricing_hash(pricing)

    selected = runtime_preflight.get("selected_candidate")
    if not isinstance(selected, Mapping):
        raise InitialRuntimeCostError("selected Initial model missing")
    model = selected.get("model")
    candidate_key = selected.get("candidate_key")
    reasoning_effort = selected.get("reasoning_effort")
    if not all(isinstance(value, str) and value for value in (model, candidate_key, reasoning_effort)):
        raise InitialRuntimeCostError("selected Initial model configuration invalid")

    variants = runtime_preflight.get("selected_request_variants")
    if not isinstance(variants, list) or len(variants) != EXPECTED_LOGICAL_CALLS:
        raise InitialRuntimeCostError("Initial runtime request variants missing")
    byte_counts = []
    for item in variants:
        if not isinstance(item, Mapping):
            raise InitialRuntimeCostError("Initial runtime request variant invalid")
        if item.get("model") != model:
            raise InitialRuntimeCostError("Initial runtime request model/cost authority mismatch")
        value = item.get("request_body_utf8_bytes")
        if type(value) is not int or value <= 0:
            raise InitialRuntimeCostError("Initial runtime request byte count invalid")
        byte_counts.append(value)

    max_request_body_utf8_bytes = max(byte_counts)
    input_tokens_upper_bound_per_call = max_request_body_utf8_bytes
    output_cap = STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.INITIAL]
    if runtime_preflight.get("max_output_tokens_per_call") != output_cap:
        raise InitialRuntimeCostError("Initial runtime output-token cap drift")

    cost = cost_upper_bound_usd(
        model=model,
        input_tokens_upper_bound=input_tokens_upper_bound_per_call,
        output_tokens_upper_bound=output_cap,
        call_count=EXPECTED_LOGICAL_CALLS,
        pricing=pricing,
    )
    long_context = pricing.get("long_context")
    if not isinstance(long_context, Mapping):
        raise InitialRuntimeCostError("long-context pricing authority missing")
    threshold = long_context.get("threshold_input_tokens_exclusive")
    if type(threshold) is not int or threshold <= 0:
        raise InitialRuntimeCostError("long-context threshold invalid")

    sources = []
    models = pricing.get("models")
    if isinstance(models, Mapping):
        record = models.get(model)
        if isinstance(record, Mapping) and isinstance(record.get("source_url"), str):
            sources.append(record["source_url"])

    artifact: dict[str, Any] = {
        "artifact_version": INITIAL_RUNTIME_COST_PREFLIGHT_VERSION,
        "run_class": INITIAL_RUNTIME_COST_PREFLIGHT_RUN_CLASS,
        "status": INITIAL_RUNTIME_COST_PREFLIGHT_STATUS,
        "code_commit_sha": runtime_preflight["code_commit_sha"],
        "runtime_request_preflight_artifact_hash": runtime_hash,
        "source_request_preflight_artifact_hash": runtime_preflight[
            "source_request_preflight_artifact_hash"
        ],
        "b4_input_freeze_artifact_hash": runtime_preflight[
            "b4_input_freeze_artifact_hash"
        ],
        "b3_reconciliation_artifact_hash": runtime_preflight[
            "b3_reconciliation_artifact_hash"
        ],
        "b2_handoff_hash": runtime_preflight["b2_handoff_hash"],
        "mandate_version": runtime_preflight["mandate_version"],
        "selected_model_authority_selection_hash": runtime_preflight[
            "selected_model_authority_selection_hash"
        ],
        "selected_model_eval_artifact_hash": runtime_preflight[
            "selected_model_eval_artifact_hash"
        ],
        "selected_candidate": dict(selected),
        "planned_paid_calls_max": EXPECTED_LOGICAL_CALLS,
        "automatic_repair_calls_authorized": False,
        "max_request_body_utf8_bytes": max_request_body_utf8_bytes,
        "input_tokens_upper_bound_per_call": input_tokens_upper_bound_per_call,
        "input_token_upper_bound_method": (
            "CONSERVATIVE: one input token per UTF-8 serialized request-body byte; "
            "the entire request body is counted even though not every HTTP JSON byte is model-billed input"
        ),
        "max_output_tokens_per_call": output_cap,
        "long_context_threshold_input_tokens_exclusive": threshold,
        "long_context_pricing_applied": input_tokens_upper_bound_per_call > threshold,
        "pricing_version": pricing.get("pricing_version"),
        "pricing_hash": pricing_hash,
        "pricing_as_of_date": pricing.get("as_of_date"),
        "pricing_source_urls": sources,
        "cached_input_discount_assumed_for_upper_bound": False,
        "total_initial_runtime_cost_upper_bound_usd": _decimal_text(cost),
        "owner_cost_approval_required": True,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_initial_runtime_cost_preflight(payload: Mapping[str, Any]) -> str:
    actual = payload.get("artifact_hash")
    if not isinstance(actual, str) or len(actual) != 64:
        raise InitialRuntimeCostError("Initial runtime cost artifact_hash missing")
    if actual != canonical_sha256(payload, exclude_fields=("artifact_hash",)):
        raise InitialRuntimeCostError("Initial runtime cost artifact_hash mismatch")
    if payload.get("artifact_version") != INITIAL_RUNTIME_COST_PREFLIGHT_VERSION:
        raise InitialRuntimeCostError("unexpected Initial runtime cost preflight version")
    if payload.get("run_class") != INITIAL_RUNTIME_COST_PREFLIGHT_RUN_CLASS:
        raise InitialRuntimeCostError("unexpected Initial runtime cost preflight run class")
    if payload.get("status") != INITIAL_RUNTIME_COST_PREFLIGHT_STATUS:
        raise InitialRuntimeCostError("Initial runtime cost preflight is not approval-ready")
    if payload.get("planned_paid_calls_max") != EXPECTED_LOGICAL_CALLS:
        raise InitialRuntimeCostError("Initial runtime paid-call ceiling drift")
    if payload.get("automatic_repair_calls_authorized") is not False:
        raise InitialRuntimeCostError("Initial runtime cost artifact must not authorize repairs")
    if payload.get("owner_cost_approval_required") is not True:
        raise InitialRuntimeCostError("Initial runtime cost artifact must require owner approval")
    for field in ("model_calls", "provider_reads", "broker_writes", "alpaca_orders"):
        if payload.get(field) != 0:
            raise InitialRuntimeCostError(f"Initial runtime cost preflight {field} must be zero")
    if payload.get("live_money") != "PROHIBITED":
        raise InitialRuntimeCostError("Initial runtime cost preflight live-money invariant drift")
    return actual
