from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from aic.b2.models import (
    ComparisonStatus,
    ComputedValue,
    DeepComparisonResult,
    InstrumentType,
)


def test_computed_value_hash_binds_payload() -> None:
    value = ComputedValue.build(
        computed_value_id="cv-1",
        metric_id="TRAILING_RETURN",
        metric_version="1",
        value=Decimal("0.125"),
        unit="RATIO",
        input_refs=("bar-1", "bar-2"),
        input_hash="abc",
        algorithm_id="TRAILING_RETURN_V1",
        algorithm_version="1",
        parameters_ref="policy-1",
        calculated_at=datetime(2026, 8, 28, 15, 0, tzinfo=UTC),
        rounding_rule="NONE",
    )
    assert len(value.output_hash) == 64

    with pytest.raises(ValidationError):
        ComputedValue(**{**value.model_dump(mode="python"), "output_hash": "0" * 64})


def test_computed_value_rejects_binary_float() -> None:
    with pytest.raises((TypeError, ValidationError)):
        ComputedValue.build(
            computed_value_id="cv-1",
            metric_id="TRAILING_RETURN",
            metric_version="1",
            value=0.125,
            unit="RATIO",
            input_refs=("bar-1",),
            input_hash="abc",
            algorithm_id="TRAILING_RETURN_V1",
            algorithm_version="1",
            parameters_ref="policy-1",
            calculated_at=datetime(2026, 8, 28, 15, 0, tzinfo=UTC),
            rounding_rule="NONE",
        )


def test_deep_comparison_requires_three_unique_common_stocks() -> None:
    result = DeepComparisonResult(
        comparison_id="cmp-1",
        snapshot_id="snap-1",
        mandate_version="m1",
        comparison_dimension_version="d1",
        candidate_symbols=("AAPL", "MSFT", "NVDA"),
        eligibility_proof_ids=("p1", "p2", "p3"),
        all_candidates_us_listed=True,
        all_candidates_instrument_type=InstrumentType.OPERATING_COMPANY_COMMON_STOCK,
        dimension_ids=("return_20d",),
        comparison_completeness=ComparisonStatus.COMPLETE,
    )
    assert result.candidate_symbols == ("AAPL", "MSFT", "NVDA")

    with pytest.raises(ValidationError):
        DeepComparisonResult(
            comparison_id="cmp-2",
            snapshot_id="snap-1",
            mandate_version="m1",
            comparison_dimension_version="d1",
            candidate_symbols=("AAPL", "AAPL", "NVDA"),
            eligibility_proof_ids=("p1", "p2", "p3"),
            all_candidates_us_listed=True,
            all_candidates_instrument_type=InstrumentType.OPERATING_COMPANY_COMMON_STOCK,
            dimension_ids=("return_20d",),
            comparison_completeness=ComparisonStatus.COMPLETE,
        )
