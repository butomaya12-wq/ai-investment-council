from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from aic.council.model_policy import API_INVARIANTS, MODEL_POLICY_VERSION
from aic.council.models import (
    CouncilClaimKind,
    CouncilClaimType,
    CouncilInputBundle,
    CouncilLane,
    CouncilMateriality,
    CouncilSupportStatus,
    ProposedCouncilClaim,
)
from aic.council.policy import COUNCIL_POLICY, JUDGE_POLICY


def _claim_payload():
    return {
        "claim_local_ref": "C1",
        "candidate_id": "NVDA",
        "lane": "BULL",
        "claim_type": "ARGUMENT",
        "claim_text": "Supported business-model thesis.",
        "source_material_claim_ids": ["B3_CLAIM_1"],
        "computed_value_ids": [],
        "conflict_ids": [],
        "claim_kind": "INFERENCE",
        "support_status": "SUPPORTED",
        "materiality": "MATERIAL",
    }


def test_model_claim_has_local_ref_and_forbids_canonical_claim_id() -> None:
    claim = ProposedCouncilClaim.model_validate(_claim_payload())
    assert claim.claim_local_ref == "C1"
    assert claim.lane is CouncilLane.BULL
    assert claim.claim_type is CouncilClaimType.ARGUMENT
    assert claim.claim_kind is CouncilClaimKind.INFERENCE
    assert claim.support_status is CouncilSupportStatus.SUPPORTED
    assert claim.materiality is CouncilMateriality.MATERIAL

    with pytest.raises(ValidationError):
        ProposedCouncilClaim.model_validate({**_claim_payload(), "claim_id": "MODEL_MUST_NOT_OWN_THIS"})
    with pytest.raises(ValidationError):
        ProposedCouncilClaim.model_validate({**_claim_payload(), "council_claim_id": "LEGACY_FORBIDDEN"})


def test_council_policy_freezes_9_3_1_topology_and_zero_retrieval_authority() -> None:
    assert [lane.value for lane in COUNCIL_POLICY.allowed_roles] == ["BULL", "BEAR", "RED_TEAM"]
    assert COUNCIL_POLICY.initial_rounds == 1
    assert COUNCIL_POLICY.rebuttal_rounds_max == 1
    assert COUNCIL_POLICY.max_initial_model_calls == 9
    assert COUNCIL_POLICY.max_rebuttal_model_calls == 3
    assert COUNCIL_POLICY.max_judge_model_calls == 1
    assert COUNCIL_POLICY.repair_attempt_limit_per_output == 1
    assert COUNCIL_POLICY.new_evidence_allowed is False
    assert COUNCIL_POLICY.new_provider_reads_allowed is False
    assert COUNCIL_POLICY.numeric_authority == "NONE"
    assert COUNCIL_POLICY.majority_vote_rule == "FORBIDDEN"

    assert JUDGE_POLICY.majority_vote_rule == "FORBIDDEN"
    assert JUDGE_POLICY.red_team_directional_vote is False
    assert JUDGE_POLICY.execution_authority is False
    assert JUDGE_POLICY.risk_authority is False
    assert JUDGE_POLICY.approval_authority is False


def test_b4_api_invariants_are_tool_free_and_store_false() -> None:
    assert MODEL_POLICY_VERSION == "MODEL_POLICY_vB4_0_1"
    assert API_INVARIANTS.api_family == "RESPONSES"
    assert API_INVARIANTS.store is False
    assert API_INVARIANTS.tools_enabled is False
    assert API_INVARIANTS.hosted_tools_enabled is False
    assert API_INVARIANTS.provider_credentials_model_visible is False
    assert API_INVARIANTS.broker_credentials_model_visible is False
    assert API_INVARIANTS.structured_outputs_required is True


def test_council_input_bundle_is_frozen_and_self_hashed() -> None:
    bundle = CouncilInputBundle.from_unhashed(
        bundle_id="B4_COUNCIL_INPUT_NVDA_x",
        candidate_id="NVDA",
        candidate_packet_id="B3_PACKET_NVDA",
        candidate_packet_hash="a" * 64,
        research_snapshot_id="B3_RESEARCH_NVDA",
        research_snapshot_hash="b" * 64,
        b2_snapshot_id="B2_SNAPSHOT",
        deep_comparison_id="B2_DEEP",
        mandate_version="ALPACA_COMPETITION_V1_2026_08_29",
        council_policy_version="COUNCIL_POLICY_vB4_0_1",
        judge_policy_version="JUDGE_POLICY_vB4_0_1",
        model_policy_version="MODEL_POLICY_vB4_0_1",
        allowed_material_claim_ids=("C1",),
        allowed_computed_value_ids=("CV1",),
        allowed_conflict_ids=(),
        shared_portfolio_context_refs=(),
        created_at=datetime(2026, 8, 29, 15, 0, tzinfo=UTC),
    )
    assert len(bundle.bundle_hash) == 64
    with pytest.raises(ValidationError):
        CouncilInputBundle.model_validate({**bundle.model_dump(mode="json"), "bundle_hash": "0" * 64})
    with pytest.raises(ValidationError):
        CouncilInputBundle.model_validate({**bundle.model_dump(mode="json"), "candidate_id": "MSFT"})
    with pytest.raises(ValidationError):
        CouncilInputBundle.model_validate({**bundle.model_dump(mode="json"), "allowed_material_claim_ids": ["C1", "C1"]})


def test_b4_models_are_immutable() -> None:
    claim = ProposedCouncilClaim.model_validate(_claim_payload())
    with pytest.raises(ValidationError):
        claim.candidate_id = "MSFT"
