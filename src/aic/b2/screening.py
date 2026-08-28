from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any, Mapping, Self

from pydantic import field_validator, model_validator

from .decimal_math import decimal_divide, decimal_multiply, decimal_subtract, decimal_sum
from .models import (
    B2Model,
    ComparisonStatus,
    DeepComparisonResult,
    InstrumentType,
)


class MetricDirection(StrEnum):
    HIGHER_IS_BETTER = "HIGHER_IS_BETTER"
    LOWER_IS_BETTER = "LOWER_IS_BETTER"


class ScreeningStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INSUFFICIENT_ELIGIBLE = "INSUFFICIENT_ELIGIBLE"
    POLICY_STOP = "POLICY_STOP"
    DATA_INCOMPLETE = "DATA_INCOMPLETE"


def _reject_float(value: Any) -> Any:
    if isinstance(value, float):
        raise TypeError("binary float is forbidden for screening policy/inputs")
    return value


class ScreeningPolicy(B2Model):
    policy_version: str
    universe_ref: str
    required_dimensions: tuple[str, ...]
    metric_directions: Mapping[str, MetricDirection]
    normalization_method: str = "MIN_MAX_V1"
    weights: Mapping[str, Decimal] | None = None
    missing_value_rule: str = "DATA_INCOMPLETE"
    shortlist_size: int = 5
    final_candidate_count: int = 3

    @field_validator("weights", mode="before")
    @classmethod
    def _weights_no_float(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise TypeError("weights must be a mapping")
        return {str(k): _reject_float(v) for k, v in value.items()}

    @model_validator(mode="after")
    def _validate_policy(self) -> Self:
        if not self.required_dimensions:
            raise ValueError("required_dimensions must not be empty")
        if len(set(self.required_dimensions)) != len(self.required_dimensions):
            raise ValueError("required_dimensions must be unique")
        if set(self.metric_directions) != set(self.required_dimensions):
            raise ValueError("metric_directions must exactly cover required_dimensions")
        if self.normalization_method != "MIN_MAX_V1":
            raise ValueError("unsupported normalization_method")
        if self.missing_value_rule != "DATA_INCOMPLETE":
            raise ValueError("unsupported missing_value_rule")
        if self.shortlist_size < self.final_candidate_count:
            raise ValueError("shortlist_size cannot be below final_candidate_count")
        if self.final_candidate_count != 3:
            raise ValueError("hackathon V1 requires exactly three final candidates")
        if self.weights is not None:
            if set(self.weights) != set(self.required_dimensions):
                raise ValueError("weights must exactly cover required_dimensions")
            total = Decimal("0")
            for dimension, weight in self.weights.items():
                if not isinstance(weight, Decimal):
                    raise TypeError(f"weight {dimension} must be Decimal")
                if not weight.is_finite() or weight < 0:
                    raise ValueError("weights must be finite and non-negative")
                total = decimal_sum((total, weight))
            if total <= 0:
                raise ValueError("at least one screening weight must be positive")
        return self


class CandidateScreenInput(B2Model):
    symbol: str
    eligibility_proof_id: str
    dimensions: Mapping[str, Decimal]

    @field_validator("dimensions", mode="before")
    @classmethod
    def _dimensions_no_float(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            raise TypeError("dimensions must be a mapping")
        return {str(k): _reject_float(v) for k, v in value.items()}

    @model_validator(mode="after")
    def _validate_dimensions(self) -> Self:
        for name, value in self.dimensions.items():
            if not isinstance(value, Decimal):
                raise TypeError(f"dimension {name} must be Decimal")
            if not value.is_finite():
                raise ValueError(f"dimension {name} must be finite")
        return self


class RankedCandidate(B2Model):
    symbol: str
    eligibility_proof_id: str
    score: Decimal
    normalized_dimensions: Mapping[str, Decimal]


class ShortlistResult(B2Model):
    screening_policy_version: str
    status: ScreeningStatus
    ranked_candidates: tuple[RankedCandidate, ...] = ()
    shortlist_symbols: tuple[str, ...] = ()
    final_candidate_symbols: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()


def _normalize_dimension(
    *,
    values: Mapping[str, Decimal],
    direction: MetricDirection,
) -> dict[str, Decimal]:
    low = min(values.values())
    high = max(values.values())
    if high == low:
        return {symbol: Decimal("0") for symbol in values}
    span = decimal_subtract(high, low)
    if direction is MetricDirection.HIGHER_IS_BETTER:
        return {
            symbol: decimal_divide(decimal_subtract(value, low), span)
            for symbol, value in values.items()
        }
    return {
        symbol: decimal_divide(decimal_subtract(high, value), span)
        for symbol, value in values.items()
    }


def screen_candidates(
    *,
    policy: ScreeningPolicy,
    candidates: tuple[CandidateScreenInput, ...],
) -> ShortlistResult:
    if policy.weights is None:
        return ShortlistResult(
            screening_policy_version=policy.policy_version,
            status=ScreeningStatus.POLICY_STOP,
            reason_codes=("MISSING_OWNER_APPROVED_WEIGHTS",),
        )

    if len(candidates) < policy.final_candidate_count:
        return ShortlistResult(
            screening_policy_version=policy.policy_version,
            status=ScreeningStatus.INSUFFICIENT_ELIGIBLE,
            reason_codes=("INSUFFICIENT_ELIGIBLE_CANDIDATES",),
        )

    if len({candidate.symbol for candidate in candidates}) != len(candidates):
        raise ValueError("candidate symbols must be unique")

    required = set(policy.required_dimensions)
    for candidate in candidates:
        if set(candidate.dimensions) != required:
            return ShortlistResult(
                screening_policy_version=policy.policy_version,
                status=ScreeningStatus.DATA_INCOMPLETE,
                reason_codes=(f"DATA_INCOMPLETE:{candidate.symbol}",),
            )

    normalized_by_dimension: dict[str, dict[str, Decimal]] = {}
    for dimension in policy.required_dimensions:
        raw_values = {
            candidate.symbol: candidate.dimensions[dimension]
            for candidate in candidates
        }
        normalized_by_dimension[dimension] = _normalize_dimension(
            values=raw_values,
            direction=policy.metric_directions[dimension],
        )

    ranked: list[RankedCandidate] = []
    for candidate in candidates:
        normalized = {
            dimension: normalized_by_dimension[dimension][candidate.symbol]
            for dimension in policy.required_dimensions
        }
        score = decimal_sum(
            decimal_multiply(normalized[dimension], policy.weights[dimension])
            for dimension in policy.required_dimensions
        )
        ranked.append(
            RankedCandidate(
                symbol=candidate.symbol,
                eligibility_proof_id=candidate.eligibility_proof_id,
                score=score,
                normalized_dimensions=normalized,
            )
        )

    ranked.sort(key=lambda row: (-row.score, row.symbol))
    shortlist = tuple(row.symbol for row in ranked[: policy.shortlist_size])
    final = shortlist[: policy.final_candidate_count]
    status = (
        ScreeningStatus.COMPLETE
        if len(final) == policy.final_candidate_count
        else ScreeningStatus.INSUFFICIENT_ELIGIBLE
    )
    return ShortlistResult(
        screening_policy_version=policy.policy_version,
        status=status,
        ranked_candidates=tuple(ranked),
        shortlist_symbols=shortlist,
        final_candidate_symbols=final,
    )


def build_deep_comparison_from_shortlist(
    *,
    comparison_id: str,
    snapshot_id: str,
    mandate_version: str,
    comparison_dimension_version: str,
    shortlist: ShortlistResult,
    dimension_ids: tuple[str, ...],
) -> DeepComparisonResult:
    if shortlist.status is not ScreeningStatus.COMPLETE:
        raise ValueError("deep comparison requires COMPLETE shortlist")
    if len(shortlist.final_candidate_symbols) != 3:
        raise ValueError("deep comparison requires exactly three final candidates")

    by_symbol = {row.symbol: row for row in shortlist.ranked_candidates}
    final = shortlist.final_candidate_symbols
    proof_ids = tuple(by_symbol[symbol].eligibility_proof_id for symbol in final)
    return DeepComparisonResult(
        comparison_id=comparison_id,
        snapshot_id=snapshot_id,
        mandate_version=mandate_version,
        comparison_dimension_version=comparison_dimension_version,
        candidate_symbols=final,
        eligibility_proof_ids=proof_ids,
        all_candidates_us_listed=True,
        all_candidates_instrument_type=InstrumentType.OPERATING_COMPANY_COMMON_STOCK,
        dimension_ids=dimension_ids,
        comparison_completeness=ComparisonStatus.COMPLETE,
    )
