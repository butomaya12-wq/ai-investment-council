"""Injectable, GET-only Alpaca option-read preflight boundary.

There is deliberately no network client here. A caller supplies a transport
with an explicit fixed surface, so fake transports can prove all wiring before
one future, separately-authorized provider read.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
import re
from typing import Any, Mapping, Protocol, Sequence

from aic.b5.production_readonly_v1 import (
    B5ProductionBlocked,
    NormalizedAccount,
    NormalizedOptionMarketInput,
    normalize_market_input,
)


class ReadSurface(str, Enum):
    PAPER_TRADING_API = "PAPER_TRADING_API"
    MARKET_DATA_API = "MARKET_DATA_API"


PAPER_TRADING_API_BASE_URL = "https://paper-api.alpaca.markets"
MARKET_DATA_API_BASE_URL = "https://data.alpaca.markets"
PAPER_ACCOUNT_PATH = "/v2/account"
PAPER_POSITIONS_PATH = "/v2/positions"
NVDA_OPTION_CONTRACTS_PATH = "/v2/options/contracts"
NVDA_OPTION_SNAPSHOTS_PATH = "/v1beta1/options/snapshots/NVDA"
_OPTION_SYMBOL_RE = re.compile(r"([A-Z]{1,6})\d{6}[CP]\d{8}")
_RAW_INTEGER_RE = re.compile(r"(?:0|[1-9][0-9]*)")


class AlpacaOptionsReadOnlyTransport(Protocol):
    """The sole transport operation is a GET on one fixed read surface."""

    def get(self, *, surface: ReadSurface, path: str, query: Mapping[str, str]) -> object:
        ...


@dataclass(frozen=True)
class PaginationReport:
    pages_read: int
    contracts_seen: int
    pagination_complete: bool


@dataclass(frozen=True)
class ContractPages:
    contracts: tuple[Mapping[str, Any], ...]
    report: PaginationReport


@dataclass(frozen=True)
class SnapshotPages:
    snapshots: Mapping[str, Mapping[str, Any]]
    report: PaginationReport


@dataclass(frozen=True)
class PositionRisk:
    current_same_underlying_premium_risk: Decimal
    current_aggregate_option_premium_risk: Decimal


def base_url_for(surface: ReadSurface) -> str:
    if surface is ReadSurface.PAPER_TRADING_API:
        return PAPER_TRADING_API_BASE_URL
    if surface is ReadSurface.MARKET_DATA_API:
        return MARKET_DATA_API_BASE_URL
    raise B5ProductionBlocked("unknown Alpaca read surface")


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise B5ProductionBlocked(f"{field} must be an object")
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise B5ProductionBlocked(f"{field} must be a non-empty trimmed string")
    return value


def _decimal(value: object, field: str, *, non_negative: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise B5ProductionBlocked(f"{field} must be a finite decimal")
    try:
        result = Decimal(str(value))
    except Exception as exc:  # pragma: no cover - Decimal exposes multiple exception types
        raise B5ProductionBlocked(f"{field} must be a finite decimal") from exc
    if not result.is_finite() or (non_negative and result < 0):
        raise B5ProductionBlocked(f"{field} must be a non-negative finite decimal")
    return result


def normalize_alpaca_integer(value: object, field: str) -> int:
    """Normalize an exact non-negative integer from a documented Alpaca raw field."""
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and _RAW_INTEGER_RE.fullmatch(value) is not None:
        return int(value)
    raise B5ProductionBlocked(f"{field} must be a canonical non-negative integer")


def _iso_date(value: object, field: str) -> date:
    try:
        return date.fromisoformat(_string(value, field))
    except ValueError as exc:
        raise B5ProductionBlocked(f"{field} must be an ISO date") from exc


def _rfc3339(value: object, field: str) -> str:
    text = _string(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise B5ProductionBlocked(f"{field} must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise B5ProductionBlocked(f"{field} must be timezone-aware")
    return text


def _page_token(value: object) -> str | None:
    if value is None:
        return None
    return _string(value, "next_page_token")


def parse_paper_account(payload: object) -> NormalizedAccount:
    """Map explicit PAPER account fields without inventing any balance."""
    account = _mapping(payload, "PAPER account")
    broker_capacity = account.get("options_buying_power")
    if broker_capacity is None:
        broker_capacity = account.get("buying_power")
    if broker_capacity is None:
        raise B5ProductionBlocked("PAPER account buying power is missing")
    equity = _decimal(account.get("equity"), "PAPER account equity", non_negative=True)
    if equity <= 0:
        raise B5ProductionBlocked("PAPER account equity must be positive")
    return NormalizedAccount(
        account_equity=equity,
        cash_available=_decimal(account.get("cash"), "PAPER account cash", non_negative=True),
        current_same_underlying_premium_risk=Decimal("0"),
        current_aggregate_option_premium_risk=Decimal("0"),
        broker_capacity=_decimal(broker_capacity, "PAPER account buying power", non_negative=True),
    )


def derive_long_option_position_risk(payload: object, *, underlying: str = "NVDA") -> PositionRisk:
    """Derive frozen long-premium risk from every open option position."""
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
        raise B5ProductionBlocked("PAPER positions must be an array")
    same = Decimal("0")
    aggregate = Decimal("0")
    for index, raw in enumerate(payload):
        position = _mapping(raw, f"PAPER positions[{index}]")
        if position.get("asset_class") != "us_option":
            continue
        symbol = _string(position.get("symbol"), f"PAPER positions[{index}].symbol")
        matched = _OPTION_SYMBOL_RE.fullmatch(symbol)
        if matched is None:
            raise B5ProductionBlocked("option position symbol is malformed")
        if position.get("side") != "long":
            raise B5ProductionBlocked("non-long option position is outside frozen premium-risk policy")
        quantity = _decimal(position.get("qty"), f"PAPER positions[{index}].qty", non_negative=True)
        if quantity <= 0:
            raise B5ProductionBlocked("open long option quantity must be positive")
        cost_basis = _decimal(position.get("cost_basis"), f"PAPER positions[{index}].cost_basis", non_negative=True)
        aggregate += cost_basis
        if matched.group(1) == underlying:
            same += cost_basis
    return PositionRisk(same, aggregate)


def _contract_query(as_of_date: date, page_token: str | None = None) -> dict[str, str]:
    query = {
        "underlying_symbols": "NVDA",
        "type": "call",
        "status": "active",
        "expiration_date_gte": (as_of_date + timedelta(days=21)).isoformat(),
        "expiration_date_lte": (as_of_date + timedelta(days=49)).isoformat(),
        "limit": "1000",
    }
    if page_token is not None:
        query["page_token"] = page_token
    return query


def _snapshot_query(as_of_date: date, page_token: str | None = None) -> dict[str, str]:
    """Bound snapshot reads to the same frozen selector expiry universe."""
    query = {
        "limit": "1000",
        "type": "call",
        "expiration_date_gte": (as_of_date + timedelta(days=21)).isoformat(),
        "expiration_date_lte": (as_of_date + timedelta(days=49)).isoformat(),
    }
    if page_token is not None:
        query["page_token"] = page_token
    return query


class AlpacaOptionsReadOnlyAdapter:
    """Fixed-surface, GET-only requests plus pure B5 market normalization."""

    def __init__(self, transport: AlpacaOptionsReadOnlyTransport) -> None:
        self._transport = transport

    def read_paper_account(self) -> object:
        return self._transport.get(surface=ReadSurface.PAPER_TRADING_API, path=PAPER_ACCOUNT_PATH, query={})

    def read_paper_positions(self) -> object:
        return self._transport.get(surface=ReadSurface.PAPER_TRADING_API, path=PAPER_POSITIONS_PATH, query={})

    def read_nvda_option_contract_metadata(self, *, as_of_date: date, max_pages: int = 10) -> ContractPages:
        if max_pages < 1:
            raise B5ProductionBlocked("contract max_pages must be positive")
        items: list[Mapping[str, Any]] = []
        page_token: str | None = None
        for page_index in range(max_pages):
            payload = _mapping(
                self._transport.get(
                    surface=ReadSurface.PAPER_TRADING_API,
                    path=NVDA_OPTION_CONTRACTS_PATH,
                    query=_contract_query(as_of_date, page_token),
                ),
                "option contracts response",
            )
            page_items = payload.get("option_contracts")
            if not isinstance(page_items, list) or not all(isinstance(item, Mapping) for item in page_items):
                raise B5ProductionBlocked("option_contracts must be an array of objects")
            items.extend(page_items)
            page_token = _page_token(payload.get("next_page_token"))
            if page_token is None:
                return ContractPages(tuple(items), PaginationReport(page_index + 1, len(items), True))
        raise B5ProductionBlocked("BLOCK_INCOMPLETE_OPTION_MARKET")

    def read_nvda_option_snapshots(
        self, *, as_of_date: date, max_pages: int = 10, max_contracts: int = 10_000
    ) -> SnapshotPages:
        if max_pages < 1 or max_contracts < 1:
            raise B5ProductionBlocked("snapshot pagination bounds must be positive")
        snapshots: dict[str, Mapping[str, Any]] = {}
        page_token: str | None = None
        for page_index in range(max_pages):
            payload = _mapping(
                self._transport.get(
                    surface=ReadSurface.MARKET_DATA_API,
                    path=NVDA_OPTION_SNAPSHOTS_PATH,
                    query=_snapshot_query(as_of_date, page_token),
                ),
                "option snapshots response",
            )
            page_snapshots = payload.get("snapshots")
            if not isinstance(page_snapshots, Mapping) or not all(isinstance(value, Mapping) for value in page_snapshots.values()):
                raise B5ProductionBlocked("snapshots must be an object of option snapshots")
            if len(snapshots) + len(page_snapshots) > max_contracts:
                raise B5ProductionBlocked("BLOCK_INCOMPLETE_OPTION_MARKET")
            for symbol, snapshot in page_snapshots.items():
                snapshots[_string(symbol, "snapshot option symbol")] = snapshot
            page_token = _page_token(payload.get("next_page_token"))
            if page_token is None:
                return SnapshotPages(snapshots, PaginationReport(page_index + 1, len(snapshots), True))
        raise B5ProductionBlocked("BLOCK_INCOMPLETE_OPTION_MARKET")

    @staticmethod
    def normalize_market_read(
        *,
        snapshot_timestamp: datetime,
        as_of_date: date,
        expected_open_interest_date: date,
        account_payload: object,
        positions_payload: object,
        contract_pages: ContractPages,
        snapshot_pages: SnapshotPages,
    ) -> NormalizedOptionMarketInput:
        """Strictly join complete contract/quote/greek records for frozen B5 selection."""
        if not contract_pages.report.pagination_complete or not snapshot_pages.report.pagination_complete:
            raise B5ProductionBlocked("BLOCK_INCOMPLETE_OPTION_MARKET")
        account = parse_paper_account(account_payload)
        risk = derive_long_option_position_risk(positions_payload)
        account = replace(
            account,
            current_same_underlying_premium_risk=risk.current_same_underlying_premium_risk,
            current_aggregate_option_premium_risk=risk.current_aggregate_option_premium_risk,
        )
        complete_contracts: list[dict[str, Any]] = []
        for raw in contract_pages.contracts:
            try:
                symbol = _string(raw.get("symbol"), "contract symbol")
                if _OPTION_SYMBOL_RE.fullmatch(symbol) is None or not symbol.startswith("NVDA"):
                    continue
                expiration = _iso_date(raw.get("expiration_date"), "contract expiration_date")
                dte = (expiration - as_of_date).days
                if not 21 <= dte <= 49:
                    continue
                if raw.get("underlying_symbol") != "NVDA" or raw.get("type") != "call":
                    continue
                if raw.get("status") != "active" or raw.get("tradable") is not True:
                    continue
                snapshot = snapshot_pages.snapshots.get(symbol)
                if snapshot is None:
                    continue
                quote = _mapping(snapshot.get("latestQuote"), "snapshot latestQuote")
                greeks = _mapping(snapshot.get("greeks"), "snapshot greeks")
                oi_date = _iso_date(raw.get("open_interest_date"), "contract open_interest_date")
                if oi_date != expected_open_interest_date:
                    continue
                if _decimal(raw.get("strike_price"), "contract strike_price") <= 0:
                    raise B5ProductionBlocked("contract strike_price must be positive")
                size = normalize_alpaca_integer(raw.get("size"), "contract size")
                if size <= 0:
                    raise B5ProductionBlocked("contract size must be a positive integer")
                open_interest = normalize_alpaca_integer(raw.get("open_interest"), "contract open_interest")
                if _decimal(quote.get("bp"), "snapshot latestQuote.bp") <= 0:
                    raise B5ProductionBlocked("snapshot latestQuote.bp must be positive")
                if _decimal(quote.get("ap"), "snapshot latestQuote.ap") <= 0:
                    raise B5ProductionBlocked("snapshot latestQuote.ap must be positive")
                _decimal(greeks.get("delta"), "snapshot greeks.delta")
                complete_contracts.append(
                    {
                        "option_symbol": symbol,
                        "contract_type": "CALL",
                        "opening_direction": "BUY_TO_OPEN",
                        "expiration": expiration.isoformat(),
                        "strike": raw.get("strike_price"),
                        "multiplier": size,
                        "bid": quote.get("bp"),
                        "ask": quote.get("ap"),
                        "delta": greeks.get("delta"),
                        "open_interest": open_interest,
                        "active": True,
                        "tradable": True,
                        "greeks_present": True,
                        "quote_timestamp": _rfc3339(quote.get("t"), "snapshot latestQuote.t"),
                        "open_interest_as_of_date": oi_date.isoformat(),
                        # This frozen selector DTO boolean means provider OI freshness
                        # was explicitly verified against expected_open_interest_date;
                        # it does not claim OI is from the latest equity session.
                        "open_interest_current_for_latest_completed_session": True,
                    }
                )
            except B5ProductionBlocked:
                # An incomplete individual contract cannot invent data or poison another.
                continue
        if not complete_contracts:
            raise B5ProductionBlocked("BLOCK_INCOMPLETE_OPTION_MARKET")
        # The frozen selector correctly treats zero OI as ineligible. Its older
        # generic DTO requires a positive integer, so preserve known zero OI
        # after validating every other field through that DTO.
        normalized_input_contracts = [
            {**item, "open_interest": 1 if item["open_interest"] == 0 else item["open_interest"]}
            for item in complete_contracts
        ]
        market = normalize_market_input(
            {
                "snapshot_timestamp": snapshot_timestamp.isoformat().replace("+00:00", "Z"),
                "as_of_date": as_of_date.isoformat(),
                "underlying_symbol": "NVDA",
                "account": {
                    "account_equity": account.account_equity,
                    "cash_available": account.cash_available,
                    "current_same_underlying_premium_risk": account.current_same_underlying_premium_risk,
                    "current_aggregate_option_premium_risk": account.current_aggregate_option_premium_risk,
                    "broker_capacity": account.broker_capacity,
                },
                "option_contracts": normalized_input_contracts,
            }
        )
        if any(item["open_interest"] == 0 for item in complete_contracts):
            market = replace(
                market,
                contracts=tuple(
                    replace(contract, selector_contract=replace(contract.selector_contract, open_interest=int(raw["open_interest"])))
                    for contract, raw in zip(market.contracts, complete_contracts, strict=True)
                ),
            )
        return market
