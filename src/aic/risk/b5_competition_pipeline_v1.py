from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping, Sequence

from .options_competition_v1 import CompetitionOptionsPolicy, OptionContractCandidate, PremiumRiskBudgetInputs, derive_premium_risk_budgets, select_long_call, size_long_call, validate_b4_invest_handoff


@dataclass(frozen=True)
class B5ReadOnlyRiskSnapshot:
    observed_at: datetime
    paper_account_id: str
    equity: Decimal | None
    same_underlying_committed_premium_at_risk: Decimal | None
    aggregate_committed_long_option_premium_at_risk: Decimal | None
    remaining_after_equity_safety_reserve: Decimal | None
    options_buying_power_after_open_orders: Decimal | None
    account_trading_eligible: bool | None
    unsupported_short_option_position: bool
    conflicting_open_option_sell_order: bool
    unvalued_open_option_exposure: bool
    account_receipt_id: str
    positions_receipt_id: str
    open_orders_receipt_id: str
    option_contracts_receipt_id: str
    option_chain_receipt_id: str

    def receipt_ids(self) -> tuple[str, ...]:
        return (self.account_receipt_id, self.positions_receipt_id, self.open_orders_receipt_id, self.option_contracts_receipt_id, self.option_chain_receipt_id)


@dataclass(frozen=True)
class B5CompetitionProposal:
    status: str
    decision_id: str
    final_decision_hash: str
    policy_hash: str
    paper_account_id: str
    underlying_symbol: str
    option_symbol: str | None
    quantity: int
    action: str | None
    order_type: str | None
    time_in_force: str | None
    environment: str | None
    initial_limit_price: Decimal | None
    max_loss_usd: Decimal | None
    safe_premium_budget: Decimal | None
    source_receipt_ids: tuple[str, ...]
    option_source_receipt_id: str | None
    reason_codes: tuple[str, ...]
    approval_authority: bool = False
    execution_authority: bool = False
    broker_writes: int = 0
    model_calls: int = 0


def _snapshot_missing(snapshot: B5ReadOnlyRiskSnapshot) -> bool:
    numerics = (snapshot.equity, snapshot.same_underlying_committed_premium_at_risk, snapshot.aggregate_committed_long_option_premium_at_risk, snapshot.remaining_after_equity_safety_reserve, snapshot.options_buying_power_after_open_orders)
    return not snapshot.paper_account_id.strip() or any(value is None for value in numerics) or any(not receipt.strip() for receipt in snapshot.receipt_ids())


def _result(*, status: str, decision_id: str, final_decision_hash: str, policy_hash: str, paper_account_id: str, underlying_symbol: str, receipts: tuple[str, ...], reason_codes: tuple[str, ...], option_symbol: str | None = None, quantity: int = 0, action: str | None = None, order_type: str | None = None, time_in_force: str | None = None, environment: str | None = None, initial_limit_price: Decimal | None = None, max_loss_usd: Decimal | None = None, safe_premium_budget: Decimal | None = None, option_source_receipt_id: str | None = None) -> B5CompetitionProposal:
    return B5CompetitionProposal(status=status, decision_id=decision_id, final_decision_hash=final_decision_hash, policy_hash=policy_hash, paper_account_id=paper_account_id, underlying_symbol=underlying_symbol, option_symbol=option_symbol, quantity=quantity, action=action, order_type=order_type, time_in_force=time_in_force, environment=environment, initial_limit_price=initial_limit_price, max_loss_usd=max_loss_usd, safe_premium_budget=safe_premium_budget, source_receipt_ids=receipts, option_source_receipt_id=option_source_receipt_id, reason_codes=reason_codes)


