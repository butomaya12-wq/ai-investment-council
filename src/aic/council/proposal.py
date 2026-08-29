from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import field_validator, model_validator

from aic.domain.canonical import canonical_sha256

from .models import (
    B4Model,
    CouncilClaimType,
    CouncilInputBundle,
    CouncilLane,
    ProposedCouncilClaim,
    RoleBoundaryStatus,
)


class InitialCouncilOpinionProposal(B4Model):
    """Strict model-facing initial opinion DTO from P-B4 prompt/schema v0.2.

    Fields whose historical names end in ``_claim_ids`` contain response-local
    ``claim_local_ref`` values until the application promotion step.  The model
    never assigns canonical MATERIAL_CLAIM_V1 identity.
    """

    opinion_id: str
    candidate_id: str
    lane: CouncilLane
    council_input_bundle_hash: str
    candidate_packet_hash: str
    mandate_version: str
    council_policy_version: str
    model_policy_version: str
    model_run_ref: str
    proposed_claims: tuple[ProposedCouncilClaim, ...]
    primary_claim_ids: tuple[str, ...]
    critical_assumption_claim_ids: tuple[str, ...]
    falsifier_claim_ids: tuple[str, ...]
    material_unknown_refs: tuple[str, ...]
    material_conflict_refs: tuple[str, ...]
    research_reopen_required: bool
    research_reopen_reason_codes: tuple[str, ...]
    role_boundary_status: RoleBoundaryStatus

    @field_validator(
        "opinion_id",
        "candidate_id",
        "council_input_bundle_hash",
        "candidate_packet_hash",
        "mandate_version",
        "council_policy_version",
        "model_policy_version",
        "model_run_ref",
    )
    @classmethod
    def _non_empty_text(cls, value: str, info) -> str:
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError(f"{info.field_name} must be a non-empty trimmed string")
        return value

    @field_validator(
        "primary_claim_ids",
        "critical_assumption_claim_ids",
        "falsifier_claim_ids",
        "material_unknown_refs",
        "material_conflict_refs",
        "research_reopen_reason_codes",
    )
    @classmethod
    def _unique_refs(cls, values: tuple[str, ...], info) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError(f"{info.field_name} must not contain duplicates")
        if any(not value or value != value.strip() for value in values):
            raise ValueError(f"{info.field_name} must contain trimmed non-empty refs")
        return values

    @model_validator(mode="after")
    def _local_ref_graph(self) -> Self:
        local_refs = tuple(claim.claim_local_ref for claim in self.proposed_claims)
        if len(set(local_refs)) != len(local_refs):
            raise ValueError("proposed claim_local_ref values must be unique within one response")
        if any(claim.candidate_id != self.candidate_id for claim in self.proposed_claims):
            raise ValueError("proposed claim candidate_id must match opinion candidate_id")
        if any(claim.lane != self.lane for claim in self.proposed_claims):
            raise ValueError("proposed claim lane must match opinion lane")
        allowed = set(local_refs)
        for field_name in (
            "primary_claim_ids",
            "critical_assumption_claim_ids",
            "falsifier_claim_ids",
        ):
            refs = set(getattr(self, field_name))
            if not refs.issubset(allowed):
                raise ValueError(f"{field_name} must reference response-local claim_local_ref values")
        claim_by_ref = {claim.claim_local_ref: claim for claim in self.proposed_claims}
        if any(
            claim_by_ref[ref].claim_type != CouncilClaimType.ASSUMPTION
            for ref in self.critical_assumption_claim_ids
        ):
            raise ValueError("critical_assumption_claim_ids must reference ASSUMPTION proposals")
        if any(
            claim_by_ref[ref].claim_type != CouncilClaimType.FALSIFIER
            for ref in self.falsifier_claim_ids
        ):
            raise ValueError("falsifier_claim_ids must reference FALSIFIER proposals")
        if self.research_reopen_required and not self.research_reopen_reason_codes:
            raise ValueError("research reopen requires at least one reason code")
        if not self.research_reopen_required and self.research_reopen_reason_codes:
            raise ValueError("research reopen reason codes require research_reopen_required=true")
        return self


def validate_initial_proposal_lineage(
    proposal: InitialCouncilOpinionProposal,
    *,
    bundle: CouncilInputBundle,
    expected_lane: CouncilLane,
) -> None:
    if expected_lane not in {CouncilLane.BULL, CouncilLane.BEAR, CouncilLane.RED_TEAM}:
        raise ValueError("initial Council opinion lane must be BULL, BEAR, or RED_TEAM")
    if proposal.lane != expected_lane:
        raise ValueError("initial opinion lane does not match scheduled lane")
    if proposal.candidate_id != bundle.candidate_id:
        raise ValueError("initial opinion candidate does not match frozen CouncilInputBundle")
    if proposal.council_input_bundle_hash != bundle.bundle_hash:
        raise ValueError("initial opinion input bundle hash mismatch")
    if proposal.candidate_packet_hash != bundle.candidate_packet_hash:
        raise ValueError("initial opinion CandidatePacket hash mismatch")
    if proposal.mandate_version != bundle.mandate_version:
        raise ValueError("initial opinion mandate_version mismatch")
    if proposal.council_policy_version != bundle.council_policy_version:
        raise ValueError("initial opinion council policy version mismatch")
    if proposal.model_policy_version != bundle.model_policy_version:
        raise ValueError("initial opinion model policy version mismatch")
    if proposal.role_boundary_status != RoleBoundaryStatus.VALID:
        raise ValueError("initial opinion role boundary is invalid")


class CouncilClaimMetadata(B4Model):
    """Non-authoritative wrapper from amendment v0.5 §3 / Manifest M018."""

    metadata_id: str
    material_claim_id: str
    lane: CouncilLane
    council_claim_type: CouncilClaimType
    opinion_or_judge_ref: str
    metadata_hash: str

    @model_validator(mode="after")
    def _hash(self) -> Self:
        expected = canonical_sha256(self, exclude_fields=("metadata_hash",))
        if self.metadata_hash != expected:
            raise ValueError("CouncilClaimMetadata metadata_hash mismatch")
        return self

    @classmethod
    def from_unhashed(
        cls,
        *,
        material_claim_id: str,
        lane: CouncilLane,
        council_claim_type: CouncilClaimType,
        opinion_or_judge_ref: str,
    ) -> "CouncilClaimMetadata":
        seed = canonical_sha256(
            [material_claim_id, lane.value, council_claim_type.value, opinion_or_judge_ref]
        )
        metadata_id = f"B4_CLAIM_METADATA_{seed[:24]}"
        provisional = cls.model_construct(
            metadata_id=metadata_id,
            material_claim_id=material_claim_id,
            lane=lane,
            council_claim_type=council_claim_type,
            opinion_or_judge_ref=opinion_or_judge_ref,
            metadata_hash="0" * 64,
        )
        return cls(
            metadata_id=metadata_id,
            material_claim_id=material_claim_id,
            lane=lane,
            council_claim_type=council_claim_type,
            opinion_or_judge_ref=opinion_or_judge_ref,
            metadata_hash=canonical_sha256(provisional, exclude_fields=("metadata_hash",)),
        )
