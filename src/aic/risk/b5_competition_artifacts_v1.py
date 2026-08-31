from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256

from .b5_competition_pipeline_v1 import B5CompetitionProposal, B5ReadOnlyRiskSnapshot
from .options_competition_v1 import CompetitionOptionsPolicy, validate_b4_invest_handoff

ARTIFACT_VERSION = "ALPACA_COMPETITION_B5_OPTIONS_V1"


class B5CompetitionArtifactError(ValueError):
    pass


@dataclass(frozen=True)
class B5CompetitionSnapshotArtifact:
    snapshot_id: str
    decision_id: str
    final_decision_hash: str
    paper_account_id: str
    observed_at: datetime
    equity: Decimal
    same_underlying_committed_premium_at_risk: Decimal
    aggregate_committed_long_option_premium_at_risk: Decimal
    remaining_after_equity_safety_reserve: Decimal
    options_buying_power_after_open_orders: Decimal
    account_trading_eligible: bool
    source_receipt_ids: tuple[str, ...]
    policy_lineage_hash: str
    snapshot_hash: str
    execution_authority: bool = False
    approval_authority: bool = False


@dataclass(frozen=True)
class B5CompetitionProposalArtifact:
    proposal_id: str
    decision_id: str
    final_decision_hash: str
    snapshot_id: str
    snapshot_hash: str
    paper_account_id: str
    status: str
    underlying_symbol: str
    option_symbol: str | None
    quantity: int
    action: str | None
    order_type: str | None
    time_in_force: str | None
    environment: str | None
    limit_price: Decimal | None
    max_loss_usd: Decimal | None
    safe_premium_budget: Decimal | None
    reason_codes: tuple[str, ...]
    policy_lineage_hash: str
    proposal_hash: str
    execution_authority: bool = False
    approval_authority: bool = False


@dataclass(frozen=True)
class B5CompetitionPortfolioImpactArtifact:
    portfolio_impact_id: str
    proposal_id: str
    proposal_hash: str
    snapshot_id: str
    snapshot_hash: str
    pre_same_underlying_premium_at_risk: Decimal
    post_same_underlying_premium_at_risk: Decimal | None
    pre_aggregate_long_option_premium_at_risk: Decimal
    post_aggregate_long_option_premium_at_risk: Decimal | None
    proposed_max_loss_usd: Decimal | None
    remaining_safety_reserve_capacity_after_proposal: Decimal | None
    impact_hash: str


@dataclass(frozen=True)
class B5CompetitionRiskResultArtifact:
    risk_result_id: str
    decision_id: str
    final_decision_hash: str
    proposal_id: str
    proposal_hash: str
    snapshot_id: str
    snapshot_hash: str
    portfolio_impact_id: str
    portfolio_impact_hash: str
    status: str
    reason_codes: tuple[str, ...]
    calculated_at: datetime
    policy_lineage_hash: str
    risk_result_hash: str
    execution_authority: bool = False
    approval_authority: bool = False


@dataclass(frozen=True)
class B5CompetitionAcceptedProposal:
    accepted_proposal_id: str
    decision_id: str
    final_decision_hash: str
    proposal_id: str
    proposal_hash: str
    risk_result_id: str
    risk_result_hash: str
    portfolio_impact_id: str
    portfolio_impact_hash: str
    snapshot_id: str
    snapshot_hash: str
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
    policy_lineage_hash: str
    accepted_at: datetime
    accepted_hash: str
    execution_authority: bool = False
    approval_authority: bool = False


@dataclass(frozen=True)
class B5CompetitionArtifactBundle:
    snapshot: B5CompetitionSnapshotArtifact
    proposal: B5CompetitionProposalArtifact
    portfolio_impact: B5CompetitionPortfolioImpactArtifact
    risk_result: B5CompetitionRiskResultArtifact
    accepted_proposal: B5CompetitionAcceptedProposal | None


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise B5CompetitionArtifactError(message)


def _id(prefix: str, identity_payload: Mapping[str, Any]) -> str:
    return f"{prefix}:{canonical_sha256(identity_payload)[:24]}"


