from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable
from urllib.parse import urlencode

from .options_competition_v1 import CompetitionOptionsPolicy


PAPER_TRADING_BASE_URL = "https://paper-api.alpaca.markets"
MARKET_DATA_BASE_URL = "https://data.alpaca.markets"


@dataclass(frozen=True)
class ReadOnlyAlpacaRequest:
    request_id: str
    method: str
    base_url: str
    path: str
    query: tuple[tuple[str, str], ...] = ()

    def url(self) -> str:
        suffix = urlencode(self.query)
        return f"{self.base_url}{self.path}" + (f"?{suffix}" if suffix else "")


def _request(
    request_id: str,
    *,
    base_url: str,
    path: str,
    query: Iterable[tuple[str, str]] = (),
) -> ReadOnlyAlpacaRequest:
    return ReadOnlyAlpacaRequest(
        request_id=request_id,
        method="GET",
        base_url=base_url,
        path=path,
        query=tuple(query),
    )


def build_b5_read_only_request_plan(
    *,
    underlying_symbol: str,
    as_of_date: date,
    policy: CompetitionOptionsPolicy,
) -> tuple[ReadOnlyAlpacaRequest, ...]:
    """Build the exact read-only Alpaca surfaces required by B5.

    The plan deliberately contains no POST/PUT/PATCH/DELETE request and cannot
    place, replace, cancel, exercise, or otherwise mutate an order or position.
    Pagination is handled by the caller by repeating the same GET endpoint with
    the provider-issued page token.
    """

    symbol = underlying_symbol.strip().upper()
    if not symbol or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for ch in symbol):
        raise ValueError("invalid underlying_symbol")

    expiration_gte = as_of_date + timedelta(days=policy.dte_min)
    expiration_lte = as_of_date + timedelta(days=policy.dte_max)

    return (
        _request(
            "B5_ACCOUNT",
            base_url=PAPER_TRADING_BASE_URL,
            path="/v2/account",
        ),
        _request(
            "B5_POSITIONS",
            base_url=PAPER_TRADING_BASE_URL,
            path="/v2/positions",
        ),
        _request(
            "B5_OPEN_ORDERS",
            base_url=PAPER_TRADING_BASE_URL,
            path="/v2/orders",
            query=(("status", "open"),),
        ),
        _request(
            "B5_OPTION_CONTRACTS",
            base_url=PAPER_TRADING_BASE_URL,
            path="/v2/options/contracts",
            query=(
                ("underlying_symbols", symbol),
                ("status", "active"),
                ("type", "call"),
                ("expiration_date_gte", expiration_gte.isoformat()),
                ("expiration_date_lte", expiration_lte.isoformat()),
                ("limit", "10000"),
            ),
        ),
        _request(
            "B5_OPTION_CHAIN",
            base_url=MARKET_DATA_BASE_URL,
            path=f"/v1beta1/options/snapshots/{symbol}",
            query=(
                ("type", "call"),
                ("expiration_date_gte", expiration_gte.isoformat()),
                ("expiration_date_lte", expiration_lte.isoformat()),
                ("limit", "1000"),
            ),
        ),
    )


def assert_read_only_request_plan(
    requests: tuple[ReadOnlyAlpacaRequest, ...],
) -> None:
    required = {
        "B5_ACCOUNT",
        "B5_POSITIONS",
        "B5_OPEN_ORDERS",
        "B5_OPTION_CONTRACTS",
        "B5_OPTION_CHAIN",
    }
    observed = {request.request_id for request in requests}
    if observed != required:
        raise ValueError("B5 read-only request surface drift")
    if any(request.method != "GET" for request in requests):
        raise ValueError("B5 request plan contains a write-capable method")
    if any(
        request.base_url not in {PAPER_TRADING_BASE_URL, MARKET_DATA_BASE_URL}
        for request in requests
    ):
        raise ValueError("B5 request plan contains an unexpected Alpaca host")
