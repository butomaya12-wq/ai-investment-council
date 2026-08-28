from decimal import Decimal

import pytest

from aic.research.model_policy import (
    API_INVARIANTS,
    MODEL_CANDIDATE_LADDER,
    MODEL_POLICY_VERSION,
    ModelEvalResult,
    ModelSelectionStatus,
    select_model_from_eval,
)


def _result(key, *, passed=True, failures=0, cost="0.01", latency=100, tokens=1000):
    return ModelEvalResult(
        candidate_key=key,
        all_required_checks_passed=passed,
        critical_safety_failures=failures,
        estimated_cost_usd=Decimal(cost),
        latency_ms=latency,
        total_tokens=tokens,
    )


def test_model_ladder_matches_frozen_b3_packet() -> None:
    assert [(row.candidate_key, row.model, row.reasoning_effort) for row in MODEL_CANDIDATE_LADDER] == [
        ("M1", "gpt-5.6-terra", "low"),
        ("M2", "gpt-5.6-terra", "medium"),
        ("M3", "gpt-5.6-sol", "medium"),
    ]


def test_api_invariants_disable_storage_and_all_hosted_tools() -> None:
    assert API_INVARIANTS.api_family == "RESPONSES"
    assert API_INVARIANTS.store is False
    assert API_INVARIANTS.tools_enabled is False
    assert API_INVARIANTS.structured_outputs_required is True
    assert API_INVARIANTS.hosted_web_search_enabled is False
    assert API_INVARIANTS.hosted_mcp_enabled is False
    assert API_INVARIANTS.code_interpreter_enabled is False


def test_lowest_cost_passing_configuration_wins() -> None:
    result = select_model_from_eval((
        _result("M1", passed=False, cost="0.001"),
        _result("M2", cost="0.020", latency=90),
        _result("M3", cost="0.010", latency=300),
    ))
    assert result.status is ModelSelectionStatus.SELECTED
    assert result.selected_candidate is not None
    assert result.selected_candidate.candidate_key == "M3"
    assert result.model_policy_version == MODEL_POLICY_VERSION


def test_tie_breaks_by_latency_then_tokens() -> None:
    result = select_model_from_eval((
        _result("M1", cost="0.01", latency=100, tokens=900),
        _result("M2", cost="0.01", latency=90, tokens=1200),
        _result("M3", cost="0.01", latency=90, tokens=1000),
    ))
    assert result.selected_candidate is not None
    assert result.selected_candidate.candidate_key == "M3"


def test_any_critical_safety_failure_disqualifies_configuration() -> None:
    result = select_model_from_eval((
        _result("M1", failures=1, cost="0.001"),
        _result("M2", cost="0.01"),
        _result("M3", passed=False, cost="0.02"),
    ))
    assert result.selected_candidate is not None
    assert result.selected_candidate.candidate_key == "M2"


def test_none_pass_is_blocked_without_tool_or_autonomy_expansion() -> None:
    result = select_model_from_eval((
        _result("M1", passed=False),
        _result("M2", failures=1),
        _result("M3", passed=False),
    ))
    assert result.status is ModelSelectionStatus.BLOCKED
    assert result.selected_candidate is None


def test_partial_ladder_eval_is_rejected() -> None:
    with pytest.raises(ValueError, match="full frozen"):
        select_model_from_eval((_result("M1"), _result("M2")))


def test_binary_float_cost_is_rejected() -> None:
    with pytest.raises(TypeError):
        ModelEvalResult(
            candidate_key="M1",
            all_required_checks_passed=True,
            critical_safety_failures=0,
            estimated_cost_usd=0.01,
            latency_ms=100,
            total_tokens=1000,
        )