def _policy_lineage(final_decision: Mapping[str, Any], *, policy: CompetitionOptionsPolicy) -> str:
    policy_refs = final_decision.get("policy_refs")
    lifecycle_ref = final_decision.get("decision_lifecycle_policy_ref")
    _require(isinstance(policy_refs, Mapping), "FinalDecision policy_refs missing")
    _require(isinstance(lifecycle_ref, Mapping), "FinalDecision decision_lifecycle_policy_ref missing")
    lineage = {
        "final_decision_policy_refs": policy_refs,
        "decision_lifecycle_policy_ref": lifecycle_ref,
        "competition_options_policy": {
            "policy_id": policy.policy_id,
            "version": policy.version,
            "policy_hash": policy.policy_hash,
        },
    }
    return canonical_sha256(lineage)


def _materialize_snapshot(snapshot: B5ReadOnlyRiskSnapshot, *, decision_id: str, final_decision_hash: str, policy_lineage_hash: str) -> B5CompetitionSnapshotArtifact:
    values = (
        snapshot.equity,
        snapshot.same_underlying_committed_premium_at_risk,
        snapshot.aggregate_committed_long_option_premium_at_risk,
        snapshot.remaining_after_equity_safety_reserve,
        snapshot.options_buying_power_after_open_orders,
    )
    _require(snapshot.observed_at.tzinfo is not None, "snapshot timestamp must be aware")
    _require(bool(snapshot.paper_account_id.strip()), "paper account id missing")
    _require(snapshot.account_trading_eligible is not None, "account trading state must be known before artifact materialization")
    _require(all(isinstance(value, Decimal) and value.is_finite() for value in values), "snapshot authoritative numerics must be finite Decimal")
    _require(all(receipt.strip() for receipt in snapshot.receipt_ids()), "snapshot receipt lineage incomplete")
    payload = {
        "artifact_version": ARTIFACT_VERSION,
        "decision_id": decision_id,
        "final_decision_hash": final_decision_hash,
        "paper_account_id": snapshot.paper_account_id,
        "observed_at": snapshot.observed_at.astimezone(timezone.utc),
        "equity": snapshot.equity,
        "same_underlying_committed_premium_at_risk": snapshot.same_underlying_committed_premium_at_risk,
        "aggregate_committed_long_option_premium_at_risk": snapshot.aggregate_committed_long_option_premium_at_risk,
        "remaining_after_equity_safety_reserve": snapshot.remaining_after_equity_safety_reserve,
        "options_buying_power_after_open_orders": snapshot.options_buying_power_after_open_orders,
        "account_trading_eligible": snapshot.account_trading_eligible,
        "source_receipt_ids": snapshot.receipt_ids(),
        "policy_lineage_hash": policy_lineage_hash,
        "execution_authority": False,
        "approval_authority": False,
    }
    snapshot_id = _id("b5snap", payload)
    snapshot_hash = canonical_sha256({"snapshot_id": snapshot_id, **payload})
    return B5CompetitionSnapshotArtifact(
        snapshot_id=snapshot_id,
        snapshot_hash=snapshot_hash,
        decision_id=decision_id,
        final_decision_hash=final_decision_hash,
        paper_account_id=snapshot.paper_account_id,
        observed_at=snapshot.observed_at.astimezone(timezone.utc),
        equity=snapshot.equity,
        same_underlying_committed_premium_at_risk=snapshot.same_underlying_committed_premium_at_risk,
        aggregate_committed_long_option_premium_at_risk=snapshot.aggregate_committed_long_option_premium_at_risk,
        remaining_after_equity_safety_reserve=snapshot.remaining_after_equity_safety_reserve,
        options_buying_power_after_open_orders=snapshot.options_buying_power_after_open_orders,
        account_trading_eligible=bool(snapshot.account_trading_eligible),
        source_receipt_ids=snapshot.receipt_ids(),
        policy_lineage_hash=policy_lineage_hash,
    )


