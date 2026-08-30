from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from aic.domain.contracts import MATERIAL_CLAIM_V1

from .models import CouncilClaimType, CouncilInputBundle, CouncilLane
from .promotion import (
    CouncilPromotionError,
    _canonical_claim_from_proposal,
    _validate_claim_refs,
    _validate_generated_text,
    _validate_numeric_provenance,
    _validate_support_semantics,
)
from .proposal import CouncilClaimMetadata, FrozenRebuttalBundle, RebuttalBundleDraft
from .rebuttal_schema_repair_v01 import REBUTTAL_ALLOWED_CLAIM_TYPES


REBUTTAL_PROMOTION_VERSION = "B4_REBUTTAL_PROMOTION_v0_1"
REBUTTAL_VALIDATION_CONTRACT_VERSION = "B4_REBUTTAL_VALIDATION_CONTRACT_v0_1"


class RebuttalPromotionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RebuttalPromotionResult:
    frozen_rebuttal_bundle: FrozenRebuttalBundle
    material_claims: tuple[object, ...]
    claim_metadata: tuple[CouncilClaimMetadata, ...]
    local_ref_to_claim_id: Mapping[str, str]
    validator_results: tuple[Mapping[str, str], ...]


def _validator(check_id: str, detail: str) -> Mapping[str, str]:
    return {"check_id": check_id, "status": "PASS", "detail": detail}


def _canonical_source_claims(model_input: Mapping[str, Any]) -> dict[str, object]:
    nested = model_input.get("candidate_model_input")
    candidate_input = nested if isinstance(nested, Mapping) else model_input
    raw_claims = candidate_input.get("material_claims")
    if not isinstance(raw_claims, (list, tuple)):
        raise RebuttalPromotionError("Rebuttal candidate model input material_claims missing")
    result: dict[str, object] = {}
    for raw in raw_claims:
        if not isinstance(raw, Mapping):
            raise RebuttalPromotionError("Rebuttal source MaterialClaim must be an object")
        try:
            claim = MATERIAL_CLAIM_V1.model_validate(dict(raw))
        except Exception as exc:
            raise RebuttalPromotionError(f"Rebuttal source MaterialClaim invalid: {exc}") from exc
        if claim.claim_id in result:
            raise RebuttalPromotionError("duplicate Rebuttal source MaterialClaim id")
        result[claim.claim_id] = claim
    return result


def _computed_values(model_input: Mapping[str, Any]) -> dict[str, str]:
    nested = model_input.get("candidate_model_input")
    candidate_input = nested if isinstance(nested, Mapping) else model_input
    raw_values = candidate_input.get("computed_values")
    if not isinstance(raw_values, (list, tuple)):
        raise RebuttalPromotionError("Rebuttal candidate model input computed_values missing")
    result: dict[str, str] = {}
    for raw in raw_values:
        if not isinstance(raw, Mapping):
            raise RebuttalPromotionError("Rebuttal ComputedValue view must be an object")
        ref = raw.get("computed_value_id")
        value = raw.get("value")
        if not isinstance(ref, str) or not ref or ref != ref.strip():
            raise RebuttalPromotionError("Rebuttal computed_value_id invalid")
        if not isinstance(value, str) or not value or value != value.strip():
            raise RebuttalPromotionError("Rebuttal computed value invalid")
        if ref in result:
            raise RebuttalPromotionError("duplicate Rebuttal computed_value_id")
        result[ref] = value
    return result


