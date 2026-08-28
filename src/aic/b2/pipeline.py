from __future__ import annotations

from enum import StrEnum
from typing import Sequence, Self

from pydantic import model_validator

from aic.domain.canonical import canonical_sha256

from .config_loader import APPROVED_SYMBOLS, B2ConfigError, assert_owner_approved_screening_policy
from .eligibility import EligibilityProof
from .models import B2Model, DeepComparisonResult, SnapshotManifest, SnapshotStatus
from .point_in_time import assert_snapshot_point_in_time
from .screening import (
    CandidateScreenInput,
    ScreeningPolicy,
    ScreeningStatus,
    ShortlistResult,
    build_deep_comparison_from_shortlist,
    screen_candidates,
)


class B2RunStatus(StrEnum):
    READY_FOR_B3 = "READY_FOR_B3"
    POLICY_STOP = "POLICY_STOP"
    DATA_INCOMPLETE = "DATA_INCOMPLETE"
    INSUFFICIENT_ELIGIBLE = "INSUFFICIENT_ELIGIBLE"
    BLOCKED_ELIGIBILITY_PROOF = "BLOCKED_ELIGIBILITY_PROOF"
    BLOCKED_EVENT_POLICY = "BLOCKED_EVENT_POLICY"
    BLOCKED_LINEAGE = "BLOCKED_LINEAGE"
    BLOCKED_SNAPSHOT = "BLOCKED_SNAPSHOT"


class B2RunResult(B2Model):
    status: B2RunStatus
    snapshot_id: str
    screening_policy_version: str
    input_hash: str
    shortlist: ShortlistResult | None = None
    deep_comparison: DeepComparisonResult | None = None
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _ready_requires_complete_artifacts(self) -> Self:
        if self.status is B2RunStatus.READY_FOR_B3:
            if self.shortlist is None or self.deep_comparison is None:
                raise ValueError("READY_FOR_B3 requires shortlist and deep comparison")
            if self.shortlist.status is not ScreeningStatus.COMPLETE:
                raise ValueError("READY_FOR_B3 requires COMPLETE shortlist")
        elif self.deep_comparison is not None:
            raise ValueError("non-ready B2 result cannot contain deep comparison")
        return self


def _input_hash(
    *,
    snapshot: SnapshotManifest,
    policy: ScreeningPolicy,
    candidates: Sequence[CandidateScreenInput],
    eligibility_proofs: Sequence[EligibilityProof],
    dimension_ids: tuple[str, ...],
) -> str:
    return canonical_sha256(
        {
            "snapshot": snapshot,
            "policy": policy,
            "candidates": tuple(candidates),
            "eligibility_proofs": tuple(eligibility_proofs),
            "dimension_ids": dimension_ids,
        }
    )


