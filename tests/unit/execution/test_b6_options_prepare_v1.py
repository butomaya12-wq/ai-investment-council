from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from aic.approval.options_v1 import (
    OwnerAuthContext,
    create_approval_envelope,
    create_approval_view,
    create_trade_proposal_from_b5,
)
from aic.execution.options_prepare_v1 import (
    B6ExecutionLockContext,
    B6PrepareError,
    prepare_b6_execution,
)
from aic.risk.b5_competition_run_v1 import B5RawAlpacaReadBundle, run_b5_from_alpaca_reads
from aic.risk.options_competition_v1 import load_competition_options_policy


BASE = datetime(2026, 8, 31, 15, 0, 0, tzinfo=timezone.utc)
LATEST_SESSION = date(2026, 8, 28)
POLICY = load_competition_options_policy(Path("config/event/competition_v1_options_policy.json"))
OPTION = "NVDA261005C00220000"


def final_decision():
    return {
        "decision_id": "decision:prepare",
        "outcome": "INVEST",
        "primary_candidate_id": "candidate:NVDA",
        "evidence_status": "COMPLETE",
        "blocking_reason_codes": [],
        "final_decision_hash": "a" * 64,
        "mandate_version": "ALPACA_COMPETITION_V1_2026_08_29",
        "risk_result_id": None,
        "policy_refs": {
            "evidence_policy": {"policy_id": "EVIDENCE", "version": "v1", "policy_hash": "1" * 64},
            "council_policy": {"policy_id": "COUNCIL", "version": "v1", "policy_hash": "2" * 64},
        },
        "decision_lifecycle_policy_ref": {
            "policy_id": "ALPACA_2026_COMPETITION_DECISION_LIFECYCLE",
            "version": "ALPACA_COMPETITION_V1_2026_08_29",
            "policy_hash": "3" * 64,
        },
    }


def raw_reads(*, observed_at: datetime, ask: str = "10.00", trading_blocked: bool = False, suffix: str = "initial"):
    account = {
        "id": "paper-account-1",
        "status": "ACTIVE",
        "trading_blocked": trading_blocked,
        "trade_suspended_by_user": False,
        "account_blocked": False,
        "equity": "100000",
        "cash": "80000",
        "buying_power": "90000",
        "non_marginable_buying_power": "85000",
    }
    contracts = {
        "option_contracts": [
            {
                "symbol": OPTION,
                "underlying_symbol": "NVDA",
                "type": "call",
                "status": "active",
                "tradable": True,
                "expiration_date": "2026-10-05",
                "style": "american",
                "strike_price": "220",
                "size": "100",
                "open_interest": "500",
                "open_interest_date": "2026-08-28",
            }
        ]
    }
    chain = {
        "snapshots": {
            OPTION: {
                "latest_quote": {
                    "timestamp": (observed_at - timedelta(seconds=5)).isoformat(),
                    "bid_price": "9.80",
                    "ask_price": ask,
                },
                "greeks": {"delta": "0.50"},
            }
        }
    }
    return B5RawAlpacaReadBundle(
        account_payload=account,
        positions_payload=[],
        open_orders_payload=[],
        option_contracts_payload=contracts,
        option_chain_payload=chain,
        observed_at=observed_at,
        latest_completed_session_date=LATEST_SESSION,
        account_receipt_id=f"receipt:account:{suffix}",
        positions_receipt_id=f"receipt:positions:{suffix}",
        open_orders_receipt_id=f"receipt:orders:{suffix}",
        option_contracts_receipt_id=f"receipt:contracts:{suffix}",
        option_chain_receipt_id=f"receipt:chain:{suffix}",
    )


def owner_auth():
    return OwnerAuthContext(
        owner_id="owner:maya",
        session_id="session:prepare",
        session_issued_at=BASE - timedelta(minutes=5),
        session_expires_at=BASE + timedelta(minutes=30),
        authentication_method_ref="server-session",
        authentication_proof_hash="7" * 64,
        status="VALID",
    )


