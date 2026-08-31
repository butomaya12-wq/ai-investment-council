from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from aic.data.providers.alpaca_cli_news import CLI_PROFILE_CREDENTIAL_PLACEHOLDER
from aic.data.providers.alpaca_news import ALPACA_NEWS_ENDPOINT, AlpacaNewsReadError
from aic.data.providers.alpaca_news_reopen import (
    MAX_REOPEN_NEWS_PAGES,
    ReopenAlpacaCliNewsTransport,
    read_alpaca_news_window_for_reopen,
)


CUTOFF = datetime(2026, 8, 28, 17, 34, tzinfo=UTC)


def _article(article_id: int, *, headline: str | None = None) -> dict:
    return {
        "id": article_id,
        "headline": headline or f"News {article_id}",
        "summary": "Summary",
        "content": "Content",
        "author": "Reporter",
        "source": "TestSource",
        "url": f"https://example.com/news/{article_id}",
        "symbols": ["NVDA"],
        "created_at": "2026-08-28T16:00:00Z",
        "updated_at": "2026-08-28T16:30:00Z",
    }


class _PagedTransport:
    def __init__(self, pages: dict[str | None, dict]) -> None:
        self.pages = pages
        self.queries: list[dict[str, str]] = []

    def get(self, *, endpoint, query, api_key_id, api_secret_key):
        assert endpoint == ALPACA_NEWS_ENDPOINT
        assert api_key_id == "test-key"
        assert api_secret_key == "test-secret"
        q = dict(query)
        self.queries.append(q)
        token = q.get("page_token")
        return 200, json.dumps(self.pages[token]).encode("utf-8")


def test_reopen_pagination_follows_token_until_complete() -> None:
    transport = _PagedTransport(
        {
            None: {"news": [_article(3), _article(2)], "next_page_token": "PAGE2"},
            "PAGE2": {"news": [_article(1)], "next_page_token": None},
        }
    )
    read = read_alpaca_news_window_for_reopen(
        symbol="NVDA",
        window_start=CUTOFF - timedelta(days=30),
        window_end=CUTOFF,
        research_cutoff=CUTOFF,
        page_size=5,
        api_key_id="test-key",
        api_secret_key="test-secret",
        transport=transport,
    )
    assert read.pagination_complete is True
    assert read.terminal_next_page_token is None
    assert read.page_count == 2
    assert tuple(article.article_id for article in read.articles) == (3, 2, 1)
    assert "page_token" not in transport.queries[0]
    assert transport.queries[1]["page_token"] == "PAGE2"
    assert len(read.page_raw_payload_hashes) == 2
    assert len(read.aggregate_payload_hash) == 64


def test_reopen_pagination_accepts_empty_string_as_terminal_provider_token() -> None:
    transport = _PagedTransport(
        {
            None: {"news": [_article(2)], "next_page_token": "PAGE2"},
            "PAGE2": {"news": [_article(1)], "next_page_token": ""},
        }
    )
    read = read_alpaca_news_window_for_reopen(
        symbol="NVDA",
        window_start=CUTOFF - timedelta(days=30),
        window_end=CUTOFF,
        research_cutoff=CUTOFF,
        page_size=5,
        api_key_id="test-key",
        api_secret_key="test-secret",
        transport=transport,
    )
    assert read.pagination_complete is True
    assert read.terminal_next_page_token is None
    assert read.page_count == 2
    assert tuple(article.article_id for article in read.articles) == (2, 1)


def test_reopen_pagination_stays_partial_at_engineering_page_cap() -> None:
    pages: dict[str | None, dict] = {}
    token: str | None = None
    for index in range(MAX_REOPEN_NEWS_PAGES):
        next_token = f"PAGE{index + 2}"
        pages[token] = {
            "news": [_article(index + 1)],
            "next_page_token": next_token,
        }
        token = next_token
    read = read_alpaca_news_window_for_reopen(
        symbol="NVDA",
        window_start=CUTOFF - timedelta(days=30),
        window_end=CUTOFF,
        research_cutoff=CUTOFF,
        page_size=5,
        max_pages=MAX_REOPEN_NEWS_PAGES,
        api_key_id="test-key",
        api_secret_key="test-secret",
        transport=_PagedTransport(pages),
    )
    assert read.pagination_complete is False
    assert read.page_count == MAX_REOPEN_NEWS_PAGES
    assert read.terminal_next_page_token is not None


