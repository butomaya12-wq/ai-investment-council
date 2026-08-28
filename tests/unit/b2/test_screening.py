from decimal import Decimal

import pytest
from pydantic import ValidationError

from aic.b2.screening import (
    CandidateScreenInput,
    MetricDirection,
    ScreeningPolicy,
    ScreeningStatus,
    build_deep_comparison_from_shortlist,
    screen_candidates,
)


def _policy(weights=None):
    return ScreeningPolicy(
        policy_version="screen-v1",
        universe_ref="us-operating-common-stock",
        required_dimensions=("return_20d", "max_drawdown"),
        metric_directions={
            "return_20d": MetricDirection.HIGHER_IS_BETTER,
            "max_drawdown": MetricDirection.HIGHER_IS_BETTER,
        },
        weights=weights,
    )


def _candidates():
    return (
        CandidateScreenInput(
            symbol="AAPL",
            eligibility_proof_id="p-aapl",
            dimensions={"return_20d": Decimal("0.10"), "max_drawdown": Decimal("-0.08")},
        ),
        CandidateScreenInput(
            symbol="MSFT",
            eligibility_proof_id="p-msft",
            dimensions={"return_20d": Decimal("0.05"), "max_drawdown": Decimal("-0.04")},
        ),
        CandidateScreenInput(
            symbol="NVDA",
            eligibility_proof_id="p-nvda",
            dimensions={"return_20d": Decimal("0.15"), "max_drawdown": Decimal("-0.12")},
        ),
        CandidateScreenInput(
            symbol="AMZN",
            eligibility_proof_id="p-amzn",
            dimensions={"return_20d": Decimal("0.02"), "max_drawdown": Decimal("-0.03")},
        ),
        CandidateScreenInput(
            symbol="GOOGL",
            eligibility_proof_id="p-googl",
            dimensions={"return_20d": Decimal("0.07"), "max_drawdown": Decimal("-0.05")},
        ),
    )


def test_missing_weights_is_policy_stop_not_builder_default() -> None:
    result = screen_candidates(policy=_policy(), candidates=_candidates())
    assert result.status is ScreeningStatus.POLICY_STOP
    assert result.ranked_candidates == ()
    assert "MISSING_OWNER_APPROVED_WEIGHTS" in result.reason_codes


def test_weighted_ranking_is_deterministic() -> None:
    policy = _policy(
        weights={"return_20d": Decimal("0.7"), "max_drawdown": Decimal("0.3")}
    )
    first = screen_candidates(policy=policy, candidates=_candidates())
    second = screen_candidates(policy=policy, candidates=_candidates())
    assert first == second
    assert first.status is ScreeningStatus.COMPLETE
    assert len(first.shortlist_symbols) == 5
    assert len(first.final_candidate_symbols) == 3


def test_missing_required_dimension_is_data_incomplete() -> None:
    policy = _policy(
        weights={"return_20d": Decimal("0.7"), "max_drawdown": Decimal("0.3")}
    )
    candidates = list(_candidates())
    candidates[0] = CandidateScreenInput(
        symbol="AAPL",
        eligibility_proof_id="p-aapl",
        dimensions={"return_20d": Decimal("0.10")},
    )
    result = screen_candidates(policy=policy, candidates=tuple(candidates))
    assert result.status is ScreeningStatus.DATA_INCOMPLETE
    assert result.ranked_candidates == ()


def test_float_weight_is_rejected() -> None:
    with pytest.raises((TypeError, ValidationError)):
        _policy(weights={"return_20d": 0.7, "max_drawdown": Decimal("0.3")})


def test_deep_comparison_is_exactly_three_from_complete_shortlist() -> None:
    policy = _policy(
        weights={"return_20d": Decimal("0.7"), "max_drawdown": Decimal("0.3")}
    )
    shortlist = screen_candidates(policy=policy, candidates=_candidates())
    comparison = build_deep_comparison_from_shortlist(
        comparison_id="cmp-1",
        snapshot_id="snap-1",
        mandate_version="mandate-1",
        comparison_dimension_version="dims-1",
        shortlist=shortlist,
        dimension_ids=("return_20d", "max_drawdown"),
    )
    assert comparison.candidate_symbols == shortlist.final_candidate_symbols
    assert len(comparison.candidate_symbols) == 3