def run_b5_competition_options(*, final_decision: Mapping[str, Any], underlying_symbol: str, option_contracts: Sequence[OptionContractCandidate], snapshot: B5ReadOnlyRiskSnapshot, policy: CompetitionOptionsPolicy) -> B5CompetitionProposal:
    handoff = validate_b4_invest_handoff(final_decision, expected_mandate_version=policy.version)
    receipts = snapshot.receipt_ids()
    base = dict(decision_id=handoff.decision_id, final_decision_hash=handoff.final_decision_hash, policy_hash=policy.policy_hash, paper_account_id=snapshot.paper_account_id, underlying_symbol=underlying_symbol, receipts=receipts)

    if snapshot.observed_at.tzinfo is None or snapshot.observed_at.utcoffset() is None:
        return _result(status="INCOMPLETE_DATA", reason_codes=("RISK_SNAPSHOT_TIMESTAMP_NOT_TIMEZONE_AWARE",), **base)
    if _snapshot_missing(snapshot):
        return _result(status="INCOMPLETE_DATA", reason_codes=("MISSING_REQUIRED_RISK_INPUT",), **base)
    if snapshot.account_trading_eligible is None:
        return _result(status="INCOMPLETE_DATA", reason_codes=("ACCOUNT_TRADING_STATE_UNKNOWN",), **base)
    if not snapshot.account_trading_eligible:
        return _result(status="BLOCK", reason_codes=("ACCOUNT_TRADING_STATE_BLOCKED",), **base)
    if snapshot.unsupported_short_option_position:
        return _result(status="BLOCK", reason_codes=("UNSUPPORTED_SHORT_OPTION_POSITION_STATE",), **base)
    if snapshot.conflicting_open_option_sell_order:
        return _result(status="BLOCK", reason_codes=("CONFLICTING_OPEN_OPTION_SELL_ORDER",), **base)
    if snapshot.unvalued_open_option_exposure:
        return _result(status="INCOMPLETE_DATA", reason_codes=("UNVALUED_OPEN_OPTION_EXPOSURE",), **base)

    selection = select_long_call(option_contracts, underlying_symbol=underlying_symbol, selection_time=snapshot.observed_at, policy=policy)
    if selection.status != "PASS" or selection.selected is None:
        return _result(status="BLOCK", max_loss_usd=Decimal("0"), reason_codes=selection.reason_codes, **base)

    selected = selection.selected
    assert selected.ask is not None
    assert snapshot.equity is not None
    assert snapshot.same_underlying_committed_premium_at_risk is not None
    assert snapshot.aggregate_committed_long_option_premium_at_risk is not None
    assert snapshot.remaining_after_equity_safety_reserve is not None
    assert snapshot.options_buying_power_after_open_orders is not None

    budgets = derive_premium_risk_budgets(equity=snapshot.equity, same_underlying_open_premium_at_risk=snapshot.same_underlying_committed_premium_at_risk, aggregate_open_long_option_premium_at_risk=snapshot.aggregate_committed_long_option_premium_at_risk, remaining_after_equity_safety_reserve=snapshot.remaining_after_equity_safety_reserve, current_alpaca_broker_capacity=snapshot.options_buying_power_after_open_orders, policy=policy)
    sizing = size_long_call(approved_limit_price=selected.ask, budgets=PremiumRiskBudgetInputs(remaining_new_position_budget=budgets.remaining_new_position_budget, remaining_same_underlying_budget=budgets.remaining_same_underlying_budget, remaining_aggregate_options_budget=budgets.remaining_aggregate_options_budget, remaining_after_equity_safety_reserve=budgets.remaining_after_equity_safety_reserve, current_alpaca_broker_capacity=budgets.current_alpaca_broker_capacity), policy=policy)

    if sizing.status != "PASS":
        return _result(status=sizing.status, option_symbol=selected.symbol, initial_limit_price=sizing.approved_limit_price, max_loss_usd=sizing.max_loss_usd, safe_premium_budget=sizing.safe_premium_budget, option_source_receipt_id=selected.source_receipt_id, reason_codes=sizing.reason_codes, **base)

    return _result(status="PASS", option_symbol=selected.symbol, quantity=sizing.quantity, action="BUY_TO_OPEN", order_type="LIMIT", time_in_force="DAY", environment="PAPER", initial_limit_price=sizing.approved_limit_price, max_loss_usd=sizing.max_loss_usd, safe_premium_budget=sizing.safe_premium_budget, option_source_receipt_id=selected.source_receipt_id, reason_codes=(), **base)
