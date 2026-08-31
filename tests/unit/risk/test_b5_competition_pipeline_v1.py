from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from aic.risk.alpaca_options_readonly_v1 import (
    PAPER_TRADING_BASE_URL,
    assert_read_only_request_plan,
    build_b5_read_only_request_plan,
)
from aic.risk.b5_competition_pipeline_v1 import (
    B5ReadOnlyRiskSnapshot,
    run_b5_competition_options,
)
from aic.risk.options_competition_v1 import (
    OptionContractCandidate,
    load_competition_options_policy,
)


NOW = datetime(2026, 8, 31, 13, 0, 0, tzinfo=timezone.utc)
POLICY = load_competition_options_policy(
    Path("config/event/competition_v1_options_policy.json")
)


def invest_decision():
    return {
        "decision_id": "decision:1",
        "outcome": "INVEST",
        "primary_candidate_id": "candidate:NVDA",
        "evidence_status": "COMPLETE",
        "blocking_reason_codes": [],
        "final_decision_hash": "a" * 64,
        "mandate_version": "ALPACA_COMPETITION_V1_2026_08_29",
        "risk_result_id": None,
    }


def option(symbol: str = "NVDA_TEST", *, ask: str = "10.00"):
    return OptionContractCandidate(
        symbol=symbol,
        underlying_symbol="NVDA",
        contract_type="CALL",
        expiration_date=NOW.date() + timedelta(days=35),
        strike=Decimal("200"),
        exercise_style="AMERICAN",
        contract_size=100,
        delta=Decimal("0.50"),
        bid=Decimal("9.50"),
        ask=Decimal(ask),
        open_interest=500,
        open_interest_current=True,
        quote_timestamp=NOW - timedelta(seconds=5),
        status="ACTIVE",
        tradable=True,
        source_receipt_id="receipt:option-chain",
    )


def snapshot(**overrides):
    values = {
        "observed_at": NOW,
        "paper_account_id": "paper-account-1",
        "equity": Decimal("100000"),
        "same_underlying_committed_premium_at_risk": Decimal("500"),
        "aggregate_committed_long_option_premium_at_risk": Decimal("1500"),
        "remaining_after_equity_safety_reserve": Decimal("50000"),
        "options_buying_power_after_open_orders": Decimal("40000"),
        "account_trading_eligible": True,
        "unsupported_short_option_position": False,
        "conflicting_open_option_sell_order": False,
        "unvalued_open_option_exposure": False,
        "account_receipt_id": "receipt:account",
        "positions_receipt_id": "receipt:positions",
        "open_orders_receipt_id": "receipt:orders",
        "option_contracts_receipt_id": "receipt:contracts",
        "option_chain_receipt_id": "receipt:chain",
    }
    values.update(overrides)
    return B5ReadOnlyRiskSnapshot(**values)


def test_read_only_plan_has_only_gets_and_dte_bounded_option_reads():
    plan = build_b5_read_only_request_plan(
        underlying_symbol="nvda",
        as_of_date=NOW.date(),
        policy=POLICY,
    )
    assert_read_only_request_plan(plan)
    assert len(plan) == 5
    assert all(request.method == "GET" for request in plan)
    assert all("orders" not in request.path or request.method == "GET" for request in plan)
    contracts = next(r for r in plan if r.request_id == "B5_OPTION_CONTRACTS")
    assert contracts.base_url == PAPER_TRADING_BASE_URL
    assert ("type", "call") in contracts.query
    assert ("expiration_date_gte", "2026-09-21") in contracts.query
    assert ("expiration_date_lte", "2026-10-19") in contracts.query
    chain = next(r for r in plan if r.request_id == "B5_OPTION_CHAIN")
    assert chain.path.endswith("/NVDA")