def _materialize_proposal(proposal: B5CompetitionProposal, *, snapshot: B5CompetitionSnapshotArtifact, policy_lineage_hash: str) -> B5CompetitionProposalArtifact:
    _require(proposal.decision_id == snapshot.decision_id, "proposal decision mismatch")
    _require(proposal.final_decision_hash == snapshot.final_decision_hash, "proposal decision hash mismatch")
    _require(proposal.paper_account_id == snapshot.paper_account_id, "proposal paper account mismatch")
    payload = {
        "artifact_version": ARTIFACT_VERSION,
        "decision_id": proposal.decision_id,
        "final_decision_hash": proposal.final_decision_hash,
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_hash": snapshot.snapshot_hash,
        "paper_account_id": proposal.paper_account_id,
        "status": proposal.status,
        "underlying_symbol": proposal.underlying_symbol,
        "option_symbol": proposal.option_symbol,
        "quantity": proposal.quantity,
        "action": proposal.action,
        "order_type": proposal.order_type,
        "time_in_force": proposal.time_in_force,
        "environment": proposal.environment,
        "limit_price": proposal.initial_limit_price,
        "max_loss_usd": proposal.max_loss_usd,
        "safe_premium_budget": proposal.safe_premium_budget,
        "reason_codes": proposal.reason_codes,
        "source_receipt_ids": proposal.source_receipt_ids,
        "option_source_receipt_id": proposal.option_source_receipt_id,
        "policy_lineage_hash": policy_lineage_hash,
        "execution_authority": False,
        "approval_authority": False,
    }
    proposal_id = _id("b5proposal", payload)
    proposal_hash = canonical_sha256({"proposal_id": proposal_id, **payload})
    return B5CompetitionProposalArtifact(
        proposal_id=proposal_id,
        proposal_hash=proposal_hash,
        decision_id=proposal.decision_id,
        final_decision_hash=proposal.final_decision_hash,
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=snapshot.snapshot_hash,
        paper_account_id=proposal.paper_account_id,
        status=proposal.status,
        underlying_symbol=proposal.underlying_symbol,
        option_symbol=proposal.option_symbol,
        quantity=proposal.quantity,
        action=proposal.action,
        order_type=proposal.order_type,
        time_in_force=proposal.time_in_force,
        environment=proposal.environment,
        limit_price=proposal.initial_limit_price,
        max_loss_usd=proposal.max_loss_usd,
        safe_premium_budget=proposal.safe_premium_budget,
        reason_codes=proposal.reason_codes,
        policy_lineage_hash=policy_lineage_hash,
    )


def _materialize_impact(proposal: B5CompetitionProposalArtifact, *, snapshot: B5CompetitionSnapshotArtifact) -> B5CompetitionPortfolioImpactArtifact:
    max_loss = proposal.max_loss_usd if proposal.status == "PASS" else None
    post_same = snapshot.same_underlying_committed_premium_at_risk + max_loss if max_loss is not None else None
    post_aggregate = snapshot.aggregate_committed_long_option_premium_at_risk + max_loss if max_loss is not None else None
    post_reserve = max(Decimal("0"), snapshot.remaining_after_equity_safety_reserve - max_loss) if max_loss is not None else None
    payload = {
        "artifact_version": ARTIFACT_VERSION,
        "proposal_id": proposal.proposal_id,
        "proposal_hash": proposal.proposal_hash,
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_hash": snapshot.snapshot_hash,
        "pre_same_underlying_premium_at_risk": snapshot.same_underlying_committed_premium_at_risk,
        "post_same_underlying_premium_at_risk": post_same,
        "pre_aggregate_long_option_premium_at_risk": snapshot.aggregate_committed_long_option_premium_at_risk,
        "post_aggregate_long_option_premium_at_risk": post_aggregate,
        "proposed_max_loss_usd": max_loss,
        "remaining_safety_reserve_capacity_after_proposal": post_reserve,
    }
    impact_id = _id("b5impact", payload)
    impact_hash = canonical_sha256({"portfolio_impact_id": impact_id, **payload})
    return B5CompetitionPortfolioImpactArtifact(
        portfolio_impact_id=impact_id,
        impact_hash=impact_hash,
        proposal_id=proposal.proposal_id,
        proposal_hash=proposal.proposal_hash,
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=snapshot.snapshot_hash,
        pre_same_underlying_premium_at_risk=snapshot.same_underlying_committed_premium_at_risk,
        post_same_underlying_premium_at_risk=post_same,
        pre_aggregate_long_option_premium_at_risk=snapshot.aggregate_committed_long_option_premium_at_risk,
        post_aggregate_long_option_premium_at_risk=post_aggregate,
        proposed_max_loss_usd=max_loss,
        remaining_safety_reserve_capacity_after_proposal=post_reserve,
    )


