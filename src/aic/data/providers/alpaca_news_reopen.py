from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Mapping, Protocol, Self, Sequence

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from aic.domain.canonical import canonical_sha256

from .alpaca_cli_news import CLI_PROFILE_CREDENTIAL_PLACEHOLDER
from .alpaca_news import (
    ALPACA_NEWS_ENDPOINT,
    AlpacaNewsArticle,
    AlpacaNewsReadError,
    AlpacaNewsTransport,
    StdlibAlpacaNewsTransport,
    _aware_utc,
    _format_rfc3339,
    _normalize_article,
    _require_string,
)


ALPACA_NEWS_REOPEN_PAGINATION_VERSION = "B3_ALPACA_NEWS_REOPEN_PAGINATION_v0_1"
MAX_REOPEN_NEWS_PAGES = 6
MAX_REOPEN_NEWS_ARTICLES = 30


class AlpacaNewsReopenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AlpacaNewsReopenRead(AlpacaNewsReopenModel):
    pagination_version: str
    symbol: str
    window_start: datetime
    window_end: datetime
    retrieved_at: datetime
    page_size: int
    page_count: int
    max_pages: int
    articles: tuple[AlpacaNewsArticle, ...]
    page_raw_payload_hashes: tuple[str, ...]
    terminal_next_page_token: str | None
    pagination_complete: bool
    aggregate_payload_hash: str

    @field_validator("window_start", "window_end", "retrieved_at")
    @classmethod
    def _aware(cls, value: datetime, info) -> datetime:
        return _aware_utc(value, field=info.field_name)

    @model_validator(mode="after")
    def _contract(self) -> Self:
        if self.pagination_version != ALPACA_NEWS_REOPEN_PAGINATION_VERSION:
            raise ValueError("unexpected reopen pagination version")
        if self.symbol != self.symbol.upper() or not self.symbol:
            raise ValueError("reopen news symbol must be canonical uppercase")
        if self.window_start > self.window_end:
            raise ValueError("reopen news window_start must not exceed window_end")
        if not 1 <= self.page_size <= 5:
            raise ValueError("reopen page_size must be within 1..5")
        if not 1 <= self.max_pages <= MAX_REOPEN_NEWS_PAGES:
            raise ValueError("reopen max_pages exceeds engineering bound")
        if not 1 <= self.page_count <= self.max_pages:
            raise ValueError("reopen page_count must be within 1..max_pages")
        if len(self.page_raw_payload_hashes) != self.page_count:
            raise ValueError("reopen page hash count must equal page_count")
        if any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in self.page_raw_payload_hashes):
            raise ValueError("reopen page hashes must be lowercase SHA-256")
        if len(self.articles) > MAX_REOPEN_NEWS_ARTICLES:
            raise ValueError("reopen article count exceeds engineering bound")
        if len({article.article_id for article in self.articles}) != len(self.articles):
            raise ValueError("reopen article IDs must be unique")
        if self.pagination_complete != (self.terminal_next_page_token is None):
            raise ValueError("reopen pagination_complete must match terminal page token")
        if self.terminal_next_page_token is not None and (
            not self.terminal_next_page_token
            or self.terminal_next_page_token != self.terminal_next_page_token.strip()
        ):
            raise ValueError("terminal page token must be null or non-empty trimmed")
        expected = canonical_sha256(self, exclude_fields=("aggregate_payload_hash",))
        if self.aggregate_payload_hash != expected:
            raise ValueError("aggregate_payload_hash does not bind reopen read")
        return self

    @classmethod
    def build(cls, **payload):
        data = dict(payload)
        data["aggregate_payload_hash"] = canonical_sha256(data)
        return cls(**data)


class ReopenAlpacaCliRunner(Protocol):
    def __call__(
        self,
        command: Sequence[str],
        *,
        stdout: int,
        stderr: int,
        timeout: int,
        check: bool,
        env: Mapping[str, str],
    ) -> subprocess.CompletedProcess[bytes]:
        ...


