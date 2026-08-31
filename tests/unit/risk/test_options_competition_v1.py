from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from aic.risk.options_competition_v1 import (
    B5CompetitionOptionsError,
    OptionContractCandidate,
    PremiumRiskBudgetInputs,
    derive_premium_risk_budgets,
    load_competition_options_policy,
    select_long_call,
    size_long_call,
    validate_b4_invest_handoff,
)


POLICY_PATH = Path("config/event/competition_v1_options_policy.json")
NOW = datetime(2026, 8, 31, 13, 0, 0, tzinfo=timezone.utc)


def policy():
    return load_competition_options_policy(POLICY_PATH)


def contract(
    symbol: str,
    *,
    dte: int = 35,
    delta: str = "0.50",
    bid: str = "9.50",
    ask: str = "10.00",
    oi: int = 500,
    quote_age_seconds: int = 5,
    tradable: bool = True,
    status: str = "ACTIVE",
    contract_size: int = 100,
    oi_current: bool = True,
) -> OptionContractCandidate:
    return OptionContractCandidate(
        symbol=symbol,
        underlying_symbol="NVDA",
        contract_type="CALL",
        expiration_date=NOW.date() + timedelta(days=dte),
        strike=Decimal("200"),
        exercise_style="AMERICAN",
        contract_size=contract_size,
        delta=Decimal(delta),
        bid=Decimal(bid),
        ask=Decimal(ask),
        open_interest=oi,
        open_interest_current=oi_current,
        quote_timestamp=NOW - timedelta(seconds=quote_age_seconds),
        status=status,
        tradable=tradable,
        source_receipt_id=f"receipt:{symbol}",
    )


def test_policy_loads_frozen_competition_options_semantics():
    p = policy()
    assert p.version == "ALPACA_COMPETITION_V1_2026_08_29"
    assert p.dte_min == 21 and p.dte_max == 49 and p.dte_target == 35
    assert p.delta_min == Decimal("0.45") and p.delta_max == Decimal("0.60")
    assert p.max_relative_spread == Decimal("0.10")
    assert p.min_open_interest == 100
    assert p.max_contracts_per_new_order == 2


def test_watch_cannot_enter_b5():
    with pytest.raises(B5CompetitionOptionsError, match="INVEST"):
        validate_b4_invest_handoff(
            {
                "decision_id": "d1",
                "outcome": "WATCH",
                "primary_candidate_id": "NVDA",
                "evidence_status": "COMPLETE",
                "blocking_reason_codes": [],
                "final_decision_hash": "a" * 64,
                "mandate_version": "ALPACA_COMPETITION_V1_2026_08_29",
                "risk_result_id": None,
            }
        )


def test_valid_invest_handoff_passes():
    result = validate_b4_invest_handoff(
        {
            "decision_id": "d1",
            "outcome": "INVEST",
            "primary_candidate_id": "NVDA",
            "evidence_status": "COMPLETE",
            "blocking_reason_codes": [],
            "final_decision_hash": "a" * 64,
            "mandate_version": "ALPACA_COMPETITION_V1_2026_08_29",
            "risk_result_id": None,
        }
    )
    assert result.primary_candidate_id == "NVDA"


def test_selector_is_deterministic_by_frozen_ranking():
    candidates = [
        contract("NVDA_C", dte=36, delta="0.50", bid="9.70", ask="10.00", oi=1000),
        contract("NVDA_B", dte=35, delta="0.52", bid="9.70", ask="10.00", oi=1000),
        contract("NVDA_A", dte=35, delta="0.50", bid="9.60", ask="10.00", oi=200),
    ]
    result = select_long_call(
        candidates, underlying_symbol="NVDA", selection_time=NOW, policy=policy()
    )
    assert result.status == "PASS"
    assert result.selected is not None
    assert result.selected.symbol == "NVDA_A"


