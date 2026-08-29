from __future__ import annotations

import json
from decimal import Decimal

import pytest

from aic.council.eval_cost import (
    B4EvalCostAuthorityError,
    cost_upper_bound_usd,
    load_openai_text_pricing,
    load_stage_eval_plan,
)
from aic.domain.canonical import canonical_sha256


def test_frozen_eval_plan_covers_e1_e16_with_69_max_calls() -> None:
    plan = load_stage_eval_plan()
    assert plan["plan_version"] == "B4_STAGE_EVAL_PLAN_v0_1"
    assert plan["stages"]["INITIAL"]["case_ids"] == [
        "E1", "E2", "E5", "E6", "E7", "E8", "E9", "E13", "E16"
    ]
    assert plan["stages"]["INITIAL"]["paid_call_count_max"] == 36
    assert plan["stages"]["REBUTTAL"]["paid_call_count_max"] == 12
    assert plan["stages"]["JUDGE"]["paid_call_count_max"] == 21
    assert plan["full_eval_paid_call_count_max"] == 69


def test_eval_plan_rejects_ladder_drift_even_with_rehashed_payload(tmp_path) -> None:
    plan = load_stage_eval_plan()
    plan["stages"]["INITIAL"]["candidate_keys"] = ["L1", "L2", "L3", "ESCAPE"]
    plan["plan_hash"] = canonical_sha256(plan, exclude_fields=("plan_hash",))
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(B4EvalCostAuthorityError, match="INITIAL eval candidate ladder drift"):
        load_stage_eval_plan(path)


def test_current_pricing_authority_and_decimal_cost_math() -> None:
    pricing = load_openai_text_pricing()
    assert pricing["pricing_version"] == "OPENAI_TEXT_PRICING_2026_08_29"
    assert pricing["models"]["gpt-5.6-luna"]["input"] == "0.20"
    assert pricing["models"]["gpt-5.6-terra"]["output"] == "12.00"
    assert pricing["models"]["gpt-5.6-sol"]["output"] == "20.00"
    cost = cost_upper_bound_usd(
        model="gpt-5.6-luna",
        input_tokens_upper_bound=1000,
        output_tokens_upper_bound=100,
        call_count=1,
        pricing=pricing,
    )
    assert cost == Decimal("0.00032")


def test_long_context_multiplier_is_applied_to_upper_bound() -> None:
    pricing = load_openai_text_pricing()
    below = cost_upper_bound_usd(
        model="gpt-5.6-sol",
        input_tokens_upper_bound=272000,
        output_tokens_upper_bound=100,
        call_count=1,
        pricing=pricing,
    )
    above = cost_upper_bound_usd(
        model="gpt-5.6-sol",
        input_tokens_upper_bound=272001,
        output_tokens_upper_bound=100,
        call_count=1,
        pricing=pricing,
    )
    assert above > below
