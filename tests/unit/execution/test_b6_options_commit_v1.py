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
from aic.execution.options_commit_v1 import B6CommitError, commit_b6_preflight
from aic.execution.options_prepare_v1 import B6ExecutionLockContext, prepare_b6_execution
from aic.risk.b5_competition_run_v1 import B5RawAlpacaReadBundle, run_b5_from_alpaca_reads
from aic.risk.options_competition_v1 import load_competition_options_policy


BASE = datetime(2026, 8, 31, 15, 0, 0, tzinfo=timezone.utc)
LATEST_SESSION = date(2026, 8, 28)
POLICY = load_competition_options_policy(Path("config/event/competition_v1_options_policy.json"))
OPTION = "NVDA261005C00220000"


def final_decision():
    return {
        "decision_id": "decision:commit",
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


def raw_reads(*, observed_at: datetime, quote_age_seconds: int = 5, ask: str = "10.00", suffix: str):
    return B5RawAlpacaReadBundle(
        account_payload={
            "id": "paper-account-1",
            "status": "ACTIVE",
            "trading_blocked": False,
            "trade_suspended_by_user": False,
            "account_blocked": False,
            "equity": "100000",
            "cash": "80000",
            "buying_power": "90000",
            "non_marginable_buying_power": "85000",
        },
        positions_payload=[],
        open_orders_payload=[],
        option_contracts_payload={
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
        },
        option_chain_payload={
            "snapshots": {
                OPTION: {
                    "latest_quote": {
                        "timestamp": (observed_at - timedelta(seconds=quote_age_seconds)).isoformat(),
                        "bid_price": "9.80",
                        "ask_price": ask,
                    },
                    "greeks": {"delta": "0.50"},
                }
            }
        },
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
        session_id="session:commit",
        session_issued_at=BASE - timedelta(minutes=5),
        session_expires_at=BASE + timedelta(minutes=30),
        authentication_method_ref="server-session",
        authentication_proof_hash="7" * 64,
        status="VALID",
    )


def prepared_context():
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
        lock_epoch=11,
        acquired_at=BASE + timedelta(seconds=3),
        expires_at=BASE + timedelta(minutes=5),
    )
    prepare_at = BASE + timedelta(seconds=10)
    prepared = prepare_b6_execution(
        final_decision=final_decision(),
        proposal=proposal,
        view=view,
        approval=approval,
        owner_auth=owner_auth(),
        execution_lock=lock,
        fresh_reads=raw_reads(observed_at=prepare_at, suffix="prepare"),
        policy=POLICY,
        now=prepare_at,
    )
    return proposal, view, approval, lock, prepared


def test_commit_preflight_revalidates_fresh_risk_and_15_second_quote_without_write_authority():
    proposal, view, approval, lock, prepared = prepared_context()
    commit_at = BASE + timedelta(seconds=20)
    result = commit_b6_preflight(
        final_decision=final_decision(),
        proposal=proposal,
        view=view,
        approval=approval,
        owner_auth=owner_auth(),
        execution_lock=lock,
        prepared_intent=prepared.intent,
        prepare_snapshot=prepared.pre_submit_snapshot,
        prepare_risk=prepared.prepare_risk,
        fresh_reads=raw_reads(observed_at=commit_at, quote_age_seconds=5, suffix="commit"),
        policy=POLICY,
        now=commit_at,
    )
    assert result.commit_ready.state == "COMMIT_READY"
    assert result.commit_snapshot.selected_option_symbol == OPTION
    assert result.commit_snapshot.selected_quote_age_seconds == 5
    assert result.commit_risk.status == "PASS"
    assert result.commit_ready.submit_attempt_count == 0
    assert result.commit_ready.broker_call_started_at is None
    assert result.commit_ready.broker_writes == 0
    assert result.commit_ready.execution_authority is False
    assert result.broker_writes == 0
    assert result.model_calls == 0


def test_quote_older_than_15_seconds_fails_commit_even_though_b5_selection_window_is_60_seconds():
    proposal, view, approval, lock, prepared = prepared_context()
    commit_at = BASE + timedelta(seconds=20)
    with pytest.raises(B6CommitError, match="COMMIT_QUOTE_STALE"):
        commit_b6_preflight(
            final_decision=final_decision(),
            proposal=proposal,
            view=view,
            approval=approval,
            owner_auth=owner_auth(),
            execution_lock=lock,
            prepared_intent=prepared.intent,
            prepare_snapshot=prepared.pre_submit_snapshot,
            prepare_risk=prepared.prepare_risk,
            fresh_reads=raw_reads(observed_at=commit_at, quote_age_seconds=16, suffix="stale"),
            policy=POLICY,
            now=commit_at,
        )


def test_commit_price_drift_requires_new_approval_instead_of_price_chasing():
    proposal, view, approval, lock, prepared = prepared_context()
    commit_at = BASE + timedelta(seconds=20)
    with pytest.raises(B6CommitError, match="STATE_DRIFT"):
        commit_b6_preflight(
            final_decision=final_decision(),
            proposal=proposal,
            view=view,
            approval=approval,
            owner_auth=owner_auth(),
            execution_lock=lock,
            prepared_intent=prepared.intent,
            prepare_snapshot=prepared.pre_submit_snapshot,
            prepare_risk=prepared.prepare_risk,
            fresh_reads=raw_reads(observed_at=commit_at, ask="10.01", suffix="drift"),
            policy=POLICY,
            now=commit_at,
        )
