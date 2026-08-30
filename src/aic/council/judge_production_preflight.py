from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256

from .initial_runtime_cost_v02 import (
    EXPECTED_CACHE_WRITE_MULTIPLIER,
    EXPECTED_CACHE_WRITE_USAGE_FIELD,
    EXPECTED_RUNTIME_PRICING_VERSION,
    load_initial_runtime_pricing,
    runtime_cost_upper_bound_usd,
)
from .judge_model_selection_v01 import (
    EXPECTED_SELECTED_JUDGE,
    verify_judge_selected_model_authority,
)
from .judge_production import (
    EXPECTED_ALLOWED_OUTCOMES,
    EXPECTED_JUDGE_ENTRY_HASH,
    EXPECTED_MAX_OUTPUT_TOKENS,
    EXPECTED_REBUTTAL_FREEZE_HASH,
    EXPECTED_REQUIRED_UNKNOWN_REFS,
    JUDGE_PRODUCTION_SCHEMA_TIGHTENING_VERSION,
    JudgeProductionContext,
    build_judge_production_request,
    request_body_utf8_bytes,
)


JUDGE_PRODUCTION_REQUEST_PREFLIGHT_VERSION = (
    "B4_JUDGE_PRODUCTION_REQUEST_PREFLIGHT_v0_1"
)
JUDGE_PRODUCTION_REQUEST_PREFLIGHT_STATUS = (
    "PASS_ZERO_CALL_JUDGE_PRODUCTION_REQUEST_PREFLIGHT"
)
JUDGE_PRODUCTION_COST_PREFLIGHT_VERSION = (
    "B4_JUDGE_PRODUCTION_COST_PREFLIGHT_v0_1"
)
JUDGE_PRODUCTION_COST_PREFLIGHT_STATUS = (
    "REQUIRES_EXPLICIT_OWNER_APPROVAL_BEFORE_PRODUCTION_JUDGE"
)
EXPECTED_PRICING_VERSION = EXPECTED_RUNTIME_PRICING_VERSION
EXPECTED_PRICING_HASH = (
    "13b67bf92f56b2962694f463850e0a0e289fc08f0c4a3d3cafe8eb928d0ee336"
)
EXPECTED_PRODUCTION_JUDGE_CALLS = 1


