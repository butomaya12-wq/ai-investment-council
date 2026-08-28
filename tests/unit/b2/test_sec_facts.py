from datetime import UTC, datetime
from decimal import Decimal

import pytest

from aic.b2.providers.sec import SecFilingRecord, SecNormalizationError
from aic.b2.providers.sec_facts import (
    SecFactPeriodType,
    SecFactSelectionPolicy,
    SecFactSelectionStatus,
    normalize_companyfacts,
    select_company_fact_at_cutoff,
)


PRIMARY = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
FALLBACK = "us-gaap:SalesRevenueNet"


def _payload(primary_value=101, fallback_value=100):
    return {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": primary_value,
                                "accn": "0001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-01",
                            }
                        ]
                    }
                },
                "SalesRevenueNet": {
                    "units": {
                        "USD": [
                            {
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": fallback_value,
                                "accn": "0001",
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-02-01",
                            }
                        ]
                    }
                },
            }
        }
    }


def _filing(accepted_at: datetime) -> SecFilingRecord:
    return SecFilingRecord(
        accession_number="0001",
        form="10-K",
        accepted_at=accepted_at,
        filing_date="2026-02-01",
        report_date="2025-12-31",
        primary_document="annual.htm",
    )


def _policy() -> SecFactSelectionPolicy:
    return SecFactSelectionPolicy(
        policy_version="revenue-fy-v1",
        concept_precedence=(PRIMARY, FALLBACK),
        required_unit="USD",
        allowed_forms=("10-K",),
        period_type=SecFactPeriodType.DURATION,
        allowed_fiscal_periods=("FY",),
    )


def test_companyfacts_numeric_boundary_converts_provider_float_to_decimal() -> None:
    facts = normalize_companyfacts(_payload(primary_value=101.25), concept_refs=(PRIMARY,))
    assert facts[0].value == Decimal("101.25")


def test_concept_precedence_selects_primary_over_fallback_for_same_period() -> None:
    facts = normalize_companyfacts(_payload(), concept_refs=(PRIMARY, FALLBACK))
    result = select_company_fact_at_cutoff(
        facts,
        (_filing(datetime(2026, 2, 1, 12, tzinfo=UTC)),),
        policy=_policy(),
        decision_cutoff=datetime(2026, 8, 28, 15, tzinfo=UTC),
    )
    assert result.status is SecFactSelectionStatus.SELECTED
    assert result.selected_fact is not None
    assert result.selected_fact.concept == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert result.selected_fact.value == Decimal("101")


def test_future_acceptance_excludes_fact_even_if_companyfacts_contains_it() -> None:
    facts = normalize_companyfacts(_payload(), concept_refs=(PRIMARY, FALLBACK))
    result = select_company_fact_at_cutoff(
        facts,
        (_filing(datetime(2026, 9, 1, 12, tzinfo=UTC)),),
        policy=_policy(),
        decision_cutoff=datetime(2026, 8, 28, 15, tzinfo=UTC),
    )
    assert result.status is SecFactSelectionStatus.INCOMPLETE
    assert result.reason_codes == ("NO_ELIGIBLE_FACT_AT_CUTOFF",)


def test_missing_accession_binding_is_incomplete_not_guessed() -> None:
    facts = normalize_companyfacts(_payload(), concept_refs=(PRIMARY,))
    result = select_company_fact_at_cutoff(
        facts,
        (),
        policy=_policy(),
        decision_cutoff=datetime(2026, 8, 28, 15, tzinfo=UTC),
    )
    assert result.status is SecFactSelectionStatus.INCOMPLETE


def test_same_top_concept_same_period_with_different_values_is_conflict() -> None:
    payload = _payload()
    rows = payload["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"]["USD"]
    rows.append({**rows[0], "val": 999})
    facts = normalize_companyfacts(payload, concept_refs=(PRIMARY,))
    result = select_company_fact_at_cutoff(
        facts,
        (_filing(datetime(2026, 2, 1, 12, tzinfo=UTC)),),
        policy=_policy(),
        decision_cutoff=datetime(2026, 8, 28, 15, tzinfo=UTC),
    )
    assert result.status is SecFactSelectionStatus.CONFLICT
    assert len(result.conflict_fact_ids) == 2


def test_boolean_numeric_value_fails_closed() -> None:
    with pytest.raises(SecNormalizationError, match="numeric"):
        normalize_companyfacts(_payload(primary_value=True), concept_refs=(PRIMARY,))


def test_policy_requires_explicit_qualified_concept_precedence() -> None:
    with pytest.raises(ValueError, match="taxonomy:concept"):
        SecFactSelectionPolicy(
            policy_version="bad",
            concept_precedence=("Revenue",),
            required_unit="USD",
            allowed_forms=("10-K",),
            period_type=SecFactPeriodType.DURATION,
        )