@pytest.mark.parametrize(
    "candidate",
    [
        contract("DTE_LOW", dte=20),
        contract("DTE_HIGH", dte=50),
        contract("DELTA_LOW", delta="0.44"),
        contract("DELTA_HIGH", delta="0.61"),
        contract("OI_LOW", oi=99),
        contract("STALE_QUOTE", quote_age_seconds=61),
        contract("NOT_TRADABLE", tradable=False),
        contract("INACTIVE", status="INACTIVE"),
        contract("NONSTANDARD", contract_size=10),
        contract("STALE_OI", oi_current=False),
        contract("CROSSED", bid="10.10", ask="10.00"),
        contract("WIDE", bid="9.00", ask="10.00"),
    ],
)
def test_hard_filter_failures_do_not_become_trade_candidates(candidate):
    result = select_long_call(
        [candidate], underlying_symbol="NVDA", selection_time=NOW, policy=policy()
    )
    assert result.status == "BLOCK"
    assert result.selected is None
    assert result.reason_codes == ("INCOMPLETE_OPTION_MARKET",)


def test_relative_spread_uses_mid_denominator():
    candidate = contract("SPREAD", bid="9.50", ask="10.00")
    assert candidate.relative_spread() == Decimal("0.50") / Decimal("9.75")


def test_risk_budget_derivation_uses_frozen_3_3_6_caps():
    budgets = derive_premium_risk_budgets(
        equity=Decimal("100000"),
        same_underlying_open_premium_at_risk=Decimal("500"),
        aggregate_open_long_option_premium_at_risk=Decimal("1500"),
        remaining_after_equity_safety_reserve=Decimal("50000"),
        current_alpaca_broker_capacity=Decimal("40000"),
        policy=policy(),
    )
    assert budgets.remaining_new_position_budget == Decimal("3000.00")
    assert budgets.remaining_same_underlying_budget == Decimal("2500.00")
    assert budgets.remaining_aggregate_options_budget == Decimal("4500.00")


def test_sizing_uses_minimum_safe_budget_and_caps_at_two_contracts():
    result = size_long_call(
        approved_limit_price=Decimal("10"),
        budgets=PremiumRiskBudgetInputs(
            remaining_new_position_budget=Decimal("3000"),
            remaining_same_underlying_budget=Decimal("2500"),
            remaining_aggregate_options_budget=Decimal("4500"),
            remaining_after_equity_safety_reserve=Decimal("50000"),
            current_alpaca_broker_capacity=Decimal("40000"),
        ),
        policy=policy(),
    )
    assert result.status == "PASS"
    assert result.safe_premium_budget == Decimal("2500")
    assert result.premium_per_contract == Decimal("1000")
    assert result.quantity == 2
    assert result.max_loss_usd == Decimal("2000")


def test_sizing_blocks_instead_of_forcing_a_cheaper_contract():
    result = size_long_call(
        approved_limit_price=Decimal("10"),
        budgets=PremiumRiskBudgetInputs(
            remaining_new_position_budget=Decimal("999"),
            remaining_same_underlying_budget=Decimal("999"),
            remaining_aggregate_options_budget=Decimal("999"),
            remaining_after_equity_safety_reserve=Decimal("999"),
            current_alpaca_broker_capacity=Decimal("999"),
        ),
        policy=policy(),
    )
    assert result.status == "BLOCK"
    assert result.quantity == 0
    assert result.reason_codes == ("INSUFFICIENT_RISK_BUDGET",)


def test_missing_required_budget_is_incomplete_data_not_pass():
    result = size_long_call(
        approved_limit_price=Decimal("10"),
        budgets=PremiumRiskBudgetInputs(
            remaining_new_position_budget=Decimal("3000"),
            remaining_same_underlying_budget=None,
            remaining_aggregate_options_budget=Decimal("6000"),
            remaining_after_equity_safety_reserve=Decimal("50000"),
            current_alpaca_broker_capacity=Decimal("50000"),
        ),
        policy=policy(),
    )
    assert result.status == "INCOMPLETE_DATA"


def test_authoritative_risk_math_rejects_binary_float():
    with pytest.raises(B5CompetitionOptionsError, match="Decimal"):
        derive_premium_risk_budgets(
            equity=100000.0,  # type: ignore[arg-type]
            same_underlying_open_premium_at_risk=Decimal("0"),
            aggregate_open_long_option_premium_at_risk=Decimal("0"),
            remaining_after_equity_safety_reserve=Decimal("50000"),
            current_alpaca_broker_capacity=Decimal("50000"),
            policy=policy(),
        )
