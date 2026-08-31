from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from aic.approval.options_v1 import TradeProposalB6
from aic.domain.canonical import canonical_sha256
from aic.execution.options_commit_v1 import B6CommitReady
from aic.execution.options_prepare_v1 import B6ExecutionLockContext
from aic.execution.options_submit_authority_v1 import (
    begin_b6_submit_attempt,
    build_alpaca_paper_option_order_request,
    issue_b6_broker_write_lease,
)
from aic.execution.options_submit_reconciliation_v1 import (
    B6SubmitRuntimeError,
    execute_b6_single_paper_submit,
)
from aic.risk.alpaca_b5_runtime_v1 import AlpacaReadOnlyCredentials


NOW = datetime(2026, 8, 31, 16, 30, 0, tzinfo=timezone.utc)
CREDS = AlpacaReadOnlyCredentials(key_id="paper-key", secret_key="paper-secret")


def proposal() -> TradeProposalB6:
    provisional = TradeProposalB6(
        proposal_id="B6PROPOSAL:submit-runtime",
        intent_id="AIC6-submit-runtime",
        decision_id="decision:submit-runtime",
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
        lock_epoch=31,
        prepare_snapshot_id="prepare:snapshot",
        prepare_snapshot_hash="2" * 64,
        prepare_risk_result_id="prepare:risk",
        prepare_risk_result_hash="3" * 64,
        commit_snapshot_id="commit:snapshot",
        commit_snapshot_hash="4" * 64,
        commit_risk_result_id="commit:risk",
        commit_risk_result_hash="5" * 64,
        policy_lineage_hash=p.policy_lineage_hash,
        committed_at=NOW - timedelta(seconds=3),
    )


def lock(p: TradeProposalB6) -> B6ExecutionLockContext:
    return B6ExecutionLockContext(
        paper_account_id=p.paper_account_id,
        holder_intent_id=p.intent_id,
        lock_epoch=31,
        acquired_at=NOW - timedelta(seconds=10),
        expires_at=NOW + timedelta(minutes=2),
    )


def submit_context():
    p = proposal()
    ready = commit_ready(p)
    execution_lock = lock(p)
    lease = issue_b6_broker_write_lease(
        commit_ready=ready,
        proposal=p,
        execution_lock=execution_lock,
        issued_at=NOW - timedelta(seconds=2),
        expires_at=NOW + timedelta(seconds=30),
    )
    marker = begin_b6_submit_attempt(
        lease=lease,
        commit_ready=ready,
        proposal=p,
        execution_lock=execution_lock,
        started_at=NOW - timedelta(seconds=1),
    )
    request = build_alpaca_paper_option_order_request(
        proposal=p,
        lease=lease,
        submitting=marker,
    )
    return p, lease, marker, request


def clock(*values):
    iterator = iter(values)
    return lambda: next(iterator)


def order_body(client_order_id: str, *, order_id: str = "order-1", status: str = "accepted") -> bytes:
    return json.dumps(
        {"id": order_id, "client_order_id": client_order_id, "status": status},
        separators=(",", ":"),
    ).encode("utf-8")


def test_known_accepted_post_uses_exactly_one_post_and_no_reconciliation_get():
    _p, lease, marker, request = submit_context()
    post_calls = []
    get_calls = []
    persisted = []

    def post(url, headers, body, timeout):
        post_calls.append((url, body))
        return 200, order_body(lease.client_order_id)

    def get(url, headers, timeout):
        get_calls.append(url)
        raise AssertionError("reconciliation GET must not run")

    result = execute_b6_single_paper_submit(
        credentials=CREDS,
        lease=lease,
        submitting=marker,
        request=request,
        persist_before_post=lambda consumed, durable_marker, exact_request: persisted.append(
            (consumed, durable_marker, exact_request)
        ),
        post_transport=post,
        reconciliation_transport=get,
        clock=clock(NOW, NOW + timedelta(milliseconds=10)),
        execute_broker_write=True,
    )
    assert len(persisted) == 1
    assert persisted[0][0].status == "CONSUMED"
    assert persisted[0][0].execution_authority is False
    assert len(post_calls) == 1
    assert get_calls == []
    assert result.final_state == "BROKER_KNOWN"
    assert result.submit_receipt.outcome == "KNOWN_ACCEPTED"
    assert result.broker_post_count == 1
    assert result.reconciliation_get_count == 0
    assert result.automatic_retry is False
    assert result.blind_retry is False


def test_deterministic_4xx_is_known_rejection_with_no_retry_or_get():
    _p, lease, marker, request = submit_context()
    post_count = 0
    get_count = 0

    def post(url, headers, body, timeout):
        nonlocal post_count
        post_count += 1
        return 422, b'{"code":42210000,"message":"order rejected"}'

    def get(url, headers, timeout):
        nonlocal get_count
        get_count += 1
        return 404, b"{}"

    result = execute_b6_single_paper_submit(
        credentials=CREDS,
        lease=lease,
        submitting=marker,
        request=request,
        persist_before_post=lambda *_: None,
        post_transport=post,
        reconciliation_transport=get,
        clock=clock(NOW, NOW + timedelta(milliseconds=10)),
        execute_broker_write=True,
    )
    assert post_count == 1
    assert get_count == 0
    assert result.final_state == "BROKER_REJECTED"
    assert result.submit_receipt.outcome == "KNOWN_REJECTED"


