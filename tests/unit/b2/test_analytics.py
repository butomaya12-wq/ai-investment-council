from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from aic.b2.analytics import (
    DailyBar,
    average_daily_dollar_volume,
    build_computed_value,
    max_drawdown,
    require_identical_sessions,
    trailing_return,
)


def test_trailing_return_is_deterministic_decimal() -> None:
    assert trailing_return([Decimal("100"), Decimal("125")]) == Decimal("0.25")


def test_max_drawdown_is_non_positive_ratio() -> None:
    prices = [Decimal("100"), Decimal("120"), Decimal("90"), Decimal("110")]
    assert max_drawdown(prices) == Decimal("-0.25")


def test_average_daily_dollar_volume_uses_decimal_only() -> None:
    bars = [
        DailyBar(session_date=date(2026, 8, 27), close=Decimal("10"), volume=Decimal("100")),
        DailyBar(session_date=date(2026, 8, 28), close=Decimal("12"), volume=Decimal("200")),
    ]
    assert average_daily_dollar_volume(bars) == Decimal("1700")


def test_binary_float_bar_is_rejected() -> None:
    with pytest.raises((TypeError, ValidationError)):
        DailyBar(session_date=date(2026, 8, 28), close=10.5, volume=Decimal("100"))


def test_session_alignment_mismatch_fails() -> None:
    first = [DailyBar(session_date=date(2026, 8, 27), close=Decimal("10"), volume=Decimal("1"))]
    second = [DailyBar(session_date=date(2026, 8, 28), close=Decimal("10"), volume=Decimal("1"))]
    with pytest.raises(ValueError, match="not identical"):
        require_identical_sessions([first, second])


def test_computed_value_factory_binds_input_and_output_hashes() -> None:
    cv = build_computed_value(
        computed_value_id="cv-1",
        metric_id="ADV",
        metric_version="1",
        value=Decimal("1700"),
        unit="USD_PER_DAY",
        input_refs=("bar-1", "bar-2"),
        input_payload={"bars": ["bar-1", "bar-2"]},
        algorithm_id="ADV_CLOSE_X_VOLUME_MEAN",
        algorithm_version="1",
        parameters_ref="policy-1",
        calculated_at=datetime(2026, 8, 28, 15, 0, tzinfo=UTC),
        rounding_rule="NONE",
    )
    assert len(cv.input_hash) == 64
    assert len(cv.output_hash) == 64
