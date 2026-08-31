from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from aic.risk.b5_competition_run_v1 import (
    B5RawAlpacaReadBundle,
    run_b5_from_alpaca_reads,
)
from aic.risk.options_competition_v1 import (
    B5CompetitionOptionsError,
    load_competition_options_policy,
)

NOW = datetime(2026, 8, 31, 14, 30, 0, tzinfo=timezone.utc)
POLICY = load_competition_options_policy(Path("config/event/competition_v1_options_policy.json"))


def final_decision(*, outcome: str = "INVEST"):
    return {
        "decision_id": "decision:e2e-b5",
        "outcome": outcome,
        "primary_candidate_id": "candidate:NVDA" if outcome == "INVEST" else None,
        "evidence_status": "COMPLETE",
        "blocking_reason_codes": [],
        "final_decision_hash": "d" * 64,
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


def raw_reads():
    option_symbol = "NVDA261005C00220000"
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
            "option_contracts": [{
                "symbol": option_symbol,
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
            }]
        },
        option_chain_payload={
            "chain": {
                option_symbol: {
                    "latest_quote": {
                        "timestamp": (NOW - timedelta(seconds=5)).isoformat(),
                        "bid_price": "9.80",
                        "ask_price": "10.00",
                    },
                    "greeks": {"delta": "0.50"},
                }
            }
        },
        observed_at=NOW,
        latest_completed_session_date=date(2026, 8, 28),
        account_receipt_id="receipt:account",
        positions_receipt_id="receipt:positions",
        open_orders_receipt_id="receipt:orders",
        option_contracts_receipt_id="receipt:contracts",
        option_chain_receipt_id="receipt:chain",
    )


def test_complete_b5_run_from_alpaca_reads_materializes_b6_handoff():
    result = run_b5_from_alpaca_reads(
        final_decision=final_decision(),
        underlying_symbol="NVDA",
        raw_reads=raw_reads(),
        policy=POLICY,
    )
    assert result.proposal_result.status == "PASS"
    assert result.artifacts.risk_result.status == "PASS"
    assert result.artifacts.accepted_proposal is not None
    assert result.artifacts.accepted_proposal.option_symbol == "NVDA261005C00220000"
    assert result.artifacts.accepted_proposal.quantity == 2
    assert result.broker_writes == 0
    assert result.model_calls == 0
    assert result.approval_authority is False
    assert result.execution_authority is False


def test_non_invest_final_decision_cannot_enter_b5_run():
    with pytest.raises(B5CompetitionOptionsError):
        run_b5_from_alpaca_reads(
            final_decision=final_decision(outcome="WATCH"),
            underlying_symbol="NVDA",
            raw_reads=raw_reads(),
            policy=POLICY,
        )


def test_same_captured_reads_replay_identically():
    first = run_b5_from_alpaca_reads(
        final_decision=final_decision(), underlying_symbol="NVDA",
        raw_reads=raw_reads(), policy=POLICY,
    )
    second = run_b5_from_alpaca_reads(
        final_decision=final_decision(), underlying_symbol="NVDA",
        raw_reads=raw_reads(), policy=POLICY,
    )
    assert first == second
