from __future__ import annotations

from decimal import Decimal

import pytest

from aic.council.initial_runtime_cost_v02 import (
    InitialRuntimeCostV02Error,
    actual_cost_usd,
    build_initial_runtime_cost_preflight,
    load_initial_runtime_pricing,
    runtime_cost_upper_bound_usd,
)
from aic.council.initial_runtime_preflight import (
    INITIAL_RUNTIME_REQUEST_PREFLIGHT_RUN_CLASS,
    INITIAL_RUNTIME_REQUEST_PREFLIGHT_VERSION,
    RUNTIME_REQUEST_PREFLIGHT_STATUS,
)
from aic.council.model_policy import OUTPUT_TOKEN_BUDGET_VERSION
from aic.domain.canonical import canonical_sha256


def _runtime_preflight() -> dict:
    variants = [
        {
            "candidate": candidate,
            "lane": lane,
            "model": "gpt-5.6-terra",
            "request_body_utf8_bytes": 20700,
        }
        for candidate in ("AAA", "BBB", "CCC")
        for lane in ("BULL", "BEAR", "RED_TEAM")
    ]
    artifact = {
        "artifact_version": INITIAL_RUNTIME_REQUEST_PREFLIGHT_VERSION,
        "run_class": INITIAL_RUNTIME_REQUEST_PREFLIGHT_RUN_CLASS,
        "status": RUNTIME_REQUEST_PREFLIGHT_STATUS,
        "code_commit_sha": "a" * 40,
        "source_request_preflight_artifact_hash": "1" * 64,
        "b4_input_freeze_artifact_hash": "2" * 64,
        "b3_reconciliation_artifact_hash": "3" * 64,
        "b2_handoff_hash": "4" * 64,
        "mandate_version": "TEST_MANDATE",
        "selected_model_authority_version": "TEST_AUTHORITY",
        "selected_model_authority_selection_hash": "5" * 64,
        "selected_model_eval_artifact_hash": "6" * 64,
        "selected_candidate": {
            "candidate_key": "L2",
            "stage": "INITIAL",
            "model": "gpt-5.6-terra",
            "reasoning_effort": "low",
            "ladder_position": 2,
        },
        "candidate_order": ["AAA", "BBB", "CCC"],
        "logical_call_count": 9,
        "planned_paid_calls_max": 9,
        "automatic_repair_calls_authorized": False,
        "output_token_budget_version": OUTPUT_TOKEN_BUDGET_VERSION,
        "max_output_tokens_per_call": 4096,
        "selected_request_variants": variants,
        "request_manifest_hash": canonical_sha256(
            {"selected_request_variants": variants}
        ),
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def _raw_usage(*, input_tokens: int, cached: int, cache_write: int, output: int) -> dict:
    return {
        "usage": {
            "input_tokens": input_tokens,
            "input_tokens_details": {
                "cached_tokens": cached,
                "cache_write_tokens": cache_write,
            },
            "output_tokens": output,
            "output_tokens_details": {"reasoning_tokens": 0},
        }
    }


def test_runtime_pricing_authority_binds_cache_write_contract() -> None:
    pricing = load_initial_runtime_pricing()
    assert pricing["pricing_hash"] == (
        "13b67bf92f56b2962694f463850e0a0e289fc08f0c4a3d3cafe8eb928d0ee336"
    )
    assert pricing["cache_write"]["implicit_prompt_caching_default"] is True
    assert pricing["cache_write"]["input_rate_multiplier"] == "1.25"
    assert pricing["cache_write"]["usage_field"] == (
        "usage.input_tokens_details.cache_write_tokens"
    )


def test_nine_call_upper_bound_prices_all_input_as_cache_write() -> None:
    pricing = load_initial_runtime_pricing()
    cost = runtime_cost_upper_bound_usd(
        model="gpt-5.6-terra",
        input_tokens_upper_bound=20700,
        output_tokens_upper_bound=4096,
        call_count=9,
        pricing=pricing,
    )
    assert cost == Decimal("0.908118")


def test_cost_preflight_uses_cache_write_aware_ceiling() -> None:
    pricing = load_initial_runtime_pricing()
    artifact = build_initial_runtime_cost_preflight(
        _runtime_preflight(),
        pricing=pricing,
    )
    assert artifact["artifact_version"] == (
        "B4_INITIAL_RUNTIME_COST_PREFLIGHT_ARTIFACT_v0_2"
    )
    assert artifact["total_initial_runtime_cost_upper_bound_usd"] == "0.908118"
    assert artifact["cache_write_input_rate_multiplier"] == "1.25"
    assert artifact["implicit_prompt_caching_default"] is True
    assert artifact["worst_case_all_input_tokens_as_cache_write_assumed"] is True
    assert artifact["model_calls"] == 0


def test_actual_cost_separates_ordinary_cached_and_cache_write_tokens() -> None:
    pricing = load_initial_runtime_pricing()
    raw = _raw_usage(
        input_tokens=1000,
        cached=200,
        cache_write=300,
        output=100,
    )
    assert actual_cost_usd(
        raw,
        model="gpt-5.6-terra",
        pricing=pricing,
    ) == Decimal("0.00299")


def test_missing_cache_write_usage_fails_closed() -> None:
    pricing = load_initial_runtime_pricing()
    raw = _raw_usage(
        input_tokens=1000,
        cached=200,
        cache_write=0,
        output=100,
    )
    del raw["usage"]["input_tokens_details"]["cache_write_tokens"]
    with pytest.raises(InitialRuntimeCostV02Error, match="cache_write_tokens"):
        actual_cost_usd(raw, model="gpt-5.6-terra", pricing=pricing)


def test_overlapping_cache_detail_counts_fail_closed() -> None:
    pricing = load_initial_runtime_pricing()
    raw = _raw_usage(
        input_tokens=1000,
        cached=800,
        cache_write=300,
        output=100,
    )
    with pytest.raises(InitialRuntimeCostV02Error, match="exceed input_tokens"):
        actual_cost_usd(raw, model="gpt-5.6-terra", pricing=pricing)
