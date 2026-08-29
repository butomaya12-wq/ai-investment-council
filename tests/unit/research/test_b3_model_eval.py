from decimal import Decimal

from aic.domain.canonical import canonical_sha256
from aic.research.model_eval import (
    EXPECTED_CASE_IDS,
    CaseRun,
    PricingRates,
    aggregate_candidate,
    build_eval_cases,
    estimate_call_cost,
    load_pricing_authority,
    select_from_candidate_runs,
)
from aic.research.model_policy import MODEL_CANDIDATE_LADDER, ModelSelectionStatus
from aic.research.planner import PlannerInputEnvelope, build_planner_request
from aic.research.runtime import RUNTIME_VERSION, ResponsesCallResult, ResponsesUsage
from aic.research.synthesize import SynthesisInputEnvelope, build_synthesis_request


MANDATE_VERSION = "ALPACA_COMPETITION_V1_2026_08_29"


def _call(*, input_tokens=1000, cached_tokens=200, output_tokens=300):
    output_text = "{}"
    return ResponsesCallResult(
        runtime_version=RUNTIME_VERSION,
        response_id="resp_eval_test",
        requested_model="gpt-5.6-terra",
        effective_model="gpt-5.6-terra",
        output_text=output_text,
        output_hash=canonical_sha256({"output_text": output_text}),
        usage=ResponsesUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            reasoning_tokens=50,
        ),
        latency_ms=100,
    )


def _case_run(case_id: str, *, passed: bool, critical: bool = False, cost="0.01"):
    payload = {
        "case_id": case_id,
        "name": case_id,
        "stage": "SYNTHESIS",
        "critical_safety": critical,
        "passed": passed,
        "findings": [],
        "response_ids": ["resp"],
        "requested_model": "model",
        "effective_models": ["model"],
        "model_calls": 1,
        "repair_attempts": 0,
        "latency_ms": 10,
        "input_tokens": 100,
        "cached_tokens": 0,
        "output_tokens": 50,
        "reasoning_tokens": 0,
        "estimated_cost_usd": cost,
        "output_hashes": ["a" * 64],
    }
    return CaseRun(
        case_id=case_id,
        name=case_id,
        stage="SYNTHESIS",
        critical_safety=critical,
        passed=passed,
        findings=(),
        response_ids=("resp",),
        requested_model="model",
        effective_models=("model",),
        model_calls=1,
        repair_attempts=0,
        latency_ms=10,
        input_tokens=100,
        cached_tokens=0,
        output_tokens=50,
        reasoning_tokens=0,
        estimated_cost_usd=Decimal(cost),
        output_hashes=("a" * 64,),
        result_hash=canonical_sha256(payload),
    )


def test_pricing_authority_is_hash_bound_and_covers_frozen_models():
    pricing = load_pricing_authority()
    assert pricing.pricing_hash == "2f835825cc3a13b10699720718824f5c15d325c5cba2141933b34ea74f32a430"
    assert set(pricing.rates) == {"gpt-5.6-terra", "gpt-5.6-sol"}
    assert pricing.rates["gpt-5.6-terra"].input_per_million == Decimal("2.00")
    assert pricing.rates["gpt-5.6-sol"].output_per_million == Decimal("20.00")


def test_cost_uses_cached_rate_without_double_counting_reasoning_tokens():
    cost = estimate_call_cost(
        _call(),
        rates=PricingRates(
            input_per_million=Decimal("2"),
            cached_input_per_million=Decimal("0.2"),
            output_per_million=Decimal("12"),
        ),
    )
    expected = (
        Decimal(800) * 2
        + Decimal(200) * Decimal("0.2")
        + Decimal(300) * 12
    ) / Decimal(1_000_000)
    assert cost == expected


def test_eval_fixture_is_exact_e1_e12_and_uses_production_request_invariants():
    cases = build_eval_cases(MANDATE_VERSION)
    assert tuple(case.case_id for case in cases) == EXPECTED_CASE_IDS
    assert sum(case.stage == "PLANNER" for case in cases) == 4
    assert sum(case.stage == "SYNTHESIS" for case in cases) == 8
    assert sum(case.critical_safety for case in cases) == 7

    model = MODEL_CANDIDATE_LADDER[0]
    for case in cases:
        input_obj = case.build_input(MANDATE_VERSION)
        if isinstance(input_obj, PlannerInputEnvelope):
            request = build_planner_request(model_candidate=model, planner_input=input_obj)
        else:
            assert isinstance(input_obj, SynthesisInputEnvelope)
            request = build_synthesis_request(model_candidate=model, synthesis_input=input_obj)
        assert request.request_payload["store"] is False
        assert request.request_payload["tools"] == []
        assert request.request_payload["parallel_tool_calls"] is False
        assert request.request_payload["text"]["format"]["strict"] is True
        if case.build_permuted_input is not None:
            permuted = case.build_permuted_input(MANDATE_VERSION)
            assert isinstance(permuted, SynthesisInputEnvelope)
            assert canonical_sha256(permuted) != canonical_sha256(input_obj)


def test_full_ladder_selection_uses_real_aggregates_and_cost_rule():
    candidate_runs = []
    costs = ("0.010", "0.020", "0.030")
    critical_cases = {"E3", "E4", "E5", "E6", "E7", "E8", "E10"}
    for candidate, cost in zip(MODEL_CANDIDATE_LADDER, costs, strict=True):
        cases = tuple(
            _case_run(
                case_id,
                passed=True,
                critical=case_id in critical_cases,
                cost=cost,
            )
            for case_id in EXPECTED_CASE_IDS
        )
        candidate_runs.append(aggregate_candidate(candidate, cases))

    selection = select_from_candidate_runs(tuple(candidate_runs))
    assert selection.status is ModelSelectionStatus.SELECTED
    assert selection.selected_candidate is not None
    assert selection.selected_candidate.candidate_key == "M1"


def test_any_required_case_failure_disqualifies_candidate():
    cases = tuple(
        _case_run(
            case_id,
            passed=case_id != "E5",
            critical=case_id == "E5",
            cost="0.001",
        )
        for case_id in EXPECTED_CASE_IDS
    )
    run = aggregate_candidate(MODEL_CANDIDATE_LADDER[0], cases)
    assert run.eval_result.all_required_checks_passed is False
    assert run.eval_result.critical_safety_failures == 1
