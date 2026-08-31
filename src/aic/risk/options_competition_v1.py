from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path
from typing import Any, Mapping, Sequence


EXPECTED_POLICY_ID = "ALPACA_2026_COMPETITION_OPTIONS_POLICY"
EXPECTED_POLICY_VERSION = "ALPACA_COMPETITION_V1_2026_08_29"
EXPECTED_POLICY_HASH = "a4e5f95746cf1e928069454e23bd0bf76e92afe38208c4d8cc0c9cb7a16f00a6"
EXPECTED_STRATEGY = "SINGLE_LEG_LONG_CALL_ONLY"
EXPECTED_RANKING = (
    "ABS_DTE_DISTANCE_TO_35_ASC",
    "ABS_DELTA_DISTANCE_TO_0_50_ASC",
    "RELATIVE_SPREAD_ASC",
    "OPEN_INTEREST_DESC",
    "CANONICAL_OPTION_SYMBOL_ASC",
)
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class B5CompetitionOptionsError(ValueError):
    """Fail-closed deterministic B5 contract error."""


@dataclass(frozen=True)
class CompetitionOptionsPolicy:
    policy_id: str
    version: str
    policy_hash: str
    dte_min: int
    dte_max: int
    dte_target: int
    delta_min: Decimal
    delta_max: Decimal
    delta_target: Decimal
    max_relative_spread: Decimal
    min_open_interest: int
    selection_quote_max_age_seconds: int
    standard_contract_size: int
    max_new_position_fraction: Decimal
    max_same_underlying_fraction: Decimal
    max_aggregate_options_fraction: Decimal
    min_equity_safety_reserve_fraction: Decimal
    max_contracts_per_new_order: int
    ranking: tuple[str, ...]


@dataclass(frozen=True)
class InvestHandoff:
    decision_id: str
    final_decision_hash: str
    primary_candidate_id: str
    mandate_version: str