def _materialize_risk(proposal: B5CompetitionProposalArtifact, *, snapshot: B5CompetitionSnapshotArtifact, impact: B5CompetitionPortfolioImpactArtifact, calculated_at: datetime, policy_lineage_hash: str) -> B5CompetitionRiskResultArtifact:
    _require(calculated_at.tzinfo is not None, "risk result timestamp must be aware")
    payload = {
        "artifact_version": ARTIFACT_VERSION,
        "decision_id": proposal.decision_id,
        "final_decision_hash": proposal.final_decision_hash,
        "proposal_id": proposal.proposal_id,
        "proposal_hash": proposal.proposal_hash,
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_hash": snapshot.snapshot_hash,
        "portfolio_impact_id": impact.portfolio_impact_id,
        "portfolio_impact_hash": impact.impact_hash,
        "status": proposal.status,
        "reason_codes": proposal.reason_codes,
        "calculated_at": calculated_at.astimezone(timezone.utc),
        "policy_lineage_hash": policy_lineage_hash,
        "execution_authority": False,
        "approval_authority": False,
    }
    risk_id = _id("b5risk", payload)
    risk_hash = canonical_sha256({"risk_result_id": risk_id, **payload})
    return B5CompetitionRiskResultArtifact(
        risk_result_id=risk_id,
        risk_result_hash=risk_hash,
        decision_id=proposal.decision_id,
        final_decision_hash=proposal.final_decision_hash,
        proposal_id=proposal.proposal_id,
        proposal_hash=proposal.proposal_hash,
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=snapshot.snapshot_hash,
        portfolio_impact_id=impact.portfolio_impact_id,
        portfolio_impact_hash=impact.impact_hash,
        status=proposal.status,
        reason_codes=proposal.reason_codes,
        calculated_at=calculated_at.astimezone(timezone.utc),
        policy_lineage_hash=policy_lineage_hash,
    )


