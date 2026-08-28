from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from aic.data.providers.alpaca_news import (
    ALPACA_NEWS_NORMALIZATION_VERSION,
    AlpacaNewsReadError,
    read_alpaca_news_window,
)


CUTOFF = datetime(2026, 8, 28, 17, 34, tzinfo=UTC)


class _WhitespaceNewsTransport:
    def __init__(self, *, symbol: str = "META") -> None:
        self.symbol = symbol

    def get(self, *, endpoint, query, api_key_id, api_secret_key):
        payload = {
            "news": [
                {
                    "id": 61486575,
                    "headline": "  Bounded headline  \n",
                    "summary": "  Bounded summary  \n",
                    "content": "\n  Bounded article content  \n",
                    "author": " Reporter \n",
                    "source": " TestSource \n",
                    "url": "  https://example.com/meta-news  \n",
                    "symbols": [self.symbol],
                    "created_at": "2026-08-28T08:29:56Z",
                    "updated_at": "2026-08-28T08:29:56Z",
                }
            ],
            "next_page_token": "next-page",
        }
        return 200, json.dumps(payload).encode("utf-8")


def test_news_narrative_whitespace_is_normalized_without_weakening_authority() -> None:
    read = read_alpaca_news_window(
        symbol="META",
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        window_end=CUTOFF,
        research_cutoff=CUTOFF,
        limit=5,
        api_key_id="test-key",
        api_secret_key="test-secret",
        transport=_WhitespaceNewsTransport(),
    )
    article = read.articles[0]
    assert read.normalization_version == "B3_ALPACA_NEWS_v0_2"
    assert ALPACA_NEWS_NORMALIZATION_VERSION == "B3_ALPACA_NEWS_v0_2"
    assert article.headline == "Bounded headline"
    assert article.summary == "Bounded summary"
    assert article.content == "Bounded article content"
    assert article.author == "Reporter"
    assert article.source == "TestSource"
    assert article.url == "https://example.com/meta-news"
    assert article.symbols == ("META",)
    assert read.pagination_complete is False
    assert read.next_page_token == "next-page"


def test_news_symbol_whitespace_still_fails_closed() -> None:
    with pytest.raises(AlpacaNewsReadError, match="trimmed"):
        read_alpaca_news_window(
            symbol="META",
            window_start=datetime(2026, 1, 1, tzinfo=UTC),
            window_end=CUTOFF,
            research_cutoff=CUTOFF,
            limit=5,
            api_key_id="test-key",
            api_secret_key="test-secret",
            transport=_WhitespaceNewsTransport(symbol=" META "),
        )
