from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


ALPACA_NEWS_ENDPOINT = "https://data.alpaca.markets/v1beta1/news"
ALPACA_NEWS_NORMALIZATION_VERSION = "B3_ALPACA_NEWS_v0_2"


class AlpacaNewsReadError(RuntimeError):
    pass


class AlpacaNewsProviderModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _aware_utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _parse_timestamp(value: object, *, field: str) -> datetime:
    if type(value) is not str or not value or value != value.strip():
        raise AlpacaNewsReadError(f"{field} must be a non-empty JSON string")
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise AlpacaNewsReadError(f"{field} is not a valid ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AlpacaNewsReadError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC)


def _require_string(value: object, *, field: str, allow_empty: bool = False) -> str:
    if type(value) is not str:
        raise AlpacaNewsReadError(f"{field} must be a JSON string")
    if value != value.strip():
        raise AlpacaNewsReadError(f"{field} must be trimmed")
    if not allow_empty and not value:
        raise AlpacaNewsReadError(f"{field} must be non-empty")
    return value


def _normalize_text_string(value: object, *, field: str, allow_empty: bool = False) -> str:
    if type(value) is not str:
        raise AlpacaNewsReadError(f"{field} must be a JSON string")
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise AlpacaNewsReadError(f"{field} must be non-empty after normalization")
    return normalized


class AlpacaNewsArticle(AlpacaNewsProviderModel):
    article_id: int
    headline: str
    summary: str
    content: str
    author: str
    source: str
    url: str
    symbols: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    content_hash: str

    @field_validator("article_id")
    @classmethod
    def _positive_id(cls, value: int) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError("article_id must be a positive JSON integer")
        return value

    @field_validator("created_at", "updated_at")
    @classmethod
    def _aware(cls, value: datetime, info) -> datetime:
        return _aware_utc(value, field=info.field_name)

    @field_validator("content_hash")
    @classmethod
    def _hash(cls, value: str) -> str:
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("content_hash must be lowercase SHA-256")
        return value

    @model_validator(mode="after")
    def _article_contract(self):
        if not self.url.startswith("https://"):
            raise ValueError("news article URL must be HTTPS provider metadata")
        if self.updated_at < self.created_at:
            raise ValueError("news updated_at must not precede created_at")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("news symbols must be unique")
        if any(symbol != symbol.upper() or not symbol for symbol in self.symbols):
            raise ValueError("news symbols must be canonical uppercase")
        return self


class AlpacaNewsRead(AlpacaNewsProviderModel):
    symbol: str
    window_start: datetime
    window_end: datetime
    retrieved_at: datetime
    articles: tuple[AlpacaNewsArticle, ...]
    next_page_token: str | None = None
    pagination_complete: bool
    raw_payload_hash: str
    http_status: int
    normalization_version: str = ALPACA_NEWS_NORMALIZATION_VERSION

    @field_validator("window_start", "window_end", "retrieved_at")
    @classmethod
    def _aware(cls, value: datetime, info) -> datetime:
        return _aware_utc(value, field=info.field_name)

    @model_validator(mode="after")
    def _read_contract(self):
        if self.symbol != self.symbol.upper() or not self.symbol:
            raise ValueError("symbol must be canonical uppercase")
        if self.window_start > self.window_end:
            raise ValueError("news window_start must not be after window_end")
        if len({article.article_id for article in self.articles}) != len(self.articles):
            raise ValueError("news article IDs must be unique")
        if self.next_page_token is not None and not self.next_page_token:
            raise ValueError("next_page_token must be null or non-empty")
        if self.pagination_complete != (self.next_page_token is None):
            raise ValueError("pagination_complete must reflect next_page_token")
        if re.fullmatch(r"[0-9a-f]{64}", self.raw_payload_hash) is None:
            raise ValueError("raw_payload_hash must be lowercase SHA-256")
        if self.http_status != 200:
            raise ValueError("successful Alpaca news read requires HTTP 200")
        return self


class AlpacaNewsTransport(Protocol):
    def get(
        self,
        *,
        endpoint: str,
        query: Mapping[str, str],
        api_key_id: str,
        api_secret_key: str,
    ) -> tuple[int, bytes]:
        ...


@dataclass(frozen=True, slots=True)
class StdlibAlpacaNewsTransport:
    timeout_seconds: int = 30

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0 or self.timeout_seconds > 60:
            raise ValueError("Alpaca news timeout_seconds must be within 1..60")

    def get(
        self,
        *,
        endpoint: str,
        query: Mapping[str, str],
        api_key_id: str,
        api_secret_key: str,
    ) -> tuple[int, bytes]:
        if endpoint != ALPACA_NEWS_ENDPOINT:
            raise AlpacaNewsReadError("Alpaca news endpoint drift")
        url = endpoint + "?" + urlencode(query)
        request = Request(
            url,
            method="GET",
            headers={
                "APCA-API-KEY-ID": api_key_id,
                "APCA-API-SECRET-KEY": api_secret_key,
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return int(response.status), response.read()
        except HTTPError as exc:
            raise AlpacaNewsReadError(f"Alpaca news HTTP failure: {exc.code}") from exc
        except URLError as exc:
            raise AlpacaNewsReadError("Alpaca news network failure") from exc


def load_alpaca_market_data_credentials(
    env: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    source = os.environ if env is None else env
    key_id = source.get("APCA_API_KEY_ID")
    secret = source.get("APCA_API_SECRET_KEY")
    if key_id is None or secret is None:
        raise AlpacaNewsReadError("Alpaca market-data credentials are missing from runtime environment")
    for name, value in (("APCA_API_KEY_ID", key_id), ("APCA_API_SECRET_KEY", secret)):
        if not isinstance(value, str) or not value or value != value.strip() or any(ch.isspace() for ch in value):
            raise AlpacaNewsReadError(f"{name} must be a non-empty trimmed credential")
    return key_id, secret


def _format_rfc3339(value: datetime) -> str:
    normalized = _aware_utc(value, field="timestamp")
    return normalized.isoformat().replace("+00:00", "Z")


def _normalize_article(raw: object) -> AlpacaNewsArticle:
    if not isinstance(raw, Mapping):
        raise AlpacaNewsReadError("news article must be a JSON object")
    article_id = raw.get("id")
    if type(article_id) is not int or article_id <= 0:
        raise AlpacaNewsReadError("news id must be a positive JSON integer")
    raw_symbols = raw.get("symbols")
    if not isinstance(raw_symbols, list):
        raise AlpacaNewsReadError("news symbols must be a JSON array")
    symbols: list[str] = []
    for value in raw_symbols:
        symbol = _require_string(value, field="news.symbol")
        if symbol != symbol.upper():
            raise AlpacaNewsReadError("news symbols must be canonical uppercase")
        symbols.append(symbol)
    if len(set(symbols)) != len(symbols):
        raise AlpacaNewsReadError("news symbols must be unique")

    headline = _normalize_text_string(raw.get("headline"), field="news.headline")
    summary = _normalize_text_string(raw.get("summary", ""), field="news.summary", allow_empty=True)
    content = _normalize_text_string(raw.get("content", ""), field="news.content", allow_empty=True)
    author = _normalize_text_string(raw.get("author", ""), field="news.author", allow_empty=True)
    source = _normalize_text_string(raw.get("source"), field="news.source")
    url = _normalize_text_string(raw.get("url"), field="news.url")
    created_at = _parse_timestamp(raw.get("created_at"), field="news.created_at")
    updated_at = _parse_timestamp(raw.get("updated_at"), field="news.updated_at")
    canonical_content = json.dumps(
        {
            "id": article_id,
            "headline": headline,
            "summary": summary,
            "content": content,
            "author": author,
            "source": source,
            "url": url,
            "symbols": symbols,
            "created_at": _format_rfc3339(created_at),
            "updated_at": _format_rfc3339(updated_at),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return AlpacaNewsArticle(
        article_id=article_id,
        headline=headline,
        summary=summary,
        content=content,
        author=author,
        source=source,
        url=url,
        symbols=tuple(symbols),
        created_at=created_at,
        updated_at=updated_at,
        content_hash=hashlib.sha256(canonical_content.encode("utf-8")).hexdigest(),
    )


def read_alpaca_news_window(
    *,
    symbol: str,
    window_start: datetime,
    window_end: datetime,
    research_cutoff: datetime,
    limit: int,
    api_key_id: str,
    api_secret_key: str,
    transport: AlpacaNewsTransport | None = None,
) -> AlpacaNewsRead:
    if not isinstance(symbol, str) or not symbol or symbol != symbol.strip() or symbol != symbol.upper():
        raise AlpacaNewsReadError("symbol must be a non-empty canonical uppercase string")
    start = _aware_utc(window_start, field="window_start")
    end = _aware_utc(window_end, field="window_end")
    cutoff = _aware_utc(research_cutoff, field="research_cutoff")
    if start > end:
        raise AlpacaNewsReadError("window_start must not be after window_end")
    if end > cutoff:
        raise AlpacaNewsReadError("Alpaca news window_end must not exceed research cutoff")
    if type(limit) is not int or limit < 1 or limit > 5:
        raise AlpacaNewsReadError("B3 Alpaca news limit must be within policy range 1..5")
    for name, value in (("api_key_id", api_key_id), ("api_secret_key", api_secret_key)):
        if not isinstance(value, str) or not value or value != value.strip() or any(ch.isspace() for ch in value):
            raise AlpacaNewsReadError(f"{name} must be a valid runtime credential")

    query = {
        "symbols": symbol,
        "start": _format_rfc3339(start),
        "end": _format_rfc3339(end),
        "sort": "desc",
        "limit": str(limit),
        "include_content": "true",
        "exclude_contentless": "false",
    }
    runtime_transport = StdlibAlpacaNewsTransport() if transport is None else transport
    http_status, raw = runtime_transport.get(
        endpoint=ALPACA_NEWS_ENDPOINT,
        query=query,
        api_key_id=api_key_id,
        api_secret_key=api_secret_key,
    )
    if http_status != 200:
        raise AlpacaNewsReadError(f"Alpaca news retrieval requires HTTP 200, got {http_status}")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AlpacaNewsReadError("Alpaca news response is not valid UTF-8 JSON") from exc
    if not isinstance(payload, Mapping):
        raise AlpacaNewsReadError("Alpaca news response must be a JSON object")
    raw_news = payload.get("news")
    if not isinstance(raw_news, list):
        raise AlpacaNewsReadError("Alpaca news response requires a news array")
    if len(raw_news) > limit:
        raise AlpacaNewsReadError("Alpaca news response exceeds requested policy limit")
    articles = tuple(_normalize_article(raw_article) for raw_article in raw_news)
    if any(symbol not in article.symbols for article in articles):
        raise AlpacaNewsReadError("Alpaca news response contains article not bound to requested symbol")
    if any(article.updated_at > cutoff or article.created_at > cutoff for article in articles):
        raise AlpacaNewsReadError("Alpaca news response contains future evidence beyond research cutoff")

    page_token = payload.get("next_page_token")
    if page_token is not None:
        page_token = _require_string(page_token, field="next_page_token")
    return AlpacaNewsRead(
        symbol=symbol,
        window_start=start,
        window_end=end,
        retrieved_at=datetime.now(UTC),
        articles=articles,
        next_page_token=page_token,
        pagination_complete=page_token is None,
        raw_payload_hash=hashlib.sha256(raw).hexdigest(),
        http_status=http_status,
    )
