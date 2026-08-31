from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from aic.risk.alpaca_b5_normalization_v1 import normalize_b5_alpaca_inputs
from aic.risk.b5_competition_pipeline_v1 import run_b5_competition_options
from aic.risk.options_competition_v1 import load_competition_options_policy


NOW = datetime(2026, 8, 31, 14, 0, 0, tzinfo=timezone.utc)
LATEST_COMPLETED_SESSION = date(2026, 8, 28)
POLICY = load_competition_options_policy(
    Path("config/event/competition_v1_options_policy.json")
)


def account(**overrides):
    payload = {
        "id": "paper-account-1",
        "status": "ACTIVE",
        "trading_blocked": False,
        "trade_suspended_by_user": False,
        "account_blocked": False,
        "equity": "100000",
        "cash": "80000",
        "buying_power": "90000",
        "non_marginable_buying_power": "85000",
    }
    payload.update(overrides)
    return payload


def positions():
    return [
        {
            "symbol": "NVDA261002C00220000",
            "asset_class": "us_option",
            "side": "long",
            "qty": "1",
            "cost_basis": "500",
        },
        {
            "symbol": "MSFT261002C00515000",
            "asset_class": "us_option",
            "side": "long",
            "qty": "1",
            "cost_basis": "1000",
        },
        {
            "symbol": "NVDA",
            "asset_class": "us_equity",
            "side": "long",
            "qty": "10",
            "cost_basis": "1800",
        },
    ]


def orders():
    return [
        {
            "symbol": "NVDA261009C00220000",
            "asset_class": "us_option",
            "side": "buy",
            "type": "limit",
            "qty": "1",
            "limit_price": "4.00",
        }
    ]


def contracts(*, oi_date: str = "2026-08-28"):
    return {
        "option_contracts": [
            {
                "symbol": "NVDA261005C00220000",
                "underlying_symbol": "NVDA",
                "type": "call",
                "status": "active",
                "tradable": True,
                "expiration_date": "2026-10-05",
                "style": "american",
                "strike_price": "220",
                "size": "100",
                "open_interest": "500",
                "open_interest_date": oi_date,
            }
        ]
    }


def chain():
    return {
        "chain": {
            "NVDA261005C00220000": {
                "latest_quote": {
                    "timestamp": (NOW - timedelta(seconds=5)).isoformat(),
                    "bid_price": 9.80,
                    "ask_price": "10.00",
                },
                "greeks": {
                    "delta": 0.50,
                },
            }
        }
    }


def invest_decision():
    return {
        "decision_id": "decision:normalized",
        "outcome": "INVEST",
        "primary_candidate_id": "candidate:NVDA",
        "evidence_status": "COMPLETE",
        "blocking_reason_codes": [],
        "final_decision_hash": "b" * 64,
        "mandate_version": "ALPACA_COMPETITION_V1_2026_08_29",
        "risk_result_id": None,
    }


def normalized(**overrides):
    values = {
        "account_payload": account(),
        "positions_payload": positions(),
        "open_orders_payload": orders(),
        "option_contracts_payload": contracts(),
        "option_chain_payload": chain(),
        "underlying_symbol": "NVDA",
        "observed_at": NOW,
        "latest_completed_session_date": LATEST_COMPLETED_SESSION,
        "policy": POLICY,
        "account_receipt_id": "receipt:account",
        "positions_receipt_id": "receipt:positions",
        "open_orders_receipt_id": "receipt:orders",
        "option_contracts_receipt_id": "receipt:contracts",
        "option_chain_receipt_id": "receipt:chain",
    }
    values.update(overrides)
    return normalize_b5_alpaca_inputs(**values)


def test_normalizer_builds_premium_risk_snapshot_from_realistic_alpaca_shapes():
    result = normalized()
    snapshot = result.snapshot

    assert snapshot.paper_account_id == "paper-account-1"
    assert snapshot.account_trading_eligible is True
    assert snapshot.equity == Decimal("100000")
    assert snapshot.same_underlying_committed_premium_at_risk == Decimal("900")
    assert snapshot.aggregate_committed_long_option_premium_at_risk == Decimal("1900")
    assert snapshot.options_buying_power_after_open_orders == Decimal("80000")
    assert snapshot.remaining_after_equity_safety_reserve == Decimal("30000")
    assert snapshot.unsupported_short_option_position is False
    assert snapshot.conflicting_open_option_sell_order is False
    assert snapshot.unvalued_open_option_exposure is False

    assert len(result.option_contracts) == 1
    option = result.option_contracts[0]
    assert option.symbol == "NVDA261005C00220000"
    assert option.bid == Decimal("9.8")
    assert option.ask == Decimal("10.00")
    assert option.delta == Decimal("0.5")
    assert option.open_interest == 500
    assert option.open_interest_current is True
    assert option.source_receipt_id == "receipt:contracts::receipt:chain"


