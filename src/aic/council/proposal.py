from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

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


# Bounded current-authority repair: prompt v0.2 lists bundle_hash / judge_proposal_hash
# in raw model outputs but gives the model neither canonical serialization nor hash
# authority. Current R0/manifest grant application authority for canonical identity and
# hashes. Therefore raw model DTOs exclude self-hashes; application freezes them only
# after validation. No model-facing semantic field is removed.
MODEL_HASH_OWNERSHIP_NORMALIZATION_VERSION = "B4_MODEL_HASH_OWNERSHIP_NORMALIZATION_v0_1"


class InitialCouncilOpinionProposal(B4Model):
    """Strict model-facing initial opinion DTO from P-B4 prompt/schema v0.2.

    Fields whose historical names end in ``_claim_ids`` contain response-local
    ``claim_local_ref`` values until the application promotion step. The model
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


class RebuttalResponseType(StrEnum):
    CONCEDE = "CONCEDE"
    REBUT = "REBUT"
    PARTIAL = "PARTIAL"
    UNRESOLVED = "UNRESOLVED"


class RebuttalItemDraft(B4Model):
    rebuttal_item_id: str
    responding_lane: Literal[CouncilLane.BULL, CouncilLane.BEAR, CouncilLane.RED_TEAM]
    opposing_finding_ids: tuple[str, ...]
    response_type: RebuttalResponseType
    response_proposed_claims: tuple[ProposedCouncilClaim, ...]
    remaining_uncertainty_refs: tuple[str, ...]

    @field_validator("rebuttal_item_id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError("rebuttal_item_id must be non-empty and trimmed")
        return value

    @field_validator("opposing_finding_ids", "remaining_uncertainty_refs")
    @classmethod
    def _refs(cls, values: tuple[str, ...], info) -> tuple[str, ...]:
        if info.field_name == "opposing_finding_ids" and not values:
            raise ValueError("opposing_finding_ids must be non-empty")
        if len(set(values)) != len(values):
            raise ValueError(f"{info.field_name} must be unique")
        if any(not value or value != value.strip() for value in values):
            raise ValueError(f"{info.field_name} must contain trimmed non-empty refs")
        return values

    @model_validator(mode="after")
    def _claim_lane(self) -> Self:
        if any(claim.lane != self.responding_lane for claim in self.response_proposed_claims):
            raise ValueError("rebuttal proposed claim lane must match responding_lane")
        local_refs = tuple(claim.claim_local_ref for claim in self.response_proposed_claims)
        if len(set(local_refs)) != len(local_refs):
            raise ValueError("rebuttal item claim_local_ref values must be unique")
        return self


class RebuttalBundleDraft(B4Model):
    """Raw model-authored rebuttal output; self-hash is application-owned."""

    rebuttal_bundle_id: str
    candidate_id: str
    council_input_bundle_hash: str
    initial_opinion_ids: tuple[str, ...]
    initial_opinion_hashes: tuple[str, ...]
    items: tuple[RebuttalItemDraft, ...]
    research_reopen_required: bool
    research_reopen_reason_codes: tuple[str, ...]

    @field_validator("rebuttal_bundle_id", "candidate_id", "council_input_bundle_hash")
    @classmethod
    def _identity(cls, value: str, info) -> str:
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError(f"{info.field_name} must be non-empty and trimmed")
        return value

    @field_validator("initial_opinion_ids", "initial_opinion_hashes", "research_reopen_reason_codes")
    @classmethod
    def _unique_lists(cls, values: tuple[str, ...], info) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError(f"{info.field_name} values must be unique")
        if any(not value or value != value.strip() for value in values):
            raise ValueError(f"{info.field_name} must contain trimmed non-empty strings")
        return values

    @model_validator(mode="after")
    def _bundle_contract(self) -> Self:
        if len(self.initial_opinion_ids) != 3 or len(self.initial_opinion_hashes) != 3:
            raise ValueError("rebuttal requires exactly three initial opinion IDs/hashes")
        if len(self.items) != 3:
            raise ValueError("rebuttal must contain exactly three lane items")
        lanes = tuple(item.responding_lane for item in self.items)
        if set(lanes) != {CouncilLane.BULL, CouncilLane.BEAR, CouncilLane.RED_TEAM}:
            raise ValueError("rebuttal must contain Bull, Bear, and Red-Team exactly once")
        local_refs = tuple(
            claim.claim_local_ref
            for item in self.items
            for claim in item.response_proposed_claims
        )
        if len(set(local_refs)) != len(local_refs):
            raise ValueError("claim_local_ref values must be unique across one rebuttal response")
        if any(
            claim.candidate_id != self.candidate_id
            for item in self.items
            for claim in item.response_proposed_claims
        ):
            raise ValueError("rebuttal proposed claims must bind the bundle candidate")
        if self.research_reopen_required and not self.research_reopen_reason_codes:
            raise ValueError("research reopen requires reason codes")
        if not self.research_reopen_required and self.research_reopen_reason_codes:
            raise ValueError("reopen reason codes require research_reopen_required=true")
        return self


class FrozenRebuttalBundle(B4Model):
    draft: RebuttalBundleDraft
    bundle_hash: str

    @model_validator(mode="after")
    def _hash(self) -> Self:
        expected = canonical_sha256(self.draft)
        if self.bundle_hash != expected:
            raise ValueError("rebuttal bundle_hash mismatch")
        return self

    @classmethod
    def from_draft(cls, draft: RebuttalBundleDraft) -> "FrozenRebuttalBundle":
        return cls(draft=draft, bundle_hash=canonical_sha256(draft))


class JudgeOutcome(StrEnum):
    INVEST = "INVEST"
    WATCH = "WATCH"
    ABSTAIN = "ABSTAIN"


class JudgeEvidenceStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"


class JudgeNextDirective(StrEnum):
    PROMOTE_FINAL_DECISION = "PROMOTE_FINAL_DECISION"
    MONITOR = "MONITOR"
    RESEARCH_REOPEN_REQUEST = "RESEARCH_REOPEN_REQUEST"
    STOP = "STOP"


class WhyNotCandidate(B4Model):
    candidate_id: str
    claim_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]

    @model_validator(mode="after")
    def _fields(self) -> Self:
        if not self.candidate_id or self.candidate_id != self.candidate_id.strip():
            raise ValueError("why-not candidate_id must be non-empty and trimmed")
        for field_name in ("claim_ids", "reason_codes"):
            values = getattr(self, field_name)
            if len(set(values)) != len(values):
                raise ValueError(f"{field_name} must be unique")
            if any(not value or value != value.strip() for value in values):
                raise ValueError(f"{field_name} must contain non-empty trimmed refs")
        return self


class DecisionChangeConditionDraft(B4Model):
    condition_id: str
    condition_text: str
    source_or_claim_refs: tuple[str, ...]

    @model_validator(mode="after")
    def _fields(self) -> Self:
        for field_name in ("condition_id", "condition_text"):
            value = getattr(self, field_name)
            if not value or value != value.strip():
                raise ValueError(f"{field_name} must be non-empty and trimmed")
        if len(set(self.source_or_claim_refs)) != len(self.source_or_claim_refs):
            raise ValueError("source_or_claim_refs must be unique")
        if any(not value or value != value.strip() for value in self.source_or_claim_refs):
            raise ValueError("source_or_claim_refs must contain non-empty trimmed refs")
        return self


class JudgeDecisionProposalDraft(B4Model):
    """Raw Judge model DTO. Lifecycle/risk/sizing/approval/execution fields are absent."""

    b4_decision_id: str
    outcome: JudgeOutcome
    primary_candidate_id: str | None
    watch_candidate_ids: tuple[str, ...]
    mandate_version: str
    deep_comparison_id: str
    judge_input_hash: str
    council_policy_version: str
    judge_policy_version: str
    model_policy_version: str
    selected_candidate_basis_claim_ids: tuple[str, ...]
    why_not_other_candidates: tuple[WhyNotCandidate, ...]
    unresolved_dispute_refs: tuple[str, ...]
    material_conflict_refs: tuple[str, ...]
    material_unknown_refs: tuple[str, ...]
    blocking_reason_codes: tuple[str, ...]
    research_reopen_required: bool
    research_reopen_reason_codes: tuple[str, ...]
    what_would_change_decision: tuple[DecisionChangeConditionDraft, ...]
    invalidation_condition_refs: tuple[str, ...]
    evidence_status: JudgeEvidenceStatus
    execution_authority: Literal[False]
    next_directive: JudgeNextDirective
    model_run_ref: str

    @field_validator(
        "b4_decision_id",
        "mandate_version",
        "deep_comparison_id",
        "judge_input_hash",
        "council_policy_version",
        "judge_policy_version",
        "model_policy_version",
        "model_run_ref",
    )
    @classmethod
    def _identity(cls, value: str, info) -> str:
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError(f"{info.field_name} must be non-empty and trimmed")
        return value

    @field_validator(
        "watch_candidate_ids",
        "selected_candidate_basis_claim_ids",
        "unresolved_dispute_refs",
        "material_conflict_refs",
        "material_unknown_refs",
        "blocking_reason_codes",
        "research_reopen_reason_codes",
        "invalidation_condition_refs",
    )
    @classmethod
    def _refs(cls, values: tuple[str, ...], info) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError(f"{info.field_name} values must be unique")
        if any(not value or value != value.strip() for value in values):
            raise ValueError(f"{info.field_name} must contain non-empty trimmed refs")
        return values

    @model_validator(mode="after")
    def _outcome_contract(self) -> Self:
        if self.primary_candidate_id is not None:
            if not self.primary_candidate_id or self.primary_candidate_id != self.primary_candidate_id.strip():
                raise ValueError("primary_candidate_id must be null or non-empty trimmed string")
        why_not_ids = tuple(item.candidate_id for item in self.why_not_other_candidates)
        if len(set(why_not_ids)) != len(why_not_ids):
            raise ValueError("why_not_other_candidates candidate IDs must be unique")
        condition_ids = tuple(item.condition_id for item in self.what_would_change_decision)
        if len(set(condition_ids)) != len(condition_ids):
            raise ValueError("what_would_change_decision condition IDs must be unique")

        if self.outcome == JudgeOutcome.INVEST:
            if self.primary_candidate_id is None:
                raise ValueError("INVEST requires primary_candidate_id")
            if self.next_directive != JudgeNextDirective.PROMOTE_FINAL_DECISION:
                raise ValueError("INVEST requires PROMOTE_FINAL_DECISION")
            if self.research_reopen_required:
                raise ValueError("INVEST forbids research_reopen_required")
            if self.evidence_status == JudgeEvidenceStatus.INSUFFICIENT:
                raise ValueError("INVEST invalid with INSUFFICIENT evidence")
        elif self.outcome == JudgeOutcome.WATCH:
            if self.next_directive not in {
                JudgeNextDirective.MONITOR,
                JudgeNextDirective.RESEARCH_REOPEN_REQUEST,
            }:
                raise ValueError("WATCH requires MONITOR or RESEARCH_REOPEN_REQUEST")
            if not self.what_would_change_decision:
                raise ValueError("WATCH requires what_would_change_decision")
        elif self.outcome == JudgeOutcome.ABSTAIN:
            if self.primary_candidate_id is not None:
                raise ValueError("ABSTAIN requires primary_candidate_id=null")
            if self.next_directive not in {
                JudgeNextDirective.STOP,
                JudgeNextDirective.RESEARCH_REOPEN_REQUEST,
            }:
                raise ValueError("ABSTAIN requires STOP or RESEARCH_REOPEN_REQUEST")

        if self.research_reopen_required and not self.research_reopen_reason_codes:
            raise ValueError("research reopen requires reason codes")
        if not self.research_reopen_required and self.research_reopen_reason_codes:
            raise ValueError("reopen reason codes require research_reopen_required=true")
        return self


class FrozenJudgeDecisionProposal(B4Model):
    draft: JudgeDecisionProposalDraft
    judge_proposal_hash: str

    @model_validator(mode="after")
    def _hash(self) -> Self:
        expected = canonical_sha256(self.draft)
        if self.judge_proposal_hash != expected:
            raise ValueError("judge_proposal_hash mismatch")
        return self

    @classmethod
    def from_draft(cls, draft: JudgeDecisionProposalDraft) -> "FrozenJudgeDecisionProposal":
        return cls(draft=draft, judge_proposal_hash=canonical_sha256(draft))


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
