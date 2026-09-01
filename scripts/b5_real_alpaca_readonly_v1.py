#!/usr/bin/env python3
"""Safe-by-default bounded PAPER/DATA read-only runner for production B5."""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
import hashlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Sequence, TextIO
from urllib.parse import urlencode, urlsplit

from aic.b5.production_readonly_v1 import (
    B5Candidate,
    B5ProductionBlocked,
    RecoveredB4Decision,
    create_b5_entry,
    select_readonly_b5,
)
from aic.b5.runtime_readonly_v1 import create_entry_at_clean_expected_head
from aic.data.providers.alpaca_options_readonly import AlpacaOptionsReadOnlyAdapter, ReadSurface


CANONICAL_BRANCH = "hackathon/alpaca-2026"
RECOVERED_B4_RELATIVE_PATH = Path(
    ".aic-runtime/b4_post_research_reopen_current_judge_captured_response_recovery_v0_1__442e8d7.json"
)
TIMEOUT_SECONDS = 15
MAX_GET_CALLS = 22
HOST_FOR_SURFACE = {
    ReadSurface.PAPER_TRADING_API: "paper-api.alpaca.markets",
    ReadSurface.MARKET_DATA_API: "data.alpaca.markets",
}
PATH_FOR_SURFACE = {
    "/v2/account": ReadSurface.PAPER_TRADING_API,
    "/v2/positions": ReadSurface.PAPER_TRADING_API,
    "/v2/options/contracts": ReadSurface.PAPER_TRADING_API,
    "/v1beta1/options/snapshots/NVDA": ReadSurface.MARKET_DATA_API,
}
PATH_CEILINGS = {
    "/v2/account": 1,
    "/v2/positions": 1,
    "/v2/options/contracts": 10,
    "/v1beta1/options/snapshots/NVDA": 10,
}


class RunnerBlocked(RuntimeError):
    """Fail-closed runner result with no sensitive detail."""


