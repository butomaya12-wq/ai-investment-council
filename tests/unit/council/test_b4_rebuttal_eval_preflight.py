from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from aic.council.initial_runtime_cost_v02 import load_initial_runtime_pricing
from aic.council.rebuttal_eval_preflight import (
    EXPECTED_EVAL_PLAN_HASH,
    EXPECTED_INITIAL_FREEZE_HASH,
    EXPECTED_PRICING_HASH,
    REBUTTAL_EVAL_COST_PREFLIGHT_STATUS,
    REBUTTAL_EVAL_REQUEST_PREFLIGHT_STATUS,
    build_rebuttal_eval_cases,
    build_rebuttal_eval_cost_preflight,
    build_rebuttal_eval_request_preflight,
    verify_rebuttal_eval_cost_preflight,
    verify_rebuttal_eval_request_preflight,
)
from aic.council.rebuttal_preflight import REBUTTAL_SOURCE_PREFLIGHT_STATUS
from aic.domain.canonical import canonical_sha256


HEAD = "c" * 40


def _source_preflight() -> dict:
    artifact = {
        "status": REBUTTAL_SOURCE_PREFLIGHT_STATUS,
        "code_commit_sha": HEAD,
        "initial_council_freeze_artifact_hash": EXPECTED_INITIAL_FREEZE_HASH,
        "eval_plan_hash": EXPECTED_EVAL_PLAN_HASH,
        "eval_paid_call_count_max": 12,
        "paid_eval_authorized": False,
        "request_manifest_hash": "d" * 64,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def test_rebuttal_eval_cases_match_frozen_stage_plan_and_safety_surface() -> None:
    cases = build_rebuttal_eval_cases()
    assert [case.case_id for case in cases] == ["E4", "E8", "E13", "E16"]
    assert [case.critical_safety for case in cases] == [True, True, True, False]
    by_id = {case.case_id: case for case in cases}
    assert by_id["E4"].required_conflict_ref == "E4_BLOCKING_CONFLICT"
    assert by_id["E8"].required_safe_source_ref == "E8_SAFE_SIGNAL"
    assert by_id["E13"].required_unknown_refs == ("E13_MATERIAL_RESEARCH_GAP",)
    assert all(by_id["E16"].required_decisive_opposing_by_lane.values())
    assert len(by_id["E16"].bundle.allowed_material_claim_ids) == 31


def test_rebuttal_eval_request_preflight_is_exact_12_call_zero_dispatch_manifest() -> None:
    artifact = build_rebuttal_eval_request_preflight(
        code_commit_sha=HEAD,
        source_preflight=_source_preflight(),
    )
    assert artifact["status"] == REBUTTAL_EVAL_REQUEST_PREFLIGHT_STATUS
    assert verify_rebuttal_eval_request_preflight(artifact) == artifact["artifact_hash"]
    assert artifact["candidate_keys"] == ["R1", "R2", "R3"]
    assert artifact["case_ids"] == ["E4", "E8", "E13", "E16"]
    assert artifact["planned_paid_calls_max"] == 12
    assert len(artifact["request_variants"]) == 12
    assert [
        (row["candidate_key"], row["case_id"])
        for row in artifact["request_variants"]
    ] == [
        (candidate_key, case_id)
        for candidate_key in ("R1", "R2", "R3")
        for case_id in ("E4", "E8", "E13", "E16")
    ]
    assert all(row["max_output_tokens"] == 6144 for row in artifact["request_variants"])
    assert all(row["request_body_utf8_bytes"] > 0 for row in artifact["request_variants"])
    assert artifact["max_request_body_utf8_bytes"] < 272000
    assert artifact["automatic_repair_calls_authorized"] is False
    assert artifact["model_calls"] == 0
    assert artifact["provider_reads"] == 0
    assert artifact["broker_writes"] == 0
    assert artifact["alpaca_orders"] == 0
    assert artifact["live_money"] == "PROHIBITED"
    assert artifact["paid_eval_authorized"] is False
    assert artifact["production_rebuttal_authorized"] is False
    assert artifact["judge_authorized"] is False
    assert artifact["rerun_authorized"] is False


def test_rebuttal_eval_cost_preflight_prices_each_exact_request_and_stays_bounded() -> None:
    request = build_rebuttal_eval_request_preflight(
        code_commit_sha=HEAD,
        source_preflight=_source_preflight(),
    )
    pricing = load_initial_runtime_pricing()
    artifact = build_rebuttal_eval_cost_preflight(request, pricing=pricing)
    assert artifact["status"] == REBUTTAL_EVAL_COST_PREFLIGHT_STATUS
    assert verify_rebuttal_eval_cost_preflight(artifact) == artifact["artifact_hash"]
    assert artifact["pricing_hash"] == EXPECTED_PRICING_HASH
    assert artifact["planned_paid_calls_max"] == 12
    assert len(artifact["per_call_cost_upper_bounds"]) == 12
    assert artifact["cache_write_input_rate_multiplier"] == "1.25"
    assert artifact["worst_case_all_input_tokens_as_cache_write_assumed"] is True
    assert artifact["cached_input_discount_assumed_for_upper_bound"] is False
    ceiling = Decimal(artifact["total_rebuttal_eval_cost_upper_bound_usd"])
    assert ceiling > 0
    assert ceiling < Decimal("10")
    assert artifact["owner_cost_approval_required"] is True
    assert artifact["model_calls"] == 0
    assert artifact["provider_reads"] == 0
    assert artifact["broker_writes"] == 0
    assert artifact["alpaca_orders"] == 0
    assert artifact["live_money"] == "PROHIBITED"
    assert artifact["paid_eval_authorized"] is False
    assert artifact["production_rebuttal_authorized"] is False
    assert artifact["judge_authorized"] is False
    assert artifact["rerun_authorized"] is False


def test_rebuttal_eval_preflight_scripts_are_zero_call_surfaces() -> None:
    for path in (
        Path("scripts/b4_rebuttal_eval_request_preflight_v01.py"),
        Path("scripts/b4_rebuttal_eval_cost_preflight_v01.py"),
    ):
        text = path.read_text(encoding="utf-8")
        assert "OPENAI_API_KEY" not in text
        assert "StdlibResponsesTransport" not in text
        assert "--execute" not in text
        assert "broker" in text.lower() or "cost" in text.lower()