def run_b2_gate(
    *,
    snapshot: SnapshotManifest,
    policy: ScreeningPolicy,
    candidates: tuple[CandidateScreenInput, ...],
    eligibility_proofs: tuple[EligibilityProof, ...],
    comparison_id: str,
    mandate_version: str,
    comparison_dimension_version: str,
    dimension_ids: tuple[str, ...],
) -> B2RunResult:
    input_hash = _input_hash(
        snapshot=snapshot,
        policy=policy,
        candidates=candidates,
        eligibility_proofs=eligibility_proofs,
        dimension_ids=dimension_ids,
    )

    try:
        assert_snapshot_point_in_time(snapshot)
    except ValueError:
        return B2RunResult(
            status=B2RunStatus.BLOCKED_SNAPSHOT,
            snapshot_id=snapshot.snapshot_id,
            screening_policy_version=policy.policy_version,
            input_hash=input_hash,
            reason_codes=("SNAPSHOT_POINT_IN_TIME_INVALID",),
        )

    if snapshot.status is not SnapshotStatus.COMPLETE:
        return B2RunResult(
            status=B2RunStatus.DATA_INCOMPLETE,
            snapshot_id=snapshot.snapshot_id,
            screening_policy_version=policy.policy_version,
            input_hash=input_hash,
            reason_codes=(f"SNAPSHOT_NOT_COMPLETE:{snapshot.status.value}",),
        )

    if (
        snapshot.mandate_version != mandate_version
        or snapshot.screening_policy_version != policy.policy_version
        or snapshot.comparison_dimension_version != comparison_dimension_version
    ):
        return B2RunResult(
            status=B2RunStatus.BLOCKED_LINEAGE,
            snapshot_id=snapshot.snapshot_id,
            screening_policy_version=policy.policy_version,
            input_hash=input_hash,
            reason_codes=("SNAPSHOT_POLICY_OR_MANDATE_LINEAGE_MISMATCH",),
        )

    proof_by_id: dict[str, EligibilityProof] = {}
    policy_exchange_sets: set[tuple[str, ...]] = set()
    for proof in eligibility_proofs:
        if proof.eligibility_proof_id in proof_by_id:
            return B2RunResult(
                status=B2RunStatus.BLOCKED_ELIGIBILITY_PROOF,
                snapshot_id=snapshot.snapshot_id,
                screening_policy_version=policy.policy_version,
                input_hash=input_hash,
                reason_codes=("DUPLICATE_ELIGIBILITY_PROOF_ID",),
            )
        proof_by_id[proof.eligibility_proof_id] = proof
        policy_exchange_sets.add(proof.allowed_exchanges)

    if len(policy_exchange_sets) > 1:
        return B2RunResult(
            status=B2RunStatus.BLOCKED_ELIGIBILITY_PROOF,
            snapshot_id=snapshot.snapshot_id,
            screening_policy_version=policy.policy_version,
            input_hash=input_hash,
            reason_codes=("INCONSISTENT_ALLOWED_EXCHANGES_POLICY",),
        )

    for candidate in candidates:
        proof = proof_by_id.get(candidate.eligibility_proof_id)
        if proof is None:
            return B2RunResult(
                status=B2RunStatus.BLOCKED_ELIGIBILITY_PROOF,
                snapshot_id=snapshot.snapshot_id,
                screening_policy_version=policy.policy_version,
                input_hash=input_hash,
                reason_codes=(f"MISSING_ELIGIBILITY_PROOF:{candidate.symbol}",),
            )
        if proof.asset.symbol != candidate.symbol or not proof.eligible or proof.reason_codes:
            return B2RunResult(
                status=B2RunStatus.BLOCKED_ELIGIBILITY_PROOF,
                snapshot_id=snapshot.snapshot_id,
                screening_policy_version=policy.policy_version,
                input_hash=input_hash,
                reason_codes=(f"INVALID_ELIGIBILITY_PROOF:{candidate.symbol}",),
            )

    shortlist = screen_candidates(policy=policy, candidates=candidates)
    if shortlist.status is ScreeningStatus.POLICY_STOP:
        return B2RunResult(
            status=B2RunStatus.POLICY_STOP,
            snapshot_id=snapshot.snapshot_id,
            screening_policy_version=policy.policy_version,
            input_hash=input_hash,
            shortlist=shortlist,
            reason_codes=shortlist.reason_codes,
        )
    if shortlist.status is ScreeningStatus.DATA_INCOMPLETE:
        return B2RunResult(
            status=B2RunStatus.DATA_INCOMPLETE,
            snapshot_id=snapshot.snapshot_id,
            screening_policy_version=policy.policy_version,
            input_hash=input_hash,
            shortlist=shortlist,
            reason_codes=shortlist.reason_codes,
        )
    if shortlist.status is ScreeningStatus.INSUFFICIENT_ELIGIBLE:
        return B2RunResult(
            status=B2RunStatus.INSUFFICIENT_ELIGIBLE,
            snapshot_id=snapshot.snapshot_id,
            screening_policy_version=policy.policy_version,
            input_hash=input_hash,
            shortlist=shortlist,
            reason_codes=shortlist.reason_codes,
        )

    deep_comparison = build_deep_comparison_from_shortlist(
        comparison_id=comparison_id,
        snapshot_id=snapshot.snapshot_id,
        mandate_version=mandate_version,
        comparison_dimension_version=comparison_dimension_version,
        shortlist=shortlist,
        dimension_ids=dimension_ids,
    )
    return B2RunResult(
        status=B2RunStatus.READY_FOR_B3,
        snapshot_id=snapshot.snapshot_id,
        screening_policy_version=policy.policy_version,
        input_hash=input_hash,
        shortlist=shortlist,
        deep_comparison=deep_comparison,
    )


def run_event_b2_gate(
    *,
    snapshot: SnapshotManifest,
    policy: ScreeningPolicy,
    candidates: tuple[CandidateScreenInput, ...],
    eligibility_proofs: tuple[EligibilityProof, ...],
    comparison_id: str,
    mandate_version: str,
    comparison_dimension_version: str,
    dimension_ids: tuple[str, ...],
) -> B2RunResult:
    input_hash = _input_hash(
        snapshot=snapshot,
        policy=policy,
        candidates=candidates,
        eligibility_proofs=eligibility_proofs,
        dimension_ids=dimension_ids,
    )
    try:
        assert_owner_approved_screening_policy(policy)
    except B2ConfigError:
        return B2RunResult(
            status=B2RunStatus.BLOCKED_EVENT_POLICY,
            snapshot_id=snapshot.snapshot_id,
            screening_policy_version=policy.policy_version,
            input_hash=input_hash,
            reason_codes=("OWNER_APPROVED_SCREENING_POLICY_MISMATCH",),
        )

    candidate_symbols = tuple(candidate.symbol for candidate in candidates)
    if candidate_symbols != APPROVED_SYMBOLS:
        return B2RunResult(
            status=B2RunStatus.BLOCKED_EVENT_POLICY,
            snapshot_id=snapshot.snapshot_id,
            screening_policy_version=policy.policy_version,
            input_hash=input_hash,
            reason_codes=("OWNER_APPROVED_UNIVERSE_MISMATCH",),
        )

    return run_b2_gate(
        snapshot=snapshot,
        policy=policy,
        candidates=candidates,
        eligibility_proofs=eligibility_proofs,
        comparison_id=comparison_id,
        mandate_version=mandate_version,
        comparison_dimension_version=comparison_dimension_version,
        dimension_ids=dimension_ids,
    )