def test_normalized_inputs_feed_the_deterministic_b5_pipeline():
    inputs = normalized()
    result = run_b5_competition_options(
        final_decision=invest_decision(),
        underlying_symbol="NVDA",
        option_contracts=inputs.option_contracts,
        snapshot=inputs.snapshot,
        policy=POLICY,
    )
    assert result.status == "PASS"
    assert result.option_symbol == "NVDA261005C00220000"
    assert result.quantity == 2
    assert result.initial_limit_price == Decimal("10.00")
    assert result.max_loss_usd == Decimal("2000.00")
    assert result.paper_account_id == "paper-account-1"
    assert result.execution_authority is False
    assert result.broker_writes == 0
    assert result.model_calls == 0


def test_stale_open_interest_is_not_silently_treated_as_current():
    inputs = normalized(option_contracts_payload=contracts(oi_date="2026-08-27"))
    assert inputs.option_contracts[0].open_interest_current is False

    result = run_b5_competition_options(
        final_decision=invest_decision(),
        underlying_symbol="NVDA",
        option_contracts=inputs.option_contracts,
        snapshot=inputs.snapshot,
        policy=POLICY,
    )
    assert result.status == "BLOCK"
    assert result.reason_codes == ("INCOMPLETE_OPTION_MARKET",)


def test_unvalued_open_option_order_routes_incomplete_data():
    market_order = [
        {
            "symbol": "NVDA261009C00220000",
            "asset_class": "us_option",
            "side": "buy",
            "type": "market",
            "qty": "1",
            "limit_price": None,
        }
    ]
    inputs = normalized(open_orders_payload=market_order)
    assert inputs.snapshot.unvalued_open_option_exposure is True

    result = run_b5_competition_options(
        final_decision=invest_decision(),
        underlying_symbol="NVDA",
        option_contracts=inputs.option_contracts,
        snapshot=inputs.snapshot,
        policy=POLICY,
    )
    assert result.status == "INCOMPLETE_DATA"
    assert result.reason_codes == ("UNVALUED_OPEN_OPTION_EXPOSURE",)


def test_short_position_or_open_sell_order_blocks_competition_b5():
    short_positions = [
        {
            "symbol": "NVDA261002C00220000",
            "asset_class": "us_option",
            "side": "short",
            "qty": "1",
            "cost_basis": "500",
        }
    ]
    short_inputs = normalized(positions_payload=short_positions)
    short_result = run_b5_competition_options(
        final_decision=invest_decision(),
        underlying_symbol="NVDA",
        option_contracts=short_inputs.option_contracts,
        snapshot=short_inputs.snapshot,
        policy=POLICY,
    )
    assert short_result.status == "BLOCK"
    assert short_result.reason_codes == ("UNSUPPORTED_SHORT_OPTION_POSITION_STATE",)

    sell_order = [
        {
            "symbol": "NVDA261009C00220000",
            "asset_class": "us_option",
            "side": "sell",
            "type": "limit",
            "qty": "1",
            "limit_price": "4.00",
        }
    ]
    sell_inputs = normalized(open_orders_payload=sell_order)
    sell_result = run_b5_competition_options(
        final_decision=invest_decision(),
        underlying_symbol="NVDA",
        option_contracts=sell_inputs.option_contracts,
        snapshot=sell_inputs.snapshot,
        policy=POLICY,
    )
    assert sell_result.status == "BLOCK"
    assert sell_result.reason_codes == ("CONFLICTING_OPEN_OPTION_SELL_ORDER",)


def test_account_block_state_is_preserved_into_b5_gate():
    inputs = normalized(account_payload=account(trading_blocked=True))
    assert inputs.snapshot.account_trading_eligible is False
    result = run_b5_competition_options(
        final_decision=invest_decision(),
        underlying_symbol="NVDA",
        option_contracts=inputs.option_contracts,
        snapshot=inputs.snapshot,
        policy=POLICY,
    )
    assert result.status == "BLOCK"
    assert result.reason_codes == ("ACCOUNT_TRADING_STATE_BLOCKED",)
