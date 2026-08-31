from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from aic.approval.options_v1 import (
    ApprovalEnvelope,
    ApprovalViewModel,
    OwnerAuthContext,
    TradeProposalB6,
    validate_approval_for_prepare,
)
from aic.domain.canonical import canonical_sha256
from aic.risk.b5_competition_run_v1 import (
    B5CompetitionRunResult,
    B5RawAlpacaReadBundle,
    run_b5_from_alpaca_reads,
)
from aic.risk.options_competition_v1 import CompetitionOptionsPolicy


class B6PrepareError(ValueError):
    """Fail-closed error while preparing an approved PAPER option intent."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise B6PrepareError(message)


def _aware(value: datetime, *, field: str) -> datetime:
    _require(value.tzinfo is not None and value.utcoffset() is not None, f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class B6ExecutionLockContext:
    paper_account_id: str
    holder_intent_id: str
    lock_epoch: int
    acquired_at: datetime
    expires_at: datetime
    status: str = "HELD"


@dataclass(frozen=True)
class B6PreSubmitSnapshot:
    snapshot_id: str
    snapshot_hash: str
    proposal_id: str
    canonical_payload_hash: str
    approval_id: str
    approval_hash: str
    paper_account_id: str
    lock_epoch: int
    b5_fresh_snapshot_id: str
    b5_fresh_snapshot_hash: str
    source_receipt_ids: tuple[str, ...]
    captured_at: datetime
    status: str = "COMPLETE"
    approval_authority: bool = False
    execution_authority: bool = False


@dataclass(frozen=True)
class B6PrepareRiskResult:
    risk_result_id: str
    risk_result_hash: str
    proposal_id: str
    canonical_payload_hash: str
    pre_submit_snapshot_id: str
    pre_submit_snapshot_hash: str
    b5_fresh_risk_result_id: str
    b5_fresh_risk_result_hash: str
    status: str
    calculated_at: datetime
    approval_authority: bool = False
    execution_authority: bool = False


@dataclass(frozen=True)
class B6ExecutionIntent:
    intent_id: str
    proposal_id: str
    canonical_payload_hash: str
    approval_id: str
    approval_hash: str
    paper_account_id: str
    lock_epoch: int
    prepare_snapshot_id: str
    prepare_snapshot_hash: str
    prepare_risk_result_id: str
    prepare_risk_result_hash: str
    policy_lineage_hash: str
    prepared_at: datetime
    state: str = "PREPARED"
    broker_call_started_at: datetime | None = None
    submit_attempt_count: int = 0
    broker_writes: int = 0
    model_calls: int = 0
    approval_authority: bool = False
    execution_authority: bool = False


@dataclass(frozen=True)
class B6PrepareResult:
    fresh_b5: B5CompetitionRunResult
    pre_submit_snapshot: B6PreSubmitSnapshot
    prepare_risk: B6PrepareRiskResult
    intent: B6ExecutionIntent
    broker_writes: int = 0
    model_calls: int = 0
    execution_authority: bool = False


def _validate_lock(
    lock: B6ExecutionLockContext,
    *,
    proposal: TradeProposalB6,
    now: datetime,
) -> None:
    current = _aware(now, field="now")
    acquired = _aware(lock.acquired_at, field="lock.acquired_at")
    expires = _aware(lock.expires_at, field="lock.expires_at")
    _require(lock.status == "HELD", "execution lock is not held")
    _require(lock.paper_account_id == proposal.paper_account_id, "execution lock account mismatch")
    _require(lock.holder_intent_id == proposal.intent_id, "execution lock intent mismatch")
    _require(isinstance(lock.lock_epoch, int) and not isinstance(lock.lock_epoch, bool) and lock.lock_epoch > 0, "lock_epoch must be positive integer")
    _require(acquired <= current < expires, "execution lock is not current")


def _assert_same_approved_economics(
    *,
    proposal: TradeProposalB6,
    fresh_b5: B5CompetitionRunResult,
) -> None:
    fresh = fresh_b5.artifacts.accepted_proposal
    _require(fresh_b5.proposal_result.status == "PASS", "fresh prepare risk is not PASS")
    _require(fresh_b5.artifacts.risk_result.status == "PASS", "fresh B5 risk artifact is not PASS")
    _require(fresh is not None, "fresh B5 accepted proposal missing")
    comparisons = {
        "paper_account_id": (fresh.paper_account_id, proposal.paper_account_id),
        "underlying_symbol": (fresh.underlying_symbol, proposal.underlying_symbol),
        "option_symbol": (fresh.option_symbol, proposal.option_symbol),
        "quantity": (fresh.quantity, proposal.quantity),
        "action": (fresh.action, proposal.action),
        "order_type": (fresh.order_type, proposal.order_type),
        "time_in_force": (fresh.time_in_force, proposal.time_in_force),
        "environment": (fresh.environment, proposal.environment),
        "limit_price": (fresh.limit_price, proposal.limit_price),
        "max_loss_usd": (fresh.max_loss_usd, proposal.max_loss_usd),
        "policy_lineage_hash": (fresh.policy_lineage_hash, proposal.policy_lineage_hash),
    }
    drifted = tuple(name for name, (actual, approved) in comparisons.items() if actual != approved)
    _require(not drifted, "STATE_DRIFT:" + ",".join(drifted))


def prepare_b6_execution(
    *,
    final_decision: Mapping[str, Any],
    proposal: TradeProposalB6,
    view: ApprovalViewModel,
    approval: ApprovalEnvelope,
    owner_auth: OwnerAuthContext,
    execution_lock: B6ExecutionLockContext,
    fresh_reads: B5RawAlpacaReadBundle,
    policy: CompetitionOptionsPolicy,
    now: datetime,
) -> B6PrepareResult:
    """Revalidate the exact approved option trade under a held PAPER account lock.

    No broker write is possible here. Fresh Alpaca reads are supplied by the read-only
    runtime and are re-run through the deterministic B5 engine. PREPARED is emitted only
    when the fresh PASS reproduces the exact option, quantity, limit and policy lineage
    that the owner approved.
    """

    current = _aware(now, field="now")
    validate_approval_for_prepare(
        proposal=proposal,
        view=view,
        approval=approval,
        owner_auth=owner_auth,
        now=current,
    )
    _validate_lock(execution_lock, proposal=proposal, now=current)

    fresh_b5 = run_b5_from_alpaca_reads(
        final_decision=final_decision,
        underlying_symbol=proposal.underlying_symbol,
        raw_reads=fresh_reads,
        policy=policy,
    )
    _assert_same_approved_economics(proposal=proposal, fresh_b5=fresh_b5)

    fresh_snapshot = fresh_b5.artifacts.snapshot
    snapshot_payload = {
        "proposal_id": proposal.proposal_id,
        "canonical_payload_hash": proposal.canonical_payload_hash,
        "approval_id": approval.approval_id,
        "approval_hash": approval.approval_hash,
        "paper_account_id": proposal.paper_account_id,
        "lock_epoch": execution_lock.lock_epoch,
        "b5_fresh_snapshot_id": fresh_snapshot.snapshot_id,
        "b5_fresh_snapshot_hash": fresh_snapshot.snapshot_hash,
        "source_receipt_ids": fresh_snapshot.source_receipt_ids,
        "captured_at": fresh_reads.observed_at.astimezone(timezone.utc),
        "status": "COMPLETE",
    }
    snapshot_hash = canonical_sha256(snapshot_payload)
    pre_submit = B6PreSubmitSnapshot(
        snapshot_id=f"B6PRE:{snapshot_hash[:24]}",
        snapshot_hash=snapshot_hash,
        proposal_id=proposal.proposal_id,
        canonical_payload_hash=proposal.canonical_payload_hash,
        approval_id=approval.approval_id,
        approval_hash=approval.approval_hash,
        paper_account_id=proposal.paper_account_id,
        lock_epoch=execution_lock.lock_epoch,
        b5_fresh_snapshot_id=fresh_snapshot.snapshot_id,
        b5_fresh_snapshot_hash=fresh_snapshot.snapshot_hash,
        source_receipt_ids=fresh_snapshot.source_receipt_ids,
        captured_at=fresh_reads.observed_at.astimezone(timezone.utc),
    )

    fresh_risk = fresh_b5.artifacts.risk_result
    prepare_risk_payload = {
        "proposal_id": proposal.proposal_id,
        "canonical_payload_hash": proposal.canonical_payload_hash,
        "pre_submit_snapshot_id": pre_submit.snapshot_id,
        "pre_submit_snapshot_hash": pre_submit.snapshot_hash,
        "b5_fresh_risk_result_id": fresh_risk.risk_result_id,
        "b5_fresh_risk_result_hash": fresh_risk.risk_result_hash,
        "status": fresh_risk.status,
        "calculated_at": fresh_reads.observed_at.astimezone(timezone.utc),
    }
    prepare_risk_hash = canonical_sha256(prepare_risk_payload)
    prepare_risk = B6PrepareRiskResult(
        risk_result_id=f"B6PREPARE_RISK:{prepare_risk_hash[:24]}",
        risk_result_hash=prepare_risk_hash,
        proposal_id=proposal.proposal_id,
        canonical_payload_hash=proposal.canonical_payload_hash,
        pre_submit_snapshot_id=pre_submit.snapshot_id,
        pre_submit_snapshot_hash=pre_submit.snapshot_hash,
        b5_fresh_risk_result_id=fresh_risk.risk_result_id,
        b5_fresh_risk_result_hash=fresh_risk.risk_result_hash,
        status=fresh_risk.status,
        calculated_at=fresh_reads.observed_at.astimezone(timezone.utc),
    )

    intent = B6ExecutionIntent(
        intent_id=proposal.intent_id,
        proposal_id=proposal.proposal_id,
        canonical_payload_hash=proposal.canonical_payload_hash,
        approval_id=approval.approval_id,
        approval_hash=approval.approval_hash,
        paper_account_id=proposal.paper_account_id,
        lock_epoch=execution_lock.lock_epoch,
        prepare_snapshot_id=pre_submit.snapshot_id,
        prepare_snapshot_hash=pre_submit.snapshot_hash,
        prepare_risk_result_id=prepare_risk.risk_result_id,
        prepare_risk_result_hash=prepare_risk.risk_result_hash,
        policy_lineage_hash=proposal.policy_lineage_hash,
        prepared_at=current,
    )
    return B6PrepareResult(
        fresh_b5=fresh_b5,
        pre_submit_snapshot=pre_submit,
        prepare_risk=prepare_risk,
        intent=intent,
    )
