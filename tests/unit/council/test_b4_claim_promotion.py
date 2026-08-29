from copy import deepcopy
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from aic.council.models import (
    CouncilClaimKind,
    CouncilClaimType,
    CouncilInputBundle,
    CouncilLane,
    CouncilMateriality,
    CouncilSupportStatus,
    ProposedCouncilClaim,
    RoleBoundaryStatus,
)
from aic.council.policy import COUNCIL_POLICY_VERSION
from aic.council.model_policy import MODEL_POLICY_VERSION
from aic.council.policy_refs import (
    build_council_model_policy_reference,
    build_council_policy_reference,
)
from aic.council.promotion import (
    CLAIM_PROMOTION_NORMALIZATION_VERSION,
    CouncilPromotionError,
    promote_initial_council_opinion,
)
from aic.council.proposal import InitialCouncilOpinionProposal
from aic.domain.contracts import MATERIAL_CLAIM_V1


pytestmark = pytest.mark.filterwarnings("ignore:Pydantic serializer warnings:UserWarning")


def _bundle(*, lane: CouncilLane = CouncilLane.BULL) -> CouncilInputBundle:
    del lane
    return CouncilInputBundle.from_unhashed(
        bundle_id="B4_INPUT_NVDA",
        candidate_id="NVDA",
        candidate_packet_id="B3_PACKET_NVDA",
        candidate_packet_hash="1" * 64,
        research_snapshot_id="B3_RESEARCH_NVDA",
        research_snapshot_hash="2" * 64,
        b2_snapshot_id="B2_SNAPSHOT",
        deep_comparison_id="B2_DEEP",
        mandate_version="ALPACA_COMPETITION_V1_2026_08_29",
        council_policy_version=COUNCIL_POLICY_VERSION,
        judge_policy_version="JUDGE_POLICY_vB4_0_1",
        model_policy_version=MODEL_POLICY_VERSION,
        allowed_material_claim_ids=["SRC_FACT"],
        allowed_computed_value_ids=["CV1"],
        allowed_conflict_ids=["CONFLICT1"],
        shared_portfolio_context_refs=[],
        created_at=datetime(2026, 8, 29, 16, 0, tzinfo=UTC),
    )


def _source_fact():
    return MATERIAL_CLAIM_V1.from_unhashed(
        claim_id="SRC_FACT",
        candidate_id="NVDA",
        category="financial_quality",
        claim_text="The frozen revenue metric was 42.",
        claim_kind="FACT",
        materiality="MATERIAL",
        evidence_ids=["EVID1"],
        computed_value_ids=["CV1"],
        conflict_ids=[],
        assumptions=[],
        support_status="SUPPORTED",
        uncertainty_note=None,
    )


def _claim(
    local_ref: str,
    *,
    lane: CouncilLane = CouncilLane.BULL,
    claim_type: CouncilClaimType = CouncilClaimType.ARGUMENT,
    text: str = "The frozen revenue metric was 42.",
    kind: CouncilClaimKind = CouncilClaimKind.FACT_RESTATEMENT,
    support: CouncilSupportStatus = CouncilSupportStatus.SUPPORTED,
    materiality: CouncilMateriality = CouncilMateriality.MATERIAL,
    source_ids: tuple[str, ...] = ("SRC_FACT",),
    computed_ids: tuple[str, ...] = ("CV1",),
    conflict_ids: tuple[str, ...] = (),
) -> ProposedCouncilClaim:
    return ProposedCouncilClaim(
        claim_local_ref=local_ref,
        candidate_id="NVDA",
        lane=lane,
        claim_type=claim_type,
        claim_text=text,
        source_material_claim_ids=source_ids,
        computed_value_ids=computed_ids,
        conflict_ids=conflict_ids,
        claim_kind=kind,
        support_status=support,
        materiality=materiality,
    )


