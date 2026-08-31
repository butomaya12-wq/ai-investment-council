from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from .b5_competition_pipeline_v1 import B5ReadOnlyRiskSnapshot
from .options_competition_v1 import (
    CompetitionOptionsPolicy,
    OptionContractCandidate,
)


_OCC_SYMBOL_RE = re.compile(
    r"^(?P<root>[A-Z]{1,6})(?P<expiry>[0-9]{6})(?P<right>[CP])(?P<strike>[0-9]{8})$"
)


class B5AlpacaNormalizationError(ValueError):
    """Fail-closed error while normalizing authoritative Alpaca B5 reads."""


@dataclass(frozen=True)
class B5NormalizedAlpacaInputs:
    snapshot: B5ReadOnlyRiskSnapshot
    option_contracts: tuple[OptionContractCandidate, ...]


@dataclass(frozen=True)
class _AccountState:
    paper_account_id: str
    equity: Decimal
    broker_capacity: Decimal
    remaining_after_equity_safety_reserve: Decimal
    trading_eligible: bool


@dataclass(frozen=True)
class _ExposureState:
    same_underlying_premium_at_risk: Decimal
    aggregate_long_option_premium_at_risk: Decimal
    unsupported_short_option_position: bool = False
    conflicting_open_option_sell_order: bool = False
    unvalued_open_option_exposure: bool = False


