from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .alpaca_options_readonly_v1 import (
    ReadOnlyAlpacaRequest,
    assert_read_only_request_plan,
    build_b5_read_only_request_plan,
)
from .b5_competition_run_v1 import (
    B5CompetitionRunResult,
    B5RawAlpacaReadBundle,
    run_b5_from_alpaca_reads,
)
from .options_competition_v1 import (
    CompetitionOptionsPolicy,
    validate_b4_invest_handoff,
)


class B5AlpacaRuntimeError(RuntimeError):
    """Fail-closed error at the B5 read-only Alpaca network boundary."""


@dataclass(frozen=True)
class AlpacaReadOnlyCredentials:
    key_id: str
    secret_key: str

    def headers(self) -> dict[str, str]:
        if not self.key_id or self.key_id != self.key_id.strip():
            raise B5AlpacaRuntimeError("Alpaca key id is missing or malformed")
        if not self.secret_key or self.secret_key != self.secret_key.strip():
            raise B5AlpacaRuntimeError("Alpaca secret key is missing or malformed")
        return {
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Accept": "application/json",
        }


@dataclass(frozen=True)
class B5ProviderReadReceipt:
    receipt_id: str
    request_id: str
    method: str
    initial_url: str
    page_count: int
    response_sha256s: tuple[str, ...]
    received_at: datetime
    status_code: int
    broker_writes: int = 0
    model_calls: int = 0


@dataclass(frozen=True)
class B5AlpacaReadOnlyRuntimeResult:
    raw_reads: B5RawAlpacaReadBundle
    receipts: tuple[B5ProviderReadReceipt, ...]
    http_get_count: int
    broker_writes: int = 0
    model_calls: int = 0
    approval_authority: bool = False
    execution_authority: bool = False


@dataclass(frozen=True)
class B5ReadOnlyProductionRunResult:
    provider_reads: B5AlpacaReadOnlyRuntimeResult
    b5: B5CompetitionRunResult
    broker_writes: int = 0
    model_calls: int = 0
    approval_authority: bool = False
    execution_authority: bool = False


