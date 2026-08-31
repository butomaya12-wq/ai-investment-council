from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping, Sequence

from .options_competition_v1 import (
    CompetitionOptionsPolicy,
    OptionContractCandidate,
    PremiumRiskBudgetInputs,
    derive_premium_risk_budgets,
    select_long_call,
    size_long_call,
    validate_b4_invest_handoff,
)


@dataclass(frozen=True)
class B5ReadOnlyRiskSnapshot:
    """Normalized read-only inputs needed by competition B5.

    Exposure fields are authoritative premium-at-risk amounts after accounting for
    currently open long-option positions and committed opening orders. This object
    grants no approval or execution authority.
    """

    observed_at: datetime
    equity: Decimal | None
    same_underlying_committed_premium_at_risk: Decimal | None
    aggregate_committed_long_option_premium_at_risk: Decimal | None
    remaining_after_equity_safety_reserve: Decimal | None
    options_buying_power_after_open_orders: Decimal | None
    account_receipt_id: str
    positions_receipt_id: str
    open_orders_receipt_id: str
    option_contracts_receipt_id: str
    option_chain_receipt_id: str

    def receipt_ids(self) -> tuple[str, ...]:
        return (
            self.account_receipt_id,
            self.positions_receipt_id,
            self.open_orders_receipt_id,
            self.option_contracts_receipt_id,
            self.option_chain_receipt_id,
        )


@dataclass(frozen=True)
class B5CompetitionProposal:
    status: str
    decision_id: str
    final_decision_hash: str
    policy_hash: str
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
    numerics = (
        snapshot.equity,
        snapshot.same_underlying_committed_premium_at_risk,
        snapshot.aggregate_committed_long_option_premium_at_risk,
        snapshot.remaining_after_equity_safety_reserve,
        snapshot.options_buying_power_after_open_orders,
    )
    return any(value is None for value in numerics) or any(
        not isinstance(receipt, str) or not receipt.strip()
        for receipt in snapshot.receipt_ids()
    )


