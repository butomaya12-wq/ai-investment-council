from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from aic.domain.canonical import canonical_sha256


class B4Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CouncilLane(StrEnum):
    BULL = "BULL"
    BEAR = "BEAR"
    RED_TEAM = "RED_TEAM"
    JUDGE = "JUDGE"


INITIAL_COUNCIL_LANES = (
    CouncilLane.BULL,
    CouncilLane.BEAR,
    CouncilLane.RED_TEAM,
)


class CouncilClaimType(StrEnum):
    ARGUMENT = "ARGUMENT"
    CHALLENGE = "CHALLENGE"
    ASSUMPTION = "ASSUMPTION"
    FALSIFIER = "FALSIFIER"
    INTEGRITY_FINDING = "INTEGRITY_FINDING"
    DECISION_BASIS = "DECISION_BASIS"


class CouncilClaimKind(StrEnum):
    FACT_RESTATEMENT = "FACT_RESTATEMENT"
    INFERENCE = "INFERENCE"
    PROCESS_FINDING = "PROCESS_FINDING"


class CouncilSupportStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    CONFLICTED = "CONFLICTED"
    INSUFFICIENT = "INSUFFICIENT"


class CouncilMateriality(StrEnum):
    MATERIAL = "MATERIAL"
    SUPPORTING = "SUPPORTING"


class RoleBoundaryStatus(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"


def _non_empty_trimmed(value: str, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise ValueError(f"{field_name} must be a non-empty trimmed string")
    return value


def _unique_refs(values: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    for value in values:
        _non_empty_trimmed(value, field_name=field_name)
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must not contain duplicates")
    return values


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


class ProposedCouncilClaim(B4Model):
    """Model-authorable B4 claim proposal.

    claim_local_ref is response-local only. Canonical MATERIAL_CLAIM_V1 identity is
    application-owned and intentionally absent from this schema.
    """

    claim_local_ref: str
    candidate_id: str
    lane: CouncilLane
    claim_type: CouncilClaimType
    claim_text: str
    source_material_claim_ids: tuple[str, ...]
    computed_value_ids: tuple[str, ...]
    conflict_ids: tuple[str, ...]
    claim_kind: CouncilClaimKind
    support_status: CouncilSupportStatus
    materiality: CouncilMateriality

    @field_validator("claim_local_ref", "candidate_id", "claim_text")
    @classmethod
    def _text(cls, value: str, info) -> str:
        return _non_empty_trimmed(value, field_name=info.field_name)

    @field_validator(
        "source_material_claim_ids",
        "computed_value_ids",
        "conflict_ids",
    )
    @classmethod
    def _refs(cls, value: tuple[str, ...], info) -> tuple[str, ...]:
        return _unique_refs(value, field_name=info.field_name)


class CouncilInputBundle(B4Model):
    """Immutable per-candidate B4 input-freeze authority from P-B4 v0.1."""

    bundle_id: str
    candidate_id: str
    candidate_packet_id: str
    candidate_packet_hash: str
    research_snapshot_id: str
    research_snapshot_hash: str
    b2_snapshot_id: str
    deep_comparison_id: str
    mandate_version: str
    council_policy_version: str
    judge_policy_version: str
    model_policy_version: str
    allowed_material_claim_ids: tuple[str, ...]
    allowed_computed_value_ids: tuple[str, ...]
    allowed_conflict_ids: tuple[str, ...]
    shared_portfolio_context_refs: tuple[str, ...]
    created_at: datetime
    bundle_hash: str

    @field_validator(
        "bundle_id",
        "candidate_id",
        "candidate_packet_id",
        "research_snapshot_id",
        "b2_snapshot_id",
        "deep_comparison_id",
        "mandate_version",
        "council_policy_version",
        "judge_policy_version",
        "model_policy_version",
    )
    @classmethod
    def _ids(cls, value: str, info) -> str:
        return _non_empty_trimmed(value, field_name=info.field_name)

    @field_validator("candidate_packet_hash", "research_snapshot_hash", "bundle_hash")
    @classmethod
    def _hashes(cls, value: str, info) -> str:
        if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
            raise ValueError(f"{info.field_name} must be lowercase SHA-256")
        return value

    @field_validator(
        "allowed_material_claim_ids",
        "allowed_computed_value_ids",
        "allowed_conflict_ids",
        "shared_portfolio_context_refs",
    )
    @classmethod
    def _unique_reference_sets(cls, value: tuple[str, ...], info) -> tuple[str, ...]:
        return _unique_refs(value, field_name=info.field_name)

    @field_validator("created_at")
    @classmethod
    def _created_at(cls, value: datetime) -> datetime:
        return _aware_utc(value, field_name="created_at")

    @model_validator(mode="after")
    def _self_hash(self) -> Self:
        expected = canonical_sha256(self, exclude_fields=("bundle_hash",))
        if self.bundle_hash != expected:
            raise ValueError("CouncilInputBundle bundle_hash mismatch")
        return self

    @classmethod
    def from_unhashed(cls, **values: object) -> "CouncilInputBundle":
        if "bundle_hash" in values:
            raise ValueError("bundle_hash is application-generated")
        provisional = {**values, "bundle_hash": "0" * 64}
        payload = cls.model_construct(**provisional)
        return cls(**values, bundle_hash=canonical_sha256(payload, exclude_fields=("bundle_hash",)))


class CouncilInputFreezeArtifact(B4Model):
    artifact_version: Literal["B4_COUNCIL_INPUT_FREEZE_ARTIFACT_v0_1"]
    run_class: Literal["B4_LOCAL_ZERO_CALL_INPUT_FREEZE"]
    b3_reconciliation_artifact_hash: str
    b2_handoff_hash: str
    mandate_version: str
    candidate_order: tuple[str, str, str]
    bundles: tuple[CouncilInputBundle, CouncilInputBundle, CouncilInputBundle]
    model_calls: Literal[0] = 0
    provider_reads: Literal[0] = 0
    broker_writes: Literal[0] = 0
    alpaca_orders: Literal[0] = 0
    live_money: Literal["PROHIBITED"] = "PROHIBITED"
    artifact_hash: str

    @field_validator("b3_reconciliation_artifact_hash", "b2_handoff_hash", "artifact_hash")
    @classmethod
    def _artifact_hashes(cls, value: str, info) -> str:
        if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
            raise ValueError(f"{info.field_name} must be lowercase SHA-256")
        return value

    @model_validator(mode="after")
    def _freeze_invariants(self) -> Self:
        if tuple(bundle.candidate_id for bundle in self.bundles) != self.candidate_order:
            raise ValueError("Council input freeze candidate order mismatch")
        if len(set(self.candidate_order)) != 3:
            raise ValueError("Council input freeze requires exactly three distinct candidates")
        expected = canonical_sha256(self, exclude_fields=("artifact_hash",))
        if self.artifact_hash != expected:
            raise ValueError("CouncilInputFreezeArtifact artifact_hash mismatch")
        return self
