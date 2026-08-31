from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from aic.domain.canonical import canonical_sha256
from aic.risk.b5_competition_artifacts_v1 import (
    B5CompetitionAcceptedProposal,
    B5CompetitionArtifactBundle,
)


ApprovalDecision = Literal["APPROVE", "REJECT", "KEEP_WATCHING"]


class B6ApprovalError(ValueError):
    """Fail-closed B6 approval/proposal contract violation."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise B6ApprovalError(message)


def _aware(value: datetime, *, field: str) -> datetime:
    _require(value.tzinfo is not None and value.utcoffset() is not None, f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _decimal_text(value: Decimal, *, field: str) -> str:
    _require(isinstance(value, Decimal) and value.is_finite(), f"{field} must be finite Decimal")
    return format(value, "f")


@dataclass(frozen=True)
class TradeProposalB6:
    proposal_id: str
    intent_id: str
    decision_id: str
    final_decision_hash: str
    b5_accepted_proposal_id: str
    b5_accepted_hash: str
    paper_account_id: str
    underlying_symbol: str
    option_symbol: str
    quantity: int
    action: str
    order_type: str
    time_in_force: str
    environment: str
    limit_price: Decimal
    max_loss_usd: Decimal
    risk_result_id: str
    risk_result_hash: str
    portfolio_impact_id: str
    portfolio_impact_hash: str
    snapshot_id: str
    snapshot_hash: str
    policy_lineage_hash: str
    canonical_payload_hash: str
    created_at: datetime
    expires_at: datetime
    status: str = "APPROVAL_PENDING"
    approval_authority: bool = False
    execution_authority: bool = False
    broker_writes: int = 0
    model_calls: int = 0

    def executable_payload(self) -> dict[str, object]:
        return {
            "intent_id": self.intent_id,
            "paper_account_id": self.paper_account_id,
            "underlying_symbol": self.underlying_symbol,
            "option_symbol": self.option_symbol,
            "quantity": self.quantity,
            "action": self.action,
            "order_type": self.order_type,
            "time_in_force": self.time_in_force,
            "environment": self.environment,
            "limit_price": _decimal_text(self.limit_price, field="limit_price"),
            "max_loss_usd": _decimal_text(self.max_loss_usd, field="max_loss_usd"),
            "decision_id": self.decision_id,
            "final_decision_hash": self.final_decision_hash,
            "b5_accepted_proposal_id": self.b5_accepted_proposal_id,
            "b5_accepted_hash": self.b5_accepted_hash,
            "risk_result_id": self.risk_result_id,
            "risk_result_hash": self.risk_result_hash,
            "portfolio_impact_id": self.portfolio_impact_id,
            "portfolio_impact_hash": self.portfolio_impact_hash,
            "snapshot_id": self.snapshot_id,
            "snapshot_hash": self.snapshot_hash,
            "policy_lineage_hash": self.policy_lineage_hash,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class ApprovalViewModel:
    proposal_id: str
    intent_id: str
    paper_account_id: str
    underlying_symbol: str
    option_symbol: str
    quantity: int
    action: str
    order_type: str
    time_in_force: str
    environment: str
    limit_price: Decimal
    max_loss_usd: Decimal
    risk_status: str
    risk_result_id: str
    risk_result_hash: str
    portfolio_impact_id: str
    portfolio_impact_hash: str
    pre_same_underlying_premium_at_risk: Decimal
    post_same_underlying_premium_at_risk: Decimal
    pre_aggregate_long_option_premium_at_risk: Decimal
    post_aggregate_long_option_premium_at_risk: Decimal
    remaining_safety_reserve_capacity_after_proposal: Decimal
    expires_at: datetime
    canonical_payload_hash: str
    displayed_payload_hash: str
    approval_authority: bool = False
    execution_authority: bool = False


@dataclass(frozen=True)
class OwnerAuthContext:
    owner_id: str
    session_id: str
    session_issued_at: datetime
    session_expires_at: datetime
    authentication_method_ref: str
    authentication_proof_hash: str
    status: str


@dataclass(frozen=True)
class ApprovalEnvelope:
    approval_id: str
    authenticated_owner_id: str
    authenticated_session_ref: str
    authentication_proof_hash: str
    proposal_id: str
    decision_id: str
    paper_account_id: str
    canonical_payload_hash: str
    displayed_payload_hash: str
    b5_accepted_hash: str
    risk_result_id: str
    risk_result_hash: str
    policy_lineage_hash: str
    decision: ApprovalDecision
    approved_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    approval_hash: str
    eligibility_to_enter_prepare: bool
    approval_authority: bool = True
    execution_authority: bool = False
    broker_writes: int = 0
    model_calls: int = 0


def _validate_proposal_integrity(proposal: TradeProposalB6) -> None:
    _require(proposal.action == "BUY_TO_OPEN", "B6 competition V1 requires BUY_TO_OPEN")
    _require(proposal.order_type == "LIMIT", "B6 competition V1 requires LIMIT")
    _require(proposal.time_in_force == "DAY", "B6 competition V1 requires DAY")
    _require(proposal.environment == "PAPER", "B6 competition V1 requires PAPER")
    _require(proposal.quantity > 0, "option quantity must be positive")
    _require(proposal.limit_price > 0, "option limit price must be positive")
    _require(proposal.max_loss_usd > 0, "max loss must be positive")
    _require(not proposal.execution_authority, "proposal cannot carry execution authority")
    expected_hash = canonical_sha256(proposal.executable_payload())
    _require(expected_hash == proposal.canonical_payload_hash, "proposal canonical payload drift")


def create_trade_proposal_from_b5(
    accepted: B5CompetitionAcceptedProposal,
    *,
    created_at: datetime,
    expires_at: datetime,
) -> TradeProposalB6:
    created = _aware(created_at, field="created_at")
    expires = _aware(expires_at, field="expires_at")
    _require(expires > created, "proposal expiry must be after creation")
    _require(accepted.action == "BUY_TO_OPEN", "B6 competition V1 requires BUY_TO_OPEN")
    _require(accepted.order_type == "LIMIT", "B6 competition V1 requires LIMIT")
    _require(accepted.time_in_force == "DAY", "B6 competition V1 requires DAY")
    _require(accepted.environment == "PAPER", "B6 competition V1 requires PAPER")
    _require(accepted.quantity > 0, "B5 accepted option quantity must be positive")
    _require(accepted.limit_price > 0, "B5 accepted option limit price must be positive")
    _require(accepted.max_loss_usd > 0, "B5 accepted max loss must be positive")
    _require(not accepted.execution_authority, "B5 accepted proposal cannot carry execution authority")

    intent_seed = {
        "b5_accepted_hash": accepted.accepted_hash,
        "paper_account_id": accepted.paper_account_id,
        "created_at": created,
    }
    intent_id = f"AIC6-{canonical_sha256(intent_seed)[:24]}"
    provisional = {
        "intent_id": intent_id,
        "paper_account_id": accepted.paper_account_id,
        "underlying_symbol": accepted.underlying_symbol,
        "option_symbol": accepted.option_symbol,
        "quantity": accepted.quantity,
        "action": accepted.action,
        "order_type": accepted.order_type,
        "time_in_force": accepted.time_in_force,
        "environment": accepted.environment,
        "limit_price": _decimal_text(accepted.limit_price, field="limit_price"),
        "max_loss_usd": _decimal_text(accepted.max_loss_usd, field="max_loss_usd"),
        "decision_id": accepted.decision_id,
        "final_decision_hash": accepted.final_decision_hash,
        "b5_accepted_proposal_id": accepted.accepted_proposal_id,
        "b5_accepted_hash": accepted.accepted_hash,
        "risk_result_id": accepted.risk_result_id,
        "risk_result_hash": accepted.risk_result_hash,
        "portfolio_impact_id": accepted.portfolio_impact_id,
        "portfolio_impact_hash": accepted.portfolio_impact_hash,
        "snapshot_id": accepted.snapshot_id,
        "snapshot_hash": accepted.snapshot_hash,
        "policy_lineage_hash": accepted.policy_lineage_hash,
        "expires_at": expires,
    }
    payload_hash = canonical_sha256(provisional)
    proposal_id = f"B6PROPOSAL:{canonical_sha256({'payload_hash': payload_hash, 'created_at': created})[:24]}"
    proposal = TradeProposalB6(
        proposal_id=proposal_id,
        intent_id=intent_id,
        decision_id=accepted.decision_id,
        final_decision_hash=accepted.final_decision_hash,
        b5_accepted_proposal_id=accepted.accepted_proposal_id,
        b5_accepted_hash=accepted.accepted_hash,
        paper_account_id=accepted.paper_account_id,
        underlying_symbol=accepted.underlying_symbol,
        option_symbol=accepted.option_symbol,
        quantity=accepted.quantity,
        action=accepted.action,
        order_type=accepted.order_type,
        time_in_force=accepted.time_in_force,
        environment=accepted.environment,
        limit_price=accepted.limit_price,
        max_loss_usd=accepted.max_loss_usd,
        risk_result_id=accepted.risk_result_id,
        risk_result_hash=accepted.risk_result_hash,
        portfolio_impact_id=accepted.portfolio_impact_id,
        portfolio_impact_hash=accepted.portfolio_impact_hash,
        snapshot_id=accepted.snapshot_id,
        snapshot_hash=accepted.snapshot_hash,
        policy_lineage_hash=accepted.policy_lineage_hash,
        canonical_payload_hash=payload_hash,
        created_at=created,
        expires_at=expires,
    )
    _validate_proposal_integrity(proposal)
    return proposal


def create_approval_view(
    proposal: TradeProposalB6,
    *,
    b5_artifacts: B5CompetitionArtifactBundle,
) -> ApprovalViewModel:
    _validate_proposal_integrity(proposal)
    accepted = b5_artifacts.accepted_proposal
    _require(accepted is not None, "B5 accepted proposal missing")
    _require(accepted.accepted_hash == proposal.b5_accepted_hash, "B5 accepted proposal lineage mismatch")
    _require(b5_artifacts.risk_result.status == "PASS", "B5 risk must be PASS for approval view")
    _require(b5_artifacts.risk_result.risk_result_id == proposal.risk_result_id, "B5 risk result id mismatch")
    _require(b5_artifacts.risk_result.risk_result_hash == proposal.risk_result_hash, "B5 risk result hash mismatch")
    impact = b5_artifacts.portfolio_impact
    _require(impact.portfolio_impact_id == proposal.portfolio_impact_id, "B5 portfolio impact id mismatch")
    _require(impact.impact_hash == proposal.portfolio_impact_hash, "B5 portfolio impact hash mismatch")
    _require(impact.post_same_underlying_premium_at_risk is not None, "post same-underlying risk missing")
    _require(impact.post_aggregate_long_option_premium_at_risk is not None, "post aggregate risk missing")
    _require(impact.remaining_safety_reserve_capacity_after_proposal is not None, "post safety reserve missing")
    return ApprovalViewModel(
        proposal_id=proposal.proposal_id,
        intent_id=proposal.intent_id,
        paper_account_id=proposal.paper_account_id,
        underlying_symbol=proposal.underlying_symbol,
        option_symbol=proposal.option_symbol,
        quantity=proposal.quantity,
        action=proposal.action,
        order_type=proposal.order_type,
        time_in_force=proposal.time_in_force,
        environment=proposal.environment,
        limit_price=proposal.limit_price,
        max_loss_usd=proposal.max_loss_usd,
        risk_status=b5_artifacts.risk_result.status,
        risk_result_id=proposal.risk_result_id,
        risk_result_hash=proposal.risk_result_hash,
        portfolio_impact_id=proposal.portfolio_impact_id,
        portfolio_impact_hash=proposal.portfolio_impact_hash,
        pre_same_underlying_premium_at_risk=impact.pre_same_underlying_premium_at_risk,
        post_same_underlying_premium_at_risk=impact.post_same_underlying_premium_at_risk,
        pre_aggregate_long_option_premium_at_risk=impact.pre_aggregate_long_option_premium_at_risk,
        post_aggregate_long_option_premium_at_risk=impact.post_aggregate_long_option_premium_at_risk,
        remaining_safety_reserve_capacity_after_proposal=impact.remaining_safety_reserve_capacity_after_proposal,
        expires_at=proposal.expires_at,
        canonical_payload_hash=proposal.canonical_payload_hash,
        displayed_payload_hash=proposal.canonical_payload_hash,
    )


def _validate_owner_auth(auth: OwnerAuthContext, *, at: datetime) -> None:
    now = _aware(at, field="auth_check_at")
    issued = _aware(auth.session_issued_at, field="session_issued_at")
    expires = _aware(auth.session_expires_at, field="session_expires_at")
    _require(auth.status == "VALID", "owner authentication context is not valid")
    _require(bool(auth.owner_id.strip()), "owner_id missing")
    _require(bool(auth.session_id.strip()), "session_id missing")
    _require(bool(auth.authentication_method_ref.strip()), "authentication method ref missing")
    _require(bool(auth.authentication_proof_hash.strip()), "authentication proof hash missing")
    _require(issued <= now < expires, "owner authentication session is not current")


def _validate_view_against_proposal(view: ApprovalViewModel, proposal: TradeProposalB6) -> None:
    _require(view.proposal_id == proposal.proposal_id, "approval view proposal mismatch")
    _require(view.intent_id == proposal.intent_id, "approval view intent mismatch")
    _require(view.paper_account_id == proposal.paper_account_id, "approval view account mismatch")
    _require(view.underlying_symbol == proposal.underlying_symbol, "approval view underlying mismatch")
    _require(view.option_symbol == proposal.option_symbol, "approval view option symbol mismatch")
    _require(view.quantity == proposal.quantity, "approval view quantity mismatch")
    _require(view.limit_price == proposal.limit_price, "approval view limit price mismatch")
    _require(view.max_loss_usd == proposal.max_loss_usd, "approval view max loss mismatch")
    _require(view.action == proposal.action, "approval view action mismatch")
    _require(view.order_type == proposal.order_type, "approval view order type mismatch")
    _require(view.time_in_force == proposal.time_in_force, "approval view TIF mismatch")
    _require(view.environment == proposal.environment, "approval view environment mismatch")
    _require(view.expires_at == proposal.expires_at, "approval view expiry mismatch")
    _require(view.risk_status == "PASS", "approval view risk status is not PASS")
    _require(view.risk_result_id == proposal.risk_result_id, "approval view risk id mismatch")
    _require(view.risk_result_hash == proposal.risk_result_hash, "approval view risk hash mismatch")
    _require(view.portfolio_impact_id == proposal.portfolio_impact_id, "approval view impact id mismatch")
    _require(view.portfolio_impact_hash == proposal.portfolio_impact_hash, "approval view impact hash mismatch")
    _require(view.canonical_payload_hash == proposal.canonical_payload_hash, "approval view canonical hash mismatch")
    _require(view.displayed_payload_hash == proposal.canonical_payload_hash, "displayed payload differs from executable payload")


def create_approval_envelope(
    *,
    proposal: TradeProposalB6,
    view: ApprovalViewModel,
    owner_auth: OwnerAuthContext,
    decision: ApprovalDecision,
    approved_at: datetime,
    expires_at: datetime,
) -> ApprovalEnvelope:
    approved = _aware(approved_at, field="approved_at")
    approval_expires = _aware(expires_at, field="approval_expires_at")
    _validate_proposal_integrity(proposal)
    _validate_owner_auth(owner_auth, at=approved)
    _validate_view_against_proposal(view, proposal)
    _require(decision in {"APPROVE", "REJECT", "KEEP_WATCHING"}, "unsupported approval decision")
    _require(approved < proposal.expires_at, "proposal expired before owner action")
    _require(approval_expires > approved, "approval expiry must be after owner action")
    _require(approval_expires <= proposal.expires_at, "approval cannot outlive proposal")

    identity = {
        "proposal_id": proposal.proposal_id,
        "decision": decision,
        "owner_id": owner_auth.owner_id,
        "session_id": owner_auth.session_id,
        "approved_at": approved,
        "canonical_payload_hash": proposal.canonical_payload_hash,
    }
    approval_id = f"B6APPROVAL:{canonical_sha256(identity)[:24]}"
    approval_payload = {
        **identity,
        "approval_id": approval_id,
        "paper_account_id": proposal.paper_account_id,
        "displayed_payload_hash": view.displayed_payload_hash,
        "b5_accepted_hash": proposal.b5_accepted_hash,
        "risk_result_id": proposal.risk_result_id,
        "risk_result_hash": proposal.risk_result_hash,
        "policy_lineage_hash": proposal.policy_lineage_hash,
        "authentication_proof_hash": owner_auth.authentication_proof_hash,
        "expires_at": approval_expires,
    }
    approval_hash = canonical_sha256(approval_payload)
    return ApprovalEnvelope(
        approval_id=approval_id,
        authenticated_owner_id=owner_auth.owner_id,
        authenticated_session_ref=owner_auth.session_id,
        authentication_proof_hash=owner_auth.authentication_proof_hash,
        proposal_id=proposal.proposal_id,
        decision_id=proposal.decision_id,
        paper_account_id=proposal.paper_account_id,
        canonical_payload_hash=proposal.canonical_payload_hash,
        displayed_payload_hash=view.displayed_payload_hash,
        b5_accepted_hash=proposal.b5_accepted_hash,
        risk_result_id=proposal.risk_result_id,
        risk_result_hash=proposal.risk_result_hash,
        policy_lineage_hash=proposal.policy_lineage_hash,
        decision=decision,
        approved_at=approved,
        expires_at=approval_expires,
        revoked_at=None,
        approval_hash=approval_hash,
        eligibility_to_enter_prepare=(decision == "APPROVE"),
    )


def validate_approval_for_prepare(
    *,
    proposal: TradeProposalB6,
    view: ApprovalViewModel,
    approval: ApprovalEnvelope,
    owner_auth: OwnerAuthContext,
    now: datetime,
) -> None:
    current = _aware(now, field="now")
    _validate_proposal_integrity(proposal)
    _validate_view_against_proposal(view, proposal)
    _validate_owner_auth(owner_auth, at=current)
    _require(approval.decision == "APPROVE", "only APPROVE can enter execution PREPARE")
    _require(approval.eligibility_to_enter_prepare, "approval does not grant PREPARE eligibility")
    _require(approval.revoked_at is None, "approval has been revoked")
    _require(current < approval.expires_at and current < proposal.expires_at, "approval or proposal expired")
    _require(approval.authenticated_owner_id == owner_auth.owner_id, "owner identity drift")
    _require(approval.authenticated_session_ref == owner_auth.session_id, "owner session drift")
    _require(approval.authentication_proof_hash == owner_auth.authentication_proof_hash, "authentication proof drift")
    _require(approval.proposal_id == proposal.proposal_id, "approval proposal mismatch")
    _require(approval.paper_account_id == proposal.paper_account_id, "approval account mismatch")
    _require(approval.canonical_payload_hash == proposal.canonical_payload_hash, "approval executable payload mismatch")
    _require(approval.displayed_payload_hash == proposal.canonical_payload_hash, "approval display payload mismatch")
    _require(approval.b5_accepted_hash == proposal.b5_accepted_hash, "B5 accepted lineage drift")
    _require(approval.risk_result_id == proposal.risk_result_id, "risk result id drift")
    _require(approval.risk_result_hash == proposal.risk_result_hash, "risk result hash drift")
    _require(approval.policy_lineage_hash == proposal.policy_lineage_hash, "policy lineage drift")
    _require(not approval.execution_authority, "approval must not be broker-write authority")
