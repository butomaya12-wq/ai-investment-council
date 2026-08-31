from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Sequence, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from aic.domain.canonical import canonical_sha256

from .alpaca_news import AlpacaNewsArticle, AlpacaNewsReadError, AlpacaNewsTransport, _aware_utc
from .alpaca_news_reopen import MAX_REOPEN_NEWS_ARTICLES, _read_page


ALPACA_NEWS_REOPEN_CONTINUATION_VERSION = "B3_ALPACA_NEWS_REOPEN_CONTINUATION_FROM_SAVED_TOKEN_v0_1"
MAX_CONTINUATION_PAGES = 4


class AlpacaNewsReopenContinuationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AlpacaNewsReopenContinuationRead(AlpacaNewsReopenContinuationModel):
    continuation_version: str
    symbol: str
    window_start: datetime
    window_end: datetime
    retrieved_at: datetime
    page_size: int
    start_page_token: str
    additional_page_count: int
    max_additional_pages: int
    retained_article_count: int
    new_articles: tuple[AlpacaNewsArticle, ...]
    total_article_count: int
    additional_page_raw_payload_hashes: tuple[str, ...]
    terminal_next_page_token: str | None
    pagination_complete: bool
    aggregate_payload_hash: str

    @field_validator("window_start", "window_end", "retrieved_at")
    @classmethod
    def _aware(cls, value: datetime, info) -> datetime:
        return _aware_utc(value, field=info.field_name)

    @model_validator(mode="after")
    def _contract(self) -> Self:
        if self.continuation_version != ALPACA_NEWS_REOPEN_CONTINUATION_VERSION:
            raise ValueError("unexpected reopen continuation version")
        if self.symbol != self.symbol.upper() or not self.symbol:
            raise ValueError("continuation symbol must be canonical uppercase")
        if self.window_start > self.window_end:
            raise ValueError("continuation window_start must not exceed window_end")
        if not 1 <= self.page_size <= 5:
            raise ValueError("continuation page_size must be within 1..5")
        if not 1 <= self.max_additional_pages <= MAX_CONTINUATION_PAGES:
            raise ValueError("continuation max_additional_pages exceeds engineering bound")
        if not 1 <= self.additional_page_count <= self.max_additional_pages:
            raise ValueError("continuation page count must be within 1..max_additional_pages")
        if len(self.additional_page_raw_payload_hashes) != self.additional_page_count:
            raise ValueError("continuation page hash count must equal page count")
        if any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in self.additional_page_raw_payload_hashes):
            raise ValueError("continuation page hashes must be lowercase SHA-256")
        if not self.start_page_token or self.start_page_token != self.start_page_token.strip():
            raise ValueError("continuation start_page_token must be non-empty trimmed")
        if self.pagination_complete != (self.terminal_next_page_token is None):
            raise ValueError("continuation pagination_complete must match terminal token")
        if self.terminal_next_page_token is not None and (
            not self.terminal_next_page_token
            or self.terminal_next_page_token != self.terminal_next_page_token.strip()
        ):
            raise ValueError("continuation terminal token must be null or non-empty trimmed")
        if len({article.article_id for article in self.new_articles}) != len(self.new_articles):
            raise ValueError("continuation new article IDs must be unique")
        if self.retained_article_count < 0:
            raise ValueError("retained_article_count must be non-negative")
        if self.total_article_count != self.retained_article_count + len(self.new_articles):
            raise ValueError("total_article_count arithmetic drift")
        if self.total_article_count > MAX_REOPEN_NEWS_ARTICLES:
            raise ValueError("continuation total article budget exceeded")
        expected = canonical_sha256(self, exclude_fields=("aggregate_payload_hash",))
        if self.aggregate_payload_hash != expected:
            raise ValueError("aggregate_payload_hash does not bind continuation read")
        return self

    @classmethod
    def build(cls, **payload):
        data = dict(payload)
        data["aggregate_payload_hash"] = canonical_sha256(data)
        return cls(**data)