@dataclass(frozen=True)
class OptionContractCandidate:
    symbol: str
    underlying_symbol: str
    contract_type: str
    expiration_date: date
    strike: Decimal
    exercise_style: str
    contract_size: int
    delta: Decimal | None
    bid: Decimal | None
    ask: Decimal | None
    open_interest: int | None
    open_interest_current: bool
    quote_timestamp: datetime | None
    status: str
    tradable: bool
    source_receipt_id: str

    def relative_spread(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        if self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            return None
        mid = (self.bid + self.ask) / Decimal("2")
        if mid <= 0:
            return None
        return (self.ask - self.bid) / mid


@dataclass(frozen=True)
class SelectionResult:
    status: str
    selected: OptionContractCandidate | None
    eligible_count: int
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class PremiumRiskBudgetInputs:
    remaining_new_position_budget: Decimal | None
    remaining_same_underlying_budget: Decimal | None
    remaining_aggregate_options_budget: Decimal | None
    remaining_after_equity_safety_reserve: Decimal | None
    current_alpaca_broker_capacity: Decimal | None


@dataclass(frozen=True)
class SizingResult:
    status: str
    quantity: int
    approved_limit_price: Decimal | None
    premium_per_contract: Decimal | None
    safe_premium_budget: Decimal | None
    max_loss_usd: Decimal | None
    reason_codes: tuple[str, ...]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise B5CompetitionOptionsError(message)


def _decimal_text(value: Any, *, field: str) -> Decimal:
    _require(
        isinstance(value, str) and value.strip() == value and bool(value),
        f"{field} must be a canonical decimal string",
    )
    try:
        out = Decimal(value)
    except InvalidOperation as exc:
        raise B5CompetitionOptionsError(f"{field} is not a valid decimal") from exc
    _require(out.is_finite(), f"{field} must be finite")
    return out


def load_competition_options_policy(path: str | Path) -> CompetitionOptionsPolicy:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise B5CompetitionOptionsError("unable to read competition options policy") from exc
    _require(isinstance(payload, dict), "options policy root must be an object")
    return competition_options_policy_from_mapping(payload)


def competition_options_policy_from_mapping(
    payload: Mapping[str, Any],
) -> CompetitionOptionsPolicy:
    _require(payload.get("active") is True, "competition options policy is not active")
    _require(payload.get("policy_id") == EXPECTED_POLICY_ID, "unexpected options policy id")
    _require(payload.get("version") == EXPECTED_POLICY_VERSION, "unexpected options policy version")
    _require(payload.get("policy_hash") == EXPECTED_POLICY_HASH, "unexpected options policy hash")
    _require(
        payload.get("broker_write_authority") is False,
        "B5 policy unexpectedly grants broker-write authority",
    )
    _require(payload.get("live_execution") is False, "B5 policy unexpectedly permits live execution")
    _require(payload.get("strategy_allowlist") == [EXPECTED_STRATEGY], "unexpected strategy allowlist")

    selector = payload.get("selector")
    risk = payload.get("risk")
    order = payload.get("order")
    sizing = payload.get("sizing")
    _require(isinstance(selector, Mapping), "selector policy missing")
    _require(isinstance(risk, Mapping), "risk policy missing")
    _require(isinstance(order, Mapping), "order policy missing")
    _require(isinstance(sizing, Mapping), "sizing policy missing")

    _require(selector.get("contract_type") == "CALL", "selector must be CALL only")
    _require(selector.get("opening_direction") == "BUY_TO_OPEN", "selector must be BUY_TO_OPEN")
    _require(selector.get("status_required") == "ACTIVE", "selector status gate drift")
    _require(selector.get("tradable_required") is True, "selector tradability gate drift")
    _require(selector.get("ask_positive_required") is True, "selector ask gate drift")
    _require(selector.get("bid_positive_required") is True, "selector bid gate drift")
    _require(selector.get("greeks_required") is True, "selector Greeks gate drift")
    _require(tuple(selector.get("ranking", ())) == EXPECTED_RANKING, "selector ranking drift")
    _require(
        selector.get("no_eligible_contract_behavior") == "BLOCK_INCOMPLETE_OPTION_MARKET",
        "no-eligible behavior drift",
    )

    _require(order.get("environment") == "PAPER", "B5/B6 order environment must be PAPER")
    _require(order.get("action") == "BUY_TO_OPEN", "order action drift")
    _require(order.get("order_type") == "LIMIT", "option order must be LIMIT")
    _require(order.get("time_in_force") == "DAY", "option TIF must be DAY")
    _require(order.get("human_approval_required") is True, "human approval must remain mandatory")
    _require(order.get("automatic_price_chase") is False, "automatic price chase must remain disabled")
    _require(order.get("blind_retry") is False, "blind retry must remain disabled")
    _require(sizing.get("quantity_type") == "POSITIVE_INTEGER", "option quantity must be integer")
    _require(
        sizing.get("deep_otm_cheaper_contract_fallback") is False,
        "deep-OTM fallback must remain disabled",
    )

    policy = CompetitionOptionsPolicy(
        policy_id=str(payload["policy_id"]),
        version=str(payload["version"]),
        policy_hash=str(payload["policy_hash"]),
        dte_min=int(selector["dte_min_calendar_days"]),
        dte_max=int(selector["dte_max_calendar_days"]),
        dte_target=int(selector["dte_target_calendar_days"]),
        delta_min=_decimal_text(selector["delta_min"], field="selector.delta_min"),
        delta_max=_decimal_text(selector["delta_max"], field="selector.delta_max"),
        delta_target=_decimal_text(selector["delta_target"], field="selector.delta_target"),
        max_relative_spread=_decimal_text(
            selector["max_relative_spread"], field="selector.max_relative_spread"
        ),
        min_open_interest=int(selector["min_open_interest"]),
        selection_quote_max_age_seconds=int(selector["selection_quote_max_age_seconds"]),
        standard_contract_size=int(selector["standard_contract_size"]),
        max_new_position_fraction=_decimal_text(
            risk["max_new_position_premium_at_risk_fraction_of_equity"],
            field="risk.max_new_position_premium_at_risk_fraction_of_equity",
        ),
        max_same_underlying_fraction=_decimal_text(
            risk["max_same_underlying_premium_at_risk_fraction_of_equity"],
            field="risk.max_same_underlying_premium_at_risk_fraction_of_equity",
        ),
        max_aggregate_options_fraction=_decimal_text(
            risk["max_aggregate_open_long_option_premium_at_risk_fraction_of_equity"],
            field="risk.max_aggregate_open_long_option_premium_at_risk_fraction_of_equity",
        ),
        min_equity_safety_reserve_fraction=_decimal_text(
            risk["min_post_proposal_equity_safety_reserve_fraction"],
            field="risk.min_post_proposal_equity_safety_reserve_fraction",
        ),
        max_contracts_per_new_order=int(risk["max_contracts_per_new_order"]),
        ranking=tuple(selector["ranking"]),
    )
    _require(policy.dte_min <= policy.dte_target <= policy.dte_max, "DTE target outside bounds")
    _require(
        policy.delta_min <= policy.delta_target <= policy.delta_max,
        "delta target outside bounds",
    )
    _require(policy.max_contracts_per_new_order == 2, "competition max-contract cap drift")
    _require(policy.standard_contract_size == 100, "non-standard contract-size policy drift")
    return policy


def validate_b4_invest_handoff(
    final_decision: Mapping[str, Any],
    *,
    expected_mandate_version: str = EXPECTED_POLICY_VERSION,
) -> InvestHandoff:
    _require(final_decision.get("outcome") == "INVEST", "B5 is locked unless B4 outcome is INVEST")
    _require(
        final_decision.get("evidence_status") in {"COMPLETE", "PARTIAL"},
        "INVEST evidence state is not B5-eligible",
    )
    blockers = final_decision.get("blocking_reason_codes")
    _require(isinstance(blockers, list) and not blockers, "INVEST contains unresolved blocking reasons")
    decision_id = final_decision.get("decision_id")
    candidate_id = final_decision.get("primary_candidate_id")
    decision_hash = final_decision.get("final_decision_hash")
    mandate_version = final_decision.get("mandate_version")
    _require(isinstance(decision_id, str) and bool(decision_id.strip()), "decision_id missing")
    _require(
        isinstance(candidate_id, str) and bool(candidate_id.strip()),
        "primary_candidate_id missing",
    )
    _require(
        isinstance(decision_hash, str) and HEX64.fullmatch(decision_hash) is not None,
        "final_decision_hash invalid",
    )
    _require(mandate_version == expected_mandate_version, "mandate_version mismatch")
    _require(
        final_decision.get("risk_result_id") is None,
        "B4 FinalDecision must not contain a B5 risk result",
    )
    return InvestHandoff(
        decision_id=decision_id,
        final_decision_hash=decision_hash,
        primary_candidate_id=candidate_id,
        mandate_version=str(mandate_version),
    )


def _quote_age_seconds(quote_timestamp: datetime, *, selection_time: datetime) -> Decimal:
    _require(
        quote_timestamp.tzinfo is not None and selection_time.tzinfo is not None,
        "quote timestamps must be timezone-aware",
    )
    delta = selection_time.astimezone(timezone.utc) - quote_timestamp.astimezone(timezone.utc)
    return Decimal(str(delta.total_seconds()))


def _eligible(
    contract: OptionContractCandidate,
    *,
    underlying_symbol: str,
    selection_time: datetime,
    policy: CompetitionOptionsPolicy,
) -> bool:
    if contract.underlying_symbol != underlying_symbol:
        return False
    if contract.contract_type != "CALL":
        return False
    if contract.status != "ACTIVE" or not contract.tradable:
        return False
    if contract.contract_size != policy.standard_contract_size:
        return False
    if contract.strike <= 0 or not contract.exercise_style.strip() or not contract.source_receipt_id.strip():
        return False

    dte = (contract.expiration_date - selection_time.date()).days
    if not (policy.dte_min <= dte <= policy.dte_max):
        return False
    if contract.bid is None or contract.ask is None:
        return False
    if contract.bid <= 0 or contract.ask <= 0 or contract.ask < contract.bid:
        return False
    if contract.quote_timestamp is None:
        return False
    quote_age = _quote_age_seconds(contract.quote_timestamp, selection_time=selection_time)
    if quote_age < 0 or quote_age > policy.selection_quote_max_age_seconds:
        return False

    spread = contract.relative_spread()
    if spread is None or spread > policy.max_relative_spread:
        return False
    if contract.open_interest is None or contract.open_interest < policy.min_open_interest:
        return False
    if not contract.open_interest_current:
        return False
    if contract.delta is None or not (policy.delta_min <= contract.delta <= policy.delta_max):
        return False
    return True


def select_long_call(
    contracts: Sequence[OptionContractCandidate],
    *,
    underlying_symbol: str,
    selection_time: datetime,
    policy: CompetitionOptionsPolicy,
) -> SelectionResult:
    _require(selection_time.tzinfo is not None, "selection_time must be timezone-aware")
    _require(bool(underlying_symbol.strip()), "underlying_symbol missing")
    eligible = [
        contract
        for contract in contracts
        if _eligible(
            contract,
            underlying_symbol=underlying_symbol,
            selection_time=selection_time,
            policy=policy,
        )
    ]
    if not eligible:
        return SelectionResult(
            status="BLOCK",
            selected=None,
            eligible_count=0,
            reason_codes=("INCOMPLETE_OPTION_MARKET",),
        )

    def rank_key(contract: OptionContractCandidate) -> tuple[Any, ...]:
        dte = (contract.expiration_date - selection_time.date()).days
        spread = contract.relative_spread()
        assert contract.delta is not None
        assert spread is not None
        assert contract.open_interest is not None
        return (
            abs(dte - policy.dte_target),
            abs(contract.delta - policy.delta_target),
            spread,
            -contract.open_interest,
            contract.symbol,
        )

    selected = min(eligible, key=rank_key)
    return SelectionResult(
        status="PASS",
        selected=selected,
        eligible_count=len(eligible),
        reason_codes=(),
    )


def derive_premium_risk_budgets(
    *,
    equity: Decimal,
    same_underlying_open_premium_at_risk: Decimal,
    aggregate_open_long_option_premium_at_risk: Decimal,
    remaining_after_equity_safety_reserve: Decimal,
    current_alpaca_broker_capacity: Decimal,
    policy: CompetitionOptionsPolicy,
) -> PremiumRiskBudgetInputs:
    values = (
        equity,
        same_underlying_open_premium_at_risk,
        aggregate_open_long_option_premium_at_risk,
        remaining_after_equity_safety_reserve,
        current_alpaca_broker_capacity,
    )
    if any(not isinstance(value, Decimal) or not value.is_finite() for value in values):
        raise B5CompetitionOptionsError("authoritative risk arithmetic requires finite Decimal inputs")
    if equity <= 0:
        raise B5CompetitionOptionsError("equity must be positive")
    if any(value < 0 for value in values[1:]):
        raise B5CompetitionOptionsError("risk exposures/capacities must be non-negative")

    return PremiumRiskBudgetInputs(
        remaining_new_position_budget=equity * policy.max_new_position_fraction,
        remaining_same_underlying_budget=max(
            Decimal("0"),
            equity * policy.max_same_underlying_fraction - same_underlying_open_premium_at_risk,
        ),
        remaining_aggregate_options_budget=max(
            Decimal("0"),
            equity * policy.max_aggregate_options_fraction - aggregate_open_long_option_premium_at_risk,
        ),
        remaining_after_equity_safety_reserve=remaining_after_equity_safety_reserve,
        current_alpaca_broker_capacity=current_alpaca_broker_capacity,
    )


def size_long_call(
    *,
    approved_limit_price: Decimal | None,
    budgets: PremiumRiskBudgetInputs,
    policy: CompetitionOptionsPolicy,
) -> SizingResult:
    budget_values = (
        budgets.remaining_new_position_budget,
        budgets.remaining_same_underlying_budget,
        budgets.remaining_aggregate_options_budget,
        budgets.remaining_after_equity_safety_reserve,
        budgets.current_alpaca_broker_capacity,
    )
    if approved_limit_price is None or any(value is None for value in budget_values):
        return SizingResult(
            status="INCOMPLETE_DATA",
            quantity=0,
            approved_limit_price=approved_limit_price,
            premium_per_contract=None,
            safe_premium_budget=None,
            max_loss_usd=None,
            reason_codes=("MISSING_REQUIRED_RISK_INPUT",),
        )
    if (
        not isinstance(approved_limit_price, Decimal)
        or not approved_limit_price.is_finite()
        or approved_limit_price <= 0
    ):
        return SizingResult(
            status="INCOMPLETE_DATA",
            quantity=0,
            approved_limit_price=approved_limit_price if isinstance(approved_limit_price, Decimal) else None,
            premium_per_contract=None,
            safe_premium_budget=None,
            max_loss_usd=None,
            reason_codes=("INVALID_LIMIT_PRICE",),
        )

    decimal_budgets = tuple(value for value in budget_values if isinstance(value, Decimal))
    if len(decimal_budgets) != len(budget_values) or any(
        not value.is_finite() or value < 0 for value in decimal_budgets
    ):
        return SizingResult(
            status="INCOMPLETE_DATA",
            quantity=0,
            approved_limit_price=approved_limit_price,
            premium_per_contract=None,
            safe_premium_budget=None,
            max_loss_usd=None,
            reason_codes=("INVALID_RISK_BUDGET_INPUT",),
        )

    premium_per_contract = approved_limit_price * Decimal(policy.standard_contract_size)
    safe_budget = min(decimal_budgets)
    qty_budget = int(
        (safe_budget / premium_per_contract).to_integral_value(rounding=ROUND_FLOOR)
    )
    quantity = min(qty_budget, policy.max_contracts_per_new_order)
    if quantity < 1:
        return SizingResult(
            status="BLOCK",
            quantity=0,
            approved_limit_price=approved_limit_price,
            premium_per_contract=premium_per_contract,
            safe_premium_budget=safe_budget,
            max_loss_usd=Decimal("0"),
            reason_codes=("INSUFFICIENT_RISK_BUDGET",),
        )

    max_loss = premium_per_contract * quantity
    return SizingResult(
        status="PASS",
        quantity=quantity,
        approved_limit_price=approved_limit_price,
        premium_per_contract=premium_per_contract,
        safe_premium_budget=safe_budget,
        max_loss_usd=max_loss,
        reason_codes=(),
    )
