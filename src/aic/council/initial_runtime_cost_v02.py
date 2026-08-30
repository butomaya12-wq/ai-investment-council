from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256

from .initial_runtime import (
    InitialProcessedResponse,
    InitialRuntimeError,
    InitialRuntimePlanItem,
    process_initial_provider_response as _process_initial_provider_response_v01,
)
from .initial_runtime_preflight import (
    EXPECTED_LOGICAL_CALLS,
    verify_initial_runtime_request_preflight,
)
from .model_policy import CouncilModelStage, STAGE_MAX_OUTPUT_TOKENS


DEFAULT_RUNTIME_PRICING_PATH = Path(
    "config/event/openai_text_pricing_2026_08_30.json"
)
EXPECTED_RUNTIME_PRICING_VERSION = (
    "OPENAI_TEXT_PRICING_2026_08_30_CACHE_WRITE_AWARE"
)
INITIAL_RUNTIME_COST_PREFLIGHT_VERSION = (
    "B4_INITIAL_RUNTIME_COST_PREFLIGHT_ARTIFACT_v0_2"
)
INITIAL_RUNTIME_COST_PREFLIGHT_RUN_CLASS = (
    "B4_LOCAL_ZERO_CALL_SELECTED_INITIAL_RUNTIME_COST_PREFLIGHT"
)
INITIAL_RUNTIME_COST_PREFLIGHT_STATUS = (
    "REQUIRES_OWNER_COST_APPROVAL_BEFORE_B4_INITIAL_RUNTIME"
)
EXPECTED_CACHE_WRITE_MULTIPLIER = Decimal("1.25")
EXPECTED_CACHE_WRITE_USAGE_FIELD = (
    "usage.input_tokens_details.cache_write_tokens"
)


class InitialRuntimeCostV02Error(ValueError):
    pass


