from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from aic.approval.options_v1 import (
    B6ApprovalError,
    OwnerAuthContext,
    create_approval_envelope,
    create_approval_view,
    create_trade_proposal_from_b5,
    validate_approval_for_prepare,
)
from aic.risk.b5_competition_artifacts_v1 import (
    B5CompetitionAcceptedProposal,
    B5CompetitionArtifactBundle,
    B5CompetitionPortfolioImpactArtifact,
    B5CompetitionProposalArtifact,
    B5CompetitionRiskResultArtifact,
    B5CompetitionSnapshotArtifact,
)


NOW = datetime(2026, 8, 31, 15, 30, 0, tzinfo=timezone.utc)


def b5_bundle() -> B5CompetitionArtifactBundle:
    snapshot = B5CompetitionSnapshotArtifact(
        snapshot_id="b5:snapshot",
        decision_id="decision:nvda",
        final_decision_hash="a" * 64,
        paper_account_id="paper-account-1",
        observed_at=NOW - timedelta(seconds=20),
        equity=Decimal("100000"),
        same_underlying_committed_premium_at_risk=Decimal("900"),
        aggregate_committed_long_option_premium_at_risk=Decimal("1900"),
        remaining_after_equity_safety_reserve=Decimal("30000"),
        options_buying_power_after_open_orders=Decimal("80000"),
        account_trading_eligible=True,
        source_receipt_ids=("r1", "r2", "r3", "r4", "r5"),
        policy_lineage_hash="1" * 64,
        snapshot_hash="2" * 64,
    )
    proposal = B5CompetitionProposalArtifact(
        proposal_id="b5:proposal",
        decision_id="decision:nvda",
        final_decision_hash="a" * 64,
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=snapshot.snapshot_hash,
        paper_account_id=snapshot.paper_account_id,
        status="PASS",
        underlying_symbol="NVDA",
        option_symbol="NVDA261005C00220000",
        quantity=2,
        action="BUY_TO_OPEN",
        order_type="LIMIT",
        time_in_force="DAY",
        environment="PAPER",
        limit_price=Decimal("10.00"),
        max_loss_usd=Decimal("2000.00"),
        safe_premium_budget=Decimal("2100.00"),
        reason_codes=(),
        policy_lineage_hash="1" * 64,
        proposal_hash="3" * 64,
    )
    impact = B5CompetitionPortfolioImpactArtifact(
        portfolio_impact_id="b5:impact",
        proposal_id=proposal.proposal_id,
        proposal_hash=proposal.proposal_hash,
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=snapshot.snapshot_hash,
        pre_same_underlying_premium_at_risk=Decimal("900"),
        post_same_underlying_premium_at_risk=Decimal("2900.00"),
        pre_aggregate_long_option_premium_at_risk=Decimal("1900"),
        post_aggregate_long_option_premium_at_risk=Decimal("3900.00"),
        proposed_max_loss_usd=Decimal("2000.00"),
        remaining_safety_reserve_capacity_after_proposal=Decimal("28000.00"),
        impact_hash="4" * 64,
    )
    risk = B5CompetitionRiskResultArtifact(
        risk_result_id="b5:risk",
        decision_id="decision:nvda",
        final_decision_hash="a" * 64,
        proposal_id=proposal.proposal_id,
        proposal_hash=proposal.proposal_hash,
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=snapshot.snapshot_hash,
        portfolio_impact_id=impact.portfolio_impact_id,
        portfolio_impact_hash=impact.impact_hash,
        status="PASS",
        reason_codes=(),
        calculated_at=NOW - timedelta(seconds=10),
        policy_lineage_hash="1" * 64,
        risk_result_hash="5" * 64,
    )
    accepted = B5CompetitionAcceptedProposal(
        accepted_proposal_id="b5:accepted",
        decision_id="decision:nvda",
        final_decision_hash="a" * 64,
        proposal_id=proposal.proposal_id,
        proposal_hash=proposal.proposal_hash,
        risk_result_id=risk.risk_result_id,
        risk_result_hash=risk.risk_result_hash,
        portfolio_impact_id=impact.portfolio_impact_id,
        portfolio_impact_hash=impact.impact_hash,
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=snapshot.snapshot_hash,
        paper_account_id=snapshot.paper_account_id,
        underlying_symbol="NVDA",
        option_symbol="NVDA261005C00220000",
        quantity=2,
        action="BUY_TO_OPEN",
        order_type="LIMIT",
        time_in_force="DAY",
        environment="PAPER",
        limit_price=Decimal("10.00"),
        max_loss_usd=Decimal("2000.00"),
        policy_lineage_hash="1" * 64,
        accepted_at=NOW - timedelta(seconds=5),
        accepted_hash="6" * 64,
    )
    return B5CompetitionArtifactBundle(
        snapshot=snapshot,
        proposal=proposal,
        portfolio_impact=impact,
        risk_result=risk,
        accepted_proposal=accepted,
    )