class JudgeProductionPreflightError(ValueError):
    pass


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def build_judge_production_request_preflight(
    *,
    code_commit_sha: str,
    context: JudgeProductionContext,
    selected_model_authority: Mapping[str, Any],
) -> dict[str, Any]:
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise JudgeProductionPreflightError("production Judge preflight requires exact git SHA")
    selection_hash = verify_judge_selected_model_authority(selected_model_authority)
    if selected_model_authority.get("selected_candidate") != EXPECTED_SELECTED_JUDGE:
        raise JudgeProductionPreflightError("production Judge preflight requires frozen J1")
    request = build_judge_production_request(context, selected_model_authority)
    request_bytes = request_body_utf8_bytes(request.request_payload)
    if request_bytes <= 0:
        raise JudgeProductionPreflightError("production Judge request byte count invalid")
    if request.request_payload.get("max_output_tokens") != EXPECTED_MAX_OUTPUT_TOKENS:
        raise JudgeProductionPreflightError("production Judge output cap drift")
    schema = request.request_payload["text"]["format"]["schema"]
    schema_hash = canonical_sha256(schema)
    request_manifest_hash = canonical_sha256(
        {
            "selected_model_authority_hash": selection_hash,
            "judge_input_hash": context.judge_input_hash,
            "judge_context_hash": context.context_hash,
            "request_hash": request.request_hash,
            "request_body_utf8_bytes": request_bytes,
        }
    )
    artifact: dict[str, Any] = {
        "artifact_version": JUDGE_PRODUCTION_REQUEST_PREFLIGHT_VERSION,
        "status": JUDGE_PRODUCTION_REQUEST_PREFLIGHT_STATUS,
        "code_commit_sha": code_commit_sha,
        "judge_entry_preflight_artifact_hash": EXPECTED_JUDGE_ENTRY_HASH,
        "rebuttal_council_freeze_artifact_hash": EXPECTED_REBUTTAL_FREEZE_HASH,
        "judge_selected_model_authority_hash": selection_hash,
        "selected_candidate": dict(EXPECTED_SELECTED_JUDGE),
        "judge_input_hash": context.judge_input_hash,
        "judge_context_hash": context.context_hash,
        "production_schema_tightening_version": JUDGE_PRODUCTION_SCHEMA_TIGHTENING_VERSION,
        "allowed_outcomes": [item.value for item in EXPECTED_ALLOWED_OUTCOMES],
        "required_unknown_refs": list(EXPECTED_REQUIRED_UNKNOWN_REFS),
        "required_research_reopen": True,
        "required_next_directive": "RESEARCH_REOPEN_REQUEST",
        "request_hash": request.request_hash,
        "request_body_utf8_bytes": request_bytes,
        "request_manifest_hash": request_manifest_hash,
        "schema_hash": schema_hash,
        "prompt_version": request.prompt_version,
        "prompt_hash": request.prompt_hash,
        "schema_version": request.schema_version,
        "input_hash": request.input_hash,
        "model": EXPECTED_SELECTED_JUDGE["model"],
        "reasoning_effort": EXPECTED_SELECTED_JUDGE["reasoning_effort"],
        "planned_paid_calls_max": EXPECTED_PRODUCTION_JUDGE_CALLS,
        "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
        "automatic_repair_calls_authorized": False,
        "owner_approval_required": True,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "production_judge_authorized": False,
        "rerun_authorized": False,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_judge_production_request_preflight(payload: Mapping[str, Any]) -> str:
    observed = payload.get("artifact_hash")
    if not isinstance(observed, str) or observed != canonical_sha256(payload, exclude_fields=("artifact_hash",)):
        raise JudgeProductionPreflightError("production Judge request preflight self-hash mismatch")
    if payload.get("artifact_version") != JUDGE_PRODUCTION_REQUEST_PREFLIGHT_VERSION:
        raise JudgeProductionPreflightError("production Judge request preflight version drift")
    if payload.get("status") != JUDGE_PRODUCTION_REQUEST_PREFLIGHT_STATUS:
        raise JudgeProductionPreflightError("production Judge request preflight is not PASS")
    if payload.get("selected_candidate") != EXPECTED_SELECTED_JUDGE:
        raise JudgeProductionPreflightError("production Judge selected candidate drift")
    if payload.get("allowed_outcomes") != [item.value for item in EXPECTED_ALLOWED_OUTCOMES]:
        raise JudgeProductionPreflightError("production Judge allowed outcome surface drift")
    if payload.get("required_unknown_refs") != list(EXPECTED_REQUIRED_UNKNOWN_REFS):
        raise JudgeProductionPreflightError("production Judge required unknown surface drift")
    if payload.get("required_research_reopen") is not True:
        raise JudgeProductionPreflightError("production Judge research-reopen requirement lost")
    if payload.get("required_next_directive") != "RESEARCH_REOPEN_REQUEST":
        raise JudgeProductionPreflightError("production Judge next directive drift")
    if payload.get("planned_paid_calls_max") != 1:
        raise JudgeProductionPreflightError("production Judge call ceiling drift")
    if payload.get("max_output_tokens_per_call") != EXPECTED_MAX_OUTPUT_TOKENS:
        raise JudgeProductionPreflightError("production Judge output cap drift")
    if payload.get("automatic_repair_calls_authorized") is not False:
        raise JudgeProductionPreflightError("production Judge repair unexpectedly authorized")
    if payload.get("owner_approval_required") is not True:
        raise JudgeProductionPreflightError("production Judge owner approval requirement missing")
    for field in ("model_calls", "provider_reads", "broker_writes", "alpaca_orders"):
        if payload.get(field) != 0:
            raise JudgeProductionPreflightError(f"production Judge request preflight {field} must be zero")
    if payload.get("live_money") != "PROHIBITED":
        raise JudgeProductionPreflightError("production Judge request preflight live-money drift")
    if payload.get("production_judge_authorized") is not False or payload.get("rerun_authorized") is not False:
        raise JudgeProductionPreflightError("production Judge request preflight grants execution/rerun")
    return observed


def build_judge_production_cost_preflight(
    request_preflight: Mapping[str, Any],
    *,
    pricing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    request_hash = verify_judge_production_request_preflight(request_preflight)
    pricing = dict(pricing or load_initial_runtime_pricing())
    pricing_hash = pricing.get("pricing_hash")
    if pricing.get("pricing_version") != EXPECTED_PRICING_VERSION or pricing_hash != EXPECTED_PRICING_HASH:
        raise JudgeProductionPreflightError("production Judge pricing authority drift")
    if pricing_hash != canonical_sha256(pricing, exclude_fields=("pricing_hash",)):
        raise JudgeProductionPreflightError("production Judge pricing self-hash mismatch")
    request_bytes = request_preflight.get("request_body_utf8_bytes")
    if type(request_bytes) is not int or request_bytes <= 0:
        raise JudgeProductionPreflightError("production Judge request byte count malformed")
    model = request_preflight.get("model")
    if model != EXPECTED_SELECTED_JUDGE["model"]:
        raise JudgeProductionPreflightError("production Judge cost model drift")
    cost = runtime_cost_upper_bound_usd(
        model=str(model),
        input_tokens_upper_bound=request_bytes,
        output_tokens_upper_bound=EXPECTED_MAX_OUTPUT_TOKENS,
        call_count=1,
        pricing=pricing,
    )
    cache_write = pricing.get("cache_write")
    if not isinstance(cache_write, Mapping):
        raise JudgeProductionPreflightError("production Judge cache-write pricing missing")
    long_context = pricing.get("long_context")
    if not isinstance(long_context, Mapping):
        raise JudgeProductionPreflightError("production Judge long-context pricing missing")
    artifact: dict[str, Any] = {
        "artifact_version": JUDGE_PRODUCTION_COST_PREFLIGHT_VERSION,
        "status": JUDGE_PRODUCTION_COST_PREFLIGHT_STATUS,
        "code_commit_sha": request_preflight["code_commit_sha"],
        "judge_selected_model_authority_hash": request_preflight[
            "judge_selected_model_authority_hash"
        ],
        "judge_production_request_preflight_artifact_hash": request_hash,
        "judge_production_request_manifest_hash": request_preflight["request_manifest_hash"],
        "request_hash": request_preflight["request_hash"],
        "model": model,
        "reasoning_effort": request_preflight["reasoning_effort"],
        "request_body_utf8_bytes": request_bytes,
        "input_token_upper_bound_method": (
            "CONSERVATIVE: one input token per UTF-8 serialized request-body byte; "
            "all input tokens additionally assumed eligible for cache-write billing"
        ),
        "input_tokens_upper_bound": request_bytes,
        "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
        "planned_paid_calls_max": 1,
        "pricing_version": pricing["pricing_version"],
        "pricing_hash": pricing_hash,
        "pricing_as_of_date": pricing["as_of_date"],
        "cache_write_input_rate_multiplier": cache_write["input_rate_multiplier"],
        "cache_write_usage_field": cache_write["usage_field"],
        "long_context_threshold_input_tokens_exclusive": long_context[
            "threshold_input_tokens_exclusive"
        ],
        "long_context_input_multiplier": long_context["input_multiplier"],
        "long_context_output_multiplier": long_context["output_multiplier"],
        "long_context_surcharge_assumed": request_bytes
        > long_context["threshold_input_tokens_exclusive"],
        "worst_case_all_input_tokens_as_cache_write_assumed": True,
        "cached_input_discount_assumed_for_upper_bound": False,
        "production_judge_cost_upper_bound_usd": _decimal_text(cost),
        "owner_cost_approval_required": True,
        "automatic_repair_calls_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "production_judge_authorized": False,
        "rerun_authorized": False,
    }
    if Decimal(str(artifact["cache_write_input_rate_multiplier"])) != EXPECTED_CACHE_WRITE_MULTIPLIER:
        raise JudgeProductionPreflightError("production Judge cache-write multiplier drift")
    if artifact["cache_write_usage_field"] != EXPECTED_CACHE_WRITE_USAGE_FIELD:
        raise JudgeProductionPreflightError("production Judge cache-write usage field drift")
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_judge_production_cost_preflight(payload: Mapping[str, Any]) -> str:
    observed = payload.get("artifact_hash")
    if not isinstance(observed, str) or observed != canonical_sha256(payload, exclude_fields=("artifact_hash",)):
        raise JudgeProductionPreflightError("production Judge cost preflight self-hash mismatch")
    if payload.get("artifact_version") != JUDGE_PRODUCTION_COST_PREFLIGHT_VERSION:
        raise JudgeProductionPreflightError("production Judge cost preflight version drift")
    if payload.get("status") != JUDGE_PRODUCTION_COST_PREFLIGHT_STATUS:
        raise JudgeProductionPreflightError("production Judge cost preflight status drift")
    if payload.get("model") != EXPECTED_SELECTED_JUDGE["model"]:
        raise JudgeProductionPreflightError("production Judge cost selected model drift")
    if payload.get("planned_paid_calls_max") != 1:
        raise JudgeProductionPreflightError("production Judge cost call ceiling drift")
    if payload.get("max_output_tokens_per_call") != EXPECTED_MAX_OUTPUT_TOKENS:
        raise JudgeProductionPreflightError("production Judge cost output cap drift")
    if payload.get("pricing_version") != EXPECTED_PRICING_VERSION or payload.get("pricing_hash") != EXPECTED_PRICING_HASH:
        raise JudgeProductionPreflightError("production Judge cost pricing drift")
    if payload.get("owner_cost_approval_required") is not True:
        raise JudgeProductionPreflightError("production Judge cost owner approval requirement missing")
    if payload.get("automatic_repair_calls_authorized") is not False:
        raise JudgeProductionPreflightError("production Judge cost repair unexpectedly authorized")
    cost = payload.get("production_judge_cost_upper_bound_usd")
    if not isinstance(cost, str) or Decimal(cost) <= 0:
        raise JudgeProductionPreflightError("production Judge cost ceiling invalid")
    for field in ("model_calls", "provider_reads", "broker_writes", "alpaca_orders"):
        if payload.get(field) != 0:
            raise JudgeProductionPreflightError(f"production Judge cost preflight {field} must be zero")
    if payload.get("live_money") != "PROHIBITED":
        raise JudgeProductionPreflightError("production Judge cost preflight live-money drift")
    if payload.get("production_judge_authorized") is not False or payload.get("rerun_authorized") is not False:
        raise JudgeProductionPreflightError("production Judge cost preflight grants execution/rerun")
    return observed
