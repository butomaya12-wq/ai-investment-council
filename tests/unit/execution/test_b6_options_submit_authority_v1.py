from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from aic.approval.options_v1 import TradeProposalB6
from aic.domain.canonical import canonical_sha256
from aic.execution.options_commit_v1 import B6CommitReady
from aic.execution.options_prepare_v1 import B6ExecutionLockContext
from aic.execution.options_submit_authority_v1 import (
    B6SubmitAuthorityError,
    PAPER_ORDER_URL,
    begin_b6_submit_attempt,
    build_alpaca_paper_option_order_request,
    consume_b6_broker_write_lease,
    issue_b6_broker_write_lease,
)


NOW = datetime(2026, 8, 31, 16, 0, 0, tzinfo=timezone.utc)


def proposal() -> TradeProposalB6:
    provisional = TradeProposalB6(
        proposal_id="B6PROPOSAL:test",
        intent_id="AIC6-test-intent",
        decision_id="decision:test",
        final_decision_hash="a" * 64,
        b5_accepted_proposal_id="b5:accepted",
        b5_accepted_hash="b" * 64,
        paper_account_id="paper-account-1",
        underlying_symbol="NVDA",
        option_symbol="NVDA261005C00220000",
        quantity=2,
        action="BUY_TO_OPEN",
        order_type="LIMIT",
        time_in_force="DAY",
        environment="PAPER",
        limit_price=Decimal("10.00"),
        max_loss_usd=Decimal("2000.00"),
        risk_result_id="risk:test",
        risk_result_hash="c" * 64,
        portfolio_impact_id="impact:test",
        portfolio_impact_hash="d" * 64,
        snapshot_id="snapshot:test",
        snapshot_hash="e" * 64,
        policy_lineage_hash="f" * 64,
        canonical_payload_hash="placeholder",
        created_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=10),
    )
    return replace(
        provisional,
        canonical_payload_hash=canonical_sha256(provisional.executable_payload()),
    )


def commit_ready(p: TradeProposalB6) -> B6CommitReady:
    return B6CommitReady(
        intent_id=p.intent_id,
        proposal_id=p.proposal_id,
        canonical_payload_hash=p.canonical_payload_hash,
        approval_id="approval:test",
        approval_hash="1" * 64,
        paper_account_id=p.paper_account_id,
        lock_epoch=21,
        prepare_snapshot_id="prepare:snapshot",
        prepare_snapshot_hash="2" * 64,
        prepare_risk_result_id="prepare:risk",
        prepare_risk_result_hash="3" * 64,
        commit_snapshot_id="commit:snapshot",
        commit_snapshot_hash="4" * 64,
        commit_risk_result_id="commit:risk",
        commit_risk_result_hash="5" * 64,
        policy_lineage_hash=p.policy_lineage_hash,
        committed_at=NOW - timedelta(seconds=2),
    )


def lock(p: TradeProposalB6) -> B6ExecutionLockContext:
    return B6ExecutionLockContext(
        paper_account_id=p.paper_account_id,
        holder_intent_id=p.intent_id,
        lock_epoch=21,
        acquired_at=NOW - timedelta(seconds=10),
        expires_at=NOW + timedelta(minutes=1),
    )


def test_single_use_lease_and_request_are_exact_paper_option_order_semantics():
    p = proposal()
    ready = commit_ready(p)
    execution_lock = lock(p)
    lease = issue_b6_broker_write_lease(
        commit_ready=ready,
        proposal=p,
        execution_lock=execution_lock,
        issued_at=NOW,
        expires_at=NOW + timedelta(seconds=20),
    )
    assert lease.execution_authority is True
    assert lease.max_broker_calls == 1
    assert lease.method == "POST"
    assert lease.url == PAPER_ORDER_URL
    assert lease.client_order_id == p.intent_id

    marker = begin_b6_submit_attempt(
        lease=lease,
        commit_ready=ready,
        proposal=p,
        execution_lock=execution_lock,
        started_at=NOW + timedelta(seconds=1),
    )
    assert marker.state == "SUBMITTING"
    assert marker.submit_attempt_count == 1
    assert marker.broker_writes_observed == 0

    request = build_alpaca_paper_option_order_request(
        proposal=p,
        lease=lease,
        submitting=marker,
    )
    assert request.method == "POST"
    assert request.url == "https://paper-api.alpaca.markets/v2/orders"
    assert request.max_send_attempts == 1
    assert request.automatic_price_chase is False
    assert request.blind_retry is False
    assert request.live_execution is False
    assert request.payload == {
        "symbol": "NVDA261005C00220000",
        "qty": "2",
        "side": "buy",
        "type": "limit",
        "time_in_force": "day",
        "limit_price": "10.00",
        "position_intent": "buy_to_open",
        "client_order_id": p.intent_id,
        "extended_hours": False,
    }
    assert "notional" not in request.payload


def test_consumed_lease_cannot_start_another_submit_attempt():
    p = proposal()
    ready = commit_ready(p)
    execution_lock = lock(p)
    lease = issue_b6_broker_write_lease(
        commit_ready=ready,
        proposal=p,
        execution_lock=execution_lock,
        issued_at=NOW,
        expires_at=NOW + timedelta(seconds=20),
    )
    consumed = consume_b6_broker_write_lease(
        lease,
        consumed_at=NOW + timedelta(seconds=1),
    )
    assert consumed.status == "CONSUMED"
    assert consumed.execution_authority is False
    with pytest.raises(B6SubmitAuthorityError, match="not ISSUED"):
        begin_b6_submit_attempt(
            lease=consumed,
            commit_ready=ready,
            proposal=p,
            execution_lock=execution_lock,
            started_at=NOW + timedelta(seconds=2),
        )


def test_mutated_proposal_cannot_reuse_old_commit_ready_or_lease_lineage():
    p = proposal()
    ready = commit_ready(p)
    execution_lock = lock(p)
    mutated = replace(p, option_symbol="NVDA261005C00230000")
    with pytest.raises(B6SubmitAuthorityError, match="proposal canonical payload drift"):
        issue_b6_broker_write_lease(
            commit_ready=ready,
            proposal=mutated,
            execution_lock=execution_lock,
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=20),
        )
