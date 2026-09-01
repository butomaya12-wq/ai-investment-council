"""Narrow injectable GET-only boundary for future Alpaca option reads.

No network client is included here.  Callers supply a transport, which makes
the production boundary testable with deterministic fake responses.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Protocol

from aic.b5.production_readonly_v1 import NormalizedOptionMarketInput, normalize_market_input


PAPER_ACCOUNT_PATH = "/v2/account"
NVDA_OPTION_CONTRACTS_PATH = "/v2/options/contracts"
NVDA_OPTION_SNAPSHOTS_PATH = "/v1beta1/options/snapshots/NVDA"


class AlpacaOptionsReadOnlyTransport(Protocol):
    """The only permitted transport capability is retrieval."""

    def get(self, *, path: str, query: Mapping[str, str]) -> Mapping[str, Any]:
        ...


class AlpacaOptionsReadOnlyAdapter:
    """Read-only endpoint selection and normalized-input construction."""

    def __init__(self, transport: AlpacaOptionsReadOnlyTransport) -> None:
        self._transport = transport

    def read_paper_account(self) -> Mapping[str, Any]:
        return self._transport.get(path=PAPER_ACCOUNT_PATH, query={})

    def read_nvda_option_contract_metadata(self) -> Mapping[str, Any]:
        return self._transport.get(path=NVDA_OPTION_CONTRACTS_PATH, query={"underlying_symbols": "NVDA"})

    def read_nvda_option_snapshots(self) -> Mapping[str, Any]:
        return self._transport.get(path=NVDA_OPTION_SNAPSHOTS_PATH, query={})

    @staticmethod
    def normalize(
        *,
        snapshot_timestamp: datetime,
        as_of_date: str,
        account: Mapping[str, Any],
        option_contracts: list[Mapping[str, Any]],
    ) -> NormalizedOptionMarketInput:
        """Normalize a caller-assembled, documented read-only snapshot."""
        return normalize_market_input(
            {
                "snapshot_timestamp": snapshot_timestamp.isoformat().replace("+00:00", "Z"),
                "as_of_date": as_of_date,
                "underlying_symbol": "NVDA",
                "account": account,
                "option_contracts": option_contracts,
            }
        )
