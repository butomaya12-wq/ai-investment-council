from __future__ import annotations

from aic.council.initial_runtime_diagnosis import material_support_violations
from aic.council.models import (
    CouncilClaimKind,
    CouncilClaimType,
    CouncilLane,
    CouncilMateriality,
    CouncilSupportStatus,
    ProposedCouncilClaim,
    RoleBoundaryStatus,
)
from aic.council.proposal import InitialCouncilOpinionProposal


def _proposal(*, support_status: CouncilSupportStatus, materiality: CouncilMateriality):
    claim = ProposedCouncilClaim(
        claim_local_ref="C1",
        candidate_id="NVDA",
        lane=CouncilLane.BULL,
        claim_type=CouncilClaimType.ARGUMENT,
        claim_text="Evidence-bounded test claim.",
        source_material_claim_ids=("SRC1",),
        computed_value_ids=(),
        conflict_ids=(),
        claim_kind=CouncilClaimKind.INFERENCE,
        support_status=support_status,
        materiality=materiality,
    )
    return InitialCouncilOpinionProposal(
        opinion_id="OP1",
        candidate_id="NVDA",
        lane=CouncilLane.BULL,
        council_input_bundle_hash="bundle",
        candidate_packet_hash="packet",
        mandate_version="mandate",
        council_policy_version="council",
        model_policy_version="model",
        model_run_ref="run",
        proposed_claims=(claim,),
        primary_claim_ids=("C1",),
        critical_assumption_claim_ids=(),
        falsifier_claim_ids=(),
        material_unknown_refs=(),
        material_conflict_refs=(),
        research_reopen_required=False,
        research_reopen_reason_codes=(),
        role_boundary_status=RoleBoundaryStatus.VALID,
    )


def test_material_conflicted_claim_is_diagnosed_without_mutation():
    proposal = _proposal(
        support_status=CouncilSupportStatus.CONFLICTED,
        materiality=CouncilMateriality.MATERIAL,
    )
    violations = material_support_violations(proposal)
    assert len(violations) == 1
    assert violations[0].claim_local_ref == "C1"
    assert violations[0].support_status == "CONFLICTED"
    assert violations[0].materiality == "MATERIAL"
    assert proposal.proposed_claims[0].support_status == CouncilSupportStatus.CONFLICTED


def test_material_insufficient_claim_is_diagnosed():
    proposal = _proposal(
        support_status=CouncilSupportStatus.INSUFFICIENT,
        materiality=CouncilMateriality.MATERIAL,
    )
    violations = material_support_violations(proposal)
    assert len(violations) == 1
    assert violations[0].support_status == "INSUFFICIENT"


def test_supported_material_claim_is_not_diagnosed():
    proposal = _proposal(
        support_status=CouncilSupportStatus.SUPPORTED,
        materiality=CouncilMateriality.MATERIAL,
    )
    assert material_support_violations(proposal) == ()


def test_non_supported_supporting_claim_is_not_material_promotion_violation():
    proposal = _proposal(
        support_status=CouncilSupportStatus.CONFLICTED,
        materiality=CouncilMateriality.SUPPORTING,
    )
    assert material_support_violations(proposal) == ()