def _proposal(*, lane: CouncilLane = CouncilLane.BULL) -> InitialCouncilOpinionProposal:
    p1 = _claim("p1", lane=lane)
    p2 = _claim(
        "p2",
        lane=lane,
        claim_type=CouncilClaimType.ASSUMPTION,
        text="Demand durability remains an inference from the frozen company evidence.",
        kind=CouncilClaimKind.INFERENCE,
        computed_ids=(),
    )
    p3 = _claim(
        "p3",
        lane=lane,
        claim_type=CouncilClaimType.FALSIFIER,
        text="A reversal in the reported demand evidence would weaken this case.",
        kind=CouncilClaimKind.INFERENCE,
        materiality=CouncilMateriality.SUPPORTING,
        computed_ids=(),
    )
    return InitialCouncilOpinionProposal(
        opinion_id=f"B4_OPINION_NVDA_{lane.value}",
        candidate_id="NVDA",
        lane=lane,
        council_input_bundle_hash=_bundle().bundle_hash,
        candidate_packet_hash="1" * 64,
        mandate_version="ALPACA_COMPETITION_V1_2026_08_29",
        council_policy_version=COUNCIL_POLICY_VERSION,
        model_policy_version=MODEL_POLICY_VERSION,
        model_run_ref=f"MODEL_RUN_{lane.value}",
        proposed_claims=(p1, p2, p3),
        primary_claim_ids=("p1",),
        critical_assumption_claim_ids=("p2",),
        falsifier_claim_ids=("p3",),
        material_unknown_refs=("GAP:ALPACA_NEWS_PAGINATION_INCOMPLETE",),
        material_conflict_refs=(),
        research_reopen_required=True,
        research_reopen_reason_codes=("CURRENT_NEWS_INCOMPLETE",),
        role_boundary_status=RoleBoundaryStatus.VALID,
    )


def _promote(proposal: InitialCouncilOpinionProposal | None = None):
    proposal = proposal or _proposal()
    return promote_initial_council_opinion(
        proposal,
        bundle=_bundle(),
        expected_lane=proposal.lane,
        source_claims={"SRC_FACT": _source_fact()},
        computed_value_values={"CV1": "42"},
        allowed_data_gap_refs=("GAP:ALPACA_NEWS_PAGINATION_INCOMPLETE",),
        required_data_gap_refs=("GAP:ALPACA_NEWS_PAGINATION_INCOMPLETE",),
        frozen_at=datetime(2026, 8, 29, 16, 5, tzinfo=UTC),
    )


def test_initial_claims_promote_to_canonical_material_claims_and_opinion() -> None:
    result = _promote()
    assert CLAIM_PROMOTION_NORMALIZATION_VERSION == "B4_CLAIM_PROMOTION_NORMALIZATION_v0_1"
    assert len(result.material_claims) == 3
    assert set(result.local_ref_to_claim_id) == {"p1", "p2", "p3"}
    assert all(claim.claim_id.startswith("B4_MATERIAL_CLAIM_NVDA_BULL_") for claim in result.material_claims)

    fact = result.material_claims[0]
    assert fact.claim_kind == "FACT"
    assert fact.category == "ARGUMENT"
    assert fact.evidence_ids == ("EVID1",)
    assert fact.computed_value_ids == ("CV1",)

    opinion = result.council_opinion
    assert opinion.lane == "BULL"
    assert opinion.input_snapshot_hash == _bundle().bundle_hash
    assert opinion.material_claim_ids == tuple(claim.claim_id for claim in result.material_claims)
    assert opinion.assumption_claim_ids == (result.local_ref_to_claim_id["p2"],)
    assert opinion.data_gap_refs == ("GAP:ALPACA_NEWS_PAGINATION_INCOMPLETE",)
    assert opinion.rebuttal_material_claim_ids == ()
    assert opinion.rebuttal_frozen_at is None
    assert len(result.claim_metadata) == 3

    persisted_text = str(opinion.model_dump(mode="json", exclude_none=False, warnings=False))
    persisted_text += str([item.model_dump(mode="json") for item in result.claim_metadata])
    assert "claim_local_ref" not in persisted_text
    assert "council_claim_id" not in persisted_text
    assert "'p1'" not in persisted_text


