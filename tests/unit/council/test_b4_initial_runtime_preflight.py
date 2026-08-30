from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import pytest

from aic.council.eval_cost import load_openai_text_pricing
from aic.council.initial_runtime_cost import (
    InitialRuntimeCostError,
    build_initial_runtime_cost_preflight,
    verify_initial_runtime_cost_preflight,
)
from aic.council.initial_runtime_preflight import (
    InitialRuntimePreflightError,
    build_initial_runtime_request_preflight,
    verify_initial_runtime_request_preflight,
)
from aic.council.model_policy import (
    INITIAL_MODEL_LADDER,
    OUTPUT_TOKEN_BUDGET_VERSION,
    STAGE_MAX_OUTPUT_TOKENS,
    CouncilModelStage,
)
from aic.council.model_selection import load_initial_selected_model_authority
from aic.domain.canonical import canonical_sha256


CANDIDATES = ("AAA", "BBB", "CCC")
LANES = (
    ("BULL", "BULL_INITIAL"),
    ("BEAR", "BEAR_INITIAL"),
    ("RED_TEAM", "RED_TEAM_INITIAL"),
)
COMMIT = "a" * 40


def _source_request_preflight() -> dict:
    variants = []
    logical_calls = []
    for candidate_index, candidate in enumerate(CANDIDATES, start=1):
        model_input_hash = canonical_sha256(
            {"candidate": candidate, "index": candidate_index}
        )
        for lane, stage in LANES:
            request_hashes = []
            schema_hashes = []
            semantic_hashes = []
            for variant_index, model_candidate in enumerate(
                INITIAL_MODEL_LADDER, start=1
            ):
                request_hash = canonical_sha256(
                    {
                        "candidate": candidate,
                        "lane": lane,
                        "candidate_key": model_candidate.candidate_key,
                    }
                )
                schema_hash = canonical_sha256(
                    {
                        "schema": candidate,
                        "lane": lane,
                        "candidate_key": model_candidate.candidate_key,
                    }
                )
                semantic_hash = canonical_sha256(
                    {"semantic_schema": candidate, "lane": lane}
                )
                variants.append(
                    {
                        "logical_call": f"{candidate}:{lane}",
                        "candidate": candidate,
                        "lane": lane,
                        "stage": stage,
                        "model_candidate_key": model_candidate.candidate_key,
                        "model": model_candidate.model,
                        "reasoning_effort": model_candidate.reasoning_effort,
                        "model_run_ref": (
                            f"B4_INITIAL_{candidate}_{lane}_"
                            f"{model_candidate.candidate_key}_{model_input_hash[:12]}"
                        ),
                        "model_input_hash": model_input_hash,
                        "request_hash": request_hash,
                        "schema_hash": schema_hash,
                        "semantic_schema_hash": semantic_hash,
                        "request_body_utf8_bytes": (
                            10_000 + candidate_index * 100 + variant_index
                        ),
                        "max_output_tokens": STAGE_MAX_OUTPUT_TOKENS[
                            CouncilModelStage.INITIAL
                        ],
                        "store": False,
                        "tools": [],
                        "parallel_tool_calls": False,
                        "truncation": "disabled",
                        "strict_json_schema": True,
                    }
                )
                request_hashes.append(request_hash)
                schema_hashes.append(schema_hash)
                semantic_hashes.append(semantic_hash)
            logical_calls.append(
                {
                    "logical_call": f"{candidate}:{lane}",
                    "candidate": candidate,
                    "lane": lane,
                    "model_input_hash": model_input_hash,
                    "request_variant_count": len(INITIAL_MODEL_LADDER),
                    "request_hashes": request_hashes,
                    "schema_hashes": schema_hashes,
                    "semantic_schema_hashes": list(dict.fromkeys(semantic_hashes)),
                    "allowed_schema_variation": "model_run_ref.const only",
                }
            )

    payload = {
        "artifact_version": "B4_INITIAL_REQUEST_PREFLIGHT_ARTIFACT_v0_1",
        "run_class": "B4_LOCAL_ZERO_CALL_REAL_INITIAL_REQUEST_PREFLIGHT",
        "status": "READY_FOR_INITIAL_STAGE_MODEL_EVAL_COST_PREFLIGHT",
        "b4_input_freeze_artifact_hash": "1" * 64,
        "b3_reconciliation_artifact_hash": "2" * 64,
        "b2_handoff_hash": "3" * 64,
        "mandate_version": "TEST_MANDATE_V1",
        "claim_promotion_normalization_version": "TEST",
        "claim_promotion_normalization_hash": "4" * 64,
        "semantic_schema_normalization_version": "TEST",
        "semantic_schema_allowed_variation": "model_run_ref.const only",
        "output_token_budget_version": OUTPUT_TOKEN_BUDGET_VERSION,
        "initial_max_output_tokens": STAGE_MAX_OUTPUT_TOKENS[
            CouncilModelStage.INITIAL
        ],
        "candidate_order": list(CANDIDATES),
        "model_inputs": [],
        "logical_call_count": 9,
        "request_variant_count": 36,
        "initial_model_ladder_count": len(INITIAL_MODEL_LADDER),
        "logical_calls": logical_calls,
        "request_variants": variants,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    payload["artifact_hash"] = canonical_sha256(payload)
    return payload


def test_runtime_preflight_selects_exact_nine_frozen_l2_requests() -> None:
    authority = load_initial_selected_model_authority()
    source = _source_request_preflight()

    artifact = build_initial_runtime_request_preflight(
        source,
        authority=authority,
        code_commit_sha=COMMIT,
    )

    assert verify_initial_runtime_request_preflight(artifact) == artifact["artifact_hash"]
    assert artifact["selected_candidate"]["candidate_key"] == "L2"
    assert artifact["selected_candidate"]["model"] == "gpt-5.6-terra"
    assert artifact["selected_candidate"]["reasoning_effort"] == "low"
    assert artifact["planned_paid_calls_max"] == 9
    assert artifact["automatic_repair_calls_authorized"] is False
    assert len(artifact["selected_request_variants"]) == 9
    assert {
        item["model_candidate_key"] for item in artifact["selected_request_variants"]
    } == {"L2"}
    assert artifact["model_calls"] == 0
    assert artifact["broker_writes"] == 0
    assert artifact["live_money"] == "PROHIBITED"


def test_runtime_preflight_rejects_source_request_order_drift() -> None:
    authority = load_initial_selected_model_authority()
    source = _source_request_preflight()
    source["request_variants"][0], source["request_variants"][1] = (
        source["request_variants"][1],
        source["request_variants"][0],
    )
    source["artifact_hash"] = canonical_sha256(
        source, exclude_fields=("artifact_hash",)
    )

    with pytest.raises(InitialRuntimePreflightError, match="order drift"):
        build_initial_runtime_request_preflight(
            source,
            authority=authority,
            code_commit_sha=COMMIT,
        )


def test_runtime_preflight_rejects_selected_request_model_tamper() -> None:
    authority = load_initial_selected_model_authority()
    source = _source_request_preflight()
    for item in source["request_variants"]:
        if item["model_candidate_key"] == "L2":
            item["model"] = "gpt-5.6-sol"
            break
    source["artifact_hash"] = canonical_sha256(
        source, exclude_fields=("artifact_hash",)
    )

    with pytest.raises(InitialRuntimePreflightError, match="model configuration drift"):
        build_initial_runtime_request_preflight(
            source,
            authority=authority,
            code_commit_sha=COMMIT,
        )


def test_runtime_preflight_hash_binds_no_auto_repair_rule() -> None:
    authority = load_initial_selected_model_authority()
    artifact = build_initial_runtime_request_preflight(
        _source_request_preflight(),
        authority=authority,
        code_commit_sha=COMMIT,
    )
    artifact["automatic_repair_calls_authorized"] = True
    artifact["artifact_hash"] = canonical_sha256(
        artifact, exclude_fields=("artifact_hash",)
    )

    with pytest.raises(InitialRuntimePreflightError, match="auto-authorize"):
        verify_initial_runtime_request_preflight(artifact)


def test_runtime_cost_preflight_is_selected_model_only_and_zero_call() -> None:
    authority = load_initial_selected_model_authority()
    runtime = build_initial_runtime_request_preflight(
        _source_request_preflight(),
        authority=authority,
        code_commit_sha=COMMIT,
    )
    pricing = load_openai_text_pricing()

    cost = build_initial_runtime_cost_preflight(runtime, pricing=pricing)

    assert verify_initial_runtime_cost_preflight(cost) == cost["artifact_hash"]
    assert cost["selected_candidate"]["candidate_key"] == "L2"
    assert cost["selected_candidate"]["model"] == "gpt-5.6-terra"
    assert cost["planned_paid_calls_max"] == 9
    assert cost["automatic_repair_calls_authorized"] is False
    assert Decimal(cost["total_initial_runtime_cost_upper_bound_usd"]) > 0
    assert cost["model_calls"] == 0
    assert cost["provider_reads"] == 0
    assert cost["broker_writes"] == 0
    assert cost["live_money"] == "PROHIBITED"


def test_runtime_cost_preflight_rejects_paid_call_count_tamper() -> None:
    authority = load_initial_selected_model_authority()
    runtime = build_initial_runtime_request_preflight(
        _source_request_preflight(),
        authority=authority,
        code_commit_sha=COMMIT,
    )
    cost = build_initial_runtime_cost_preflight(
        runtime,
        pricing=load_openai_text_pricing(),
    )
    changed = deepcopy(cost)
    changed["planned_paid_calls_max"] = 10
    changed["artifact_hash"] = canonical_sha256(
        changed, exclude_fields=("artifact_hash",)
    )

    with pytest.raises(InitialRuntimeCostError, match="paid-call ceiling drift"):
        verify_initial_runtime_cost_preflight(changed)
