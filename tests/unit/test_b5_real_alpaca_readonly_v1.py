from __future__ import annotations

from datetime import UTC, date, datetime
import importlib.util
import io
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "scripts" / "b5_real_alpaca_readonly_v1.py"
SPEC = importlib.util.spec_from_file_location("b5_real_alpaca_readonly_v1", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def entry():
    return runner.create_b5_entry(
        runner.RecoveredB4Decision("a" * 64, "b" * 64, "c" * 64, "NVDA"), b5_code_commit_sha="0" * 40
    )


def inputs() -> object:
    return runner.ExecuteInputs("a" * 40, date(2026, 9, 1), date(2026, 8, 28))


class Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body


class Connection:
    def __init__(self, host: str, response: Response, calls: list[dict[str, object]]) -> None:
        self.host = host
        self.response = response
        self.calls = calls

    def request(self, method: str, target: str, body: object, headers: dict[str, str]) -> None:
        self.calls.append({"host": self.host, "method": method, "target": target, "body": body, "headers": headers})

    def getresponse(self) -> Response:
        return self.response

    def close(self) -> None:
        return None


def valid_payloads(*, quote_time: str = "2026-09-01T15:00:00Z") -> list[object]:
    return [
        {"account_number": "123456789", "equity": "77777", "cash": "66666", "options_buying_power": "55555"},
        [],
        {"option_contracts": [{
            "symbol": "NVDA261006C00200000", "status": "active", "tradable": True,
            "expiration_date": "2026-10-06", "underlying_symbol": "NVDA", "type": "call",
            "strike_price": "200", "size": "100", "open_interest": "100", "open_interest_date": "2026-08-28",
        }], "next_page_token": None},
        {"snapshots": {"NVDA261006C00200000": {
            "latestQuote": {"bp": "2.40", "ap": "2.50", "t": quote_time}, "greeks": {"delta": "0.50"},
        }}, "next_page_token": None},
    ]


def queued_factory(payloads: list[object], calls: list[dict[str, object]]):
    def factory(host: str, *, timeout: int) -> Connection:
        assert timeout == runner.TIMEOUT_SECONDS
        return Connection(host, Response(200, json.dumps(payloads.pop(0)).encode("utf-8")), calls)
    return factory


def test_default_mode_requires_no_credentials_or_real_transport() -> None:
    output = io.StringIO()
    constructed = False

    def factory(*_args, **_kwargs):
        nonlocal constructed
        constructed = True
        raise AssertionError("default mode must not construct real transport")

    assert runner.main([], environment={}, output=output, connection_factory=factory) == 0
    assert constructed is False
    assert "TRANSPORT_SELF_TEST=PASS" in output.getvalue()
    assert "NETWORK_CALLS=0" in output.getvalue()


@pytest.mark.parametrize(
    ("git_values", "expected"),
    [
        ({("branch", "--show-current"): "wrong", ("rev-parse", "HEAD"): "a" * 40, ("status", "--porcelain=v1", "--untracked-files=no"): ""}, "BLOCK_BRANCH"),
        ({("branch", "--show-current"): runner.CANONICAL_BRANCH, ("rev-parse", "HEAD"): "b" * 40, ("status", "--porcelain=v1", "--untracked-files=no"): ""}, "BLOCK_HEAD"),
        ({("branch", "--show-current"): runner.CANONICAL_BRANCH, ("rev-parse", "HEAD"): "a" * 40, ("status", "--porcelain=v1", "--untracked-files=no"): " M x"}, "BLOCK_TRACKED_DIRTY"),
    ],
)
def test_repository_gates_block_before_transport(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, git_values, expected: str) -> None:
    artifact = tmp_path / runner.RECOVERED_B4_RELATIVE_PATH
    artifact.parent.mkdir()
    artifact.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(runner, "_git", lambda _repository, *args: git_values[args])
    constructed = False

    def factory(*_args, **_kwargs):
        nonlocal constructed
        constructed = True
        raise AssertionError

    output = io.StringIO()
    assert runner.run_execute(inputs(), repository=tmp_path, environment={"APCA_API_KEY_ID": "id", "APCA_API_SECRET_KEY": "secret"}, output=output, connection_factory=factory) == 1
    assert expected in output.getvalue()
    assert constructed is False


def test_missing_credentials_and_invalid_cli_block_before_transport(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner, "_preflight", lambda *_args: entry())
    constructed = False

    def factory(*_args, **_kwargs):
        nonlocal constructed
        constructed = True
        raise AssertionError

    output = io.StringIO()
    assert runner.run_execute(inputs(), repository=tmp_path, environment={}, output=output, connection_factory=factory) == 1
    assert "BLOCK_CREDENTIALS" in output.getvalue() and constructed is False
    output = io.StringIO()
    assert runner.main(["--execute-read", "--expected-head", "bad", "--as-of-date", "not-a-date", "--expected-open-interest-date", "2026-08-28"], output=output, connection_factory=factory) == 1
    assert "BLOCK_EXPECTED_HEAD" in output.getvalue() and constructed is False


def test_route_method_and_ceilings_are_fail_closed() -> None:
    calls: list[dict[str, object]] = []
    transport = runner.BoundedGetOnlyTransport(
        key_id="id", secret_key="secret", capture=runner.MemoryCapture(), emit=lambda _line: None,
        connection_factory=queued_factory([{}] * 3, calls),
    )
    assert transport.get(surface=runner.ReadSurface.PAPER_TRADING_API, path="/v2/account", query={}) == {}
    with pytest.raises(runner.RunnerBlocked, match="BLOCK_GET_CEILING"):
        transport.get(surface=runner.ReadSurface.PAPER_TRADING_API, path="/v2/account", query={})
    with pytest.raises(runner.RunnerBlocked, match="BLOCK_READ_ROUTE"):
        transport.get(surface=runner.ReadSurface.MARKET_DATA_API, path="/v2/account", query={})
    transport.total_gets = runner.MAX_GET_CALLS
    with pytest.raises(runner.RunnerBlocked, match="BLOCK_GET_CEILING"):
        transport.get(surface=runner.ReadSurface.PAPER_TRADING_API, path="/v2/positions", query={})
    assert calls[0]["method"] == "GET" and calls[0]["body"] is None


@pytest.mark.parametrize("error", [TimeoutError(), OSError()])
def test_transport_errors_consume_one_call_without_retry(error: Exception) -> None:
    attempts = 0

    def factory(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise error

    transport = runner.BoundedGetOnlyTransport(
        key_id="id", secret_key="secret", capture=runner.MemoryCapture(), emit=lambda _line: None, connection_factory=factory
    )
    with pytest.raises(runner.RunnerBlocked, match="BLOCK_TRANSPORT"):
        transport.get(surface=runner.ReadSurface.PAPER_TRADING_API, path="/v2/account", query={})
    assert attempts == 1 and transport.total_gets == 1 and transport.path_gets["/v2/account"] == 1


def test_non_200_and_malformed_json_are_captured_before_block() -> None:
    for response, expected in ((Response(503, b"down"), "BLOCK_HTTP_STATUS"), (Response(200, b"not-json"), "BLOCK_RESPONSE_JSON")):
        capture = runner.MemoryCapture()
        calls: list[dict[str, object]] = []
        transport = runner.BoundedGetOnlyTransport(
            key_id="id", secret_key="secret", capture=capture, emit=lambda _line: None,
            connection_factory=lambda host, *, timeout: Connection(host, response, calls),
        )
        with pytest.raises(runner.RunnerBlocked, match=expected):
            transport.get(surface=runner.ReadSurface.PAPER_TRADING_API, path="/v2/account", query={})
        assert capture.records[0]["kind"] == "response" and transport.total_gets == 1 and len(calls) == 1


def test_existing_capture_path_blocks_without_transport_or_append(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    existing = tmp_path / "existing.jsonl"
    existing.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(runner, "_preflight", lambda *_args: entry())
    monkeypatch.setattr(runner, "_capture_path", lambda *_args: existing)
    constructed = False

    def factory(*_args, **_kwargs):
        nonlocal constructed
        constructed = True
        raise AssertionError

    output = io.StringIO()
    assert runner.run_execute(inputs(), repository=tmp_path, environment={"APCA_API_KEY_ID": "id", "APCA_API_SECRET_KEY": "secret"}, output=output, connection_factory=factory) == 1
    assert existing.read_text(encoding="utf-8") == "preserve" and constructed is False


def test_execute_uses_explicit_oi_date_sanitizes_console_and_capture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner, "_preflight", lambda *_args: entry())
    capture_path = tmp_path / "capture.jsonl"
    monkeypatch.setattr(runner, "_capture_path", lambda *_args: capture_path)
    calls: list[dict[str, object]] = []
    output = io.StringIO()
    assert runner.run_execute(
        inputs(), repository=tmp_path, environment={"APCA_API_KEY_ID": "key-value", "APCA_API_SECRET_KEY": "secret-value"}, output=output,
        now=lambda: datetime(2026, 9, 1, 15, 0, tzinfo=UTC), connection_factory=queued_factory(valid_payloads(), calls),
    ) == 0
    text = output.getvalue()
    assert "B5_STATUS=B5_READY_FOR_APPROVAL" in text and "EXPECTED_OPEN_INTEREST_DATE=2026-08-28" in text
    assert all(secret not in text for secret in ("key-value", "secret-value", "123456789", "77777", "66666", "55555"))
    captured = capture_path.read_text(encoding="utf-8")
    assert "key-value" not in captured and "secret-value" not in captured
    assert calls and all(call["method"] == "GET" and call["body"] is None for call in calls)


def test_snapshot_scope_and_quote_freshness_remain_enforced(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner, "_preflight", lambda *_args: entry())
    monkeypatch.setattr(runner, "_capture_path", lambda *_args: tmp_path / "capture.jsonl")
    calls: list[dict[str, object]] = []
    output = io.StringIO()
    runner.run_execute(
        inputs(), repository=tmp_path, environment={"APCA_API_KEY_ID": "id", "APCA_API_SECRET_KEY": "secret"}, output=output,
        now=lambda: datetime(2026, 9, 1, 15, 2, tzinfo=UTC), connection_factory=queued_factory(valid_payloads(quote_time="2026-09-01T15:00:00Z"), calls),
    )
    snapshot_query = parse_qs(urlsplit(str(calls[-1]["target"])).query)
    assert snapshot_query == {
        "limit": ["1000"], "type": ["call"], "expiration_date_gte": ["2026-09-22"], "expiration_date_lte": ["2026-10-20"],
    }
    assert "BLOCK_B5_NORMALIZATION" in output.getvalue()


def test_runner_source_has_only_get_request_literal_and_no_write_capability() -> None:
    source = RUNNER_PATH.read_text(encoding="utf-8")
    assert '"GET"' in source
    forbidden = ("/v2/orders", "submit_order", "create_order", "replace_order", "cancel_order", "exercise")
    assert not any(value in source for value in forbidden)
