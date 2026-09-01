"""Offline, test-only Competition V1 options adapter; it is not canonical B5 authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_FLOOR
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from aic.domain.canonical import canonical_sha256
from aic.research.mandate import (
    COMPETITION_MANDATE_VERSION,
    COMPETITION_OPTIONS_POLICY_HASH,
    load_competition_options_policy,
)


ADAPTER_NAME = "COMPETITION_V1_OPTION_INTENT"
ADAPTER_CLASSIFICATION = "NON_CANONICAL_VERTICAL_SLICE_ADAPTER"
TEST_MODE = "TEST_FIXTURE"


class B5Blocked(ValueError):
    """Fail-closed B5 test adapter rejection."""


@dataclass(frozen=True)
class OptionContract:
    option_symbol: str
    contract_type: str
    opening_direction: str
    expiration: date
    strike: Decimal
    multiplier: int
    bid: Decimal
    ask: Decimal
    delta: Decimal | None
    open_interest: int
    active: bool
    tradable: bool
    greeks_present: bool
    quote_age_seconds: int
    open_interest_current_for_latest_completed_session: bool


@dataclass(frozen=True)
class PremiumRiskResult:
    status: str
    reason: str | None
    quantity: int
    max_loss_usd: Decimal
    same_underlying_risk_after: Decimal
    aggregate_option_risk_after: Decimal
    cash_reserve_after: Decimal


@dataclass(frozen=True)
class CompetitionV1OptionIntent:
    """Immutable event DTO, explicitly not a canonical ProposalCandidate replacement."""

    decision_id: str
    final_decision_hash: str
    candidate_id: str
    underlying_symbol: str
    option_symbol: str
    contract_type: str
    opening_direction: str
    expiration: str
    strike: Decimal
    multiplier: int
    selection_bid: Decimal
    selection_ask: Decimal
    approved_limit_price: Decimal
    delta: Decimal
    open_interest: int
    relative_spread: Decimal
    dte: int
    quantity: int
    premium_per_contract: Decimal
    max_loss_usd: Decimal
    account_equity: Decimal
    premium_risk_after: Decimal
    same_underlying_risk_after: Decimal
    aggregate_option_risk_after: Decimal
    cash_reserve_after: Decimal
    risk_status: str
    environment: str
    order_type: str
    time_in_force: str
    extended_hours: bool
    mandate_version: str
    options_policy_hash: str
    created_at: str
    payload_hash: str
    adapter_name: str = ADAPTER_NAME
    adapter_classification: str = ADAPTER_CLASSIFICATION

    def payload(self, *, include_hash: bool = True) -> dict[str, object]:
        result = {
            "adapter_name": self.adapter_name,
            "adapter_classification": self.adapter_classification,
            "decision_id": self.decision_id,
            "final_decision_hash": self.final_decision_hash,
            "candidate_id": self.candidate_id,
            "underlying_symbol": self.underlying_symbol,
            "option_symbol": self.option_symbol,
            "contract_type": self.contract_type,
            "opening_direction": self.opening_direction,
            "expiration": self.expiration,
            "strike": str(self.strike),
            "multiplier": self.multiplier,
            "selection_bid": str(self.selection_bid),
            "selection_ask": str(self.selection_ask),
            "approved_limit_price": str(self.approved_limit_price),
            "delta": str(self.delta),
            "open_interest": self.open_interest,
            "relative_spread": str(self.relative_spread),
            "DTE": self.dte,
            "quantity": self.quantity,
            "premium_per_contract": str(self.premium_per_contract),
            "max_loss_usd": str(self.max_loss_usd),
            "account_equity": str(self.account_equity),
            "premium_risk_after": str(self.premium_risk_after),
            "same_underlying_risk_after": str(self.same_underlying_risk_after),
            "aggregate_option_risk_after": str(self.aggregate_option_risk_after),
            "cash_reserve_after": str(self.cash_reserve_after),
            "risk_status": self.risk_status,
            "environment": self.environment,
            "order_type": self.order_type,
            "time_in_force": self.time_in_force,
            "extended_hours": self.extended_hours,
            "mandate_version": self.mandate_version,
            "options_policy_hash": self.options_policy_hash,
            "created_at": self.created_at,
        }
        if include_hash:
            result["payload_hash"] = self.payload_hash
        return result


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise B5Blocked(f"invalid Decimal for {field}")
    try:
        result = Decimal(str(value))
    except Exception as exc:  # pragma: no cover - Decimal has several input errors
        raise B5Blocked(f"invalid Decimal for {field}") from exc
    if not result.is_finite():
        raise B5Blocked(f"non-finite Decimal for {field}")
    return result


def load_test_fixture(path: Path, *, mode: str) -> dict[str, Any]:
    """Load the explicit test-only INVEST boundary; default/production modes reject it."""
    if mode != TEST_MODE:
        raise B5Blocked("TEST_FIXTURE mode is required")
    fixture = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(fixture, dict):
        raise B5Blocked("fixture root must be an object")
    if fixture.get("mode") != TEST_MODE:
        raise B5Blocked("fixture mode drift")
    if fixture.get("classification") != ["TEST_FIXTURE", "NON_PRODUCTION", "NO_BROKER_AUTHORITY"]:
        raise B5Blocked("fixture classification drift")
    if fixture.get("adapter") != "COMPETITION_V1_ONLY_NON_CANONICAL_VERTICAL_SLICE_ADAPTER":
        raise B5Blocked("fixture adapter drift")
    if fixture.get("execution_authority") is not False:
        raise B5Blocked("test fixture must not grant execution authority")
    decision = fixture.get("final_decision")
    if not isinstance(decision, dict) or decision.get("outcome") != "INVEST":
        raise B5Blocked("B5 requires TEST_FIXTURE INVEST decision")
    if decision.get("risk_result_id") is not None:
        raise B5Blocked("B4 boundary must have null risk_result_id")
    return fixture


def parse_contracts(payload: Mapping[str, Any]) -> tuple[date, list[OptionContract]]:
    if payload.get("mode") != TEST_MODE:
        raise B5Blocked("option chain must be TEST_FIXTURE")
    as_of = date.fromisoformat(str(payload["as_of_date"]))
    contracts: list[OptionContract] = []
    for raw in payload.get("contracts", []):
        if not isinstance(raw, Mapping):
            raise B5Blocked("option contract must be an object")
        contracts.append(
            OptionContract(
                option_symbol=str(raw["option_symbol"]),
                contract_type=str(raw["contract_type"]),
                opening_direction=str(raw["opening_direction"]),
                expiration=date.fromisoformat(str(raw["expiration"])),
                strike=_decimal(raw["strike"], "strike"),
                multiplier=int(raw["multiplier"]),
                bid=_decimal(raw["bid"], "bid"),
                ask=_decimal(raw["ask"], "ask"),
                delta=None if raw.get("delta") is None else _decimal(raw["delta"], "delta"),
                open_interest=int(raw["open_interest"]),
                active=raw.get("active") is True,
                tradable=raw.get("tradable") is True,
                greeks_present=raw.get("greeks_present") is True,
                quote_age_seconds=int(raw.get("quote_age_seconds", -1)),
                open_interest_current_for_latest_completed_session=(
                    raw.get("open_interest_current_for_latest_completed_session") is True
                ),
            )
        )
    return as_of, contracts


def relative_spread_from_quote(bid: Decimal, ask: Decimal) -> Decimal:
    if bid <= 0 or ask <= 0 or ask < bid:
        raise B5Blocked("invalid option quote")
    midpoint = (bid + ask) / Decimal("2")
    if midpoint <= 0:
        raise B5Blocked("invalid option quote")
    return (ask - bid) / midpoint


def relative_spread(contract: OptionContract) -> Decimal:
    return relative_spread_from_quote(contract.bid, contract.ask)


def eligible_contracts(contracts: Sequence[OptionContract], *, as_of: date) -> list[OptionContract]:
    policy = load_competition_options_policy()["selector"]
    delta_min = _decimal(policy["delta_min"], "delta_min")
    delta_max = _decimal(policy["delta_max"], "delta_max")
    spread_max = _decimal(policy["max_relative_spread"], "max_relative_spread")
    quote_max_age = int(policy["selection_quote_max_age_seconds"])
    dte_min = int(policy["dte_min_calendar_days"])
    dte_max = int(policy["dte_max_calendar_days"])
    min_open_interest = int(policy["min_open_interest"])
    eligible: list[OptionContract] = []
    for contract in contracts:
        dte = (contract.expiration - as_of).days
        if (
            contract.contract_type != policy["contract_type"]
            or contract.opening_direction != policy["opening_direction"]
            or contract.multiplier != int(policy["standard_contract_size"])
            or not dte_min <= dte <= dte_max
            or contract.delta is None
            or not delta_min <= contract.delta <= delta_max
            or contract.open_interest < min_open_interest
            or not contract.active
            or not contract.tradable
            or not contract.greeks_present
            or contract.quote_age_seconds < 0
            or contract.quote_age_seconds > quote_max_age
            or not contract.open_interest_current_for_latest_completed_session
        ):
            continue
        try:
            spread = relative_spread(contract)
        except B5Blocked:
            continue
        if spread <= spread_max:
            eligible.append(contract)
    return eligible


def select_contract(contracts: Sequence[OptionContract], *, as_of: date) -> OptionContract:
    eligible = eligible_contracts(contracts, as_of=as_of)
    if not eligible:
        raise B5Blocked("BLOCK_INCOMPLETE_OPTION_MARKET")
    policy = load_competition_options_policy()["selector"]
    target_dte = int(policy["dte_target_calendar_days"])
    target_delta = _decimal(policy["delta_target"], "delta_target")
    return min(
        eligible,
        key=lambda contract: (
            abs((contract.expiration - as_of).days - target_dte),
            abs(contract.delta - target_delta) if contract.delta is not None else Decimal("Infinity"),
            relative_spread(contract),
            -contract.open_interest,
            contract.option_symbol,
        ),
    )


def calculate_premium_risk(account: Mapping[str, Any], premium_per_contract: Decimal) -> PremiumRiskResult:
    """Pure frozen-policy risk/sizing calculation shared by B5 and B6."""
    if premium_per_contract <= 0:
        return PremiumRiskResult("BLOCK", "BLOCK_INVALID_PREMIUM", 0, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"))
    policy = load_competition_options_policy()["risk"]
    equity = _decimal(account["account_equity"], "account_equity")
    cash = _decimal(account["cash_available"], "cash_available")
    same_current = _decimal(account["current_same_underlying_premium_risk"], "same risk")
    aggregate_current = _decimal(account["current_aggregate_option_premium_risk"], "aggregate risk")
    broker_capacity = _decimal(account["broker_capacity"], "broker capacity")
    new_cap = equity * _decimal(policy["max_new_position_premium_at_risk_fraction_of_equity"], "new cap")
    same_cap = equity * _decimal(policy["max_same_underlying_premium_at_risk_fraction_of_equity"], "same cap")
    aggregate_cap = equity * _decimal(policy["max_aggregate_open_long_option_premium_at_risk_fraction_of_equity"], "aggregate cap")
    reserve = equity * _decimal(policy["min_post_proposal_equity_safety_reserve_fraction"], "reserve")
    if same_current > same_cap or aggregate_current > aggregate_cap:
        return PremiumRiskResult("BLOCK", "BLOCK_RISK_CAP_EXCEEDED", 0, Decimal("0"), same_current, aggregate_current, cash)
    safe_budget = min(new_cap, same_cap - same_current, aggregate_cap - aggregate_current, cash - reserve, broker_capacity)
    quantity = int((safe_budget / premium_per_contract).to_integral_value(rounding=ROUND_FLOOR)) if safe_budget > 0 else 0
    quantity = min(quantity, int(policy["max_contracts_per_new_order"]))
    if quantity < 1:
        return PremiumRiskResult("BLOCK", "BLOCK_INSUFFICIENT_RISK_BUDGET", 0, Decimal("0"), same_current, aggregate_current, cash)
    max_loss = premium_per_contract * quantity
    return PremiumRiskResult("PASS", None, quantity, max_loss, same_current + max_loss, aggregate_current + max_loss, cash - max_loss)


def build_option_intent(
    fixture: Mapping[str, Any],
    option_chain: Mapping[str, Any],
    *,
    mode: str,
) -> CompetitionV1OptionIntent:
    """Select and size one long call using only frozen policy and fixture inputs."""
    if mode != TEST_MODE or fixture.get("mode") != TEST_MODE:
        raise B5Blocked("production/default mode may not start test B5")
    decision = fixture.get("final_decision")
    if not isinstance(decision, Mapping) or decision.get("outcome") != "INVEST":
        raise B5Blocked("B5 never starts without INVEST")
    as_of, contracts = parse_contracts(option_chain)
    selected = select_contract(contracts, as_of=as_of)
    premium = selected.ask * Decimal(selected.multiplier)
    account = option_chain.get("account")
    if not isinstance(account, Mapping):
        raise B5Blocked("account fixture missing")
    risk = calculate_premium_risk(account, premium)
    if risk.status != "PASS":
        raise B5Blocked(risk.reason or "BLOCK_RISK")
    equity = _decimal(account["account_equity"], "account_equity")
    values: dict[str, object] = {
        "decision_id": str(decision["decision_id"]),
        "final_decision_hash": str(decision["final_decision_hash"]),
        "candidate_id": str(decision["primary_candidate_id"]),
        "underlying_symbol": str(option_chain["underlying_symbol"]),
        "option_symbol": selected.option_symbol,
        "contract_type": "CALL",
        "opening_direction": "BUY_TO_OPEN",
        "expiration": selected.expiration.isoformat(),
        "strike": selected.strike,
        "multiplier": 100,
        "selection_bid": selected.bid,
        "selection_ask": selected.ask,
        "approved_limit_price": selected.ask,
        "delta": selected.delta,
        "open_interest": selected.open_interest,
        "relative_spread": relative_spread(selected),
        "dte": (selected.expiration - as_of).days,
        "quantity": risk.quantity,
        "premium_per_contract": premium,
        "max_loss_usd": risk.max_loss_usd,
        "account_equity": equity,
        "premium_risk_after": risk.max_loss_usd,
        "same_underlying_risk_after": risk.same_underlying_risk_after,
        "aggregate_option_risk_after": risk.aggregate_option_risk_after,
        "cash_reserve_after": risk.cash_reserve_after,
        "risk_status": "PASS",
        "environment": "PAPER",
        "order_type": "LIMIT",
        "time_in_force": "DAY",
        "extended_hours": False,
        "mandate_version": COMPETITION_MANDATE_VERSION,
        "options_policy_hash": COMPETITION_OPTIONS_POLICY_HASH,
        "created_at": str(decision["created_at"]),
    }
    draft = CompetitionV1OptionIntent(payload_hash="", **values)  # type: ignore[arg-type]
    return CompetitionV1OptionIntent(payload_hash=canonical_sha256(draft.payload(include_hash=False)), **values)  # type: ignore[arg-type]
