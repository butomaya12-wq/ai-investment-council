from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

import pytest

from aic.council.model_policy import MODEL_POLICY_VERSION
from aic.council.models import (
    CouncilClaimKind,
    CouncilClaimType,
    CouncilInputBundle,
    CouncilLane,
    CouncilMateriality,
    CouncilSupportStatus,
    ProposedCouncilClaim,
)
from aic.council.policy import COUNCIL_POLICY_VERSION
from aic.council.proposal import RebuttalBundleDraft, RebuttalItemDraft, RebuttalResponseType
from aic.council.rebuttal_promotion import (
    REBUTTAL_PROMOTION_VERSION,
    RebuttalPromotionError,
    promote_rebuttal_bundle,
)
from aic.domain.contracts import MATERIAL_CLAIM_V1


def _bundle() -> CouncilInputBundle:
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
        allowed_material_claim_ids=("SRC_FACT",),
        allowed_computed_value_ids=("CV1",),
        allowed_conflict_ids=("CONFLICT1",),
        shared_portfolio_context_refs=(),
        created_at=datetime(2026, 8, 29, 16, 0, tzinfo=UTC),
    )


def _source_fact():
    return MATERIAL_CLAIM_V1.from_unhashed(
        claim_id="SRC_FACT",
        candidate_id="NVDA",
        category="financial_quality",
        claim_text="Frozen metric was 42.",
        claim_kind="FACT",
        materiality="MATERIAL",
        evidence_ids=["EVID1"],
        computed_value_ids=["CV1"],
        conflict_ids=[],
        assumptions=[],
        support_status="SUPPORTED",
        uncertainty_note=None,
    )


def _initial_claim(claim_id: str, text: str):
    return MATERIAL_CLAIM_V1.from_unhashed(
        claim_id=claim_id,
        candidate_id="NVDA",
        category="ARGUMENT",
        claim_text=text,
        claim_kind="INFERENCE",
        materiality="MATERIAL",
        evidence_ids=["EVID1"],
        computed_value_ids=[],
        conflict_ids=[],
        assumptions=[],
        support_status="SUPPORTED",
        uncertainty_note=None,
    )


def _initial_records() -> list[dict]:
    rows = []
    for lane, claim_id, char in (
        ("BULL", "INIT_BULL", "a"),
        ("BEAR", "INIT_BEAR", "b"),
        ("RED_TEAM", "INIT_RED", "c"),
    ):
        claim = _initial_claim(claim_id, f"Frozen {lane} claim.")
        rows.append(
            {
                "candidate_id": "NVDA",
                "lane": lane,
                "material_claims": [
                    claim.model_dump(mode="json", exclude_none=False, warnings=False)
                ],
                "council_opinion": {
                    "opinion_id": f"OP_{lane}",
                    "data_gap_refs": ["GAP1"],
                },
                "council_opinion_hash": char * 64,
            }
        )
    return rows


def _model_input() -> dict:
    return {
        "candidate_model_input": {
            "material_claims": [
                _source_fact().model_dump(mode="json", exclude_none=False, warnings=False)
            ],
            "computed_values": [
                {
                    "computed_value_id": "CV1",
                    "metric_id": "M1",
                    "value": "42",
                    "unit": "count",
                }
            ],
            "data_gap_refs": ["GAP1"],
        },
        "initial_council": {"marker": "FROZEN"},
    }


def _claim(
    *,
    lane: CouncilLane = CouncilLane.BULL,
    claim_type: CouncilClaimType = CouncilClaimType.ARGUMENT,
    text: str = "The frozen record supports this bounded rebuttal inference.",
) -> ProposedCouncilClaim:
    return ProposedCouncilClaim(
        claim_local_ref="REB_C1",
        candidate_id="NVDA",
        lane=lane,
        claim_type=claim_type,
        claim_text=text,
        source_material_claim_ids=("SRC_FACT",),
        computed_value_ids=(),
        conflict_ids=(),
        claim_kind=CouncilClaimKind.INFERENCE,
        support_status=CouncilSupportStatus.SUPPORTED,
        materiality=CouncilMateriality.MATERIAL,
    )


