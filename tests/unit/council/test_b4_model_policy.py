from decimal import Decimal

from aic.council.model_policy import (
    INITIAL_MODEL_LADDER,
    JUDGE_MODEL_LADDER,
    REBUTTAL_MODEL_LADDER,
    CouncilModelStage,
    StageModelEvalResult,
    StageModelSelectionStatus,
    select_stage_model_from_eval,
)


def _result(key: str, *, passed=True, failures=0, cost="0.01", latency=100, tokens=1000):
    return StageModelEvalResult(
        candidate_key=key,
        all_required_checks_passed=passed,
        critical_safety_failures=failures,
        estimated_cost_usd=Decimal(cost),
        latency_ms=latency,
        total_tokens=tokens,
    )


def test_stage_ladders_match_current_b4_contract() -> None:
    assert [(x.candidate_key, x.model, x.reasoning_effort) for x in INITIAL_MODEL_LADDER] == [
        ("L1", "gpt-5.6-luna", "medium"),
        ("L2", "gpt-5.6-terra", "low"),
        ("L3", "gpt-5.6-terra", "medium"),
        ("L4", "gpt-5.6-sol", "medium"),
    ]
    assert [(x.candidate_key, x.model, x.reasoning_effort) for x in REBUTTAL_MODEL_LADDER] == [
        ("R1", "gpt-5.6-terra", "low"),
        ("R2", "gpt-5.6-terra", "medium"),
        ("R3", "gpt-5.6-sol", "medium"),
    ]
    assert [(x.candidate_key, x.model, x.reasoning_effort) for x in JUDGE_MODEL_LADDER] == [
        ("J1", "gpt-5.6-terra", "medium"),
        ("J2", "gpt-5.6-sol", "medium"),
        ("J3", "gpt-5.6-sol", "high"),
    ]


def test_stage_selection_is_eval_driven_cost_then_latency_then_tokens() -> None:
    result = select_stage_model_from_eval(
        CouncilModelStage.JUDGE,
        (
            _result("J1", cost="0.10", latency=80),
            _result("J2", cost="0.05", latency=300),
            _result("J3", passed=False, cost="0.01"),
        ),
    )
    assert result.status is StageModelSelectionStatus.SELECTED
    assert result.selected_candidate is not None
    assert result.selected_candidate.candidate_key == "J2"


def test_critical_failure_disqualifies_and_no_pass_blocks() -> None:
    selected = select_stage_model_from_eval(
        CouncilModelStage.REBUTTAL,
        (
            _result("R1", failures=1, cost="0.001"),
            _result("R2", cost="0.01"),
            _result("R3", passed=False, cost="0.02"),
        ),
    )
    assert selected.selected_candidate is not None
    assert selected.selected_candidate.candidate_key == "R2"

    blocked = select_stage_model_from_eval(
        CouncilModelStage.REBUTTAL,
        (
            _result("R1", passed=False),
            _result("R2", failures=1),
            _result("R3", passed=False),
        ),
    )
    assert blocked.status is StageModelSelectionStatus.BLOCKED
    assert blocked.selected_candidate is None


def test_partial_ladder_is_rejected() -> None:
    try:
        select_stage_model_from_eval(
            CouncilModelStage.INITIAL,
            (_result("L1"), _result("L2"), _result("L3")),
        )
    except ValueError as exc:
        assert "full frozen" in str(exc)
    else:
        raise AssertionError("partial model ladder must fail closed")