@dataclass(frozen=True, slots=True)
class ReopenAlpacaCliNewsTransport:
    profile: str = "paper"
    executable: str = "alpaca"
    timeout_seconds: int = 30
    runner: ReopenAlpacaCliRunner = subprocess.run

    def __post_init__(self) -> None:
        if not self.profile or self.profile != self.profile.strip():
            raise ValueError("Alpaca CLI profile must be a non-empty trimmed string")
        if not self.executable or self.executable != self.executable.strip():
            raise ValueError("Alpaca CLI executable must be a non-empty trimmed string")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 60:
            raise ValueError("Alpaca CLI timeout_seconds must be within 1..60")

    def get(
        self,
        *,
        endpoint: str,
        query: Mapping[str, str],
        api_key_id: str,
        api_secret_key: str,
    ) -> tuple[int, bytes]:
        if endpoint != ALPACA_NEWS_ENDPOINT:
            raise AlpacaNewsReadError("reopen Alpaca CLI news endpoint drift")
        if (
            api_key_id != CLI_PROFILE_CREDENTIAL_PLACEHOLDER
            or api_secret_key != CLI_PROFILE_CREDENTIAL_PLACEHOLDER
        ):
            raise AlpacaNewsReadError(
                "reopen Alpaca CLI transport requires profile-auth placeholder binding"
            )
        base_keys = {
            "symbols",
            "start",
            "end",
            "sort",
            "limit",
            "include_content",
            "exclude_contentless",
        }
        if set(query) not in (base_keys, base_keys | {"page_token"}):
            raise AlpacaNewsReadError("reopen Alpaca CLI news query shape drift")
        if query["include_content"] != "true" or query["exclude_contentless"] != "false":
            raise AlpacaNewsReadError("reopen Alpaca CLI news content flags drift")
        page_token = query.get("page_token")
        if page_token is not None and (
            not page_token or page_token != page_token.strip()
        ):
            raise AlpacaNewsReadError("reopen Alpaca CLI page_token must be non-empty trimmed")

        executable = shutil.which(self.executable)
        if executable is None:
            raise AlpacaNewsReadError("Alpaca CLI executable is unavailable")
        command = [
            executable,
            "data",
            "news",
            "--symbols",
            query["symbols"],
            "--start",
            query["start"],
            "--end",
            query["end"],
            "--sort",
            query["sort"],
            "--limit",
            query["limit"],
            "--include-content=true",
            "--exclude-contentless=false",
        ]
        if page_token is not None:
            command.extend(["--page-token", page_token])
        command.extend(["--profile", self.profile, "--quiet"])

        runtime_env = dict(os.environ)
        runtime_env["ALPACA_QUIET"] = "1"
        runtime_env.pop("ALPACA_LIVE_TRADE", None)
        try:
            completed = self.runner(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_seconds,
                check=False,
                env=runtime_env,
            )
        except subprocess.TimeoutExpired as exc:
            raise AlpacaNewsReadError("reopen Alpaca CLI news request timed out") from exc
        except OSError as exc:
            raise AlpacaNewsReadError("reopen Alpaca CLI news process failed to start") from exc
        if completed.returncode == 2:
            raise AlpacaNewsReadError("Alpaca CLI profile authentication failed")
        if completed.returncode != 0:
            raise AlpacaNewsReadError("reopen Alpaca CLI news request failed")
        if not completed.stdout:
            raise AlpacaNewsReadError("reopen Alpaca CLI news returned empty stdout")
        return 200, bytes(completed.stdout)


@dataclass(frozen=True, slots=True)
class _NewsPage:
    articles: tuple[AlpacaNewsArticle, ...]
    next_page_token: str | None
    raw_payload_hash: str


