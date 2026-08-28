from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from aic.data.providers.alpaca_news import (
    ALPACA_NEWS_ENDPOINT,
    AlpacaNewsReadError,
    read_alpaca_news_window,
)
from aic.data.providers.sec_filings import SecFilingReadError, read_sec_filing_sections


CUTOFF = datetime(2026, 8, 28, 17, 34, tzinfo=UTC)
ACCESSION = "0001045810-26-000021"
SEC_URI = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm"


class _FakeSecTransport:
    def __init__(self, *, future: bool = False) -> None:
        self.future = future
        self.calls: list[str] = []

    def get(self, *, url: str, user_agent: str, accept: str):
        self.calls.append(url)
        assert user_agent == "AIC test contact@example.com"
        if "submissions/CIK" in url:
            accepted = "2026-08-29T00:00:00Z" if self.future else "2026-02-25T21:00:00Z"
            payload = {
                "filings": {
                    "recent": {
                        "accessionNumber": [ACCESSION],
                        "acceptanceDateTime": [accepted],
                    }
                }
            }
            return 200, json.dumps(payload).encode("utf-8")
        assert url == SEC_URI
        business = "Business operating context and segment disclosure. " * 30
        risk = "Material risk factor disclosure and uncertainty. " * 30
        mda = "Management discussion of operating results and liquidity. " * 30
        html = f"""
        <html><body>
        <div>Table of Contents ITEM 1. BUSINESS ITEM 1A. RISK FACTORS</div>
        <h2>ITEM 1. BUSINESS</h2><p>{business}</p>
        <h2>ITEM 1A. RISK FACTORS</h2><p>{risk}</p>
        <h2>ITEM 1B. UNRESOLVED STAFF COMMENTS</h2>
        <h2>ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS</h2><p>{mda}</p>
        <h2>ITEM 7A. QUANTITATIVE AND QUALITATIVE DISCLOSURES</h2>
        </body></html>
        """
        return 200, html.encode("utf-8")


class _FakeNewsTransport:
    def __init__(self, *, next_page_token=None, future=False) -> None:
        self.next_page_token = next_page_token
        self.future = future
        self.query = None
        self.endpoint = None

    def get(self, *, endpoint, query, api_key_id, api_secret_key):
        self.endpoint = endpoint
        self.query = dict(query)
        assert api_key_id == "test-key"
        assert api_secret_key == "test-secret"
        updated = "2026-08-28T18:00:00Z" if self.future else "2026-08-28T16:30:00Z"
        payload = {
            "news": [
                {
                    "id": 123,
                    "headline": "NVIDIA announces bounded test news",
                    "summary": "Summary",
                    "content": "Article content",
                    "author": "Reporter",
                    "source": "TestSource",
                    "url": "https://example.com/nvda-news",
                    "symbols": ["NVDA"],
                    "created_at": "2026-08-28T16:00:00Z",
                    "updated_at": updated,
                }
            ],
            "next_page_token": self.next_page_token,
        }
        return 200, json.dumps(payload).encode("utf-8")


def test_sec_provider_reads_only_bound_official_filing_and_extracts_requested_sections() -> None:
    transport = _FakeSecTransport()
    read = read_sec_filing_sections(
        accession=ACCESSION,
        source_uri=SEC_URI,
        section_names=("Business", "Risk Factors", "MD&A"),
        research_cutoff=CUTOFF,
        user_agent="AIC test contact@example.com",
        transport=transport,
    )
    assert tuple(section.section_name for section in read.sections) == (
        "Business",
        "Risk Factors",
        "MD&A",
    )
    assert all(len(section.text) >= 500 for section in read.sections)
    assert len(read.raw_payload_hash) == 64
    assert transport.calls[1] == SEC_URI


def test_sec_provider_fails_closed_on_future_filing() -> None:
    with pytest.raises(SecFilingReadError, match="not knowable"):
        read_sec_filing_sections(
            accession=ACCESSION,
            source_uri=SEC_URI,
            section_names=("Risk Factors",),
            research_cutoff=CUTOFF,
            user_agent="AIC test contact@example.com",
            transport=_FakeSecTransport(future=True),
        )


def test_alpaca_news_provider_uses_fixed_endpoint_and_marks_unfinished_pagination() -> None:
    transport = _FakeNewsTransport(next_page_token="next-page")
    read = read_alpaca_news_window(
        symbol="NVDA",
        window_start=CUTOFF - timedelta(days=7),
        window_end=CUTOFF,
        research_cutoff=CUTOFF,
        limit=5,
        api_key_id="test-key",
        api_secret_key="test-secret",
        transport=transport,
    )
    assert transport.endpoint == ALPACA_NEWS_ENDPOINT
    assert transport.query["symbols"] == "NVDA"
    assert transport.query["limit"] == "5"
    assert transport.query["include_content"] == "true"
    assert read.pagination_complete is False
    assert read.next_page_token == "next-page"
    assert read.articles[0].symbols == ("NVDA",)


def test_alpaca_news_provider_rejects_future_article_and_window() -> None:
    with pytest.raises(AlpacaNewsReadError, match="future evidence"):
        read_alpaca_news_window(
            symbol="NVDA",
            window_start=CUTOFF - timedelta(days=1),
            window_end=CUTOFF,
            research_cutoff=CUTOFF,
            limit=1,
            api_key_id="test-key",
            api_secret_key="test-secret",
            transport=_FakeNewsTransport(future=True),
        )
    with pytest.raises(AlpacaNewsReadError, match="must not exceed research cutoff"):
        read_alpaca_news_window(
            symbol="NVDA",
            window_start=CUTOFF - timedelta(days=1),
            window_end=CUTOFF + timedelta(seconds=1),
            research_cutoff=CUTOFF,
            limit=1,
            api_key_id="test-key",
            api_secret_key="test-secret",
            transport=_FakeNewsTransport(),
        )
