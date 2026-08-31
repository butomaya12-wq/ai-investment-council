from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from aic.approval.options_v1 import TradeProposalB6
from aic.domain.canonical import canonical_sha256

from .options_commit_v1 import B6CommitReady
from .options_prepare_v1 import B6ExecutionLockContext


PAPER_ORDER_URL = "https://paper-api.alpaca.markets/v2/orders"


class B6SubmitAuthorityError(ValueError):
    """Fail-closed error for the single-use Alpaca PAPER broker-write authority."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise B6SubmitAuthorityError(message)


def _aware(value: datetime, *, field: str) -> datetime:
    _require(value.tzinfo is not None and value.utcoffset() is not None, f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _price_text(value: Decimal) -> str:
    _require(isinstance(value, Decimal) and value.is_finite() and value > 0, "limit price must be positive finite Decimal")
    return format(value, "f")


@dataclass(frozen=True)
class B6BrokerWriteLease:
    lease_id: str
    intent_id: str
    proposal_id: str
    canonical_payload_hash: str
    paper_account_id: str
    lock_epoch: int
    commit_snapshot_id: str
    commit_snapshot_hash: str
    commit_risk_result_id: str
    commit_risk_result_hash: str
    method: str
    url: str
    client_order_id: str
    max_broker_calls: int
    issued_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None
    status: str = "ISSUED"
    execution_authority: bool = True
    model_calls: int = 0


@dataclass(frozen=True)
class B6SubmittingMarker:
    marker_id: str
    marker_hash: str
    lease_id: str
    intent_id: str
    proposal_id: str
    canonical_payload_hash: str
    paper_account_id: str
    lock_epoch: int
    client_order_id: str
    broker_call_started_at: datetime
    submit_attempt_count: int
    state: str = "SUBMITTING"
    broker_writes_observed: int = 0
    model_calls: int = 0


@dataclass(frozen=True)
class AlpacaPaperOptionOrderRequest:
    method: str
    url: str
    headers_content_type: str
    client_order_id: str
    payload: dict[str, Any]
    payload_hash: str
    broker_write_authority_lease_id: str
    max_send_attempts: int = 1
    automatic_price_chase: bool = False
    blind_retry: bool = False
    live_execution: bool = False


def _validate_commit_ready(
    *,
    commit_ready: B6CommitReady,
    proposal: TradeProposalB6,
    lock: B6ExecutionLockContext,
    now: datetime,
) -> None:
    current = _aware(now, field="now")
    lock_expires = _aware(lock.expires_at, field="lock.expires_at")
    _require(
        canonical_sha256(proposal.executable_payload()) == proposal.canonical_payload_hash,
        "proposal canonical payload drift",
    )
    _require(commit_ready.state == "COMMIT_READY", "intent is not COMMIT_READY")
    _require(commit_ready.submit_attempt_count == 0, "intent already has a submit attempt")
    _require(commit_ready.broker_call_started_at is None, "broker call already started")
    _require(commit_ready.broker_writes == 0, "COMMIT_READY already records broker write")
    _require(not commit_ready.execution_authority, "COMMIT_READY must not itself be write authority")
    _require(commit_ready.intent_id == proposal.intent_id, "COMMIT_READY intent mismatch")
    _require(commit_ready.proposal_id == proposal.proposal_id, "COMMIT_READY proposal mismatch")
    _require(commit_ready.canonical_payload_hash == proposal.canonical_payload_hash, "COMMIT_READY payload mismatch")
    _require(commit_ready.paper_account_id == proposal.paper_account_id, "COMMIT_READY account mismatch")
    _require(commit_ready.policy_lineage_hash == proposal.policy_lineage_hash, "COMMIT_READY policy lineage mismatch")
    _require(lock.status == "HELD", "execution lock is not held")
    _require(lock.paper_account_id == proposal.paper_account_id, "execution lock account mismatch")
    _require(lock.holder_intent_id == proposal.intent_id, "execution lock intent mismatch")
    _require(lock.lock_epoch == commit_ready.lock_epoch, "execution lock epoch mismatch")
    _require(current < lock_expires, "execution lock expired")


def issue_b6_broker_write_lease(
    *,
    commit_ready: B6CommitReady,
    proposal: TradeProposalB6,
    execution_lock: B6ExecutionLockContext,
    issued_at: datetime,
    expires_at: datetime,
) -> B6BrokerWriteLease:
    issued = _aware(issued_at, field="issued_at")
    expires = _aware(expires_at, field="expires_at")
    _validate_commit_ready(
        commit_ready=commit_ready,
        proposal=proposal,
        lock=execution_lock,
        now=issued,
    )
    _require(expires > issued, "broker-write lease expiry must be after issue")
    _require(expires <= execution_lock.expires_at.astimezone(timezone.utc), "broker-write lease cannot outlive execution lock")
    _require(proposal.environment == "PAPER", "broker-write lease cannot target LIVE")
    _require(proposal.action == "BUY_TO_OPEN", "broker-write lease action drift")
    _require(proposal.order_type == "LIMIT", "broker-write lease order type drift")
    _require(proposal.time_in_force == "DAY", "broker-write lease TIF drift")

    client_order_id = proposal.intent_id
    _require(len(client_order_id) <= 128, "client_order_id exceeds Alpaca limit")
    identity = {
        "intent_id": commit_ready.intent_id,
        "proposal_id": proposal.proposal_id,
        "canonical_payload_hash": proposal.canonical_payload_hash,
        "paper_account_id": proposal.paper_account_id,
        "lock_epoch": commit_ready.lock_epoch,
        "commit_snapshot_hash": commit_ready.commit_snapshot_hash,
        "commit_risk_result_hash": commit_ready.commit_risk_result_hash,
        "method": "POST",
        "url": PAPER_ORDER_URL,
        "client_order_id": client_order_id,
        "issued_at": issued,
        "expires_at": expires,
    }
    lease_id = f"B6LEASE:{canonical_sha256(identity)[:24]}"
    return B6BrokerWriteLease(
        lease_id=lease_id,
        intent_id=commit_ready.intent_id,
        proposal_id=proposal.proposal_id,
        canonical_payload_hash=proposal.canonical_payload_hash,
        paper_account_id=proposal.paper_account_id,
        lock_epoch=commit_ready.lock_epoch,
        commit_snapshot_id=commit_ready.commit_snapshot_id,
        commit_snapshot_hash=commit_ready.commit_snapshot_hash,
        commit_risk_result_id=commit_ready.commit_risk_result_id,
        commit_risk_result_hash=commit_ready.commit_risk_result_hash,
        method="POST",
        url=PAPER_ORDER_URL,
        client_order_id=client_order_id,
        max_broker_calls=1,
        issued_at=issued,
        expires_at=expires,
    )


def begin_b6_submit_attempt(
    *,
    lease: B6BrokerWriteLease,
    commit_ready: B6CommitReady,
    proposal: TradeProposalB6,
    execution_lock: B6ExecutionLockContext,
    started_at: datetime,
) -> B6SubmittingMarker:
    started = _aware(started_at, field="started_at")
    _validate_commit_ready(
        commit_ready=commit_ready,
        proposal=proposal,
        lock=execution_lock,
        now=started,
    )
    _require(lease.status == "ISSUED", "broker-write lease is not ISSUED")
    _require(lease.consumed_at is None, "broker-write lease already consumed")
    _require(lease.max_broker_calls == 1, "broker-write lease call bound drift")
    _require(lease.execution_authority is True, "broker-write lease lacks execution authority")
    _require(lease.method == "POST" and lease.url == PAPER_ORDER_URL, "broker-write lease endpoint drift")
    _require(lease.intent_id == commit_ready.intent_id == proposal.intent_id, "broker-write lease intent mismatch")
    _require(lease.proposal_id == proposal.proposal_id, "broker-write lease proposal mismatch")
    _require(lease.canonical_payload_hash == proposal.canonical_payload_hash, "broker-write lease payload mismatch")
    _require(lease.paper_account_id == proposal.paper_account_id, "broker-write lease account mismatch")
    _require(lease.lock_epoch == execution_lock.lock_epoch, "broker-write lease lock epoch mismatch")
    _require(lease.commit_snapshot_hash == commit_ready.commit_snapshot_hash, "broker-write lease commit snapshot drift")
    _require(lease.commit_risk_result_hash == commit_ready.commit_risk_result_hash, "broker-write lease commit risk drift")
    _require(lease.issued_at <= started < lease.expires_at, "broker-write lease is not current")

    payload = {
        "lease_id": lease.lease_id,
        "intent_id": lease.intent_id,
        "proposal_id": proposal.proposal_id,
        "canonical_payload_hash": proposal.canonical_payload_hash,
        "paper_account_id": proposal.paper_account_id,
        "lock_epoch": lease.lock_epoch,
        "client_order_id": lease.client_order_id,
        "broker_call_started_at": started,
        "submit_attempt_count": 1,
        "state": "SUBMITTING",
    }
    marker_hash = canonical_sha256(payload)
    return B6SubmittingMarker(
        marker_id=f"B6SUBMITTING:{marker_hash[:24]}",
        marker_hash=marker_hash,
        lease_id=lease.lease_id,
        intent_id=lease.intent_id,
        proposal_id=proposal.proposal_id,
        canonical_payload_hash=proposal.canonical_payload_hash,
        paper_account_id=proposal.paper_account_id,
        lock_epoch=lease.lock_epoch,
        client_order_id=lease.client_order_id,
        broker_call_started_at=started,
        submit_attempt_count=1,
    )


def build_alpaca_paper_option_order_request(
    *,
    proposal: TradeProposalB6,
    lease: B6BrokerWriteLease,
    submitting: B6SubmittingMarker,
) -> AlpacaPaperOptionOrderRequest:
    _require(
        canonical_sha256(proposal.executable_payload()) == proposal.canonical_payload_hash,
        "proposal canonical payload drift",
    )
    _require(submitting.state == "SUBMITTING", "durable SUBMITTING marker missing")
    _require(submitting.submit_attempt_count == 1, "submit attempt count must be exactly one")
    _require(submitting.broker_writes_observed == 0, "broker write already observed before request construction")
    _require(submitting.lease_id == lease.lease_id, "SUBMITTING marker lease mismatch")
    _require(submitting.intent_id == proposal.intent_id == lease.intent_id, "SUBMITTING intent mismatch")
    _require(submitting.canonical_payload_hash == proposal.canonical_payload_hash, "SUBMITTING payload mismatch")
    _require(lease.status == "ISSUED" and lease.consumed_at is None, "broker-write lease unavailable")
    _require(lease.method == "POST" and lease.url == PAPER_ORDER_URL, "broker-write endpoint drift")
    _require(proposal.environment == "PAPER", "live execution is prohibited")
    _require(proposal.action == "BUY_TO_OPEN", "only BUY_TO_OPEN is allowed")
    _require(proposal.order_type == "LIMIT", "only LIMIT is allowed")
    _require(proposal.time_in_force == "DAY", "only DAY is allowed")
    _require(proposal.quantity > 0, "option quantity must be positive")

    payload = {
        "symbol": proposal.option_symbol,
        "qty": str(proposal.quantity),
        "side": "buy",
        "type": "limit",
        "time_in_force": "day",
        "limit_price": _price_text(proposal.limit_price),
        "position_intent": "buy_to_open",
        "client_order_id": lease.client_order_id,
        "extended_hours": False,
    }
    payload_hash = canonical_sha256(payload)
    return AlpacaPaperOptionOrderRequest(
        method="POST",
        url=PAPER_ORDER_URL,
        headers_content_type="application/json",
        client_order_id=lease.client_order_id,
        payload=payload,
        payload_hash=payload_hash,
        broker_write_authority_lease_id=lease.lease_id,
    )


def consume_b6_broker_write_lease(
    lease: B6BrokerWriteLease,
    *,
    consumed_at: datetime,
) -> B6BrokerWriteLease:
    consumed = _aware(consumed_at, field="consumed_at")
    _require(lease.status == "ISSUED" and lease.consumed_at is None, "broker-write lease already consumed")
    _require(lease.issued_at <= consumed < lease.expires_at, "broker-write lease is not current")
    return replace(lease, consumed_at=consumed, status="CONSUMED", execution_authority=False)
