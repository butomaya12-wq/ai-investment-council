from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from aic.domain.canonical import canonical_sha256


class B2Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InstrumentType(StrEnum):
    OPERATING_COMPANY_COMMON_STOCK = "OPERATING_COMPANY_COMMON_STOCK"
    ETF = "ETF"
    FUND = "FUND"
    ADR = "ADR"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class ProofStatus(StrEnum):
    PROVEN = "PROVEN"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"


class SnapshotStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    CONFLICTED = "CONFLICTED"
    STALE = "STALE"
    FAILED = "FAILED"


class ComparisonStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INSUFFICIENT_ELIGIBLE = "INSUFFICIENT_ELIGIBLE"
    POLICY_STOP = "POLICY_STOP"
    DATA_INCOMPLETE = "DATA_INCOMPLETE"


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return value.astimezone(UTC)


def _reject_binary_float(value: Any) -> Any:
    if isinstance(value, float):
        raise TypeError("binary float is forbidden for authoritative numeric values")
    return value


class AssetRecord(B2Model):
    symbol: str
    asset_class: str
    status: str
    tradable: bool
    exchange: str
    name: str | None = None
    fractionable: bool | None = None

    @field_validator("symbol", "asset_class", "status", "exchange")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("non-empty string required")
        return value


class SecurityTypeProof(B2Model):
    proof_id: str
    symbol: str
    instrument_type: InstrumentType
    source_type: str
    source_uri: str
    source_record_ref: str
    as_of: datetime
    retrieved_at: datetime
    snapshot_hash: str
    status: ProofStatus

    _aware_as_of = field_validator("as_of")(_require_aware)
    _aware_retrieved = field_validator("retrieved_at")(_require_aware)


class ProviderReadReceipt(B2Model):
    provider_read_receipt_id: str
    provider: str
    endpoint_class: str
    request_start: datetime
    response_received_at: datetime
    request_parameters_hash: str
    pagination_complete: bool
    raw_payload_hash: str
    record_count: int
    http_status: int | None = None
    error: str | None = None

    _aware_request = field_validator("request_start")(_require_aware)
    _aware_response = field_validator("response_received_at")(_require_aware)

    @field_validator("record_count")
    @classmethod
    def _non_negative_count(cls, value: int) -> int:
        if value < 0:
            raise ValueError("record_count must be non-negative")
        return value


class EvidenceItem(B2Model):
    evidence_id: str
    provider: str
    source_type: str
    source_uri: str
    request_parameters_ref: str
    entity_id: str
    field_or_claim: str
    raw_value_or_record_ref: str
    normalized_value: str | int | bool | Decimal | None = None
    published_at: datetime | None = None
    observed_at: datetime | None = None
    retrieved_at: datetime
    as_of: datetime
    freshness_rule_id: str
    knowable_at_cutoff: bool
    authoritative_for: tuple[str, ...] = ()
    conflict_group: str | None = None
    provider_read_receipt_id: str
    raw_content_hash: str
    normalization_version: str

    @field_validator("normalized_value", mode="before")
    @classmethod
    def _no_float_normalized_value(cls, value: Any) -> Any:
        return _reject_binary_float(value)

    @field_validator("published_at", "observed_at", "retrieved_at", "as_of")
    @classmethod
    def _aware_optional(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_aware(value)


class ComputedValue(B2Model):
    computed_value_id: str
    metric_id: str
    metric_version: str
    value: Decimal
    unit: str
    input_refs: tuple[str, ...]
    input_hash: str
    algorithm_id: str
    algorithm_version: str
    parameters_ref: str
    calculated_at: datetime
    rounding_rule: str
    output_hash: str

    @field_validator("value", mode="before")
    @classmethod
    def _no_float_value(cls, value: Any) -> Any:
        return _reject_binary_float(value)

    _aware_calculated = field_validator("calculated_at")(_require_aware)

    @model_validator(mode="after")
    def _valid_output_hash(self) -> Self:
        expected = canonical_sha256(self, exclude_fields=("output_hash",))
        if self.output_hash != expected:
            raise ValueError("output_hash does not bind the canonical ComputedValue payload")
        return self

    @classmethod
    def build(cls, **payload: Any) -> Self:
        data = dict(payload)
        if "value" in data:
            data["value"] = _reject_binary_float(data["value"])
        data["output_hash"] = canonical_sha256(data)
        return cls(**data)


class SnapshotManifest(B2Model):
    snapshot_id: str
    created_at: datetime
    decision_cutoff: datetime
    mandate_version: str
    screening_policy_version: str
    evidence_policy_version: str
    comparison_dimension_version: str
    provider_receipt_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    computed_value_ids: tuple[str, ...]
    asset_master_as_of: datetime
    market_as_of: datetime
    sec_filing_cutoff: datetime
    portfolio_snapshot_ref: str
    manifest_hash: str
    status: SnapshotStatus

    @field_validator(
        "created_at",
        "decision_cutoff",
        "asset_master_as_of",
        "market_as_of",
        "sec_filing_cutoff",
    )
    @classmethod
    def _aware_times(cls, value: datetime) -> datetime:
        return _require_aware(value)

    @model_validator(mode="after")
    def _valid_manifest_hash(self) -> Self:
        expected = canonical_sha256(self, exclude_fields=("manifest_hash",))
        if self.manifest_hash != expected:
            raise ValueError("manifest_hash does not bind the canonical snapshot payload")
        return self

    @classmethod
    def build(cls, **payload: Any) -> Self:
        data = dict(payload)
        data["manifest_hash"] = canonical_sha256(data)
        return cls(**data)


class DeepComparisonResult(B2Model):
    comparison_id: str
    snapshot_id: str
    mandate_version: str
    comparison_dimension_version: str
    candidate_symbols: tuple[str, str, str]
    eligibility_proof_ids: tuple[str, str, str]
    all_candidates_us_listed: bool
    all_candidates_instrument_type: InstrumentType
    dimension_ids: tuple[str, ...]
    comparison_completeness: ComparisonStatus
    material_gaps: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _exact_three_common_stocks(self) -> Self:
        if len(set(self.candidate_symbols)) != 3:
            raise ValueError("candidate_symbols must contain exactly three unique symbols")
        if len(set(self.eligibility_proof_ids)) != 3:
            raise ValueError("eligibility_proof_ids must contain exactly three unique proofs")
        if not self.all_candidates_us_listed:
            raise ValueError("all three candidates must be US-listed")
        if self.all_candidates_instrument_type is not InstrumentType.OPERATING_COMPANY_COMMON_STOCK:
            raise ValueError("B2 deep comparison is limited to operating-company common stock")
        if not self.dimension_ids:
            raise ValueError("at least one comparison dimension is required")
        return self