class CaptureWriter:
    """Exclusive, owner-only JSONL capture with a durable record per response."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self._stream = os.fdopen(descriptor, "w", encoding="utf-8")
        os.chmod(path, 0o600)

    def write(self, record: Mapping[str, object]) -> None:
        self._stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        self._stream.flush()
        os.fsync(self._stream.fileno())

    def close(self) -> str:
        if not self._stream.closed:
            self._stream.flush()
            os.fsync(self._stream.fileno())
            self._stream.close()
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


class MemoryCapture:
    """Self-test capture substitute; it never touches the filesystem."""

    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def write(self, record: Mapping[str, object]) -> None:
        self.records.append(dict(record))


def _safe_query_metadata(query: Mapping[str, str]) -> dict[str, object]:
    visible = {key: value for key, value in query.items() if key != "page_token"}
    token = query.get("page_token")
    return {
        "values": dict(sorted(visible.items())),
        "page_token_present": token is not None,
        "page_token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest() if token is not None else None,
    }


def _page_metadata(path: str, payload: object) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        return {"page_item_count": "NOT_APPLICABLE", "next_page_token_present": "NOT_APPLICABLE"}
    collection_name = "option_contracts" if path == "/v2/options/contracts" else "snapshots"
    collection = payload.get(collection_name)
    if isinstance(collection, (list, Mapping)):
        item_count: int | str = len(collection)
    else:
        item_count = "NOT_APPLICABLE"
    return {
        "page_item_count": item_count,
        "next_page_token_present": payload.get("next_page_token") is not None,
    }


class BoundedGetOnlyTransport:
    """The runner's only concrete network boundary: bounded stdlib HTTPS GET."""

    def __init__(
        self,
        *,
        key_id: str,
        secret_key: str,
        capture: CaptureWriter | MemoryCapture,
        emit: Callable[[str], None],
        connection_factory: Callable[..., Any] = http.client.HTTPSConnection,
    ) -> None:
        self._key_id = key_id
        self._secret_key = secret_key
        self._capture = capture
        self._emit = emit
        self._connection_factory = connection_factory
        self.total_gets = 0
        self.path_gets = {path: 0 for path in PATH_CEILINGS}

    def get(self, *, surface: ReadSurface, path: str, query: Mapping[str, str]) -> object:
        expected_surface = PATH_FOR_SURFACE.get(path)
        if expected_surface is None or surface is not expected_surface:
            raise RunnerBlocked("BLOCK_READ_ROUTE")
        if not isinstance(query, Mapping) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in query.items()
        ):
            raise RunnerBlocked("BLOCK_QUERY")
        if self.total_gets >= MAX_GET_CALLS or self.path_gets[path] >= PATH_CEILINGS[path]:
            raise RunnerBlocked("BLOCK_GET_CEILING")

        host = HOST_FOR_SURFACE[surface]
        target_query = urlencode(sorted(query.items()), safe="")
        target = path if not target_query else f"{path}?{target_query}"
        self.total_gets += 1
        self.path_gets[path] += 1
        call_number = self.total_gets
        started = time.monotonic()
        connection = None
        try:
            connection = self._connection_factory(host, timeout=TIMEOUT_SECONDS)
            connection.request(
                "GET",
                target,
                body=None,
                headers={
                    "Accept": "application/json",
                    "APCA-API-KEY-ID": self._key_id,
                    "APCA-API-SECRET-KEY": self._secret_key,
                },
            )
            response = connection.getresponse()
            raw_body = response.read()
            elapsed_ms = int((time.monotonic() - started) * 1000)
            response_record = {
                "kind": "response",
                "call_number": call_number,
                "host": host,
                "method": "GET",
                "path": path,
                "query": _safe_query_metadata(query),
                "http_status": response.status,
                "elapsed_ms": elapsed_ms,
                "response_bytes": len(raw_body),
                "response_sha256": hashlib.sha256(raw_body).hexdigest(),
                "raw_response_body_base64": base64.b64encode(raw_body).decode("ascii"),
            }
            self._capture.write(response_record)
            if response.status != 200:
                self._emit_call(response_record, "NOT_APPLICABLE", "NOT_APPLICABLE")
                raise RunnerBlocked("BLOCK_HTTP_STATUS")
            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._emit_call(response_record, "NOT_APPLICABLE", "NOT_APPLICABLE")
                raise RunnerBlocked("BLOCK_RESPONSE_JSON") from exc
            metadata = _page_metadata(path, payload)
            self._capture.write({"kind": "page_metadata", "call_number": call_number, **metadata})
            self._emit_call(response_record, metadata["page_item_count"], metadata["next_page_token_present"])
            return payload
        except RunnerBlocked:
            raise
        except (TimeoutError, OSError) as exc:
            self._emit(f"TRANSPORT_BLOCK={type(exc).__name__}")
            raise RunnerBlocked("BLOCK_TRANSPORT") from exc
        except Exception as exc:
            self._emit(f"TRANSPORT_BLOCK={type(exc).__name__}")
            raise RunnerBlocked("BLOCK_TRANSPORT") from exc
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    def _emit_call(self, record: Mapping[str, object], item_count: object, token_present: object) -> None:
        self._emit(f"GET_CALL={record['call_number']}")
        self._emit(f"HOST={record['host']}")
        self._emit(f"PATH={record['path']}")
        self._emit(f"ELAPSED_MS={record['elapsed_ms']}")
        self._emit(f"HTTP_STATUS={record['http_status']}")
        self._emit(f"RESPONSE_BYTES={record['response_bytes']}")
        self._emit(f"RESPONSE_SHA256={record['response_sha256']}")
        self._emit(f"PAGE_ITEM_COUNT={item_count}")
        self._emit(f"NEXT_PAGE_TOKEN_PRESENT={str(token_present).lower()}")


@dataclass(frozen=True)
class ExecuteInputs:
    expected_head: str
    as_of_date: date
    expected_open_interest_date: date