def _decimal(value: object, *, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise InitialRuntimeCostV02Error(f"{field_name} must be decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise InitialRuntimeCostV02Error(f"{field_name} invalid decimal") from exc
    if not parsed.is_finite() or parsed < 0:
        raise InitialRuntimeCostV02Error(
            f"{field_name} must be finite and non-negative"
        )
    return parsed


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite() or value < 0:
        raise InitialRuntimeCostV02Error("cost must be finite and non-negative")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _verify_pricing_hash(pricing: Mapping[str, Any]) -> str:
    actual = pricing.get("pricing_hash")
    if not isinstance(actual, str) or len(actual) != 64:
        raise InitialRuntimeCostV02Error("runtime pricing_hash missing")
    expected = canonical_sha256(pricing, exclude_fields=("pricing_hash",))
    if actual != expected:
        raise InitialRuntimeCostV02Error("runtime pricing_hash mismatch")
    return actual


def load_initial_runtime_pricing(
    path: Path = DEFAULT_RUNTIME_PRICING_PATH,
) -> dict[str, Any]:
    try:
        pricing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InitialRuntimeCostV02Error(
            f"unable to read cache-write-aware runtime pricing: {path}"
        ) from exc
    if not isinstance(pricing, dict):
        raise InitialRuntimeCostV02Error("runtime pricing root must be object")
    _verify_pricing_hash(pricing)
    if pricing.get("pricing_version") != EXPECTED_RUNTIME_PRICING_VERSION:
        raise InitialRuntimeCostV02Error("unexpected runtime pricing version")
    if pricing.get("unit") != "USD_PER_1M_TEXT_TOKENS":
        raise InitialRuntimeCostV02Error("runtime pricing unit drift")
    if pricing.get("upper_bound_cached_input_discount_assumed") is not False:
        raise InitialRuntimeCostV02Error(
            "runtime upper bound must not assume cached-input discount"
        )
    if pricing.get("upper_bound_cache_write_all_input_assumed") is not True:
        raise InitialRuntimeCostV02Error(
            "runtime upper bound must assume all input can be cache-write billed"
        )

    cache_write = pricing.get("cache_write")
    if not isinstance(cache_write, Mapping):
        raise InitialRuntimeCostV02Error("runtime cache-write pricing missing")
    if cache_write.get("implicit_prompt_caching_default") is not True:
        raise InitialRuntimeCostV02Error(
            "runtime pricing must model default implicit prompt caching"
        )
    if cache_write.get("billing_basis") != "INPUT_TOKEN_SUBSET":
        raise InitialRuntimeCostV02Error("runtime cache-write billing basis drift")
    if (
        _decimal(
            cache_write.get("input_rate_multiplier"),
            field_name="cache_write.input_rate_multiplier",
        )
        != EXPECTED_CACHE_WRITE_MULTIPLIER
    ):
        raise InitialRuntimeCostV02Error("runtime cache-write multiplier drift")
    if cache_write.get("usage_field") != EXPECTED_CACHE_WRITE_USAGE_FIELD:
        raise InitialRuntimeCostV02Error("runtime cache-write usage field drift")
    for field_name in ("source_url_guidance", "source_url_model"):
        source = cache_write.get(field_name)
        if not isinstance(source, str) or not source.startswith(
            "https://developers.openai.com/"
        ):
            raise InitialRuntimeCostV02Error(
                f"runtime cache-write official source missing: {field_name}"
            )

    models = pricing.get("models")
    if not isinstance(models, Mapping):
        raise InitialRuntimeCostV02Error("runtime pricing models missing")
    for model in ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"):
        record = models.get(model)
        if not isinstance(record, Mapping):
            raise InitialRuntimeCostV02Error(f"runtime pricing missing {model}")
        _decimal(record.get("input"), field_name=f"{model}.input")
        _decimal(record.get("cached_input"), field_name=f"{model}.cached_input")
        _decimal(record.get("output"), field_name=f"{model}.output")
    return pricing


def _rates(
    *,
    model: str,
    input_tokens: int,
    pricing: Mapping[str, Any],
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    models = pricing.get("models")
    if not isinstance(models, Mapping) or not isinstance(models.get(model), Mapping):
        raise InitialRuntimeCostV02Error(
            f"runtime pricing does not cover selected model: {model}"
        )
    record = models[model]
    input_rate = _decimal(record.get("input"), field_name=f"{model}.input")
    cached_rate = _decimal(
        record.get("cached_input"), field_name=f"{model}.cached_input"
    )
    output_rate = _decimal(record.get("output"), field_name=f"{model}.output")

    long_context = pricing.get("long_context")
    if not isinstance(long_context, Mapping):
        raise InitialRuntimeCostV02Error("runtime long-context pricing missing")
    threshold = long_context.get("threshold_input_tokens_exclusive")
    if type(threshold) is not int or threshold <= 0:
        raise InitialRuntimeCostV02Error("runtime long-context threshold invalid")
    if input_tokens > threshold:
        input_multiplier = _decimal(
            long_context.get("input_multiplier"),
            field_name="long_context.input_multiplier",
        )
        output_multiplier = _decimal(
            long_context.get("output_multiplier"),
            field_name="long_context.output_multiplier",
        )
        input_rate *= input_multiplier
        cached_rate *= input_multiplier
        output_rate *= output_multiplier

    cache_write = pricing.get("cache_write")
    if not isinstance(cache_write, Mapping):
        raise InitialRuntimeCostV02Error("runtime cache-write pricing missing")
    cache_write_rate = input_rate * _decimal(
        cache_write.get("input_rate_multiplier"),
        field_name="cache_write.input_rate_multiplier",
    )
    return input_rate, cached_rate, cache_write_rate, output_rate


def runtime_cost_upper_bound_usd(
    *,
    model: str,
    input_tokens_upper_bound: int,
    output_tokens_upper_bound: int,
    call_count: int,
    pricing: Mapping[str, Any],
) -> Decimal:
    if (
        input_tokens_upper_bound < 0
        or output_tokens_upper_bound < 0
        or call_count < 0
    ):
        raise InitialRuntimeCostV02Error(
            "runtime cost upper-bound counters must be non-negative"
        )
    _, _, cache_write_rate, output_rate = _rates(
        model=model,
        input_tokens=input_tokens_upper_bound,
        pricing=pricing,
    )
    per_call = (
        Decimal(input_tokens_upper_bound) * cache_write_rate
        + Decimal(output_tokens_upper_bound) * output_rate
    ) / Decimal(1_000_000)
    return Decimal(call_count) * per_call


def _usage_counts(raw: Mapping[str, Any]) -> tuple[int, int, int, int, int]:
    usage = raw.get("usage")
    if not isinstance(usage, Mapping):
        raise InitialRuntimeCostV02Error(
            "runtime response lacks usage needed for complete cost receipt"
        )
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    input_details = usage.get("input_tokens_details")
    output_details = usage.get("output_tokens_details")
    if type(input_tokens) is not int or input_tokens < 0:
        raise InitialRuntimeCostV02Error("usage.input_tokens invalid")
    if type(output_tokens) is not int or output_tokens < 0:
        raise InitialRuntimeCostV02Error("usage.output_tokens invalid")
    if not isinstance(input_details, Mapping):
        raise InitialRuntimeCostV02Error("usage.input_tokens_details missing")
    cached_tokens = input_details.get("cached_tokens")
    cache_write_tokens = input_details.get("cache_write_tokens")
    if type(cached_tokens) is not int or cached_tokens < 0:
        raise InitialRuntimeCostV02Error("usage.cached_tokens invalid")
    if type(cache_write_tokens) is not int or cache_write_tokens < 0:
        raise InitialRuntimeCostV02Error("usage.cache_write_tokens invalid")
    if cached_tokens + cache_write_tokens > input_tokens:
        raise InitialRuntimeCostV02Error(
            "cached_tokens + cache_write_tokens exceed input_tokens"
        )
    reasoning_tokens = 0
    if isinstance(output_details, Mapping):
        value = output_details.get("reasoning_tokens")
        if value is not None:
            if type(value) is not int or value < 0:
                raise InitialRuntimeCostV02Error("usage.reasoning_tokens invalid")
            reasoning_tokens = value
    return (
        input_tokens,
        cached_tokens,
        cache_write_tokens,
        output_tokens,
        reasoning_tokens,
    )


def actual_cost_usd(
    raw: Mapping[str, Any],
    *,
    model: str,
    pricing: Mapping[str, Any],
) -> Decimal:
    (
        input_tokens,
        cached_tokens,
        cache_write_tokens,
        output_tokens,
        _,
    ) = _usage_counts(raw)
    ordinary_input_tokens = input_tokens - cached_tokens - cache_write_tokens
    input_rate, cached_rate, cache_write_rate, output_rate = _rates(
        model=model,
        input_tokens=input_tokens,
        pricing=pricing,
    )
    return (
        Decimal(ordinary_input_tokens) * input_rate
        + Decimal(cached_tokens) * cached_rate
        + Decimal(cache_write_tokens) * cache_write_rate
        + Decimal(output_tokens) * output_rate
    ) / Decimal(1_000_000)


def build_initial_runtime_cost_preflight(
    runtime_preflight: Mapping[str, Any],
    *,
    pricing: Mapping[str, Any],
) -> dict[str, Any]:
    runtime_hash = verify_initial_runtime_request_preflight(runtime_preflight)
    pricing_hash = _verify_pricing_hash(pricing)
    if pricing.get("pricing_version") != EXPECTED_RUNTIME_PRICING_VERSION:
        raise InitialRuntimeCostV02Error("unexpected runtime pricing version")

    selected = runtime_preflight.get("selected_candidate")
    if not isinstance(selected, Mapping):
        raise InitialRuntimeCostV02Error("selected Initial model missing")
    model = selected.get("model")
    if not isinstance(model, str) or not model:
        raise InitialRuntimeCostV02Error("selected Initial model invalid")

    variants = runtime_preflight.get("selected_request_variants")
    if not isinstance(variants, list) or len(variants) != EXPECTED_LOGICAL_CALLS:
        raise InitialRuntimeCostV02Error("Initial runtime request variants missing")
    byte_counts: list[int] = []
    for item in variants:
        if not isinstance(item, Mapping) or item.get("model") != model:
            raise InitialRuntimeCostV02Error("Initial runtime request/model drift")
        value = item.get("request_body_utf8_bytes")
        if type(value) is not int or value <= 0:
            raise InitialRuntimeCostV02Error("Initial runtime request byte count invalid")
        byte_counts.append(value)

    max_request_body_utf8_bytes = max(byte_counts)
    input_tokens_upper_bound_per_call = max_request_body_utf8_bytes
    output_cap = STAGE_MAX_OUTPUT_TOKENS[CouncilModelStage.INITIAL]
    if runtime_preflight.get("max_output_tokens_per_call") != output_cap:
        raise InitialRuntimeCostV02Error("Initial runtime output-token cap drift")

    cost = runtime_cost_upper_bound_usd(
        model=model,
        input_tokens_upper_bound=input_tokens_upper_bound_per_call,
        output_tokens_upper_bound=output_cap,
        call_count=EXPECTED_LOGICAL_CALLS,
        pricing=pricing,
    )
    cache_write = pricing["cache_write"]
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
            "all input tokens are additionally assumed eligible for the higher "
            "GPT-5.6 cache-write rate"
        ),
        "max_output_tokens_per_call": output_cap,
        "pricing_version": pricing["pricing_version"],
        "pricing_hash": pricing_hash,
        "pricing_as_of_date": pricing["as_of_date"],
        "cache_write_input_rate_multiplier": cache_write[
            "input_rate_multiplier"
        ],
        "cache_write_usage_field": cache_write["usage_field"],
        "implicit_prompt_caching_default": cache_write[
            "implicit_prompt_caching_default"
        ],
        "worst_case_all_input_tokens_as_cache_write_assumed": True,
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
        raise InitialRuntimeCostV02Error("Initial runtime cost artifact_hash missing")
    if actual != canonical_sha256(payload, exclude_fields=("artifact_hash",)):
        raise InitialRuntimeCostV02Error("Initial runtime cost artifact_hash mismatch")
    if payload.get("artifact_version") != INITIAL_RUNTIME_COST_PREFLIGHT_VERSION:
        raise InitialRuntimeCostV02Error("unexpected Initial runtime cost version")
    if payload.get("run_class") != INITIAL_RUNTIME_COST_PREFLIGHT_RUN_CLASS:
        raise InitialRuntimeCostV02Error("unexpected Initial runtime cost run class")
    if payload.get("status") != INITIAL_RUNTIME_COST_PREFLIGHT_STATUS:
        raise InitialRuntimeCostV02Error("Initial runtime cost preflight not ready")
    if payload.get("planned_paid_calls_max") != EXPECTED_LOGICAL_CALLS:
        raise InitialRuntimeCostV02Error("Initial runtime paid-call ceiling drift")
    if payload.get("automatic_repair_calls_authorized") is not False:
        raise InitialRuntimeCostV02Error("Initial runtime must forbid repairs")
    if payload.get("owner_cost_approval_required") is not True:
        raise InitialRuntimeCostV02Error("Initial runtime must require approval")
    if payload.get("cache_write_input_rate_multiplier") != "1.25":
        raise InitialRuntimeCostV02Error("cache-write multiplier missing")
    if payload.get("implicit_prompt_caching_default") is not True:
        raise InitialRuntimeCostV02Error("implicit cache default missing")
    if payload.get("worst_case_all_input_tokens_as_cache_write_assumed") is not True:
        raise InitialRuntimeCostV02Error("cache-write worst-case assumption missing")
    for field in ("model_calls", "provider_reads", "broker_writes", "alpaca_orders"):
        if payload.get(field) != 0:
            raise InitialRuntimeCostV02Error(f"Initial runtime cost {field} must be zero")
    if payload.get("live_money") != "PROHIBITED":
        raise InitialRuntimeCostV02Error("Initial runtime live-money invariant drift")
    return actual


def process_initial_provider_response(
    item: InitialRuntimePlanItem,
    *,
    raw_response: Mapping[str, Any],
    latency_ms: int,
    frozen_at: Any,
    pricing: Mapping[str, Any],
) -> InitialProcessedResponse:
    processed = _process_initial_provider_response_v01(
        item,
        raw_response=raw_response,
        latency_ms=latency_ms,
        frozen_at=frozen_at,
        pricing=pricing,
    )
    corrected_cost = actual_cost_usd(
        raw_response,
        model=item.request.request_payload["model"],
        pricing=pricing,
    )
    return replace(processed, actual_cost_usd=corrected_cost)