def test_timeout_reconciles_by_client_order_id_get_without_second_post():
    _p, lease, marker, request = submit_context()
    post_count = 0
    get_urls = []

    def post(url, headers, body, timeout):
        nonlocal post_count
        post_count += 1
        raise TimeoutError("ambiguous after dispatch")

    def get(url, headers, timeout):
        get_urls.append(url)
        return 200, order_body(lease.client_order_id, order_id="order-after-timeout", status="new")

    result = execute_b6_single_paper_submit(
        credentials=CREDS,
        lease=lease,
        submitting=marker,
        request=request,
        persist_before_post=lambda *_: None,
        post_transport=post,
        reconciliation_transport=get,
        clock=clock(NOW, NOW + timedelta(milliseconds=10), NOW + timedelta(milliseconds=20)),
        execute_broker_write=True,
    )
    assert post_count == 1
    assert len(get_urls) == 1
    assert get_urls[0].startswith("https://paper-api.alpaca.markets/v2/orders:by_client_order_id?")
    assert "client_order_id=AIC6-submit-runtime" in get_urls[0]
    assert result.submit_receipt.outcome == "UNKNOWN"
    assert result.reconciliation_receipt is not None
    assert result.reconciliation_receipt.outcome == "FOUND"
    assert result.reconciliation_receipt.broker_order_id == "order-after-timeout"
    assert result.final_state == "BROKER_KNOWN"
    assert result.broker_post_count == 1
    assert result.reconciliation_get_count == 1


def test_timeout_then_404_remains_unknown_and_never_reposts():
    _p, lease, marker, request = submit_context()
    post_count = 0
    get_count = 0

    def post(url, headers, body, timeout):
        nonlocal post_count
        post_count += 1
        raise TimeoutError("unknown")

    def get(url, headers, timeout):
        nonlocal get_count
        get_count += 1
        return 404, b'{"message":"order not found"}'

    result = execute_b6_single_paper_submit(
        credentials=CREDS,
        lease=lease,
        submitting=marker,
        request=request,
        persist_before_post=lambda *_: None,
        post_transport=post,
        reconciliation_transport=get,
        clock=clock(NOW, NOW + timedelta(milliseconds=10), NOW + timedelta(milliseconds=20)),
        execute_broker_write=True,
    )
    assert post_count == 1
    assert get_count == 1
    assert result.reconciliation_receipt is not None
    assert result.reconciliation_receipt.outcome == "NOT_FOUND"
    assert result.final_state == "SUBMIT_OUTCOME_UNKNOWN"
    assert result.automatic_retry is False


def test_mismatched_2xx_client_order_id_is_ambiguous_and_reconciles_expected_id():
    _p, lease, marker, request = submit_context()
    post_count = 0
    get_count = 0

    def post(url, headers, body, timeout):
        nonlocal post_count
        post_count += 1
        return 200, order_body("wrong-client-id")

    def get(url, headers, timeout):
        nonlocal get_count
        get_count += 1
        return 200, order_body(lease.client_order_id, order_id="correct-order")

    result = execute_b6_single_paper_submit(
        credentials=CREDS,
        lease=lease,
        submitting=marker,
        request=request,
        persist_before_post=lambda *_: None,
        post_transport=post,
        reconciliation_transport=get,
        clock=clock(NOW, NOW + timedelta(milliseconds=10), NOW + timedelta(milliseconds=20)),
        execute_broker_write=True,
    )
    assert post_count == 1
    assert get_count == 1
    assert result.submit_receipt.outcome == "UNKNOWN"
    assert result.final_state == "BROKER_KNOWN"


def test_persistence_failure_or_missing_explicit_write_flag_prevents_post():
    _p, lease, marker, request = submit_context()
    post_count = 0

    def post(url, headers, body, timeout):
        nonlocal post_count
        post_count += 1
        return 200, order_body(lease.client_order_id)

    with pytest.raises(B6SubmitRuntimeError, match="explicit broker-write"):
        execute_b6_single_paper_submit(
            credentials=CREDS,
            lease=lease,
            submitting=marker,
            request=request,
            persist_before_post=lambda *_: None,
            post_transport=post,
            clock=clock(NOW),
        )
    assert post_count == 0

    with pytest.raises(RuntimeError, match="durability unavailable"):
        execute_b6_single_paper_submit(
            credentials=CREDS,
            lease=lease,
            submitting=marker,
            request=request,
            persist_before_post=lambda *_: (_ for _ in ()).throw(RuntimeError("durability unavailable")),
            post_transport=post,
            clock=clock(NOW),
            execute_broker_write=True,
        )
    assert post_count == 0