def _parse_sha(value: str) -> str:
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise RunnerBlocked("BLOCK_EXPECTED_HEAD")
    return value


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise RunnerBlocked(f"BLOCK_{field}") from exc


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(["git", *arguments], cwd=repository, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RunnerBlocked("BLOCK_GIT")
    return completed.stdout.strip()


def _tracked_clean(repository: Path) -> bool:
    return not _git(repository, "status", "--porcelain=v1", "--untracked-files=no")


def _preflight(repository: Path, inputs: ExecuteInputs):
    if _git(repository, "branch", "--show-current") != CANONICAL_BRANCH:
        raise RunnerBlocked("BLOCK_BRANCH")
    if _git(repository, "rev-parse", "HEAD") != inputs.expected_head:
        raise RunnerBlocked("BLOCK_HEAD")
    if not _tracked_clean(repository):
        raise RunnerBlocked("BLOCK_TRACKED_DIRTY")
    artifact = repository / RECOVERED_B4_RELATIVE_PATH
    if not artifact.is_file():
        raise RunnerBlocked("BLOCK_B4_ARTIFACT")
    return create_entry_at_clean_expected_head(
        repository=repository, recovered_b4_artifact=artifact, expected_commit_sha=inputs.expected_head
    )


def _capture_path(repository: Path, expected_head: str, now: datetime) -> Path:
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return repository / ".aic-runtime" / f"b5_real_alpaca_readonly_v1__{expected_head[:7]}__{stamp}.jsonl"


def _synthetic_account() -> dict[str, str]:
    return {"equity": "100000", "cash": "80000", "options_buying_power": "50000"}


def _synthetic_contract(symbol: str, strike: str) -> dict[str, object]:
    return {
        "symbol": symbol,
        "status": "active",
        "tradable": True,
        "expiration_date": "2026-10-06",
        "underlying_symbol": "NVDA",
        "type": "call",
        "strike_price": strike,
        "size": "100",
        "open_interest": "100",
        "open_interest_date": "2026-08-28",
    }


def _synthetic_snapshot() -> dict[str, object]:
    return {"latestQuote": {"bp": "2.40", "ap": "2.50", "t": "2026-09-01T15:00:00Z"}, "greeks": {"delta": "0.50"}}


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self.status = 200
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body


class _FakeConnection:
    def __init__(self, host: str, payload: object, calls: list[dict[str, object]]) -> None:
        self.host = host
        self._payload = payload
        self._calls = calls

    def request(self, method: str, target: str, body: object, headers: Mapping[str, str]) -> None:
        self._calls.append({"host": self.host, "method": method, "target": target, "body": body, "headers": dict(headers)})

    def getresponse(self) -> _FakeResponse:
        return _FakeResponse(self._payload)

    def close(self) -> None:
        return None


def run_self_test(emit: Callable[[str], None]) -> None:
    calls: list[dict[str, object]] = []
    payloads: list[object] = [
        _synthetic_account(),
        [],
        {"option_contracts": [_synthetic_contract("NVDA261006C00200000", "200")], "next_page_token": "contract-page-two"},
        {"option_contracts": [_synthetic_contract("NVDA261006C00210000", "210")], "next_page_token": None},
        {"snapshots": {"NVDA261006C00200000": _synthetic_snapshot()}, "next_page_token": "snapshot-page-two"},
        {"snapshots": {"NVDA261006C00210000": _synthetic_snapshot()}, "next_page_token": None},
    ]

    def factory(host: str, *, timeout: int) -> _FakeConnection:
        assert host in set(HOST_FOR_SURFACE.values())
        assert timeout == TIMEOUT_SECONDS
        return _FakeConnection(host, payloads.pop(0), calls)

    transport = BoundedGetOnlyTransport(
        key_id="self-test-key", secret_key="self-test-secret", capture=MemoryCapture(), emit=lambda _line: None,
        connection_factory=factory,
    )
    adapter = AlpacaOptionsReadOnlyAdapter(transport)
    as_of = date(2026, 9, 1)
    account = adapter.read_paper_account()
    positions = adapter.read_paper_positions()
    contracts = adapter.read_nvda_option_contract_metadata(as_of_date=as_of, max_pages=10)
    snapshots = adapter.read_nvda_option_snapshots(as_of_date=as_of, max_pages=10, max_contracts=10_000)
    market = adapter.normalize_market_read(
        snapshot_timestamp=datetime(2026, 9, 1, 15, 0, tzinfo=UTC),
        as_of_date=as_of,
        expected_open_interest_date=date(2026, 8, 28),
        account_payload=account,
        positions_payload=positions,
        contract_pages=contracts,
        snapshot_pages=snapshots,
    )
    entry = create_b5_entry(
        RecoveredB4Decision("a" * 64, "b" * 64, "c" * 64, "NVDA"), b5_code_commit_sha="0" * 40
    )
    result = select_readonly_b5(entry, market)
    if result.status != "B5_READY_FOR_APPROVAL" or result.candidate is None:
        raise RunnerBlocked("BLOCK_SELF_TEST_SELECTION")
    if result.candidate.execution_authority or result.candidate.broker_write_authority:
        raise RunnerBlocked("BLOCK_SELF_TEST_AUTHORITY")
    if len(calls) != 6 or any(call["method"] != "GET" or call["body"] is not None for call in calls):
        raise RunnerBlocked("BLOCK_SELF_TEST_TRANSPORT")
    parsed = [urlsplit(str(call["target"])) for call in calls]
    contract_queries = [dict(item.split("=", 1) for item in value.query.split("&")) for value in parsed[2:4]]
    snapshot_queries = [dict(item.split("=", 1) for item in value.query.split("&")) for value in parsed[4:6]]
    expected_contract = {
        "underlying_symbols": "NVDA", "type": "call", "status": "active",
        "expiration_date_gte": "2026-09-22", "expiration_date_lte": "2026-10-20", "limit": "1000",
    }
    expected_snapshot = {
        "type": "call", "expiration_date_gte": "2026-09-22", "expiration_date_lte": "2026-10-20", "limit": "1000",
    }
    if {key: value.replace("%2F", "/") for key, value in contract_queries[0].items()} != expected_contract:
        raise RunnerBlocked("BLOCK_SELF_TEST_CONTRACT_SCOPE")
    if any(query.get("page_token") is None for query in (contract_queries[1], snapshot_queries[1])):
        raise RunnerBlocked("BLOCK_SELF_TEST_PAGINATION")
    if {key: value.replace("%2F", "/") for key, value in snapshot_queries[0].items()} != expected_snapshot:
        raise RunnerBlocked("BLOCK_SELF_TEST_SNAPSHOT_SCOPE")
    emit("TRANSPORT_SELF_TEST=PASS")
    emit("SNAPSHOT_SCOPE_SELF_TEST=PASS")
    emit("OI_FRESHNESS_SELF_TEST=PASS")
    emit("B5_SELECTION_SELF_TEST=PASS")
    emit("NETWORK_CALLS=0")
    emit("BROKER_WRITES=0")
    emit("ALPACA_ORDERS=0")


def _candidate_fields(candidate: B5Candidate) -> dict[str, object]:
    return {
        "SELECTED_OPTION_SYMBOL": candidate.option_symbol,
        "EXPIRATION": candidate.expiration,
        "STRIKE": candidate.strike,
        "BID": candidate.bid,
        "ASK": candidate.ask,
        "DELTA": candidate.delta,
        "OPEN_INTEREST": candidate.open_interest,
        "RELATIVE_SPREAD": candidate.relative_spread,
        "DTE": candidate.dte,
        "QUANTITY": candidate.quantity,
        "PREMIUM_PER_CONTRACT": candidate.premium_per_contract,
        "MAX_LOSS_USD": candidate.max_loss_usd,
        "SAME_UNDERLYING_RISK_AFTER": candidate.same_underlying_risk_after,
        "AGGREGATE_OPTION_RISK_AFTER": candidate.aggregate_option_risk_after,
        "CASH_RESERVE_AFTER": candidate.cash_reserve_after,
        "ENVIRONMENT": candidate.environment,
        "ORDER_TYPE": candidate.order_type,
        "TIME_IN_FORCE": candidate.time_in_force,
        "EXTENDED_HOURS": candidate.extended_hours,
    }


def _emit_summary(output: TextIO, values: Mapping[str, object]) -> None:
    for key, value in values.items():
        print(f"{key}={value}", file=output)


def run_execute(
    inputs: ExecuteInputs,
    *,
    repository: Path,
    environment: Mapping[str, str],
    output: TextIO,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    connection_factory: Callable[..., Any] = http.client.HTTPSConnection,
) -> int:
    values: dict[str, object] = {
        "CANONICAL_HEAD": inputs.expected_head,
        "B5_ENTRY_STATUS": "NOT_RUN", "B5_ENTRY_HASH": "NOT_RUN", "REAL_ALPACA_SESSION_STARTED": "NO",
        "GET_CALLS_TOTAL": 0, "ACCOUNT_GETS": 0, "POSITIONS_GETS": 0, "CONTRACT_GETS": 0, "SNAPSHOT_GETS": 0,
        "CONTRACT_PAGES": "NOT_APPLICABLE", "CONTRACTS_SEEN": "NOT_APPLICABLE", "CONTRACT_PAGINATION_COMPLETE": "NOT_APPLICABLE",
        "SNAPSHOT_PAGES": "NOT_APPLICABLE", "SNAPSHOTS_SEEN": "NOT_APPLICABLE", "SNAPSHOT_PAGINATION_COMPLETE": "NOT_APPLICABLE",
        "AS_OF_DATE": inputs.as_of_date, "EXPECTED_OPEN_INTEREST_DATE": inputs.expected_open_interest_date,
        "B5_STATUS": "NOT_RUN", "B5_BLOCK_REASON": "NOT_APPLICABLE", "EXECUTION_AUTHORITY": "false",
        "BROKER_WRITE_AUTHORITY": "false", "MODEL_CALLS": 0, "OPENAI_CALLS": 0, "PAID_LLM_COST_USD": 0,
        "BROKER_WRITES": 0, "ALPACA_ORDERS": 0, "B6_STARTED": "NO", "PAPER_ORDER_SENT": "NO", "LIVE_MONEY": "PROHIBITED",
        "LOCAL_CAPTURE_PATH": "NOT_CREATED", "LOCAL_CAPTURE_SHA256": "NOT_APPLICABLE", "LOCAL_CAPTURE_CONTAINS_CREDENTIALS": "NO",
        "TRACKED_WORKTREE_CLEAN": "NO",
    }
    capture: CaptureWriter | None = None
    transport: BoundedGetOnlyTransport | None = None
    try:
        entry = _preflight(repository, inputs)
        values["B5_ENTRY_STATUS"] = entry.status
        values["B5_ENTRY_HASH"] = entry.entry_hash
        key_id = environment.get("APCA_API_KEY_ID", "")
        secret_key = environment.get("APCA_API_SECRET_KEY", "")
        if not key_id or not secret_key:
            raise RunnerBlocked("BLOCK_CREDENTIALS")
        capture_path = _capture_path(repository, inputs.expected_head, now())
        values["LOCAL_CAPTURE_PATH"] = capture_path
        capture = CaptureWriter(capture_path)
        transport = BoundedGetOnlyTransport(
            key_id=key_id, secret_key=secret_key, capture=capture, emit=lambda line: print(line, file=output),
            connection_factory=connection_factory,
        )
        adapter = AlpacaOptionsReadOnlyAdapter(transport)
        values["REAL_ALPACA_SESSION_STARTED"] = "YES"
        account = adapter.read_paper_account()
        positions = adapter.read_paper_positions()
        contract_pages = adapter.read_nvda_option_contract_metadata(as_of_date=inputs.as_of_date, max_pages=10)
        snapshot_pages = adapter.read_nvda_option_snapshots(
            as_of_date=inputs.as_of_date, max_pages=10, max_contracts=10_000
        )
        snapshot_timestamp = now()
        market = adapter.normalize_market_read(
            snapshot_timestamp=snapshot_timestamp,
            as_of_date=inputs.as_of_date,
            expected_open_interest_date=inputs.expected_open_interest_date,
            account_payload=account,
            positions_payload=positions,
            contract_pages=contract_pages,
            snapshot_pages=snapshot_pages,
        )
        result = select_readonly_b5(entry, market)
        values.update({
            "CONTRACT_PAGES": contract_pages.report.pages_read,
            "CONTRACTS_SEEN": contract_pages.report.contracts_seen,
            "CONTRACT_PAGINATION_COMPLETE": str(contract_pages.report.pagination_complete).lower(),
            "SNAPSHOT_PAGES": snapshot_pages.report.pages_read,
            "SNAPSHOTS_SEEN": snapshot_pages.report.contracts_seen,
            "SNAPSHOT_PAGINATION_COMPLETE": str(snapshot_pages.report.pagination_complete).lower(),
            "B5_STATUS": result.status,
            "B5_BLOCK_REASON": result.reason or "NOT_APPLICABLE",
        })
        if result.candidate is not None:
            values.update(_candidate_fields(result.candidate))
    except RunnerBlocked as exc:
        values["B5_BLOCK_REASON"] = str(exc)
    except B5ProductionBlocked:
        values["B5_STATUS"] = "BLOCK_INCOMPLETE_OPTION_MARKET"
        values["B5_BLOCK_REASON"] = "BLOCK_B5_NORMALIZATION"
    except Exception:
        values["B5_BLOCK_REASON"] = "BLOCK_LOCAL_RUNTIME"
    finally:
        if transport is not None:
            values["GET_CALLS_TOTAL"] = transport.total_gets
            values["ACCOUNT_GETS"] = transport.path_gets["/v2/account"]
            values["POSITIONS_GETS"] = transport.path_gets["/v2/positions"]
            values["CONTRACT_GETS"] = transport.path_gets["/v2/options/contracts"]
            values["SNAPSHOT_GETS"] = transport.path_gets["/v1beta1/options/snapshots/NVDA"]
        if capture is not None:
            values["LOCAL_CAPTURE_SHA256"] = capture.close()
        try:
            values["TRACKED_WORKTREE_CLEAN"] = "YES" if _tracked_clean(repository) else "NO"
        except RunnerBlocked:
            values["TRACKED_WORKTREE_CLEAN"] = "NO"
        _emit_summary(output, values)
    return 0 if values["B5_BLOCK_REASON"] == "NOT_APPLICABLE" or values["B5_STATUS"] != "NOT_RUN" else 1


def _inputs_from_namespace(namespace: argparse.Namespace) -> ExecuteInputs:
    if not namespace.expected_head or not namespace.as_of_date or not namespace.expected_open_interest_date:
        raise RunnerBlocked("BLOCK_EXECUTE_INPUTS")
    return ExecuteInputs(
        expected_head=_parse_sha(namespace.expected_head),
        as_of_date=_parse_date(namespace.as_of_date, "AS_OF_DATE"),
        expected_open_interest_date=_parse_date(namespace.expected_open_interest_date, "EXPECTED_OPEN_INTEREST_DATE"),
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    repository: Path | None = None,
    environment: Mapping[str, str] | None = None,
    output: TextIO | None = None,
    connection_factory: Callable[..., Any] = http.client.HTTPSConnection,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-read", action="store_true")
    parser.add_argument("--expected-head")
    parser.add_argument("--as-of-date")
    parser.add_argument("--expected-open-interest-date")
    namespace = parser.parse_args(argv)
    target_repository = repository or Path(__file__).resolve().parents[1]
    target_environment = environment if environment is not None else os.environ
    target_output = output if output is not None else sys.stdout
    if not namespace.execute_read:
        try:
            run_self_test(lambda line: print(line, file=target_output))
            return 0
        except RunnerBlocked as exc:
            print(f"B5_BLOCK_REASON={exc}", file=target_output)
            return 1
    try:
        inputs = _inputs_from_namespace(namespace)
    except RunnerBlocked as exc:
        print(f"B5_BLOCK_REASON={exc}", file=target_output)
        return 1
    return run_execute(
        inputs, repository=target_repository, environment=target_environment, output=target_output,
        connection_factory=connection_factory,
    )


if __name__ == "__main__":
    raise SystemExit(main())