def _read_page(
    *,
    symbol: str,
    window_start: datetime,
    window_end: datetime,
    research_cutoff: datetime,
    page_size: int,
    page_token: str | None,
    api_key_id: str,
    api_secret_key: str,
    transport: AlpacaNewsTransport,
) -> _NewsPage:
    query = {
        "symbols": symbol,
        "start": _format_rfc3339(window_start),
        "end": _format_rfc3339(window_end),
        "sort": "desc",
        "limit": str(page_size),
        "include_content": "true",
        "exclude_contentless": "false",
    }
    if page_token is not None:
        query["page_token"] = page_token
    http_status, raw = transport.get(
        endpoint=ALPACA_NEWS_ENDPOINT,
        query=query,
        api_key_id=api_key_id,
        api_secret_key=api_secret_key,
    )
    if http_status != 200:
        raise AlpacaNewsReadError(
            f"reopen Alpaca news retrieval requires HTTP 200, got {http_status}"
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AlpacaNewsReadError(
            "reopen Alpaca news response is not valid UTF-8 JSON"
        ) from exc
    if not isinstance(payload, Mapping):
        raise AlpacaNewsReadError("reopen Alpaca news response must be a JSON object")
    raw_news = payload.get("news")
    if not isinstance(raw_news, list):
        raise AlpacaNewsReadError("reopen Alpaca news response requires a news array")
    if len(raw_news) > page_size:
        raise AlpacaNewsReadError("reopen Alpaca news response exceeds requested page size")
    articles = tuple(_normalize_article(raw_article) for raw_article in raw_news)
    if any(symbol not in article.symbols for article in articles):
        raise AlpacaNewsReadError(
            "reopen Alpaca news response contains article not bound to requested symbol"
        )
    if any(
        article.updated_at > research_cutoff or article.created_at > research_cutoff
        for article in articles
    ):
        raise AlpacaNewsReadError(
            "reopen Alpaca news response contains future evidence beyond research cutoff"
        )
    next_page_token = payload.get("next_page_token")
    if next_page_token is not None:
        next_page_token = _require_string(
            next_page_token, field="next_page_token"
        )
    return _NewsPage(
        articles=articles,
        next_page_token=next_page_token,
        raw_payload_hash=hashlib.sha256(raw).hexdigest(),
    )


def read_alpaca_news_window_for_reopen(
    *,
    symbol: str,
    window_start: datetime,
    window_end: datetime,
    research_cutoff: datetime,
    page_size: int = 5,
    max_pages: int = MAX_REOPEN_NEWS_PAGES,
    api_key_id: str,
    api_secret_key: str,
    transport: AlpacaNewsTransport | None = None,
) -> AlpacaNewsReopenRead:
    if not isinstance(symbol, str) or not symbol or symbol != symbol.strip() or symbol != symbol.upper():
        raise AlpacaNewsReadError("symbol must be a non-empty canonical uppercase string")
    start = _aware_utc(window_start, field="window_start")
    end = _aware_utc(window_end, field="window_end")
    cutoff = _aware_utc(research_cutoff, field="research_cutoff")
    if start > end:
        raise AlpacaNewsReadError("window_start must not be after window_end")
    if end > cutoff:
        raise AlpacaNewsReadError("Alpaca news window_end must not exceed research cutoff")
    if type(page_size) is not int or not 1 <= page_size <= 5:
        raise AlpacaNewsReadError("reopen Alpaca news page_size must be within 1..5")
    if type(max_pages) is not int or not 1 <= max_pages <= MAX_REOPEN_NEWS_PAGES:
        raise AlpacaNewsReadError(
            f"reopen Alpaca news max_pages must be within 1..{MAX_REOPEN_NEWS_PAGES}"
        )
    for name, value in (("api_key_id", api_key_id), ("api_secret_key", api_secret_key)):
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or any(ch.isspace() for ch in value)
        ):
            raise AlpacaNewsReadError(f"{name} must be a valid runtime credential")

    runtime_transport = StdlibAlpacaNewsTransport() if transport is None else transport
    page_token: str | None = None
    seen_tokens: set[str] = set()
    page_hashes: list[str] = []
    articles_by_id: dict[int, AlpacaNewsArticle] = {}
    pagination_complete = False

    for _page_index in range(1, max_pages + 1):
        page = _read_page(
            symbol=symbol,
            window_start=start,
            window_end=end,
            research_cutoff=cutoff,
            page_size=page_size,
            page_token=page_token,
            api_key_id=api_key_id,
            api_secret_key=api_secret_key,
            transport=runtime_transport,
        )
        page_hashes.append(page.raw_payload_hash)
        for article in page.articles:
            prior = articles_by_id.get(article.article_id)
            if prior is not None:
                if prior.content_hash != article.content_hash:
                    raise AlpacaNewsReadError(
                        "reopen pagination returned duplicate article id with changed content"
                    )
                continue
            articles_by_id[article.article_id] = article
        if len(articles_by_id) > MAX_REOPEN_NEWS_ARTICLES:
            raise AlpacaNewsReadError("reopen news article budget exceeded")
        if page.next_page_token is None:
            page_token = None
            pagination_complete = True
            break
        if page.next_page_token in seen_tokens:
            raise AlpacaNewsReadError("reopen Alpaca news page-token cycle detected")
        seen_tokens.add(page.next_page_token)
        page_token = page.next_page_token

    return AlpacaNewsReopenRead.build(
        pagination_version=ALPACA_NEWS_REOPEN_PAGINATION_VERSION,
        symbol=symbol,
        window_start=start,
        window_end=end,
        retrieved_at=datetime.now(UTC),
        page_size=page_size,
        page_count=len(page_hashes),
        max_pages=max_pages,
        articles=tuple(articles_by_id.values()),
        page_raw_payload_hashes=tuple(page_hashes),
        terminal_next_page_token=None if pagination_complete else page_token,
        pagination_complete=pagination_complete,
    )