def owner_auth(*, status: str = "VALID") -> OwnerAuthContext:
    return OwnerAuthContext(
        owner_id="owner:maya",
        session_id="session:1",
        session_issued_at=NOW - timedelta(minutes=5),
        session_expires_at=NOW + timedelta(minutes=30),
        authentication_method_ref="server-session",
        authentication_proof_hash="7" * 64,
        status=status,
    )


def proposal_and_view():
    bundle = b5_bundle()
    accepted = bundle.accepted_proposal
    assert accepted is not None
    proposal = create_trade_proposal_from_b5(
        accepted,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    view = create_approval_view(proposal, b5_artifacts=bundle)
    return bundle, proposal, view


def test_b6_copies_exact_options_economics_without_execution_authority():
    _bundle, proposal, _view = proposal_and_view()
    assert proposal.option_symbol == "NVDA261005C00220000"
    assert proposal.quantity == 2
    assert proposal.action == "BUY_TO_OPEN"
    assert proposal.order_type == "LIMIT"
    assert proposal.time_in_force == "DAY"
    assert proposal.environment == "PAPER"
    assert proposal.limit_price == Decimal("10.00")
    assert proposal.max_loss_usd == Decimal("2000.00")
    payload = proposal.executable_payload()
    assert payload["quantity"] == 2
    assert "notional" not in payload
    assert "qty" not in payload
    assert proposal.execution_authority is False
    assert proposal.broker_writes == 0
    assert proposal.model_calls == 0


def test_approval_view_exposes_risk_and_portfolio_impact_with_exact_display_binding():
    bundle, proposal, view = proposal_and_view()
    assert view.canonical_payload_hash == proposal.canonical_payload_hash
    assert view.displayed_payload_hash == proposal.canonical_payload_hash
    assert view.risk_status == "PASS"
    assert view.risk_result_hash == bundle.risk_result.risk_result_hash
    assert view.pre_same_underlying_premium_at_risk == Decimal("900")
    assert view.post_same_underlying_premium_at_risk == Decimal("2900.00")
    assert view.pre_aggregate_long_option_premium_at_risk == Decimal("1900")
    assert view.post_aggregate_long_option_premium_at_risk == Decimal("3900.00")
    assert view.remaining_safety_reserve_capacity_after_proposal == Decimal("28000.00")


def test_reject_and_keep_watching_never_enter_prepare():
    _bundle, proposal, view = proposal_and_view()
    for decision in ("REJECT", "KEEP_WATCHING"):
        approval = create_approval_envelope(
            proposal=proposal,
            view=view,
            owner_auth=owner_auth(),
            decision=decision,
            approved_at=NOW + timedelta(seconds=1),
            expires_at=NOW + timedelta(minutes=5),
        )
        assert approval.eligibility_to_enter_prepare is False
        assert approval.execution_authority is False
        assert approval.broker_writes == 0
        with pytest.raises(B6ApprovalError, match="only APPROVE"):
            validate_approval_for_prepare(
                proposal=proposal,
                view=view,
                approval=approval,
                owner_auth=owner_auth(),
                now=NOW + timedelta(minutes=1),
            )


def test_approve_requires_server_valid_auth_and_exact_display():
    _bundle, proposal, view = proposal_and_view()
    with pytest.raises(B6ApprovalError, match="authentication context"):
        create_approval_envelope(
            proposal=proposal,
            view=view,
            owner_auth=owner_auth(status="INVALID"),
            decision="APPROVE",
            approved_at=NOW + timedelta(seconds=1),
            expires_at=NOW + timedelta(minutes=5),
        )

    drifted_view = replace(view, quantity=1)
    with pytest.raises(B6ApprovalError, match="quantity mismatch"):
        create_approval_envelope(
            proposal=proposal,
            view=drifted_view,
            owner_auth=owner_auth(),
            decision="APPROVE",
            approved_at=NOW + timedelta(seconds=1),
            expires_at=NOW + timedelta(minutes=5),
        )


def test_approve_grants_prepare_eligibility_but_never_broker_write_authority():
    _bundle, proposal, view = proposal_and_view()
    approval = create_approval_envelope(
        proposal=proposal,
        view=view,
        owner_auth=owner_auth(),
        decision="APPROVE",
        approved_at=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=5),
    )
    assert approval.eligibility_to_enter_prepare is True
    assert approval.approval_authority is True
    assert approval.execution_authority is False
    assert approval.broker_writes == 0
    validate_approval_for_prepare(
        proposal=proposal,
        view=view,
        approval=approval,
        owner_auth=owner_auth(),
        now=NOW + timedelta(minutes=1),
    )


def test_post_approval_economic_mutation_is_caught_by_canonical_reconstruction():
    _bundle, proposal, view = proposal_and_view()
    approval = create_approval_envelope(
        proposal=proposal,
        view=view,
        owner_auth=owner_auth(),
        decision="APPROVE",
        approved_at=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=5),
    )
    drifted_proposal = replace(proposal, limit_price=Decimal("10.01"))
    with pytest.raises(B6ApprovalError, match="canonical payload drift"):
        validate_approval_for_prepare(
            proposal=drifted_proposal,
            view=view,
            approval=approval,
            owner_auth=owner_auth(),
            now=NOW + timedelta(minutes=1),
        )
