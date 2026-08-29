from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Mapping

from aic.domain.canonical import canonical_datetime, canonical_sha256
from aic.domain.contracts import COUNCIL_OPINION_V1, MATERIAL_CLAIM_V1

from .claim_promotion_authority import (
    NORMALIZATION_VERSION as CLAIM_PROMOTION_NORMALIZATION_VERSION,
    load_claim_promotion_normalization,
)
from .models import (
    CouncilClaimKind,
    CouncilClaimType,
    CouncilInputBundle,
    CouncilLane,
    CouncilMateriality,
    CouncilSupportStatus,
    ProposedCouncilClaim,
)
from .policy_refs import (
    build_council_model_policy_reference,
    build_council_policy_reference,
)
from .proposal import (
    CouncilClaimMetadata,
    InitialCouncilOpinionProposal,
    validate_initial_proposal_lineage,
)


_FORBIDDEN_AUTHORITY_RE = re.compile(
    r"(?i)(?:\bBUY\b|\bSELL\b|\bSHORT\b|\bTARGET\s+PRICE\b|"
    r"\bPOSITION\s+SIZE\b|\bPLACE\s+(?:AN?\s+)?ORDER\b|"
    r"\bEXECUTE\s+(?:THE\s+)?(?:TRADE|ORDER)\b|"
    r"\bRISK\s+PASS(?:ED)?\b|\bAPPROV(?:E|ED)\s+(?:TRADE|ORDER)\b)"
)
_INJECTION_RE = re.compile(
    r"(?i)(?:ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions|"
    r"system\s+prompt|developer\s+message|run\s+(?:a\s+)?shell|"
    r"execute\s+(?:this\s+)?command|curl\s+https?://|wget\s+https?://)"
)
_NUMERIC_RE = re.compile(r"(?<![A-Za-z0-9_])[-+]?\d+(?:\.\d+)?%?")


class CouncilPromotionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class InitialOpinionPromotionResult:
    material_claims: tuple[object, ...]
    claim_metadata: tuple[CouncilClaimMetadata, ...]
    council_opinion: object
    local_ref_to_claim_id: Mapping[str, str]
    validator_results: tuple[Mapping[str, str], ...]


def _validator(check_id: str, detail: str) -> Mapping[str, str]:
    return {"check_id": check_id, "status": "PASS", "detail": detail}


def _canonical_source_claim(value: object):
    try:
        return MATERIAL_CLAIM_V1.model_validate(value)
    except Exception as exc:
        raise CouncilPromotionError(f"source MaterialClaim is not canonical: {exc}") from exc