Transport = Callable[[str, Mapping[str, str], float], tuple[int, bytes]]
Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stdlib_get(url: str, headers: Mapping[str, str], timeout_seconds: float) -> tuple[int, bytes]:
    request = Request(url=url, headers=dict(headers), method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310 - hosts are allowlisted before dispatch
            return int(response.status), response.read()
    except HTTPError as exc:
        raise B5AlpacaRuntimeError(f"Alpaca read failed with HTTP {exc.code}") from exc
    except URLError as exc:
        raise B5AlpacaRuntimeError("Alpaca read transport failed") from exc


def _decode_json(body: bytes, *, request_id: str) -> Any:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise B5AlpacaRuntimeError(f"{request_id} returned invalid JSON") from exc
    if not isinstance(value, (dict, list)):
        raise B5AlpacaRuntimeError(f"{request_id} returned an unsupported JSON root")
    return value


def _next_page_token(payload: Any, *, request_id: str) -> str | None:
    if not isinstance(payload, Mapping) or "next_page_token" not in payload:
        return None
    token = payload.get("next_page_token")
    if token in (None, ""):
        return None
    if not isinstance(token, str) or token != token.strip():
        raise B5AlpacaRuntimeError(f"{request_id} returned a malformed next_page_token")
    return token


def _paged_request(request: ReadOnlyAlpacaRequest, page_token: str) -> ReadOnlyAlpacaRequest:
    query = tuple((key, value) for key, value in request.query if key != "page_token")
    return ReadOnlyAlpacaRequest(
        request_id=request.request_id,
        method=request.method,
        base_url=request.base_url,
        path=request.path,
        query=query + (("page_token", page_token),),
    )


def _merge_pages(request_id: str, pages: tuple[Any, ...]) -> Any:
    if len(pages) == 1:
        return pages[0]

    if request_id == "B5_OPTION_CONTRACTS":
        combined: list[Any] = []
        for page in pages:
            if not isinstance(page, Mapping):
                raise B5AlpacaRuntimeError("option contracts page must be an object")
            records = page.get("option_contracts", page.get("contracts"))
            if not isinstance(records, list):
                raise B5AlpacaRuntimeError("option contracts page is missing records")
            combined.extend(records)
        return {"option_contracts": combined}

    if request_id == "B5_OPTION_CHAIN":
        combined_chain: dict[str, Any] = {}
        for page in pages:
            if not isinstance(page, Mapping):
                raise B5AlpacaRuntimeError("option chain page must be an object")
            records = page.get("snapshots", page.get("chain", page.get("option_chain")))
            if not isinstance(records, Mapping):
                raise B5AlpacaRuntimeError("option chain page is missing snapshots")
            for symbol, snapshot in records.items():
                if symbol in combined_chain and combined_chain[symbol] != snapshot:
                    raise B5AlpacaRuntimeError("option chain pagination returned conflicting duplicate symbols")
                combined_chain[symbol] = snapshot
        return {"snapshots": combined_chain}

    raise B5AlpacaRuntimeError(f"unexpected pagination on {request_id}")


def _logical_receipt(
    *,
    request: ReadOnlyAlpacaRequest,
    page_hashes: tuple[str, ...],
    received_at: datetime,
    status_code: int,
) -> B5ProviderReadReceipt:
    identity = json.dumps(
        {
            "request_id": request.request_id,
            "method": request.method,
            "initial_url": request.url(),
            "page_hashes": page_hashes,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    receipt_id = f"B5PRR:{hashlib.sha256(identity).hexdigest()[:24]}"
    return B5ProviderReadReceipt(
        receipt_id=receipt_id,
        request_id=request.request_id,
        method=request.method,
        initial_url=request.url(),
        page_count=len(page_hashes),
        response_sha256s=page_hashes,
        received_at=received_at,
        status_code=status_code,
    )


def execute_b5_alpaca_read_only_runtime(
    *,
    final_decision: Mapping[str, Any],
    underlying_symbol: str,
    as_of_date: date,
    latest_completed_session_date: date,
    policy: CompetitionOptionsPolicy,
    credentials: AlpacaReadOnlyCredentials,
    transport: Transport | None = None,
    clock: Clock = _utc_now,
    timeout_seconds: float = 10.0,
    max_pages_per_request: int = 20,
) -> B5AlpacaReadOnlyRuntimeResult:
    """Capture the exact B5 Alpaca read set with zero write-capable surfaces.

    The B4 INVEST handoff is validated before the first network dispatch. The runtime
    performs only GET requests to the allowlisted PAPER trading and market-data hosts,
    has no automatic retries, and binds every logical response to a SHA-256 receipt.
    """

    validate_b4_invest_handoff(final_decision, expected_mandate_version=policy.version)
    if timeout_seconds <= 0:
        raise B5AlpacaRuntimeError("timeout_seconds must be positive")
    if max_pages_per_request < 1:
        raise B5AlpacaRuntimeError("max_pages_per_request must be positive")

    plan = build_b5_read_only_request_plan(
        underlying_symbol=underlying_symbol,
        as_of_date=as_of_date,
        policy=policy,
    )
    assert_read_only_request_plan(plan)
    headers = credentials.headers()
    dispatch = transport or _stdlib_get

    payloads: dict[str, Any] = {}
    receipts: list[B5ProviderReadReceipt] = []
    http_get_count = 0

    for logical_request in plan:
        current = logical_request
        pages: list[Any] = []
        page_hashes: list[str] = []
        seen_tokens: set[str] = set()
        final_status = 0

        for _page_index in range(max_pages_per_request):
            if current.method != "GET":
                raise B5AlpacaRuntimeError("B5 runtime encountered a non-GET request")
            if current.base_url not in {
                "https://paper-api.alpaca.markets",
                "https://data.alpaca.markets",
            }:
                raise B5AlpacaRuntimeError("B5 runtime encountered a non-allowlisted Alpaca host")

            status_code, body = dispatch(current.url(), headers, timeout_seconds)
            http_get_count += 1
            if status_code < 200 or status_code >= 300:
                raise B5AlpacaRuntimeError(
                    f"{logical_request.request_id} returned HTTP {status_code}"
                )
            payload = _decode_json(body, request_id=logical_request.request_id)
            pages.append(payload)
            page_hashes.append(hashlib.sha256(body).hexdigest())
            final_status = status_code

            token = _next_page_token(payload, request_id=logical_request.request_id)
            if token is None:
                break
            if token in seen_tokens:
                raise B5AlpacaRuntimeError(
                    f"{logical_request.request_id} repeated a pagination token"
                )
            seen_tokens.add(token)
            current = _paged_request(logical_request, token)
        else:
            raise B5AlpacaRuntimeError(
                f"{logical_request.request_id} exceeded bounded pagination"
            )

        received_at = clock()
        if received_at.tzinfo is None or received_at.utcoffset() is None:
            raise B5AlpacaRuntimeError("runtime clock must be timezone-aware")
        received_at = received_at.astimezone(timezone.utc)
        payloads[logical_request.request_id] = _merge_pages(
            logical_request.request_id, tuple(pages)
        )
        receipts.append(
            _logical_receipt(
                request=logical_request,
                page_hashes=tuple(page_hashes),
                received_at=received_at,
                status_code=final_status,
            )
        )

    by_id = {receipt.request_id: receipt for receipt in receipts}
    observed_at = max(receipt.received_at for receipt in receipts)
    raw_reads = B5RawAlpacaReadBundle(
        account_payload=payloads["B5_ACCOUNT"],
        positions_payload=payloads["B5_POSITIONS"],
        open_orders_payload=payloads["B5_OPEN_ORDERS"],
        option_contracts_payload=payloads["B5_OPTION_CONTRACTS"],
        option_chain_payload=payloads["B5_OPTION_CHAIN"],
        observed_at=observed_at,
        latest_completed_session_date=latest_completed_session_date,
        account_receipt_id=by_id["B5_ACCOUNT"].receipt_id,
        positions_receipt_id=by_id["B5_POSITIONS"].receipt_id,
        open_orders_receipt_id=by_id["B5_OPEN_ORDERS"].receipt_id,
        option_contracts_receipt_id=by_id["B5_OPTION_CONTRACTS"].receipt_id,
        option_chain_receipt_id=by_id["B5_OPTION_CHAIN"].receipt_id,
    )
    return B5AlpacaReadOnlyRuntimeResult(
        raw_reads=raw_reads,
        receipts=tuple(receipts),
        http_get_count=http_get_count,
    )


def run_b5_read_only_production_path(
    *,
    final_decision: Mapping[str, Any],
    underlying_symbol: str,
    as_of_date: date,
    latest_completed_session_date: date,
    policy: CompetitionOptionsPolicy,
    credentials: AlpacaReadOnlyCredentials,
    transport: Transport | None = None,
    clock: Clock = _utc_now,
    timeout_seconds: float = 10.0,
    max_pages_per_request: int = 20,
) -> B5ReadOnlyProductionRunResult:
    provider_reads = execute_b5_alpaca_read_only_runtime(
        final_decision=final_decision,
        underlying_symbol=underlying_symbol,
        as_of_date=as_of_date,
        latest_completed_session_date=latest_completed_session_date,
        policy=policy,
        credentials=credentials,
        transport=transport,
        clock=clock,
        timeout_seconds=timeout_seconds,
        max_pages_per_request=max_pages_per_request,
    )
    b5 = run_b5_from_alpaca_reads(
        final_decision=final_decision,
        underlying_symbol=underlying_symbol,
        raw_reads=provider_reads.raw_reads,
        policy=policy,
    )
    return B5ReadOnlyProductionRunResult(provider_reads=provider_reads, b5=b5)