def _proposal(*, claim: ProposedCouncilClaim | None = None) -> RebuttalBundleDraft:
    claim = claim or _claim()
    return RebuttalBundleDraft(
        rebuttal_bundle_id="REB_NVDA_001",
        candidate_id="NVDA",
        council_input_bundle_hash=_bundle().bundle_hash,
        initial_opinion_ids=("OP_BULL", "OP_BEAR", "OP_RED_TEAM"),
        initial_opinion_hashes=("a" * 64, "b" * 64, "c" * 64),
        items=(
            RebuttalItemDraft(
                rebuttal_item_id="REB_BULL",
                responding_lane=CouncilLane.BULL,
                opposing_finding_ids=("INIT_BEAR",),
                response_type=RebuttalResponseType.REBUT,
                response_proposed_claims=(claim,),
                remaining_uncertainty_refs=("GAP1",),
            ),
            RebuttalItemDraft(
                rebuttal_item_id="REB_BEAR",
                responding_lane=CouncilLane.BEAR,
                opposing_finding_ids=("INIT_BULL",),
                response_type=RebuttalResponseType.PARTIAL,
                response_proposed_claims=(),
                remaining_uncertainty_refs=("GAP1",),
            ),
            RebuttalItemDraft(
                rebuttal_item_id="REB_RED",
                responding_lane=CouncilLane.RED_TEAM,
                opposing_finding_ids=("INIT_BULL",),
                response_type=RebuttalResponseType.UNRESOLVED,
                response_proposed_claims=(),
                remaining_uncertainty_refs=("GAP1",),
            ),
        ),
        research_reopen_required=True,
        research_reopen_reason_codes=("CURRENT_NEWS_INCOMPLETE",),
    )


def _promote(proposal: RebuttalBundleDraft | None = None):
    return promote_rebuttal_bundle(
        proposal or _proposal(),
        bundle=_bundle(),
        model_input=_model_input(),
        initial_records=_initial_records(),
        required_unknown_refs=("GAP1",),
    )


def test_rebuttal_promotion_accepts_bounded_other_lane_response_and_canonicalizes_claim() -> None:
    result = _promote()
    assert REBUTTAL_PROMOTION_VERSION == "B4_REBUTTAL_PROMOTION_v0_1"
    assert result.frozen_rebuttal_bundle.draft.rebuttal_bundle_id == "REB_NVDA_001"
    assert len(result.material_claims) == 1
    assert result.material_claims[0].claim_id.startswith("B4_MATERIAL_CLAIM_NVDA_BULL_")
    assert result.material_claims[0].claim_kind == "INFERENCE"
    assert result.claim_metadata[0].opinion_or_judge_ref == "REB_NVDA_001"
    assert result.local_ref_to_claim_id["REB_C1"] == result.material_claims[0].claim_id
    assert {row["check_id"] for row in result.validator_results} >= {
        "B4-V025",
        "B4-V026",
        "B4-V027",
        "B4-V028",
        "B4-CLAIM-NUMERIC",
        "B4-REBUTTAL-CLAIM-TYPE",
    }


def test_rebuttal_same_lane_target_is_rejected() -> None:
    raw = _proposal().model_dump(mode="python")
    changed = deepcopy(raw)
    changed["items"][0]["opposing_finding_ids"] = ("INIT_BULL",)
    proposal = RebuttalBundleDraft.model_validate(changed)
    with pytest.raises(RebuttalPromotionError, match="same-lane or nonexistent"):
        _promote(proposal)


def test_rebuttal_cannot_erase_required_frozen_unknown() -> None:
    raw = _proposal().model_dump(mode="python")
    changed = deepcopy(raw)
    changed["items"][1]["remaining_uncertainty_refs"] = ()
    proposal = RebuttalBundleDraft.model_validate(changed)
    with pytest.raises(RebuttalPromotionError, match="may not erase frozen material unknown"):
        _promote(proposal)


def test_rebuttal_judge_only_decision_basis_is_rejected() -> None:
    proposal = _proposal(claim=_claim(claim_type=CouncilClaimType.DECISION_BASIS))
    with pytest.raises(RebuttalPromotionError, match="DECISION_BASIS"):
        _promote(proposal)


def test_rebuttal_numeric_hallucination_is_rejected() -> None:
    proposal = _proposal(claim=_claim(text="The frozen metric was 99."))
    with pytest.raises(RebuttalPromotionError, match="numeric token"):
        _promote(proposal)


def test_rebuttal_initial_opinion_order_is_hash_bound_and_fail_closed() -> None:
    raw = _proposal().model_dump(mode="python")
    changed = deepcopy(raw)
    changed["initial_opinion_ids"] = ("OP_BEAR", "OP_BULL", "OP_RED_TEAM")
    proposal = RebuttalBundleDraft.model_validate(changed)
    with pytest.raises(RebuttalPromotionError, match="Bull/Bear/Red order"):
        _promote(proposal)