def _source_claim_map(source_claims: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, raw in source_claims.items():
        claim = _canonical_source_claim(raw)
        if claim.claim_id != key:
            raise CouncilPromotionError("source MaterialClaim mapping key/claim_id mismatch")
        if key in result:
            raise CouncilPromotionError("duplicate source MaterialClaim id")
        result[key] = claim
    return result


def _validate_generated_text(claim: ProposedCouncilClaim) -> None:
    if _FORBIDDEN_AUTHORITY_RE.search(claim.claim_text):
        raise CouncilPromotionError("B4 generated claim contains forbidden trade/risk authority text")
    if _INJECTION_RE.search(claim.claim_text):
        raise CouncilPromotionError("B4 generated claim contains prompt/tool directive residue")


def _validate_claim_refs(
    claim: ProposedCouncilClaim,
    *,
    bundle: CouncilInputBundle,
    source_by_id: Mapping[str, object],
    computed_value_values: Mapping[str, str],
) -> tuple[object, ...]:
    if claim.candidate_id != bundle.candidate_id:
        raise CouncilPromotionError("proposed claim candidate escapes frozen CouncilInputBundle")
    if not set(claim.source_material_claim_ids).issubset(set(bundle.allowed_material_claim_ids)):
        raise CouncilPromotionError("proposed claim references MaterialClaim outside frozen bundle")
    if not set(claim.computed_value_ids).issubset(set(bundle.allowed_computed_value_ids)):
        raise CouncilPromotionError("proposed claim references ComputedValue outside frozen bundle")
    if not set(claim.conflict_ids).issubset(set(bundle.allowed_conflict_ids)):
        raise CouncilPromotionError("proposed claim references conflict outside frozen bundle")

    parents: list[object] = []
    for source_id in claim.source_material_claim_ids:
        parent = source_by_id.get(source_id)
        if parent is None:
            raise CouncilPromotionError("proposed claim references unavailable canonical MaterialClaim")
        if parent.candidate_id != bundle.candidate_id:
            raise CouncilPromotionError("source MaterialClaim candidate mismatch")
        parents.append(parent)

    for computed_id in claim.computed_value_ids:
        value = computed_value_values.get(computed_id)
        if value is None:
            raise CouncilPromotionError("direct ComputedValue reference lacks supplied deterministic value")
        if not isinstance(value, str) or not value or value != value.strip():
            raise CouncilPromotionError("computed_value_values must contain canonical non-empty strings")

    return tuple(parents)


def _validate_support_semantics(
    claim: ProposedCouncilClaim,
    *,
    parents: tuple[object, ...],
) -> None:
    if (
        claim.materiality == CouncilMateriality.MATERIAL
        and claim.support_status != CouncilSupportStatus.SUPPORTED
    ):
        raise CouncilPromotionError("unsupported/conflicted MATERIAL B4 claim may not promote")

    if claim.claim_kind == CouncilClaimKind.FACT_RESTATEMENT:
        if not (parents or claim.computed_value_ids):
            raise CouncilPromotionError("FACT_RESTATEMENT requires canonical source claim or ComputedValue")
        if any(parent.claim_kind != "FACT" for parent in parents):
            raise CouncilPromotionError("FACT_RESTATEMENT may not restate an INFERENCE as fact")
    else:
        if not (parents or claim.computed_value_ids or claim.conflict_ids):
            raise CouncilPromotionError("B4 inference/process finding requires frozen provenance refs")


def _numeric_tokens(text: str) -> set[str]:
    return {match.group(0) for match in _NUMERIC_RE.finditer(text)}


def _validate_numeric_provenance(
    claim: ProposedCouncilClaim,
    *,
    parents: tuple[object, ...],
    computed_value_values: Mapping[str, str],
) -> None:
    tokens = _numeric_tokens(claim.claim_text)
    if not tokens:
        return
    allowed: set[str] = set()
    for parent in parents:
        allowed.update(_numeric_tokens(parent.claim_text))
    for computed_id in claim.computed_value_ids:
        allowed.add(computed_value_values[computed_id])
    missing = sorted(tokens - allowed)
    if missing:
        raise CouncilPromotionError(
            "B4 numeric token is not bound to cited source claim/computed value: "
            + ", ".join(missing)
        )


def _ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _canonical_claim_from_proposal(
    claim: ProposedCouncilClaim,
    *,
    bundle: CouncilInputBundle,
    opinion_id: str,
    parents: tuple[object, ...],
) -> object:
    authority = load_claim_promotion_normalization()
    evidence_ids = _ordered_unique(
        [ref for parent in parents for ref in parent.evidence_ids]
    )
    computed_value_ids = _ordered_unique(
        [ref for parent in parents for ref in parent.computed_value_ids]
        + list(claim.computed_value_ids)
    )
    conflict_ids = _ordered_unique(
        [ref for parent in parents for ref in parent.conflict_ids]
        + list(claim.conflict_ids)
    )
    assumptions = _ordered_unique(
        [assumption for parent in parents for assumption in parent.assumptions]
    )

    if not set(computed_value_ids).issubset(set(bundle.allowed_computed_value_ids)):
        raise CouncilPromotionError("promoted claim computed-value closure escapes frozen bundle")
    if not set(conflict_ids).issubset(set(bundle.allowed_conflict_ids)):
        raise CouncilPromotionError("promoted claim conflict closure escapes frozen bundle")

    try:
        canonical_claim_kind = authority.claim_kind_mapping[claim.claim_kind.value]
    except KeyError as exc:
        raise CouncilPromotionError("claim kind lacks frozen normalization authority") from exc

    identity_seed = canonical_sha256(
        {
            "normalization_version": authority.normalization_version,
            "normalization_hash": authority.normalization_hash,
            "bundle_hash": bundle.bundle_hash,
            "opinion_id": opinion_id,
            "candidate_id": bundle.candidate_id,
            "lane": claim.lane.value,
            "claim_local_ref": claim.claim_local_ref,
            "claim_type": claim.claim_type.value,
            "claim_text": claim.claim_text,
            "source_material_claim_ids": list(claim.source_material_claim_ids),
            "computed_value_ids": list(claim.computed_value_ids),
            "conflict_ids": list(claim.conflict_ids),
        }
    )
    claim_id = (
        f"B4_MATERIAL_CLAIM_{bundle.candidate_id}_{claim.lane.value}_{identity_seed[:20]}"
    )
    return MATERIAL_CLAIM_V1.from_unhashed(
        claim_id=claim_id,
        candidate_id=bundle.candidate_id,
        category=claim.claim_type.value,
        claim_text=claim.claim_text,
        claim_kind=canonical_claim_kind,
        materiality=claim.materiality.value,
        evidence_ids=evidence_ids,
        computed_value_ids=computed_value_ids,
        conflict_ids=conflict_ids,
        assumptions=assumptions,
        support_status=claim.support_status.value,
        uncertainty_note=None,
    )


def promote_initial_council_opinion(
    proposal: InitialCouncilOpinionProposal,
    *,
    bundle: CouncilInputBundle,
    expected_lane: CouncilLane,
    source_claims: Mapping[str, object],
    computed_value_values: Mapping[str, str],
    allowed_data_gap_refs: tuple[str, ...] = (),
    required_data_gap_refs: tuple[str, ...] = (),
    frozen_at: datetime,
) -> InitialOpinionPromotionResult:
    """Promote one validated initial model proposal with zero model/provider/broker calls."""

    validate_initial_proposal_lineage(proposal, bundle=bundle, expected_lane=expected_lane)
    if expected_lane == CouncilLane.JUDGE:
        raise CouncilPromotionError("Judge claims are not initial CouncilOpinion claims")
    if any(
        claim.claim_type == CouncilClaimType.DECISION_BASIS
        for claim in proposal.proposed_claims
    ):
        raise CouncilPromotionError("DECISION_BASIS is Judge-only and forbidden in initial opinions")
    if frozen_at.tzinfo is None or frozen_at.utcoffset() is None:
        raise CouncilPromotionError("initial_frozen_at must be timezone-aware")
    frozen_at = frozen_at.astimezone(UTC)

    allowed_gaps = set(allowed_data_gap_refs)
    required_gaps = set(required_data_gap_refs)
    if not set(proposal.material_unknown_refs).issubset(allowed_gaps):
        raise CouncilPromotionError("initial opinion contains unknown/gap ref outside application allowlist")
    if not required_gaps.issubset(set(proposal.material_unknown_refs)):
        raise CouncilPromotionError("application-required data gap ref may not be hidden by Council output")
    if not set(proposal.material_conflict_refs).issubset(set(bundle.allowed_conflict_ids)):
        raise CouncilPromotionError("initial opinion material_conflict_refs escape frozen bundle")

    source_by_id = _source_claim_map(source_claims)
    promoted: list[object] = []
    local_to_canonical: dict[str, str] = {}
    for claim in proposal.proposed_claims:
        _validate_generated_text(claim)
        parents = _validate_claim_refs(
            claim,
            bundle=bundle,
            source_by_id=source_by_id,
            computed_value_values=computed_value_values,
        )
        _validate_support_semantics(claim, parents=parents)
        _validate_numeric_provenance(
            claim,
            parents=parents,
            computed_value_values=computed_value_values,
        )
        canonical = _canonical_claim_from_proposal(
            claim,
            bundle=bundle,
            opinion_id=proposal.opinion_id,
            parents=parents,
        )
        promoted.append(canonical)
        local_to_canonical[claim.claim_local_ref] = canonical.claim_id

    def mapped(local_refs: tuple[str, ...]) -> list[str]:
        return [local_to_canonical[ref] for ref in local_refs]

    council_policy_ref = build_council_policy_reference()
    council_model_policy_ref = build_council_model_policy_reference()
    if council_policy_ref.version != bundle.council_policy_version:
        raise CouncilPromotionError("canonical council policy ref does not match frozen bundle")
    if council_model_policy_ref.version != bundle.model_policy_version:
        raise CouncilPromotionError("canonical council model policy ref does not match frozen bundle")

    data_gap_refs = _ordered_unique(
        list(proposal.material_unknown_refs) + list(proposal.material_conflict_refs)
    )
    opinion = COUNCIL_OPINION_V1.from_unhashed(
        opinion_id=proposal.opinion_id,
        lane=proposal.lane.value,
        candidate_id=bundle.candidate_id,
        candidate_packet_id=bundle.candidate_packet_id,
        candidate_packet_hash=bundle.candidate_packet_hash,
        input_snapshot_hash=bundle.bundle_hash,
        mandate_version=bundle.mandate_version,
        council_policy_ref=council_policy_ref.model_dump(
            mode="json", exclude_none=False, warnings=False
        ),
        council_model_policy_ref=council_model_policy_ref.model_dump(
            mode="json", exclude_none=False, warnings=False
        ),
        material_claim_ids=[claim.claim_id for claim in promoted],
        assumption_claim_ids=mapped(proposal.critical_assumption_claim_ids),
        data_gap_refs=data_gap_refs,
        initial_frozen_at=canonical_datetime(frozen_at),
        rebuttal_material_claim_ids=[],
        rebuttal_frozen_at=None,
        model_run_ref=proposal.model_run_ref,
    )

    metadata = tuple(
        CouncilClaimMetadata.from_unhashed(
            material_claim_id=local_to_canonical[raw.claim_local_ref],
            lane=raw.lane,
            council_claim_type=raw.claim_type,
            opinion_or_judge_ref=opinion.opinion_id,
        )
        for raw in proposal.proposed_claims
    )

    persisted = {
        **opinion.model_dump(mode="json", exclude_none=False, warnings=False),
        "claim_metadata": [item.model_dump(mode="json") for item in metadata],
    }
    if "claim_local_ref" in str(persisted) or "council_claim_id" in str(persisted):
        raise CouncilPromotionError("response-local/legacy claim identity leaked into persisted output")

    return InitialOpinionPromotionResult(
        material_claims=tuple(promoted),
        claim_metadata=metadata,
        council_opinion=opinion,
        local_ref_to_claim_id=dict(local_to_canonical),
        validator_results=(
            _validator("B4-R0R-V01-V05", "claim promotion and canonical claim authority valid"),
            _validator("B4-PROMPT-V01-V02", "no model canonical claim id or local-ref persistence"),
            _validator("B4-CLAIM-NUMERIC", "numeric provenance bound to frozen sources/computed values"),
            _validator("B4-OPINION-LINEAGE", "canonical CouncilOpinion matches frozen input and policy refs"),
        ),
    )