def _initial_claim_lane_map(
    initial_records: Sequence[Mapping[str, Any]],
    *,
    candidate_id: str,
) -> dict[str, CouncilLane]:
    if len(initial_records) != 3:
        raise RebuttalPromotionError("Rebuttal requires exactly three frozen Initial records")
    result: dict[str, CouncilLane] = {}
    observed_lanes: list[CouncilLane] = []
    for raw in initial_records:
        if raw.get("candidate_id") != candidate_id:
            raise RebuttalPromotionError("Rebuttal Initial record candidate mismatch")
        lane_raw = raw.get("lane")
        try:
            lane = CouncilLane(lane_raw)
        except Exception as exc:
            raise RebuttalPromotionError("Rebuttal Initial record lane invalid") from exc
        if lane not in {CouncilLane.BULL, CouncilLane.BEAR, CouncilLane.RED_TEAM}:
            raise RebuttalPromotionError("Rebuttal Initial record lane is not a Council lane")
        observed_lanes.append(lane)
        claims = raw.get("material_claims")
        if not isinstance(claims, list) or not claims:
            raise RebuttalPromotionError("Rebuttal Initial record promoted claims missing")
        for claim_raw in claims:
            if not isinstance(claim_raw, Mapping):
                raise RebuttalPromotionError("Rebuttal Initial promoted claim must be an object")
            try:
                claim = MATERIAL_CLAIM_V1.model_validate(dict(claim_raw))
            except Exception as exc:
                raise RebuttalPromotionError(f"Rebuttal Initial promoted claim invalid: {exc}") from exc
            if claim.candidate_id != candidate_id:
                raise RebuttalPromotionError("Rebuttal Initial promoted claim candidate mismatch")
            if claim.claim_id in result:
                raise RebuttalPromotionError("duplicate promoted Initial claim id across lanes")
            result[claim.claim_id] = lane
    if tuple(observed_lanes) != (CouncilLane.BULL, CouncilLane.BEAR, CouncilLane.RED_TEAM):
        raise RebuttalPromotionError("Rebuttal Initial records must be ordered Bull/Bear/Red-Team")
    return result


