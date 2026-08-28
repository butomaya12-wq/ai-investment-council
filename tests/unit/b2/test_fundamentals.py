from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from aic.b2.fundamentals import (
    FundamentalCompatibilityError,
    build_growth_computed_value,
    period_growth,
    same_period_ratio,
)
from aic.b2.providers.sec_facts import SecCompanyFact


def _fact(
    *,
    fact_id: str,
    concept: str,
    value: str,
    fiscal_year: int,
    period_start: date,
    period_end: date,
    accession: str,
) -> SecCompanyFact:
    return SecCompanyFact(
        fact_id=fact_id,
        taxonomy="us-gaap",
        concept=concept,
        unit="USD",
        value=Decimal(value),
        period_start=period_start,
        period_end=period_end,
        filed_at=date(2026, 2, 1),
        accession_no=accession,
        form="10-K",
        fiscal_year=fiscal_year,
        fiscal_period="FY",
    )


def test_period_growth_uses_compatible_consecutive_fiscal_years() -> None:
    prior = _fact(fact_id="p", concept="Revenue", value="100", fiscal_year=2024, period_start=date(2024, 1, 1), period_end=date(2024, 12, 31), accession="a")
    current = _fact(fact_id="c", concept="Revenue", value="110", fiscal_year=2025, period_start=date(2025, 1, 1), period_end=date(2025, 12, 31), accession="b")
    assert period_growth(current, prior) == Decimal("0.1")


def test_growth_rejects_concept_change() -> None:
    prior = _fact(fact_id="p", concept="RevenueOld", value="100", fiscal_year=2024, period_start=date(2024, 1, 1), period_end=date(2024, 12, 31), accession="a")
    current = _fact(fact_id="c", concept="RevenueNew", value="110", fiscal_year=2025, period_start=date(2025, 1, 1), period_end=date(2025, 12, 31), accession="b")
    with pytest.raises(FundamentalCompatibilityError, match="same taxonomy/concept"):
        period_growth(current, prior)


def test_growth_rejects_non_consecutive_fiscal_years() -> None:
    prior = _fact(fact_id="p", concept="Revenue", value="100", fiscal_year=2023, period_start=date(2023, 1, 1), period_end=date(2023, 12, 31), accession="a")
    current = _fact(fact_id="c", concept="Revenue", value="110", fiscal_year=2025, period_start=date(2025, 1, 1), period_end=date(2025, 12, 31), accession="b")
    with pytest.raises(FundamentalCompatibilityError, match="consecutive"):
        period_growth(current, prior)


def test_growth_rejects_zero_denominator() -> None:
    prior = _fact(fact_id="p", concept="Revenue", value="0", fiscal_year=2024, period_start=date(2024, 1, 1), period_end=date(2024, 12, 31), accession="a")
    current = _fact(fact_id="c", concept="Revenue", value="110", fiscal_year=2025, period_start=date(2025, 1, 1), period_end=date(2025, 12, 31), accession="b")
    with pytest.raises(FundamentalCompatibilityError, match="denominator"):
        period_growth(current, prior)


def test_same_period_ratio_requires_same_filing_and_period() -> None:
    revenue = _fact(fact_id="r", concept="Revenue", value="200", fiscal_year=2025, period_start=date(2025, 1, 1), period_end=date(2025, 12, 31), accession="a")
    operating_income = _fact(fact_id="o", concept="OperatingIncomeLoss", value="50", fiscal_year=2025, period_start=date(2025, 1, 1), period_end=date(2025, 12, 31), accession="a")
    assert same_period_ratio(operating_income, revenue) == Decimal("0.25")


def test_same_period_ratio_rejects_different_accession() -> None:
    revenue = _fact(fact_id="r", concept="Revenue", value="200", fiscal_year=2025, period_start=date(2025, 1, 1), period_end=date(2025, 12, 31), accession="a")
    operating_income = _fact(fact_id="o", concept="OperatingIncomeLoss", value="50", fiscal_year=2025, period_start=date(2025, 1, 1), period_end=date(2025, 12, 31), accession="b")
    with pytest.raises(FundamentalCompatibilityError, match="same filing accession"):
        same_period_ratio(operating_income, revenue)


def test_growth_computed_value_binds_fact_provenance() -> None:
    prior = _fact(fact_id="p", concept="Revenue", value="100", fiscal_year=2024, period_start=date(2024, 1, 1), period_end=date(2024, 12, 31), accession="a")
    current = _fact(fact_id="c", concept="Revenue", value="110", fiscal_year=2025, period_start=date(2025, 1, 1), period_end=date(2025, 12, 31), accession="b")
    result = build_growth_computed_value(
        computed_value_id="cv-1",
        metric_id="revenue_growth",
        metric_version="v1",
        current=current,
        prior=prior,
        calculated_at=datetime(2026, 8, 28, 16, tzinfo=UTC),
        parameters_ref="fundamentals-policy-v1",
    )
    assert result.value == Decimal("0.1")
    assert result.input_refs == ("c", "p")
    assert len(result.input_hash) == 64
    assert len(result.output_hash) == 64
