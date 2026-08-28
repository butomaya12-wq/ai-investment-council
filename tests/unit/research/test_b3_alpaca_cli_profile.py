from __future__ import annotations

import json
import subprocess

import pytest

from aic.data.providers.alpaca_cli_news import (
    CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
    AlpacaCliNewsTransport,
)
from aic.data.providers.alpaca_news import ALPACA_NEWS_ENDPOINT, AlpacaNewsReadError


QUERY = {
    "symbols": "NVDA",
    "start": "2026-08-01T00:00:00Z",
    "end": "2026-08-28T17:34:00Z",
    "sort": "desc",
    "limit": "5",
    "include_content": "true",
    "exclude_contentless": "false",
}


class _Runner:
    def __init__(self, *, returncode: int = 0) -> None:
        self.returncode = returncode
        self.command = None
        self.env = None

    def __call__(self, command, *, stdout, stderr, timeout, check, env):
        self.command = list(command)
        self.env = dict(env)
        payload = {
            "news": [],
            "next_page_token": None,
        }
        return subprocess.CompletedProcess(
            args=command,
            returncode=self.returncode,
            stdout=json.dumps(payload).encode("utf-8"),
            stderr=b"redacted diagnostics",
        )


def test_cli_profile_transport_is_read_only_and_binds_exact_news_query(monkeypatch) -> None:
    monkeypatch.setattr(
        "aic.data.providers.alpaca_cli_news.shutil.which",
        lambda executable: "/opt/homebrew/bin/alpaca",
    )
    runner = _Runner()
    transport = AlpacaCliNewsTransport(profile="paper", runner=runner)
    status, raw = transport.get(
        endpoint=ALPACA_NEWS_ENDPOINT,
        query=QUERY,
        api_key_id=CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
        api_secret_key=CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
    )
    assert status == 200
    assert json.loads(raw)["news"] == []
    assert runner.command == [
        "/opt/homebrew/bin/alpaca",
        "data",
        "news",
        "--symbols",
        "NVDA",
        "--start",
        "2026-08-01T00:00:00Z",
        "--end",
        "2026-08-28T17:34:00Z",
        "--sort",
        "desc",
        "--limit",
        "5",
        "--include-content=true",
        "--exclude-contentless=false",
        "--profile",
        "paper",
        "--quiet",
    ]
    assert "order" not in runner.command
    assert "POST" not in runner.command
    assert runner.env["ALPACA_QUIET"] == "1"
    assert "ALPACA_LIVE_TRADE" not in runner.env


def test_cli_profile_transport_fails_closed_on_query_or_auth_drift(monkeypatch) -> None:
    monkeypatch.setattr(
        "aic.data.providers.alpaca_cli_news.shutil.which",
        lambda executable: "/opt/homebrew/bin/alpaca",
    )
    transport = AlpacaCliNewsTransport(profile="paper", runner=_Runner())
    bad_query = dict(QUERY)
    bad_query["page_token"] = "escape"
    with pytest.raises(AlpacaNewsReadError, match="query shape drift"):
        transport.get(
            endpoint=ALPACA_NEWS_ENDPOINT,
            query=bad_query,
            api_key_id=CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
            api_secret_key=CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
        )
    with pytest.raises(AlpacaNewsReadError, match="placeholder"):
        transport.get(
            endpoint=ALPACA_NEWS_ENDPOINT,
            query=QUERY,
            api_key_id="real-looking-key",
            api_secret_key="real-looking-secret",
        )


def test_cli_profile_transport_maps_cli_auth_failure_without_stderr_leak(monkeypatch) -> None:
    monkeypatch.setattr(
        "aic.data.providers.alpaca_cli_news.shutil.which",
        lambda executable: "/opt/homebrew/bin/alpaca",
    )
    transport = AlpacaCliNewsTransport(profile="paper", runner=_Runner(returncode=2))
    with pytest.raises(AlpacaNewsReadError, match="profile authentication failed") as exc_info:
        transport.get(
            endpoint=ALPACA_NEWS_ENDPOINT,
            query=QUERY,
            api_key_id=CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
            api_secret_key=CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
        )
    assert "redacted diagnostics" not in str(exc_info.value)
