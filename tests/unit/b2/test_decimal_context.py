from decimal import Decimal, getcontext

from aic.b2.analytics import trailing_return
from aic.b2.decimal_math import DECIMAL_CONTEXT_ID, decimal_divide
from aic.b2.screening import CandidateScreenInput, MetricDirection, ScreeningPolicy, screen_candidates


def test_decimal_context_id_is_frozen() -> None:
    assert DECIMAL_CONTEXT_ID == "DECIMAL128_34_HALF_EVEN_V1"


def test_global_decimal_precision_does_not_change_division_result() -> None:
    original = getcontext().prec
    try:
        getcontext().prec = 6
        low_precision = decimal_divide(Decimal("1"), Decimal("7"))
        getcontext().prec = 50
        high_precision = decimal_divide(Decimal("1"), Decimal("7"))
    finally:
        getcontext().prec = original
    assert low_precision == high_precision
    assert len(low_precision.as_tuple().digits) == 34


def test_global_decimal_precision_does_not_change_trailing_return() -> None:
    original = getcontext().prec
    try:
        getcontext().prec = 7
        first = trailing_return((Decimal("3"), Decimal("10")))
        getcontext().prec = 60
        second = trailing_return((Decimal("3"), Decimal("10")))
    finally:
        getcontext().prec = original
    assert first == second


def test_screening_score_is_independent_of_global_decimal_precision() -> None:
    policy = ScreeningPolicy(
        policy_version="screen-v1",
        universe_ref="demo",
        required_dimensions=("return", "drawdown"),
        metric_directions={
            "return": MetricDirection.HIGHER_IS_BETTER,
            "drawdown": MetricDirection.LOWER_IS_BETTER,
        },
        weights={"return": Decimal("0.5"), "drawdown": Decimal("0.5")},
        shortlist_size=3,
        final_candidate_count=3,
    )
    candidates = (
        CandidateScreenInput(symbol="AAA", eligibility_proof_id="p1", dimensions={"return": Decimal("1"), "drawdown": Decimal("3")}),
        CandidateScreenInput(symbol="BBB", eligibility_proof_id="p2", dimensions={"return": Decimal("2"), "drawdown": Decimal("2")}),
        CandidateScreenInput(symbol="CCC", eligibility_proof_id="p3", dimensions={"return": Decimal("3"), "drawdown": Decimal("1")}),
    )
    original = getcontext().prec
    try:
        getcontext().prec = 6
        first = screen_candidates(policy=policy, candidates=candidates)
        getcontext().prec = 60
        second = screen_candidates(policy=policy, candidates=candidates)
    finally:
        getcontext().prec = original
    assert first == second