def run_b5_competition_options(
    *,
    final_decision: Mapping[str, Any],
    underlying_symbol: str,
    option_contracts: Sequence[OptionContractCandidate],
    snapshot: B5ReadOnlyRiskSnapshot,
    policy: CompetitionOptionsPolicy,
) -> B5CompetitionProposal:
    """Run the deterministic B5 competition path with zero broker writes.

    This function is intentionally side-effect free. A PASS is a proposal/risk
    result only; B6 must still perform human approval, commit-time revalidation,
    and the single Alpaca PAPER execution attempt.
    """

    handoff = validate_b4_invest_handoff(
        final_decision,
        expected_mandate_version=policy.version,
    )
    receipts = snapshot.receipt_ids()

    if snapshot.observed_at.tzinfo is None:
        return B5CompetitionProposal(
            status="INCOMPLETE_DATA",
            decision_id=handoff.decision_id,
            final_decision_hash=handoff.final_decision_hash,
            policy_hash=policy.policy_hash,
            underlying_symbol=underlying_symbol,
            option_symbol=None,
            quantity=0,
            action=None,
            order_type=None,
            time_in_force=None,
            environment=None,
            initial_limit_price=None,
            max_loss_usd=None,
            safe_premium_budget=None,
            source_receipt_ids=receipts,
            option_source_receipt_id=None,
            reason_codes=("RISK_SNAPSHOT_TIMESTAMP_NOT_TIMEZONE_AWARE",),
        )

    if _snapshot_missing(snapshot):
        return B5CompetitionProposal(
            status="INCOMPLETE_DATA",
            decision_id=handoff.decision_id,
            final_decision_hash=handoff.final_decision_hash,
            policy_hash=policy.policy_hash,
            underlying_symbol=underlying_symbol,
            option_symbol=None,
            quantity=0,
            action=None,
            order_type=None,
            time_in_force=None,
            environment=None,
            initial_limit_price=None,
            max_loss_usd=None,
            safe_premium_budget=None,
            source_receipt_ids=receipts,
            option_source_receipt_id=None,
            reason_codes=("MISSING_REQUIRED_RISK_INPUT",),
        )

    selection = select_long_call(
        option_contracts,
        underlying_symbol=underlying_symbol,
        selection_time=snapshot.observed_at,
        policy=policy,
    )
    if selection.status != "PASS" or selection.selected is None:
        return B5CompetitionProposal(
            status="BLOCK",
            decision_id=handoff.decision_id,
            final_decision_hash=handoff.final_decision_hash,
            policy_hash=policy.policy_hash,
            underlying_symbol=underlying_symbol,
            option_symbol=None,
            quantity=0,
            action=None,
            order_type=None,
            time_in_force=None,
            environment=None,
            initial_limit_price=None,
            max_loss_usd=Decimal("0"),
            safe_premium_budget=None,
            source_receipt_ids=receipts,
            option_source_receipt_id=None,
            reason_codes=selection.reason_codes,
        )

    selected = selection.selected
    assert selected.ask is not None
    assert snapshot.equity is not None
    assert snapshot.same_underlying_committed_premium_at_risk is not None
    assert snapshot.aggregate_committed_long_option_premium_at_risk is not None
    assert snapshot.remaining_after_equity_safety_reserve is not None
    assert snapshot.options_buying_power_after_open_orders is not None

    budgets = derive_premium_risk_budgets(
        equity=snapshot.equity,
        same_underlying_open_premium_at_risk=(
            snapshot.same_underlying_committed_premium_at_risk
        ),
        aggregate_open_long_option_premium_at_risk=(
            snapshot.aggregate_committed_long_option_premium_at_risk
        ),
        remaining_after_equity_safety_reserve=(
            snapshot.remaining_after_equity_safety_reserve
        ),
        current_alpaca_broker_capacity=(
            snapshot.options_buying_power_after_open_orders
        ),
        policy=policy,
    )
    sizing = size_long_call(
        approved_limit_price=selected.ask,
        budgets=PremiumRiskBudgetInputs(
            remaining_new_position_budget=budgets.remaining_new_position_budget,
            remaining_same_underlying_budget=budgets.remaining_same_underlying_budget,
            remaining_aggregate_options_budget=budgets.remaining_aggregate_options_budget,
            remaining_after_equity_safety_reserve=(
                budgets.remaining_after_equity_safety_reserve
            ),
            current_alpaca_broker_capacity=budgets.current_alpaca_broker_capacity,
        ),
        policy=policy,
    )

    if sizing.status != "PASS":
        return B5CompetitionProposal(
            status=sizing.status,
            decision_id=handoff.decision_id,
            final_decision_hash=handoff.final_decision_hash,
            policy_hash=policy.policy_hash,
            underlying_symbol=underlying_symbol,
            option_symbol=selected.symbol,
            quantity=0,
            action=None,
            order_type=None,
            time_in_force=None,
            environment=None,
            initial_limit_price=sizing.approved_limit_price,
            max_loss_usd=sizing.max_loss_usd,
            safe_premium_budget=sizing.safe_premium_budget,
            source_receipt_ids=receipts,
            option_source_receipt_id=selected.source_receipt_id,
            reason_codes=sizing.reason_codes,
        )

    return B5CompetitionProposal(
        status="PASS",
        decision_id=handoff.decision_id,
        final_decision_hash=handoff.final_decision_hash,
        policy_hash=policy.policy_hash,
        underlying_symbol=underlying_symbol,
        option_symbol=selected.symbol,
        quantity=sizing.quantity,
        action="BUY_TO_OPEN",
        order_type="LIMIT",
        time_in_force="DAY",
        environment="PAPER",
        initial_limit_price=sizing.approved_limit_price,
        max_loss_usd=sizing.max_loss_usd,
        safe_premium_budget=sizing.safe_premium_budget,
        source_receipt_ids=receipts,
        option_source_receipt_id=selected.source_receipt_id,
        reason_codes=(),
    )