def _materialize_accepted(proposal: B5CompetitionProposalArtifact, *, snapshot: B5CompetitionSnapshotArtifact, impact: B5CompetitionPortfolioImpactArtifact, risk_result: B5CompetitionRiskResultArtifact, accepted_at: datetime, policy_lineage_hash: str) -> B5CompetitionAcceptedProposal | None:
    if risk_result.status != "PASS":
        return None
    _require(accepted_at.tzinfo is not None, "accepted timestamp must be aware")
    _require(proposal.status == "PASS", "PASS risk must bind PASS proposal")
    _require(proposal.option_symbol is not None, "PASS proposal option symbol missing")
    _require(proposal.quantity > 0, "PASS proposal quantity invalid")
    _require(proposal.action == "BUY_TO_OPEN", "PASS proposal action drift")
    _require(proposal.order_type == "LIMIT", "PASS proposal order type drift")
    _require(proposal.time_in_force == "DAY", "PASS proposal TIF drift")
    _require(proposal.environment == "PAPER", "PASS proposal environment drift")
    _require(isinstance(proposal.limit_price, Decimal) and proposal.limit_price > 0, "PASS proposal limit price invalid")
    _require(isinstance(proposal.max_loss_usd, Decimal) and proposal.max_loss_usd > 0, "PASS proposal max loss invalid")
    _require(risk_result.proposal_hash == proposal.proposal_hash, "risk/proposal hash mismatch")
    _require(risk_result.snapshot_hash == snapshot.snapshot_hash, "risk/snapshot hash mismatch")
    _require(risk_result.portfolio_impact_hash == impact.impact_hash, "risk/impact hash mismatch")
    payload = {
        "artifact_version": ARTIFACT_VERSION,
        "decision_id": proposal.decision_id,
        "final_decision_hash": proposal.final_decision_hash,
        "proposal_id": proposal.proposal_id,
        "proposal_hash": proposal.proposal_hash,
        "risk_result_id": risk_result.risk_result_id,
        "risk_result_hash": risk_result.risk_result_hash,
        "portfolio_impact_id": impact.portfolio_impact_id,
        "portfolio_impact_hash": impact.impact_hash,
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_hash": snapshot.snapshot_hash,
        "paper_account_id": proposal.paper_account_id,
        "underlying_symbol": proposal.underlying_symbol,
        "option_symbol": proposal.option_symbol,
        "quantity": proposal.quantity,
        "action": proposal.action,
        "order_type": proposal.order_type,
        "time_in_force": proposal.time_in_force,
        "environment": proposal.environment,
        "limit_price": proposal.limit_price,
        "max_loss_usd": proposal.max_loss_usd,
        "policy_lineage_hash": policy_lineage_hash,
        "accepted_at": accepted_at.astimezone(timezone.utc),
        "execution_authority": False,
        "approval_authority": False,
    }
    accepted_id = _id("b5accepted", payload)
    accepted_hash = canonical_sha256({"accepted_proposal_id": accepted_id, **payload})
    return B5CompetitionAcceptedProposal(
        accepted_proposal_id=accepted_id,
        accepted_hash=accepted_hash,
        decision_id=proposal.decision_id,
        final_decision_hash=proposal.final_decision_hash,
        proposal_id=proposal.proposal_id,
        proposal_hash=proposal.proposal_hash,
        risk_result_id=risk_result.risk_result_id,
        risk_result_hash=risk_result.risk_result_hash,
        portfolio_impact_id=impact.portfolio_impact_id,
        portfolio_impact_hash=impact.impact_hash,
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=snapshot.snapshot_hash,
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
        policy_lineage_hash=policy_lineage_hash,
        accepted_at=accepted_at.astimezone(timezone.utc),
    )


def materialize_b5_competition_artifacts(*, final_decision: Mapping[str, Any], raw_snapshot: B5ReadOnlyRiskSnapshot, proposal_result: B5CompetitionProposal, policy: CompetitionOptionsPolicy, calculated_at: datetime) -> B5CompetitionArtifactBundle:
    handoff = validate_b4_invest_handoff(final_decision, expected_mandate_version=policy.version)
    _require(proposal_result.decision_id == handoff.decision_id, "proposal does not bind current FinalDecision")
    _require(proposal_result.final_decision_hash == handoff.final_decision_hash, "proposal FinalDecision hash mismatch")
    _require(proposal_result.policy_hash == policy.policy_hash, "proposal options policy hash mismatch")
    lineage_hash = _policy_lineage(final_decision, policy=policy)
    snapshot = _materialize_snapshot(raw_snapshot, decision_id=handoff.decision_id, final_decision_hash=handoff.final_decision_hash, policy_lineage_hash=lineage_hash)
    proposal = _materialize_proposal(proposal_result, snapshot=snapshot, policy_lineage_hash=lineage_hash)
    impact = _materialize_impact(proposal, snapshot=snapshot)
    risk = _materialize_risk(proposal, snapshot=snapshot, impact=impact, calculated_at=calculated_at, policy_lineage_hash=lineage_hash)
    accepted = _materialize_accepted(proposal, snapshot=snapshot, impact=impact, risk_result=risk, accepted_at=calculated_at, policy_lineage_hash=lineage_hash)
    return B5CompetitionArtifactBundle(snapshot=snapshot, proposal=proposal, portfolio_impact=impact, risk_result=risk, accepted_proposal=accepted)