def _provider_decimal(value: Any, *, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise B5AlpacaNormalizationError(f"{field} must be numeric")
    if isinstance(value, Decimal):
        out = value
    elif isinstance(value, int):
        out = Decimal(value)
    elif isinstance(value, float):
        out = Decimal(str(value))
    elif isinstance(value, str):
        try:
            out = Decimal(value)
        except InvalidOperation as exc:
            raise B5AlpacaNormalizationError(f"{field} is not a decimal") from exc
    else:
        raise B5AlpacaNormalizationError(f"{field} has unsupported numeric type")
    if not out.is_finite():
        raise B5AlpacaNormalizationError(f"{field} must be finite")
    return out


def _provider_int(value: Any, *, field: str) -> int:
    decimal_value = _provider_decimal(value, field=field)
    integral = decimal_value.to_integral_value()
    if decimal_value != integral:
        raise B5AlpacaNormalizationError(f"{field} must be an integer")
    return int(integral)


def _provider_string(
    value: Any,
    *,
    field: str,
    uppercase: bool = False,
) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise B5AlpacaNormalizationError(f"{field} must be a non-empty trimmed string")
    return value.upper() if uppercase else value


def _provider_bool(value: Any, *, field: str) -> bool:
    if type(value) is not bool:
        raise B5AlpacaNormalizationError(f"{field} must be a JSON boolean")
    return value


def _provider_datetime(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise B5AlpacaNormalizationError(f"{field} must be an ISO timestamp")
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise B5AlpacaNormalizationError(f"{field} is not a valid ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise B5AlpacaNormalizationError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _provider_date(value: Any, *, field: str) -> date:
    if not isinstance(value, str) or not value.strip():
        raise B5AlpacaNormalizationError(f"{field} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise B5AlpacaNormalizationError(f"{field} is not a valid ISO date") from exc


def _records(
    payload: Any,
    *,
    container_keys: tuple[str, ...],
    field: str,
) -> tuple[Mapping[str, Any], ...]:
    raw: Any = payload
    if isinstance(payload, Mapping):
        for key in container_keys:
            if key in payload:
                raw = payload[key]
                break
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise B5AlpacaNormalizationError(f"{field} must be a list of objects")
    records: list[Mapping[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise B5AlpacaNormalizationError(f"{field} contains a non-object record")
        records.append(item)
    return tuple(records)


def _occ_identity(symbol: str) -> tuple[str, str]:
    match = _OCC_SYMBOL_RE.fullmatch(symbol)
    if match is None:
        raise B5AlpacaNormalizationError(
            f"option symbol is not a standard reconstructible OCC symbol: {symbol}"
        )
    return match.group("root"), match.group("right")


def _normalize_account(
    payload: Mapping[str, Any],
    *,
    policy: CompetitionOptionsPolicy,
) -> _AccountState:
    if not isinstance(payload, Mapping):
        raise B5AlpacaNormalizationError("account payload must be an object")

    paper_account_id = _provider_string(payload.get("id"), field="account.id")
    status = _provider_string(payload.get("status"), field="account.status", uppercase=True)
    trading_blocked = _provider_bool(
        payload.get("trading_blocked"), field="account.trading_blocked"
    )
    trade_suspended = _provider_bool(
        payload.get("trade_suspended_by_user"),
        field="account.trade_suspended_by_user",
    )
    account_blocked = (
        False
        if "account_blocked" not in payload
        else _provider_bool(payload.get("account_blocked"), field="account.account_blocked")
    )

    equity = _provider_decimal(payload.get("equity"), field="account.equity")
    cash = _provider_decimal(payload.get("cash"), field="account.cash")
    buying_power = _provider_decimal(
        payload.get("buying_power"), field="account.buying_power"
    )
    if equity <= 0:
        raise B5AlpacaNormalizationError("account.equity must be positive")
    if buying_power < 0:
        raise B5AlpacaNormalizationError("account.buying_power must be non-negative")

    capacities = [max(Decimal("0"), cash), buying_power]
    if payload.get("non_marginable_buying_power") is not None:
        non_marginable = _provider_decimal(
            payload.get("non_marginable_buying_power"),
            field="account.non_marginable_buying_power",
        )
        capacities.append(max(Decimal("0"), non_marginable))
    broker_capacity = min(capacities)

    reserve_required = equity * policy.min_equity_safety_reserve_fraction
    remaining_after_reserve = max(
        Decimal("0"),
        broker_capacity - reserve_required,
    )
    return _AccountState(
        paper_account_id=paper_account_id,
        equity=equity,
        broker_capacity=broker_capacity,
        remaining_after_equity_safety_reserve=remaining_after_reserve,
        trading_eligible=(
            status == "ACTIVE"
            and not trading_blocked
            and not trade_suspended
            and not account_blocked
        ),
    )


def _normalize_position_exposure(
    payload: Any,
    *,
    underlying_symbol: str,
) -> _ExposureState:
    same = Decimal("0")
    aggregate = Decimal("0")
    unsupported_short = False
    unvalued = False

    for position in _records(
        payload,
        container_keys=("positions",),
        field="positions",
    ):
        asset_class = position.get("asset_class")
        if asset_class != "us_option":
            continue

        try:
            symbol = _provider_string(
                position.get("symbol"), field="position.symbol", uppercase=True
            )
            root, _right = _occ_identity(symbol)
            side = _provider_string(
                position.get("side"), field=f"position[{symbol}].side"
            ).lower()
            qty = _provider_decimal(
                position.get("qty"), field=f"position[{symbol}].qty"
            )
            cost_basis = _provider_decimal(
                position.get("cost_basis"), field=f"position[{symbol}].cost_basis"
            )
        except B5AlpacaNormalizationError:
            unvalued = True
            continue

        if qty <= 0 or cost_basis < 0:
            unvalued = True
            continue
        if side == "short":
            unsupported_short = True
            continue
        if side != "long":
            unvalued = True
            continue

        premium = abs(cost_basis)
        aggregate += premium
        if root == underlying_symbol:
            same += premium

    return _ExposureState(
        same_underlying_premium_at_risk=same,
        aggregate_long_option_premium_at_risk=aggregate,
        unsupported_short_option_position=unsupported_short,
        unvalued_open_option_exposure=unvalued,
    )


def _normalize_open_order_exposure(
    payload: Any,
    *,
    underlying_symbol: str,
    policy: CompetitionOptionsPolicy,
) -> _ExposureState:
    same = Decimal("0")
    aggregate = Decimal("0")
    conflicting_sell = False
    unvalued = False

    for order in _records(
        payload,
        container_keys=("orders",),
        field="open_orders",
    ):
        symbol_value = order.get("symbol")
        asset_class = order.get("asset_class")
        if asset_class != "us_option":
            if not isinstance(symbol_value, str) or _OCC_SYMBOL_RE.fullmatch(symbol_value) is None:
                continue

        try:
            symbol = _provider_string(
                symbol_value, field="open_order.symbol", uppercase=True
            )
            root, _right = _occ_identity(symbol)
            side = _provider_string(
                order.get("side"), field=f"open_order[{symbol}].side"
            ).lower()
        except B5AlpacaNormalizationError:
            unvalued = True
            continue

        if side == "sell":
            conflicting_sell = True
            continue
        if side != "buy":
            unvalued = True
            continue

        order_type = str(order.get("type") or order.get("order_type") or "").lower()
        if order_type != "limit":
            unvalued = True
            continue

        try:
            qty = _provider_decimal(
                order.get("qty"), field=f"open_order[{symbol}].qty"
            )
            limit_price = _provider_decimal(
                order.get("limit_price"),
                field=f"open_order[{symbol}].limit_price",
            )
        except B5AlpacaNormalizationError:
            unvalued = True
            continue
        if qty <= 0 or qty != qty.to_integral_value() or limit_price <= 0:
            unvalued = True
            continue

        premium = qty * limit_price * Decimal(policy.standard_contract_size)
        aggregate += premium
        if root == underlying_symbol:
            same += premium

    return _ExposureState(
        same_underlying_premium_at_risk=same,
        aggregate_long_option_premium_at_risk=aggregate,
        conflicting_open_option_sell_order=conflicting_sell,
        unvalued_open_option_exposure=unvalued,
    )


def _chain_records(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise B5AlpacaNormalizationError("option chain payload must be an object")
    for key in ("chain", "snapshots", "option_chain"):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            return nested
    return payload


def _normalize_option_contracts(
    contracts_payload: Any,
    chain_payload: Any,
    *,
    underlying_symbol: str,
    latest_completed_session_date: date,
    contracts_receipt_id: str,
    chain_receipt_id: str,
) -> tuple[OptionContractCandidate, ...]:
    contracts = _records(
        contracts_payload,
        container_keys=("option_contracts", "contracts"),
        field="option_contracts",
    )
    chain = _chain_records(chain_payload)
    source_receipt_id = f"{contracts_receipt_id}::{chain_receipt_id}"
    normalized: list[OptionContractCandidate] = []

    for raw in contracts:
        symbol = _provider_string(
            raw.get("symbol"), field="option_contract.symbol", uppercase=True
        )
        contract_underlying = _provider_string(
            raw.get("underlying_symbol"),
            field=f"option_contract[{symbol}].underlying_symbol",
            uppercase=True,
        )
        if contract_underlying != underlying_symbol:
            continue
        root, right = _occ_identity(symbol)
        if root != underlying_symbol:
            raise B5AlpacaNormalizationError(
                f"option contract underlying/OCC root mismatch for {symbol}"
            )

        contract_type = _provider_string(
            raw.get("type"),
            field=f"option_contract[{symbol}].type",
            uppercase=True,
        )
        if right == "C" and contract_type != "CALL":
            raise B5AlpacaNormalizationError(
                f"option contract type/OCC right mismatch for {symbol}"
            )
        if right == "P" and contract_type != "PUT":
            raise B5AlpacaNormalizationError(
                f"option contract type/OCC right mismatch for {symbol}"
            )

        snapshot = chain.get(symbol)
        if not isinstance(snapshot, Mapping):
            continue
        quote = snapshot.get("latest_quote")
        greeks = snapshot.get("greeks")
        if not isinstance(quote, Mapping) or not isinstance(greeks, Mapping):
            bid = ask = delta = None
            quote_timestamp = None
        else:
            try:
                bid = _provider_decimal(
                    quote.get("bid_price"), field=f"option_chain[{symbol}].bid_price"
                )
                ask = _provider_decimal(
                    quote.get("ask_price"), field=f"option_chain[{symbol}].ask_price"
                )
                delta = _provider_decimal(
                    greeks.get("delta"), field=f"option_chain[{symbol}].delta"
                )
                quote_timestamp = _provider_datetime(
                    quote.get("timestamp"),
                    field=f"option_chain[{symbol}].quote_timestamp",
                )
            except B5AlpacaNormalizationError:
                bid = ask = delta = None
                quote_timestamp = None

        oi_value = raw.get("open_interest")
        oi_date_value = raw.get("open_interest_date")
        try:
            open_interest = (
                None
                if oi_value is None
                else _provider_int(
                    oi_value, field=f"option_contract[{symbol}].open_interest"
                )
            )
            open_interest_date = (
                None
                if oi_date_value is None
                else _provider_date(
                    oi_date_value,
                    field=f"option_contract[{symbol}].open_interest_date",
                )
            )
        except B5AlpacaNormalizationError:
            open_interest = None
            open_interest_date = None

        normalized.append(
            OptionContractCandidate(
                symbol=symbol,
                underlying_symbol=contract_underlying,
                contract_type=contract_type,
                expiration_date=_provider_date(
                    raw.get("expiration_date"),
                    field=f"option_contract[{symbol}].expiration_date",
                ),
                strike=_provider_decimal(
                    raw.get("strike_price"),
                    field=f"option_contract[{symbol}].strike_price",
                ),
                exercise_style=_provider_string(
                    raw.get("style"),
                    field=f"option_contract[{symbol}].style",
                    uppercase=True,
                ),
                contract_size=_provider_int(
                    raw.get("size"),
                    field=f"option_contract[{symbol}].size",
                ),
                delta=delta,
                bid=bid,
                ask=ask,
                open_interest=open_interest,
                open_interest_current=(
                    open_interest_date is not None
                    and open_interest_date >= latest_completed_session_date
                ),
                quote_timestamp=quote_timestamp,
                status=_provider_string(
                    raw.get("status"),
                    field=f"option_contract[{symbol}].status",
                    uppercase=True,
                ),
                tradable=_provider_bool(
                    raw.get("tradable"),
                    field=f"option_contract[{symbol}].tradable",
                ),
                source_receipt_id=source_receipt_id,
            )
        )
    return tuple(normalized)


def normalize_b5_alpaca_inputs(
    *,
    account_payload: Mapping[str, Any],
    positions_payload: Any,
    open_orders_payload: Any,
    option_contracts_payload: Any,
    option_chain_payload: Any,
    underlying_symbol: str,
    observed_at: datetime,
    latest_completed_session_date: date,
    policy: CompetitionOptionsPolicy,
    account_receipt_id: str,
    positions_receipt_id: str,
    open_orders_receipt_id: str,
    option_contracts_receipt_id: str,
    option_chain_receipt_id: str,
) -> B5NormalizedAlpacaInputs:
    """Normalize the five read-only Alpaca B5 surfaces into deterministic inputs."""

    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise B5AlpacaNormalizationError("observed_at must be timezone-aware")
    symbol = _provider_string(
        underlying_symbol, field="underlying_symbol", uppercase=True
    )
    receipts = (
        account_receipt_id,
        positions_receipt_id,
        open_orders_receipt_id,
        option_contracts_receipt_id,
        option_chain_receipt_id,
    )
    if any(not isinstance(value, str) or not value.strip() for value in receipts):
        raise B5AlpacaNormalizationError("all B5 provider receipt IDs are required")

    account = _normalize_account(account_payload, policy=policy)
    positions = _normalize_position_exposure(
        positions_payload,
        underlying_symbol=symbol,
    )
    orders = _normalize_open_order_exposure(
        open_orders_payload,
        underlying_symbol=symbol,
        policy=policy,
    )

    snapshot = B5ReadOnlyRiskSnapshot(
        observed_at=observed_at.astimezone(timezone.utc),
        paper_account_id=account.paper_account_id,
        equity=account.equity,
        same_underlying_committed_premium_at_risk=(
            positions.same_underlying_premium_at_risk
            + orders.same_underlying_premium_at_risk
        ),
        aggregate_committed_long_option_premium_at_risk=(
            positions.aggregate_long_option_premium_at_risk
            + orders.aggregate_long_option_premium_at_risk
        ),
        remaining_after_equity_safety_reserve=(
            account.remaining_after_equity_safety_reserve
        ),
        options_buying_power_after_open_orders=account.broker_capacity,
        account_trading_eligible=account.trading_eligible,
        unsupported_short_option_position=(
            positions.unsupported_short_option_position
        ),
        conflicting_open_option_sell_order=(
            orders.conflicting_open_option_sell_order
        ),
        unvalued_open_option_exposure=(
            positions.unvalued_open_option_exposure
            or orders.unvalued_open_option_exposure
        ),
        account_receipt_id=account_receipt_id,
        positions_receipt_id=positions_receipt_id,
        open_orders_receipt_id=open_orders_receipt_id,
        option_contracts_receipt_id=option_contracts_receipt_id,
        option_chain_receipt_id=option_chain_receipt_id,
    )
    option_contracts = _normalize_option_contracts(
        option_contracts_payload,
        option_chain_payload,
        underlying_symbol=symbol,
        latest_completed_session_date=latest_completed_session_date,
        contracts_receipt_id=option_contracts_receipt_id,
        chain_receipt_id=option_chain_receipt_id,
    )
    return B5NormalizedAlpacaInputs(
        snapshot=snapshot,
        option_contracts=option_contracts,
    )