def _initial_opinion_identity(
    initial_records: Sequence[Mapping[str, Any]],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    ids: list[str] = []
    hashes: list[str] = []
    for raw in initial_records:
        opinion = raw.get("council_opinion")
        if not isinstance(opinion, Mapping):
            raise RebuttalPromotionError("Rebuttal Initial CouncilOpinion missing")
        opinion_id = opinion.get("opinion_id")
        opinion_hash = raw.get("council_opinion_hash")
        if not isinstance(opinion_id, str) or not opinion_id:
            raise RebuttalPromotionError("Rebuttal Initial opinion_id missing")
        if not isinstance(opinion_hash, str) or len(opinion_hash) != 64:
            raise RebuttalPromotionError("Rebuttal Initial opinion hash missing")
        ids.append(opinion_id)
        hashes.append(opinion_hash)
    if len(set(ids)) != 3 or len(set(hashes)) != 3:
        raise RebuttalPromotionError("Rebuttal Initial opinion identities must be unique")
    return tuple(ids), tuple(hashes)


def _allowed_uncertainty_refs(initial_records: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    values: list[str] = []
    for raw in initial_records:
        opinion = raw.get("council_opinion")
        if not isinstance(opinion, Mapping):
            raise RebuttalPromotionError("Rebuttal Initial CouncilOpinion missing")
        refs = opinion.get("data_gap_refs")
        if not isinstance(refs, (list, tuple)):
            raise RebuttalPromotionError("Rebuttal Initial data_gap_refs missing")
        for ref in refs:
            if not isinstance(ref, str) or not ref or ref != ref.strip():
                raise RebuttalPromotionError("Rebuttal Initial uncertainty ref invalid")
            if ref not in values:
                values.append(ref)
    return tuple(values)


def promote_rebuttal_bundle(
    proposal: RebuttalBundleDraft,
    *,
    bundle: CouncilInputBundle,
    model_input: Mapping[str, Any],
    initial_records: Sequence[Mapping[str, Any]],
    required_unknown_refs: tuple[str, ...] = (),
) -> RebuttalPromotionResult:
    if proposal.candidate_id != bundle.candidate_id:
        raise RebuttalPromotionError("Rebuttal candidate escapes frozen CouncilInputBundle")
    if proposal.council_input_bundle_hash != bundle.bundle_hash:
        raise RebuttalPromotionError("Rebuttal bundle hash does not bind frozen CouncilInputBundle")

    initial_ids, initial_hashes = _initial_opinion_identity(initial_records)
    if tuple(proposal.initial_opinion_ids) != initial_ids:
        raise RebuttalPromotionError("Rebuttal initial_opinion_ids differ from frozen Bull/Bear/Red order")
    if tuple(proposal.initial_opinion_hashes) != initial_hashes:
        raise RebuttalPromotionError("Rebuttal initial_opinion_hashes differ from frozen Bull/Bear/Red order")

    claim_lane = _initial_claim_lane_map(initial_records, candidate_id=bundle.candidate_id)
    allowed_uncertainty = set(_allowed_uncertainty_refs(initial_records))
    required_unknown = set(required_unknown_refs)
    if not required_unknown.issubset(allowed_uncertainty):
        raise RebuttalPromotionError("required Rebuttal unknown ref is absent from frozen Initial opinions")

    source_by_id = _canonical_source_claims(model_input)
    if tuple(source_by_id) != bundle.allowed_material_claim_ids:
        raise RebuttalPromotionError("Rebuttal source MaterialClaim allowlist/order drift")
    computed = _computed_values(model_input)
    if tuple(computed) != bundle.allowed_computed_value_ids:
        raise RebuttalPromotionError("Rebuttal ComputedValue allowlist/order drift")

    promoted: list[object] = []
    metadata: list[CouncilClaimMetadata] = []
    local_to_canonical: dict[str, str] = {}
    for item in proposal.items:
        responding = item.responding_lane
        allowed_opponents = {
            claim_id
            for claim_id, owner_lane in claim_lane.items()
            if owner_lane != responding
        }
        if not set(item.opposing_finding_ids).issubset(allowed_opponents):
            raise RebuttalPromotionError("Rebuttal item targets same-lane or nonexistent Initial finding")
        if not set(item.remaining_uncertainty_refs).issubset(allowed_uncertainty):
            raise RebuttalPromotionError("Rebuttal item introduces uncertainty ref outside frozen Initial record")
        if not required_unknown.issubset(set(item.remaining_uncertainty_refs)):
            raise RebuttalPromotionError("Rebuttal item may not erase frozen material unknown without new evidence")

        for claim in item.response_proposed_claims:
            if claim.lane != responding:
                raise RebuttalPromotionError("Rebuttal proposed claim lane differs from responding lane")
            if claim.claim_type == CouncilClaimType.DECISION_BASIS:
                raise RebuttalPromotionError("DECISION_BASIS is Judge-only and forbidden in Rebuttal claims")
            if claim.claim_type.value not in REBUTTAL_ALLOWED_CLAIM_TYPES:
                raise RebuttalPromotionError("Rebuttal claim type lacks Council-stage authority")
            try:
                _validate_generated_text(claim)
                parents = _validate_claim_refs(
                    claim,
                    bundle=bundle,
                    source_by_id=source_by_id,
                    computed_value_values=computed,
                )
                _validate_support_semantics(claim, parents=parents)
                _validate_numeric_provenance(
                    claim,
                    parents=parents,
                    computed_value_values=computed,
                )
                canonical = _canonical_claim_from_proposal(
                    claim,
                    bundle=bundle,
                    opinion_id=proposal.rebuttal_bundle_id,
                    parents=parents,
                )
            except CouncilPromotionError as exc:
                raise RebuttalPromotionError(str(exc)) from exc
            if claim.claim_local_ref in local_to_canonical:
                raise RebuttalPromotionError("duplicate Rebuttal claim_local_ref across bundle")
            local_to_canonical[claim.claim_local_ref] = canonical.claim_id
            promoted.append(canonical)
            metadata.append(
                CouncilClaimMetadata.from_unhashed(
                    material_claim_id=canonical.claim_id,
                    lane=claim.lane,
                    council_claim_type=claim.claim_type,
                    opinion_or_judge_ref=proposal.rebuttal_bundle_id,
                )
            )

    frozen = FrozenRebuttalBundle.from_draft(proposal)
    return RebuttalPromotionResult(
        frozen_rebuttal_bundle=frozen,
        material_claims=tuple(promoted),
        claim_metadata=tuple(metadata),
        local_ref_to_claim_id=dict(local_to_canonical),
        validator_results=(
            _validator("B4-V025", "Rebuttal evaluated only against frozen Initial Council records"),
            _validator("B4-V026", "all opposing finding refs belong to other frozen lanes"),
            _validator("B4-V027", "all Rebuttal claim evidence/provenance stays inside frozen candidate bundle"),
            _validator("B4-V028", "required frozen material unknown refs remain visible"),
            _validator("B4-CLAIM-NUMERIC", "numeric provenance bound to frozen sources/computed values"),
            _validator("B4-REBUTTAL-CLAIM-TYPE", "Judge-only claim types absent from Rebuttal claims"),
        ),
    )
