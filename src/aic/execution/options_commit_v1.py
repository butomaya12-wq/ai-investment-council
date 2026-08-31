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

from .options_prepare_v1 import (
    B6ExecutionIntent,
    B6ExecutionLockContext,
    B6PrepareRiskResult,
    B6PreSubmitSnapshot,
)


EXPECTED_COMMIT_QUOTE_MAX_AGE_SECONDS = 15


class B6CommitError(ValueError):
    """Fail-closed error at the final pre-broker-write COMMIT gate."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise B6CommitError(message)


def _aware(value: datetime, *, field: str) -> datetime:
    _require(value.tzinfo is not None and value.utcoffset() is not None, f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class B6CommitSnapshot:
    snapshot_id: str
    snapshot_hash: str
    intent_id: str
    proposal_id: str
    canonical_payload_hash: str
    approval_id: str
    approval_hash: str
    paper_account_id: str
    lock_epoch: int
    fresh_b5_snapshot_id: str
    fresh_b5_snapshot_hash: str
    selected_option_symbol: str
    selected_quote_timestamp: datetime
    selected_quote_age_seconds: int
    source_receipt_ids: tuple[str, ...]
    captured_at: datetime
    status: str = "COMPLETE"
    broker_writes: int = 0
    model_calls: int = 0
    execution_authority: bool = False


@dataclass(frozen=True)
class B6CommitRiskResult:
    risk_result_id: str
    risk_result_hash: str
    intent_id: str
    proposal_id: str
    canonical_payload_hash: str
    commit_snapshot_id: str
    commit_snapshot_hash: str
    fresh_b5_risk_result_id: str
    fresh_b5_risk_result_hash: str
    status: str
    calculated_at: datetime
    broker_writes: int = 0
    model_calls: int = 0
    execution_authority: bool = False


@dataclass(frozen=True)
class B6CommitReady:
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
    commit_snapshot_id: str
    commit_snapshot_hash: str
    commit_risk_result_id: str
    commit_risk_result_hash: str
    policy_lineage_hash: str
    committed_at: datetime
    state: str = "COMMIT_READY"
    broker_call_started_at: datetime | None = None
    submit_attempt_count: int = 0
    broker_writes: int = 0
    model_calls: int = 0
    approval_authority: bool = False
    execution_authority: bool = False


@dataclass(frozen=True)
class B6CommitPreflightResult:
    fresh_b5: B5CompetitionRunResult
    commit_snapshot: B6CommitSnapshot
    commit_risk: B6CommitRiskResult
    commit_ready: B6CommitReady
    broker_writes: int = 0
    model_calls: int = 0
    execution_authority: bool = False


def _validate_lock(
    *,
    lock: B6ExecutionLockContext,
    proposal: TradeProposalB6,
    intent: B6ExecutionIntent,
    now: datetime,
) -> None:
    current = _aware(now, field="now")
    acquired = _aware(lock.acquired_at, field="lock.acquired_at")
    expires = _aware(lock.expires_at, field="lock.expires_at")
    _require(lock.status == "HELD", "execution lock is not held")
    _require(lock.paper_account_id == proposal.paper_account_id == intent.paper_account_id, "execution lock account mismatch")
    _require(lock.holder_intent_id == proposal.intent_id == intent.intent_id, "execution lock intent mismatch")
    _require(lock.lock_epoch == intent.lock_epoch and lock.lock_epoch > 0, "execution lock epoch mismatch")
    _require(acquired <= current < expires, "execution lock is not current")


def _validate_prepared_lineage(
    *,
    proposal: TradeProposalB6,
    approval: ApprovalEnvelope,
    intent: B6ExecutionIntent,
    prepare_snapshot: B6PreSubmitSnapshot,
    prepare_risk: B6PrepareRiskResult,
) -> None:
    _require(intent.state == "PREPARED", "execution intent is not PREPARED")
    _require(intent.submit_attempt_count == 0, "execution intent already has a submit attempt")
    _require(intent.broker_call_started_at is None, "broker call already started")
    _require(intent.broker_writes == 0, "prepared intent already records a broker write")
    _require(not intent.execution_authority, "PREPARED intent cannot carry execution authority")
    _require(intent.proposal_id == proposal.proposal_id, "PREPARED intent proposal mismatch")
    _require(intent.canonical_payload_hash == proposal.canonical_payload_hash, "PREPARED intent payload mismatch")
    _require(intent.approval_id == approval.approval_id, "PREPARED intent approval mismatch")
    _require(intent.approval_hash == approval.approval_hash, "PREPARED intent approval hash mismatch")
    _require(intent.policy_lineage_hash == proposal.policy_lineage_hash, "PREPARED intent policy lineage mismatch")
    _require(intent.prepare_snapshot_id == prepare_snapshot.snapshot_id, "PREPARED snapshot id mismatch")
    _require(intent.prepare_snapshot_hash == prepare_snapshot.snapshot_hash, "PREPARED snapshot hash mismatch")
    _require(intent.prepare_risk_result_id == prepare_risk.risk_result_id, "PREPARED risk id mismatch")
    _require(intent.prepare_risk_result_hash == prepare_risk.risk_result_hash, "PREPARED risk hash mismatch")
    _require(prepare_risk.status == "PASS", "PREPARE risk is not PASS")
    _require(prepare_snapshot.status == "COMPLETE", "PREPARE snapshot is not complete")


def _fresh_approved_economics(
    *,
    proposal: TradeProposalB6,
    fresh_b5: B5CompetitionRunResult,
) -> None:
    fresh = fresh_b5.artifacts.accepted_proposal
    _require(fresh_b5.proposal_result.status == "PASS", "commit-time deterministic risk is not PASS")
    _require(fresh_b5.artifacts.risk_result.status == "PASS", "commit-time B5 risk artifact is not PASS")
    _require(fresh is not None, "commit-time accepted proposal missing")
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


def _selected_quote_timestamp(
    *,
    proposal: TradeProposalB6,
    fresh_b5: B5CompetitionRunResult,
) -> datetime:
    matches = tuple(
        contract
        for contract in fresh_b5.normalized_inputs.option_contracts
        if contract.symbol == proposal.option_symbol
    )
    _require(len(matches) == 1, "approved option is not uniquely present in commit snapshot")
    quote_timestamp = matches[0].quote_timestamp
    _require(quote_timestamp is not None, "approved option has no commit-time quote timestamp")
    return _aware(quote_timestamp, field="commit_quote_timestamp")


def commit_b6_preflight(
    *,
    final_decision: Mapping[str, Any],
    proposal: TradeProposalB6,
    view: ApprovalViewModel,
    approval: ApprovalEnvelope,
    owner_auth: OwnerAuthContext,
    execution_lock: B6ExecutionLockContext,
    prepared_intent: B6ExecutionIntent,
    prepare_snapshot: B6PreSubmitSnapshot,
    prepare_risk: B6PrepareRiskResult,
    fresh_reads: B5RawAlpacaReadBundle,
    policy: CompetitionOptionsPolicy,
    now: datetime,
) -> B6CommitPreflightResult:
    """Final deterministic gate immediately before a broker-write lease may exist.

    This function performs zero broker writes. It revalidates approval, lock, prepared
    lineage, fresh B5 risk, exact approved economics, and the frozen 15-second option
    quote freshness requirement. COMMIT_READY still carries no execution authority.
    """

    current = _aware(now, field="now")
    validate_approval_for_prepare(
        proposal=proposal,
        view=view,
        approval=approval,
        owner_auth=owner_auth,
        now=current,
    )
    _validate_prepared_lineage(
        proposal=proposal,
        approval=approval,
        intent=prepared_intent,
        prepare_snapshot=prepare_snapshot,
        prepare_risk=prepare_risk,
    )
    _validate_lock(
        lock=execution_lock,
        proposal=proposal,
        intent=prepared_intent,
        now=current,
    )

    observed_at = _aware(fresh_reads.observed_at, field="fresh_reads.observed_at")
    _require(observed_at <= current, "commit snapshot cannot be observed in the future")

    fresh_b5 = run_b5_from_alpaca_reads(
        final_decision=final_decision,
        underlying_symbol=proposal.underlying_symbol,
        raw_reads=fresh_reads,
        policy=policy,
    )
    _fresh_approved_economics(proposal=proposal, fresh_b5=fresh_b5)
    quote_timestamp = _selected_quote_timestamp(proposal=proposal, fresh_b5=fresh_b5)
    quote_age = (current - quote_timestamp).total_seconds()
    _require(quote_age >= 0, "commit quote timestamp is in the future")
    _require(
        quote_age <= EXPECTED_COMMIT_QUOTE_MAX_AGE_SECONDS,
        "COMMIT_QUOTE_STALE",
    )
    quote_age_seconds = int(quote_age)

    fresh_snapshot = fresh_b5.artifacts.snapshot
    snapshot_payload = {
        "intent_id": prepared_intent.intent_id,
        "proposal_id": proposal.proposal_id,
        "canonical_payload_hash": proposal.canonical_payload_hash,
        "approval_id": approval.approval_id,
        "approval_hash": approval.approval_hash,
        "paper_account_id": proposal.paper_account_id,
        "lock_epoch": execution_lock.lock_epoch,
        "fresh_b5_snapshot_id": fresh_snapshot.snapshot_id,
        "fresh_b5_snapshot_hash": fresh_snapshot.snapshot_hash,
        "selected_option_symbol": proposal.option_symbol,
        "selected_quote_timestamp": quote_timestamp,
        "selected_quote_age_seconds": quote_age_seconds,
        "source_receipt_ids": fresh_snapshot.source_receipt_ids,
        "captured_at": observed_at,
        "status": "COMPLETE",
    }
    snapshot_hash = canonical_sha256(snapshot_payload)
    commit_snapshot = B6CommitSnapshot(
        snapshot_id=f"B6COMMIT:{snapshot_hash[:24]}",
        snapshot_hash=snapshot_hash,
        intent_id=prepared_intent.intent_id,
        proposal_id=proposal.proposal_id,
        canonical_payload_hash=proposal.canonical_payload_hash,
        approval_id=approval.approval_id,
        approval_hash=approval.approval_hash,
        paper_account_id=proposal.paper_account_id,
        lock_epoch=execution_lock.lock_epoch,
        fresh_b5_snapshot_id=fresh_snapshot.snapshot_id,
        fresh_b5_snapshot_hash=fresh_snapshot.snapshot_hash,
        selected_option_symbol=proposal.option_symbol,
        selected_quote_timestamp=quote_timestamp,
        selected_quote_age_seconds=quote_age_seconds,
        source_receipt_ids=fresh_snapshot.source_receipt_ids,
        captured_at=observed_at,
    )

    fresh_risk = fresh_b5.artifacts.risk_result
    risk_payload = {
        "intent_id": prepared_intent.intent_id,
        "proposal_id": proposal.proposal_id,
        "canonical_payload_hash": proposal.canonical_payload_hash,
        "commit_snapshot_id": commit_snapshot.snapshot_id,
        "commit_snapshot_hash": commit_snapshot.snapshot_hash,
        "fresh_b5_risk_result_id": fresh_risk.risk_result_id,
        "fresh_b5_risk_result_hash": fresh_risk.risk_result_hash,
        "status": fresh_risk.status,
        "calculated_at": observed_at,
    }
    risk_hash = canonical_sha256(risk_payload)
    commit_risk = B6CommitRiskResult(
        risk_result_id=f"B6COMMIT_RISK:{risk_hash[:24]}",
        risk_result_hash=risk_hash,
        intent_id=prepared_intent.intent_id,
        proposal_id=proposal.proposal_id,
        canonical_payload_hash=proposal.canonical_payload_hash,
        commit_snapshot_id=commit_snapshot.snapshot_id,
        commit_snapshot_hash=commit_snapshot.snapshot_hash,
        fresh_b5_risk_result_id=fresh_risk.risk_result_id,
        fresh_b5_risk_result_hash=fresh_risk.risk_result_hash,
        status=fresh_risk.status,
        calculated_at=observed_at,
    )

    commit_ready = B6CommitReady(
        intent_id=prepared_intent.intent_id,
        proposal_id=proposal.proposal_id,
        canonical_payload_hash=proposal.canonical_payload_hash,
        approval_id=approval.approval_id,
        approval_hash=approval.approval_hash,
        paper_account_id=proposal.paper_account_id,
        lock_epoch=execution_lock.lock_epoch,
        prepare_snapshot_id=prepare_snapshot.snapshot_id,
        prepare_snapshot_hash=prepare_snapshot.snapshot_hash,
        prepare_risk_result_id=prepare_risk.risk_result_id,
        prepare_risk_result_hash=prepare_risk.risk_result_hash,
        commit_snapshot_id=commit_snapshot.snapshot_id,
        commit_snapshot_hash=commit_snapshot.snapshot_hash,
        commit_risk_result_id=commit_risk.risk_result_id,
        commit_risk_result_hash=commit_risk.risk_result_hash,
        policy_lineage_hash=proposal.policy_lineage_hash,
        committed_at=current,
    )
    return B6CommitPreflightResult(
        fresh_b5=fresh_b5,
        commit_snapshot=commit_snapshot,
        commit_risk=commit_risk,
        commit_ready=commit_ready,
    )
