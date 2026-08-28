from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Sequence

from pydantic import field_validator

from aic.domain.canonical import canonical_sha256

from .decimal_math import decimal_divide, decimal_multiply, decimal_subtract, decimal_sum
from .models import B2Model, ComputedValue


def _require_decimal(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError("authoritative analytics inputs must use decimal.Decimal")
    if not value.is_finite():
        raise ValueError("non-finite Decimal is forbidden")
    return value


class DailyBar(B2Model):
    session_date: date
    close: Decimal
    volume: Decimal

    @field_validator("close", "volume", mode="before")
    @classmethod
    def _decimal_only(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise TypeError("binary float is forbidden")
        return value

    @field_validator("close")
    @classmethod
    def _positive_close(cls, value: Decimal) -> Decimal:
        _require_decimal(value)
        if value <= 0:
            raise ValueError("close must be positive")
        return value

    @field_validator("volume")
    @classmethod
    def _non_negative_volume(cls, value: Decimal) -> Decimal:
        _require_decimal(value)
        if value < 0:
            raise ValueError("volume must be non-negative")
        return value


def trailing_return(prices: Sequence[Decimal]) -> Decimal:
    if len(prices) < 2:
        raise ValueError("at least two prices are required")
    normalized = tuple(_require_decimal(v) for v in prices)
    if normalized[0] <= 0:
        raise ValueError("initial price must be positive")
    if any(v <= 0 for v in normalized):
        raise ValueError("prices must be positive")
    return decimal_subtract(decimal_divide(normalized[-1], normalized[0]), Decimal("1"))


def max_drawdown(prices: Sequence[Decimal]) -> Decimal:
    if not prices:
        raise ValueError("at least one price is required")
    normalized = tuple(_require_decimal(v) for v in prices)
    if any(v <= 0 for v in normalized):
        raise ValueError("prices must be positive")

    peak = normalized[0]
    worst = Decimal("0")
    for price in normalized:
        if price > peak:
            peak = price
        drawdown = decimal_subtract(decimal_divide(price, peak), Decimal("1"))
        if drawdown < worst:
            worst = drawdown
    return worst


def average_daily_dollar_volume(bars: Sequence[DailyBar]) -> Decimal:
    if not bars:
        raise ValueError("at least one bar is required")
    dates = [bar.session_date for bar in bars]
    if len(set(dates)) != len(dates):
        raise ValueError("duplicate session dates are forbidden")
    total = decimal_sum(decimal_multiply(bar.close, bar.volume) for bar in bars)
    return decimal_divide(total, Decimal(len(bars)))


def require_identical_sessions(series: Sequence[Sequence[DailyBar]]) -> tuple[date, ...]:
    if not series:
        raise ValueError("at least one series is required")
    baseline = tuple(bar.session_date for bar in series[0])
    if not baseline:
        raise ValueError("series must not be empty")
    for candidate in series[1:]:
        sessions = tuple(bar.session_date for bar in candidate)
        if sessions != baseline:
            raise ValueError("candidate/benchmark sessions are not identical")
    return baseline


def build_computed_value(
    *,
    computed_value_id: str,
    metric_id: str,
    metric_version: str,
    value: Decimal,
    unit: str,
    input_refs: Iterable[str],
    input_payload: Any,
    algorithm_id: str,
    algorithm_version: str,
    parameters_ref: str,
    calculated_at,
    rounding_rule: str,
) -> ComputedValue:
    _require_decimal(value)
    return ComputedValue.build(
        computed_value_id=computed_value_id,
        metric_id=metric_id,
        metric_version=metric_version,
        value=value,
        unit=unit,
        input_refs=tuple(input_refs),
        input_hash=canonical_sha256(input_payload),
        algorithm_id=algorithm_id,
        algorithm_version=algorithm_version,
        parameters_ref=parameters_ref,
        calculated_at=calculated_at,
        rounding_rule=rounding_rule,
    )