def test_process_finding_normalizes_to_inference_while_metadata_preserves_integrity_type() -> None:
    lane = CouncilLane.RED_TEAM
    raw = _claim(
        "red1",
        lane=lane,
        claim_type=CouncilClaimType.INTEGRITY_FINDING,
        text="The frozen source evidence requires an integrity qualification.",
        kind=CouncilClaimKind.PROCESS_FINDING,
        computed_ids=(),
    )
    proposal = InitialCouncilOpinionProposal(
        opinion_id="B4_OPINION_NVDA_RED",
        candidate_id="NVDA",
        lane=lane,
        council_input_bundle_hash=_bundle().bundle_hash,
        candidate_packet_hash="1" * 64,
        mandate_version="ALPACA_COMPETITION_V1_2026_08_29",
        council_policy_version=COUNCIL_POLICY_VERSION,
        model_policy_version=MODEL_POLICY_VERSION,
        model_run_ref="MODEL_RUN_RED",
        proposed_claims=(raw,),
        primary_claim_ids=("red1",),
        critical_assumption_claim_ids=(),
        falsifier_claim_ids=(),
        material_unknown_refs=("GAP:ALPACA_NEWS_PAGINATION_INCOMPLETE",),
        material_conflict_refs=(),
        research_reopen_required=True,
        research_reopen_reason_codes=("CURRENT_NEWS_INCOMPLETE",),
        role_boundary_status=RoleBoundaryStatus.VALID,
    )
    result = _promote(proposal)
    assert result.material_claims[0].claim_kind == "INFERENCE"
    assert result.material_claims[0].category == "INTEGRITY_FINDING"
    assert result.claim_metadata[0].council_claim_type == CouncilClaimType.INTEGRITY_FINDING
    assert result.claim_metadata[0].material_claim_id == result.material_claims[0].claim_id


def test_model_cannot_supply_canonical_claim_id() -> None:
    raw = _claim("p1").model_dump(mode="json")
    raw["claim_id"] = "MODEL_ASSIGNED_CANONICAL_ID"
    with pytest.raises(ValidationError):
        ProposedCouncilClaim.model_validate(raw)


def test_source_claim_escape_fails_before_canonical_persistence() -> None:
    raw = _proposal().model_dump(mode="python")
    escaped = deepcopy(raw)
    escaped["proposed_claims"][0]["source_material_claim_ids"] = ("OUTSIDE",)
    proposal = InitialCouncilOpinionProposal.model_validate(escaped)
    with pytest.raises(CouncilPromotionError, match="outside frozen bundle"):
        _promote(proposal)


def test_material_insufficient_claim_cannot_promote() -> None:
    raw = _proposal().model_dump(mode="python")
    changed = deepcopy(raw)
    changed["proposed_claims"][0]["support_status"] = "INSUFFICIENT"
    proposal = InitialCouncilOpinionProposal.model_validate(changed)
    with pytest.raises(CouncilPromotionError, match="MATERIAL B4 claim"):
        _promote(proposal)


def test_numeric_hallucination_fails_without_exact_source_or_computed_binding() -> None:
    raw = _proposal().model_dump(mode="python")
    changed = deepcopy(raw)
    changed["proposed_claims"][0]["claim_text"] = "The frozen revenue metric was 99."
    proposal = InitialCouncilOpinionProposal.model_validate(changed)
    with pytest.raises(CouncilPromotionError, match="numeric token"):
        _promote(proposal)


def test_required_application_gap_cannot_be_hidden() -> None:
    raw = _proposal().model_dump(mode="python")
    changed = deepcopy(raw)
    changed["material_unknown_refs"] = ()
    proposal = InitialCouncilOpinionProposal.model_validate(changed)
    with pytest.raises(CouncilPromotionError, match="may not be hidden"):
        _promote(proposal)


def test_policy_references_are_canonical_hash_bound_objects() -> None:
    council_ref = build_council_policy_reference()
    model_ref = build_council_model_policy_reference()
    assert council_ref.version == COUNCIL_POLICY_VERSION
    assert model_ref.version == MODEL_POLICY_VERSION
    assert council_ref.policy_reference_hash != council_ref.policy_hash
    assert model_ref.policy_reference_hash != model_ref.policy_hash
    assert len(council_ref.policy_reference_id) == 64
    assert len(model_ref.policy_reference_id) == 64