def test_b5_pipeline_produces_paper_limit_proposal_but_no_authority():
    result = run_b5_competition_options(
        final_decision=invest_decision(),
        underlying_symbol="NVDA",
        option_contracts=[option()],
        snapshot=snapshot(),
        policy=POLICY,
    )
    assert result.status == "PASS"
    assert result.paper_account_id == "paper-account-1"
    assert result.option_symbol == "NVDA_TEST"
    assert result.quantity == 2
    assert result.action == "BUY_TO_OPEN"
    assert result.order_type == "LIMIT"
    assert result.time_in_force == "DAY"
    assert result.environment == "PAPER"
    assert result.initial_limit_price == Decimal("10.00")
    assert result.max_loss_usd == Decimal("2000.00")
    assert result.approval_authority is False
    assert result.execution_authority is False
    assert result.broker_writes == 0
    assert result.model_calls == 0
    assert result.final_decision_hash == "a" * 64
    assert result.option_source_receipt_id == "receipt:option-chain"


def test_b5_pipeline_blocks_when_no_contract_survives_hard_filters():
    stale = option("NVDA_STALE")
    stale = OptionContractCandidate(
        **{
            **stale.__dict__,
            "quote_timestamp": NOW - timedelta(seconds=61),
        }
    )
    result = run_b5_competition_options(
        final_decision=invest_decision(),
        underlying_symbol="NVDA",
        option_contracts=[stale],
        snapshot=snapshot(),
        policy=POLICY,
    )
    assert result.status == "BLOCK"
    assert result.quantity == 0
    assert result.execution_authority is False
    assert result.reason_codes == ("INCOMPLETE_OPTION_MARKET",)


def test_b5_pipeline_fails_closed_on_missing_authoritative_risk_input():
    result = run_b5_competition_options(
        final_decision=invest_decision(),
        underlying_symbol="NVDA",
        option_contracts=[option()],
        snapshot=snapshot(options_buying_power_after_open_orders=None),
        policy=POLICY,
    )
    assert result.status == "INCOMPLETE_DATA"
    assert result.quantity == 0
    assert result.action is None
    assert result.execution_authority is False


def test_b5_pipeline_blocks_if_even_one_contract_exceeds_safe_budget():
    result = run_b5_competition_options(
        final_decision=invest_decision(),
        underlying_symbol="NVDA",
        option_contracts=[option(ask="10.00")],
        snapshot=snapshot(
            remaining_after_equity_safety_reserve=Decimal("999"),
            options_buying_power_after_open_orders=Decimal("999"),
        ),
        policy=POLICY,
    )
    assert result.status == "BLOCK"
    assert result.quantity == 0
    assert result.action is None
    assert result.reason_codes == ("INSUFFICIENT_RISK_BUDGET",)


def test_b5_snapshot_requires_lineage_receipts():
    result = run_b5_competition_options(
        final_decision=invest_decision(),
        underlying_symbol="NVDA",
        option_contracts=[option()],
        snapshot=snapshot(option_chain_receipt_id=""),
        policy=POLICY,
    )
    assert result.status == "INCOMPLETE_DATA"
    assert result.source_receipt_ids[-1] == ""


def test_account_block_and_portfolio_state_gates_are_fail_closed():
    blocked = run_b5_competition_options(
        final_decision=invest_decision(),
        underlying_symbol="NVDA",
        option_contracts=[option()],
        snapshot=snapshot(account_trading_eligible=False),
        policy=POLICY,
    )
    assert blocked.status == "BLOCK"
    assert blocked.reason_codes == ("ACCOUNT_TRADING_STATE_BLOCKED",)

    short = run_b5_competition_options(
        final_decision=invest_decision(),
        underlying_symbol="NVDA",
        option_contracts=[option()],
        snapshot=snapshot(unsupported_short_option_position=True),
        policy=POLICY,
    )
    assert short.status == "BLOCK"
    assert short.reason_codes == ("UNSUPPORTED_SHORT_OPTION_POSITION_STATE",)

    unknown_order = run_b5_competition_options(
        final_decision=invest_decision(),
        underlying_symbol="NVDA",
        option_contracts=[option()],
        snapshot=snapshot(unvalued_open_option_exposure=True),
        policy=POLICY,
    )
    assert unknown_order.status == "INCOMPLETE_DATA"
    assert unknown_order.reason_codes == ("UNVALUED_OPEN_OPTION_EXPOSURE",)