def read_alpaca_news_continuation_from_saved_token(
    *,
    symbol: str,
    window_start: datetime,
    window_end: datetime,
    research_cutoff: datetime,
    start_page_token: str,
    retained_articles: Sequence[AlpacaNewsArticle],
    page_size: int = 5,
    max_additional_pages: int = MAX_CONTINUATION_PAGES,
    api_key_id: str,
    api_secret_key: str,
    transport: AlpacaNewsTransport,
) -> AlpacaNewsReopenContinuationRead:
    if not isinstance(symbol, str) or not symbol or symbol != symbol.strip() or symbol != symbol.upper():
        raise AlpacaNewsReadError("continuation symbol must be canonical uppercase")
    start = _aware_utc(window_start, field="window_start")
    end = _aware_utc(window_end, field="window_end")
    cutoff = _aware_utc(research_cutoff, field="research_cutoff")
    if start > end:
        raise AlpacaNewsReadError("continuation window_start must not be after window_end")
    if end > cutoff:
        raise AlpacaNewsReadError("continuation window_end must not exceed research cutoff")
    if type(page_size) is not int or not 1 <= page_size <= 5:
        raise AlpacaNewsReadError("continuation page_size must be within 1..5")
    if type(max_additional_pages) is not int or not 1 <= max_additional_pages <= MAX_CONTINUATION_PAGES:
        raise AlpacaNewsReadError("continuation max_additional_pages must be within 1..4")
    if not isinstance(start_page_token, str) or not start_page_token or start_page_token != start_page_token.strip():
        raise AlpacaNewsReadError("continuation start_page_token must be non-empty trimmed")
    if transport is None:
        raise AlpacaNewsReadError("continuation transport is required")
    for name, value in (("api_key_id", api_key_id), ("api_secret_key", api_secret_key)):
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or any(ch.isspace() for ch in value)
        ):
            raise AlpacaNewsReadError(f"{name} must be a valid runtime credential")

    retained_by_id: dict[int, AlpacaNewsArticle] = {}
    for article in retained_articles:
        if not isinstance(article, AlpacaNewsArticle):
            raise AlpacaNewsReadError("retained article must be typed AlpacaNewsArticle")
        prior = retained_by_id.get(article.article_id)
        if prior is not None and prior.content_hash != article.content_hash:
            raise AlpacaNewsReadError("retained duplicate article id has changed content")
        retained_by_id[article.article_id] = article
    if len(retained_by_id) != len(retained_articles):
        raise AlpacaNewsReadError("retained article IDs must be unique")
    if len(retained_by_id) > MAX_REOPEN_NEWS_ARTICLES:
        raise AlpacaNewsReadError("retained article budget exceeded")

    page_token = start_page_token
    seen_tokens: set[str] = {start_page_token}
    page_hashes: list[str] = []
    new_by_id: dict[int, AlpacaNewsArticle] = {}
    pagination_complete = False

    for _page_index in range(1, max_additional_pages + 1):
        page = _read_page(
            symbol=symbol,
            window_start=start,
            window_end=end,
            research_cutoff=cutoff,
            page_size=page_size,
            page_token=page_token,
            api_key_id=api_key_id,
            api_secret_key=api_secret_key,
            transport=transport,
        )
        page_hashes.append(page.raw_payload_hash)
        for article in page.articles:
            retained = retained_by_id.get(article.article_id)
            if retained is not None:
                if retained.content_hash != article.content_hash:
                    raise AlpacaNewsReadError(
                        "continuation duplicate retained article id has changed content"
                    )
                continue
            prior = new_by_id.get(article.article_id)
            if prior is not None:
                if prior.content_hash != article.content_hash:
                    raise AlpacaNewsReadError(
                        "continuation duplicate new article id has changed content"
                    )
                continue
            new_by_id[article.article_id] = article
        if len(retained_by_id) + len(new_by_id) > MAX_REOPEN_NEWS_ARTICLES:
            raise AlpacaNewsReadError("continuation total article budget exceeded")
        if page.next_page_token is None:
            page_token = None
            pagination_complete = True
            break
        if page.next_page_token in seen_tokens:
            raise AlpacaNewsReadError("continuation page-token cycle detected")
        seen_tokens.add(page.next_page_token)
        page_token = page.next_page_token

    return AlpacaNewsReopenContinuationRead.build(
        continuation_version=ALPACA_NEWS_REOPEN_CONTINUATION_VERSION,
        symbol=symbol,
        window_start=start,
        window_end=end,
        retrieved_at=datetime.now(UTC),
        page_size=page_size,
        start_page_token=start_page_token,
        additional_page_count=len(page_hashes),
        max_additional_pages=max_additional_pages,
        retained_article_count=len(retained_by_id),
        new_articles=tuple(new_by_id.values()),
        total_article_count=len(retained_by_id) + len(new_by_id),
        additional_page_raw_payload_hashes=tuple(page_hashes),
        terminal_next_page_token=None if pagination_complete else page_token,
        pagination_complete=pagination_complete,
    )