def test_reopen_pagination_rejects_page_token_cycle() -> None:
    transport = _PagedTransport(
        {
            None: {"news": [_article(1)], "next_page_token": "PAGE2"},
            "PAGE2": {"news": [_article(2)], "next_page_token": "PAGE2"},
        }
    )
    with pytest.raises(AlpacaNewsReadError, match="cycle"):
        read_alpaca_news_window_for_reopen(
            symbol="NVDA",
            window_start=CUTOFF - timedelta(days=30),
            window_end=CUTOFF,
            research_cutoff=CUTOFF,
            api_key_id="test-key",
            api_secret_key="test-secret",
            transport=transport,
        )


def test_reopen_pagination_dedupes_identical_overlap_and_rejects_changed_content() -> None:
    identical = _PagedTransport(
        {
            None: {"news": [_article(1)], "next_page_token": "PAGE2"},
            "PAGE2": {"news": [_article(1)], "next_page_token": None},
        }
    )
    read = read_alpaca_news_window_for_reopen(
        symbol="NVDA",
        window_start=CUTOFF - timedelta(days=30),
        window_end=CUTOFF,
        research_cutoff=CUTOFF,
        api_key_id="test-key",
        api_secret_key="test-secret",
        transport=identical,
    )
    assert len(read.articles) == 1

    changed = _PagedTransport(
        {
            None: {"news": [_article(1)], "next_page_token": "PAGE2"},
            "PAGE2": {
                "news": [_article(1, headline="Changed after page boundary")],
                "next_page_token": None,
            },
        }
    )
    with pytest.raises(AlpacaNewsReadError, match="changed content"):
        read_alpaca_news_window_for_reopen(
            symbol="NVDA",
            window_start=CUTOFF - timedelta(days=30),
            window_end=CUTOFF,
            research_cutoff=CUTOFF,
            api_key_id="test-key",
            api_secret_key="test-secret",
            transport=changed,
        )


class _Runner:
    def __init__(self) -> None:
        self.command = None
        self.env = None

    def __call__(self, command, *, stdout, stderr, timeout, check, env):
        self.command = list(command)
        self.env = dict(env)
        payload = {"news": [], "next_page_token": None}
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=json.dumps(payload).encode("utf-8"),
            stderr=b"",
        )


def test_reopen_cli_transport_supports_bounded_page_token(monkeypatch) -> None:
    monkeypatch.setattr(
        "aic.data.providers.alpaca_news_reopen.shutil.which",
        lambda executable: "/opt/homebrew/bin/alpaca",
    )
    runner = _Runner()
    transport = ReopenAlpacaCliNewsTransport(profile="paper", runner=runner)
    query = {
        "symbols": "NVDA",
        "start": "2026-08-01T00:00:00Z",
        "end": "2026-08-28T17:34:00Z",
        "sort": "desc",
        "limit": "5",
        "include_content": "true",
        "exclude_contentless": "false",
        "page_token": "PAGE2",
    }
    status, _ = transport.get(
        endpoint=ALPACA_NEWS_ENDPOINT,
        query=query,
        api_key_id=CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
        api_secret_key=CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
    )
    assert status == 200
    assert "--page-token" in runner.command
    idx = runner.command.index("--page-token")
    assert runner.command[idx + 1] == "PAGE2"
    assert "order" not in runner.command
    assert runner.env["ALPACA_QUIET"] == "1"
    assert "ALPACA_LIVE_TRADE" not in runner.env


def test_reopen_cli_transport_rejects_query_escape(monkeypatch) -> None:
    monkeypatch.setattr(
        "aic.data.providers.alpaca_news_reopen.shutil.which",
        lambda executable: "/opt/homebrew/bin/alpaca",
    )
    transport = ReopenAlpacaCliNewsTransport(profile="paper", runner=_Runner())
    bad = {
        "symbols": "NVDA",
        "start": "2026-08-01T00:00:00Z",
        "end": "2026-08-28T17:34:00Z",
        "sort": "desc",
        "limit": "5",
        "include_content": "true",
        "exclude_contentless": "false",
        "url": "https://evil.example",
    }
    with pytest.raises(AlpacaNewsReadError, match="query shape drift"):
        transport.get(
            endpoint=ALPACA_NEWS_ENDPOINT,
            query=bad,
            api_key_id=CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
            api_secret_key=CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
        )
