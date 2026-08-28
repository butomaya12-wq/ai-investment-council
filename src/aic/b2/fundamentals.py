from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .analytics import build_computed_value
from .decimal_math import DECIMAL_CONTEXT_ID, decimal_divide, decimal_subtract
from .models import ComputedValue
from .providers.sec_facts import SecCompanyFact


class FundamentalCompatibilityError(ValueError):
    pass


def _qualified(fact: SecCompanyFact) -> str:
    return f"{fact.taxonomy}:{fact.concept}"


def require_compatible_growth_pair(
    current: SecCompanyFact,
    prior: SecCompanyFact,
) -> None:
    if current.period_start is None or prior.period_start is None:
        raise FundamentalCompatibilityError("growth requires duration facts")
    if _qualified(current) != _qualified(prior):
        raise FundamentalCompatibilityError("growth facts must use the same taxonomy/concept")
    if current.unit != prior.unit:
        raise FundamentalCompatibilityError("growth facts must use the same unit")
    if current.form != prior.form:
        raise FundamentalCompatibilityError("growth facts must use the same form")
    if current.fiscal_period is None or current.fiscal_period != prior.fiscal_period:
        raise FundamentalCompatibilityError("growth facts must use the same fiscal period")
    if current.fiscal_year is None or prior.fiscal_year is None:
        raise FundamentalCompatibilityError("growth facts require fiscal years")
    if current.fiscal_year != prior.fiscal_year + 1:
        raise FundamentalCompatibilityError("growth facts require consecutive fiscal years")
    if current.period_end <= prior.period_end:
        raise FundamentalCompatibilityError("current period must end after prior period")


def period_growth(current: SecCompanyFact, prior: SecCompanyFact) -> Decimal:
    require_compatible_growth_pair(current, prior)
    if prior.value == 0:
        raise FundamentalCompatibilityError("growth denominator must not be zero")
    return decimal_subtract(decimal_divide(current.value, prior.value), Decimal("1"))


def require_same_period_ratio(
    numerator: SecCompanyFact,
    denominator: SecCompanyFact,
) -> None:
    if numerator.period_start is None or denominator.period_start is None:
        raise FundamentalCompatibilityError("ratio requires duration facts")
    if numerator.unit != denominator.unit:
        raise FundamentalCompatibilityError("ratio facts must use the same unit")
    if numerator.period_start != denominator.period_start or numerator.period_end != denominator.period_end:
        raise FundamentalCompatibilityError("ratio facts must cover the same period")
    if numerator.form != denominator.form:
        raise FundamentalCompatibilityError("ratio facts must use the same form")
    if numerator.accession_no != denominator.accession_no:
        raise FundamentalCompatibilityError("ratio facts must come from the same filing accession")
    if numerator.fiscal_year != denominator.fiscal_year or numerator.fiscal_period != denominator.fiscal_period:
        raise FundamentalCompatibilityError("ratio facts must share fiscal-year/period identity")


def same_period_ratio(
    numerator: SecCompanyFact,
    denominator: SecCompanyFact,
) -> Decimal:
    require_same_period_ratio(numerator, denominator)
    if denominator.value == 0:
        raise FundamentalCompatibilityError("ratio denominator must not be zero")
    return decimal_divide(numerator.value, denominator.value)


def build_growth_computed_value(
    *,
    computed_value_id: str,
    metric_id: str,
    metric_version: str,
    current: SecCompanyFact,
    prior: SecCompanyFact,
    calculated_at: datetime,
    parameters_ref: str,
) -> ComputedValue:
    value = period_growth(current, prior)
    return build_computed_value(
        computed_value_id=computed_value_id,
        metric_id=metric_id,
        metric_version=metric_version,
        value=value,
        unit="ratio",
        input_refs=(current.fact_id, prior.fact_id),
        input_payload={
            "current_fact_id": current.fact_id,
            "prior_fact_id": prior.fact_id,
            "current_value": str(current.value),
            "prior_value": str(prior.value),
            "current_period_end": current.period_end.isoformat(),
            "prior_period_end": prior.period_end.isoformat(),
        },
        algorithm_id="SEC_PERIOD_GROWTH",
        algorithm_version="V1",
        parameters_ref=parameters_ref,
        calculated_at=calculated_at,
        rounding_rule=DECIMAL_CONTEXT_ID,
    )


def build_ratio_computed_value(
    *,
    computed_value_id: str,
    metric_id: str,
    metric_version: str,
    numerator: SecCompanyFact,
    denominator: SecCompanyFact,
    calculated_at: datetime,
    parameters_ref: str,
) -> ComputedValue:
    value = same_period_ratio(numerator, denominator)
    return build_computed_value(
        computed_value_id=computed_value_id,
        metric_id=metric_id,
        metric_version=metric_version,
        value=value,
        unit="ratio",
        input_refs=(numerator.fact_id, denominator.fact_id),
        input_payload={
            "numerator_fact_id": numerator.fact_id,
            "denominator_fact_id": denominator.fact_id,
            "numerator_value": str(numerator.value),
            "denominator_value": str(denominator.value),
            "period_start": numerator.period_start.isoformat(),
            "period_end": numerator.period_end.isoformat(),
        },
        algorithm_id="SEC_SAME_PERIOD_RATIO",
        algorithm_version="V1",
        parameters_ref=parameters_ref,
        calculated_at=calculated_at,
        rounding_rule=DECIMAL_CONTEXT_ID,
    )