def approved_context():
    initial = run_b5_from_alpaca_reads(
        final_decision=final_decision(),
        underlying_symbol="NVDA",
        raw_reads=raw_reads(observed_at=BASE, suffix="initial"),
        policy=POLICY,
    )
    accepted = initial.artifacts.accepted_proposal
    assert accepted is not None
    proposal = create_trade_proposal_from_b5(
        accepted,
        created_at=BASE + timedelta(seconds=1),
        expires_at=BASE + timedelta(minutes=10),
    )
    view = create_approval_view(proposal, b5_artifacts=initial.artifacts)
    approval = create_approval_envelope(
        proposal=proposal,
        view=view,
        owner_auth=owner_auth(),
        decision="APPROVE",
        approved_at=BASE + timedelta(seconds=2),
        expires_at=BASE + timedelta(minutes=8),
    )
    lock = B6ExecutionLockContext(
        paper_account_id=proposal.paper_account_id,
        holder_intent_id=proposal.intent_id,
        lock_epoch=1,
        acquired_at=BASE + timedelta(seconds=3),
        expires_at=BASE + timedelta(minutes=5),
    )
    return proposal, view, approval, lock


def test_stable_fresh_state_yields_prepared_but_zero_write_authority():
    proposal, view, approval, lock = approved_context()
    fresh_at = BASE + timedelta(seconds=10)
    result = prepare_b6_execution(
        final_decision=final_decision(),
        proposal=proposal,
        view=view,
        approval=approval,
        owner_auth=owner_auth(),
        execution_lock=lock,
        fresh_reads=raw_reads(observed_at=fresh_at, suffix="prepare"),
        policy=POLICY,
        now=fresh_at,
    )
    assert result.intent.state == "PREPARED"
    assert result.intent.intent_id == proposal.intent_id
    assert result.intent.lock_epoch == 1
    assert result.intent.submit_attempt_count == 0
    assert result.intent.broker_call_started_at is None
    assert result.intent.broker_writes == 0
    assert result.intent.execution_authority is False
    assert result.prepare_risk.status == "PASS"
    assert result.pre_submit_snapshot.status == "COMPLETE"
    assert result.broker_writes == 0
    assert result.model_calls == 0


def test_fresh_limit_price_drift_blocks_prepare_instead_of_mutating_approval():
    proposal, view, approval, lock = approved_context()
    fresh_at = BASE + timedelta(seconds=10)
    with pytest.raises(B6PrepareError, match="STATE_DRIFT"):
        prepare_b6_execution(
            final_decision=final_decision(),
            proposal=proposal,
            view=view,
            approval=approval,
            owner_auth=owner_auth(),
            execution_lock=lock,
            fresh_reads=raw_reads(observed_at=fresh_at, ask="10.01", suffix="price-drift"),
            policy=POLICY,
            now=fresh_at,
        )


def test_fresh_risk_block_never_creates_prepared_intent():
    proposal, view, approval, lock = approved_context()
    fresh_at = BASE + timedelta(seconds=10)
    with pytest.raises(B6PrepareError, match="fresh prepare risk is not PASS"):
        prepare_b6_execution(
            final_decision=final_decision(),
            proposal=proposal,
            view=view,
            approval=approval,
            owner_auth=owner_auth(),
            execution_lock=lock,
            fresh_reads=raw_reads(observed_at=fresh_at, trading_blocked=True, suffix="blocked"),
            policy=POLICY,
            now=fresh_at,
        )


def test_wrong_or_expired_lock_blocks_before_prepare():
    proposal, view, approval, lock = approved_context()
    fresh_at = BASE + timedelta(seconds=10)
    wrong_lock = B6ExecutionLockContext(
        paper_account_id=lock.paper_account_id,
        holder_intent_id="another-intent",
        lock_epoch=lock.lock_epoch,
        acquired_at=lock.acquired_at,
        expires_at=lock.expires_at,
    )
    with pytest.raises(B6PrepareError, match="lock intent mismatch"):
        prepare_b6_execution(
            final_decision=final_decision(),
            proposal=proposal,
            view=view,
            approval=approval,
            owner_auth=owner_auth(),
            execution_lock=wrong_lock,
            fresh_reads=raw_reads(observed_at=fresh_at, suffix="wrong-lock"),
            policy=POLICY,
            now=fresh_at,
        )
