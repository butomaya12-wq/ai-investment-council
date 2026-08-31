from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aic.data.providers.alpaca_cli_news import CLI_PROFILE_CREDENTIAL_PLACEHOLDER
from aic.data.providers.alpaca_news import AlpacaNewsReadError, _normalize_article
from aic.data.providers.alpaca_news_reopen import ALPACA_NEWS_REOPEN_PAGINATION_VERSION, AlpacaNewsReopenRead
from aic.data.providers.alpaca_news_reopen_continuation import (
    AlpacaNewsReopenContinuationRead,
    read_alpaca_news_continuation_from_saved_token,
)
from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_residual_external_read_continuation_runtime_v01 as runtime


START = datetime(2026, 8, 28, 17, 34, tzinfo=UTC)
END = datetime(2026, 8, 31, 8, 58, 17, tzinfo=UTC)


def _raw_article(article_id: int, *, symbol: str = "NVDA", content: str | None = None) -> dict:
    text = content if content is not None else f"content-{article_id}"
    return {
        "id": article_id,
        "headline": f"headline-{article_id}",
        "summary": f"summary-{article_id}",
        "content": text,
        "author": "AIC Test",
        "source": "Test Source",
        "url": f"https://example.com/{article_id}",
        "symbols": [symbol],
        "created_at": "2026-08-30T10:00:00Z",
        "updated_at": "2026-08-30T10:00:00Z",
    }


class FakeTransport:
    def __init__(self, pages: list[dict]):
        self.pages = list(pages)
        self.queries: list[dict[str, str]] = []

    def get(self, *, endpoint, query, api_key_id, api_secret_key):
        self.queries.append(dict(query))
        if not self.pages:
            raise AssertionError("unexpected extra provider page")
        return 200, json.dumps(self.pages.pop(0)).encode("utf-8")


def _read_continuation(*, pages: list[dict], retained_raw: list[dict] | None = None, max_pages: int = 4):
    retained = tuple(_normalize_article(row) for row in (retained_raw or []))
    transport = FakeTransport(pages)
    read = read_alpaca_news_continuation_from_saved_token(
        symbol="NVDA",
        window_start=START,
        window_end=END,
        research_cutoff=END,
        start_page_token="saved-token",
        retained_articles=retained,
        page_size=5,
        max_additional_pages=max_pages,
        api_key_id=CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
        api_secret_key=CLI_PROFILE_CREDENTIAL_PLACEHOLDER,
        transport=transport,
    )
    return read, transport


def test_continuation_starts_from_saved_token_and_never_replays_first_page():
    read, transport = _read_continuation(
        pages=[
            {"news": [_raw_article(101)], "next_page_token": "next-2"},
            {"news": [_raw_article(102)], "next_page_token": None},
        ],
        retained_raw=[_raw_article(1)],
    )
    assert transport.queries[0]["page_token"] == "saved-token"
    assert transport.queries[1]["page_token"] == "next-2"
    assert read.additional_page_count == 2
    assert read.retained_article_count == 1
    assert [row.article_id for row in read.new_articles] == [101, 102]
    assert read.total_article_count == 3
    assert read.pagination_complete is True
    assert read.terminal_next_page_token is None


def test_continuation_allows_identical_retained_duplicate_without_readding_it():
    retained = _raw_article(11)
    read, _transport = _read_continuation(
        pages=[{"news": [retained, _raw_article(12)], "next_page_token": None}],
        retained_raw=[retained],
    )
    assert [row.article_id for row in read.new_articles] == [12]
    assert read.total_article_count == 2


def test_continuation_rejects_changed_content_for_retained_article_id():
    retained = _raw_article(21, content="old")
    changed = _raw_article(21, content="changed")
    with pytest.raises(AlpacaNewsReadError, match="changed content"):
        _read_continuation(
            pages=[{"news": [changed], "next_page_token": None}],
            retained_raw=[retained],
        )


def test_continuation_nonterminal_after_four_pages_is_partial_not_transport_error():
    read, transport = _read_continuation(
        pages=[
            {"news": [_raw_article(31)], "next_page_token": "t2"},
            {"news": [_raw_article(32)], "next_page_token": "t3"},
            {"news": [_raw_article(33)], "next_page_token": "t4"},
            {"news": [_raw_article(34)], "next_page_token": "t5"},
        ],
        max_pages=4,
    )
    assert len(transport.queries) == 4
    assert read.additional_page_count == 4
    assert read.pagination_complete is False
    assert read.terminal_next_page_token == "t5"


def test_continuation_detects_cycle_seeded_by_saved_token():
    with pytest.raises(AlpacaNewsReadError, match="cycle"):
        _read_continuation(
            pages=[{"news": [_raw_article(41)], "next_page_token": "saved-token"}],
        )


def _synthetic_reopen_read(*, symbol: str, complete: bool) -> AlpacaNewsReopenRead:
    return AlpacaNewsReopenRead.build(
        pagination_version=ALPACA_NEWS_REOPEN_PAGINATION_VERSION,
        symbol=symbol,
        window_start=START,
        window_end=END,
        retrieved_at=END,
        page_size=5,
        page_count=1,
        max_pages=2,
        articles=(),
        page_raw_payload_hashes=("1" * 64,),
        terminal_next_page_token=None if complete else "more",
        pagination_complete=complete,
    )


def _synthetic_continuation_read() -> AlpacaNewsReopenContinuationRead:
    return AlpacaNewsReopenContinuationRead.build(
        continuation_version="B3_ALPACA_NEWS_REOPEN_CONTINUATION_FROM_SAVED_TOKEN_v0_1",
        symbol="NVDA",
        window_start=START,
        window_end=END,
        retrieved_at=END,
        page_size=5,
        start_page_token=runtime.EXPECTED_NVDA_START_TOKEN,
        additional_page_count=1,
        max_additional_pages=4,
        retained_article_count=10,
        new_articles=(),
        total_article_count=10,
        additional_page_raw_payload_hashes=("2" * 64,),
        terminal_next_page_token=None,
        pagination_complete=True,
    )


def test_execute_once_retains_partial_news_and_continues_through_all_bundles(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(runtime, "verify_continuation_preflight", lambda _payload: runtime.EXPECTED_CONTINUATION_PREFLIGHT_HASH)
    monkeypatch.setattr(runtime, "verify_original_preflight", lambda _payload: runtime.EXPECTED_ORIGINAL_PREFLIGHT_HASH)
    retained = _synthetic_reopen_read(symbol="NVDA", complete=False)
    retained = AlpacaNewsReopenRead.build(
        pagination_version=ALPACA_NEWS_REOPEN_PAGINATION_VERSION,
        symbol="NVDA",
        window_start=START,
        window_end=END,
        retrieved_at=END,
        page_size=5,
        page_count=2,
        max_pages=2,
        articles=(),
        page_raw_payload_hashes=("3" * 64, "4" * 64),
        terminal_next_page_token=runtime.EXPECTED_NVDA_START_TOKEN,
        pagination_complete=False,
    )
    monkeypatch.setattr(runtime, "verify_original_result", lambda _payload: {
        "result_artifact_hash": runtime.EXPECTED_ORIGINAL_RESULT_HASH,
        "retained_response_hash": runtime.EXPECTED_RETAINED_NVDA_EVIDENCE_HASH,
        "retained_typed_response": retained,
    })

    news_reads = iter([
        _synthetic_reopen_read(symbol="MSFT", complete=False),
        _synthetic_reopen_read(symbol="META", complete=True),
    ])
    monkeypatch.setattr(runtime, "read_alpaca_news_window_for_reopen", lambda **_kwargs: next(news_reads))
    monkeypatch.setattr(runtime, "read_alpaca_news_continuation_from_saved_token", lambda **_kwargs: _synthetic_continuation_read())

    class FakeCliTransport:
        def __init__(self, *args, **kwargs):
            pass
    monkeypatch.setattr(runtime, "ReopenAlpacaCliNewsTransport", FakeCliTransport)

    def fake_row(_preflight, bundle_id):
        if bundle_id in {"ER2_MSFT_NEWS_REFRESH", "ER3_META_NEWS_REFRESH"}:
            return {"resolved_request_contract": {
                "window_start_utc": "2026-08-28T17:34:00Z",
                "window_end_utc": runtime.EXPECTED_REOPEN_CUTOFF_UTC,
                "page_size": 5,
                "max_pages": 2,
            }}
        if bundle_id == "ER5_CURRENT_PORTFOLIO_EQUITY":
            return {"resolved_request_contract": {
                "start_utc": "2026-08-24T08:58:17Z",
                "end_utc": runtime.EXPECTED_REOPEN_CUTOFF_UTC,
                "timeframe": "1Day",
                "intraday_reporting": "market_hours",
            }}
        if bundle_id == "ER6_DYNAMIC_MARKET_CONTEXT":
            return {"resolved_request_contract": {
                "start_utc": "2026-07-17T08:58:17Z",
                "end_utc": runtime.EXPECTED_REOPEN_CUTOFF_UTC,
                "timeframe": "1Hour",
                "feed": "iex",
                "sort": "asc",
                "limit": 1000,
            }}
        raise AssertionError(bundle_id)
    monkeypatch.setattr(runtime, "_original_request_row", fake_row)

    cli_calls = []
    def fake_cli(*, bundle_id, command, journal, timeout_seconds=45):
        cli_calls.append(bundle_id)
        journal.attempt_count += 1
        if bundle_id == "CR3_CURRENT_PAPER_POSITIONS":
            return [], "5" * 64, "6" * 64
        if bundle_id == "CR4_CURRENT_PORTFOLIO_EQUITY":
            return {"equity": []}, "7" * 64, "8" * 64
        if bundle_id == "CR5_DYNAMIC_MARKET_CONTEXT":
            return {"bars": {}, "next_page_token": None}, "9" * 64, "a" * 64
        raise AssertionError(bundle_id)
    monkeypatch.setattr(runtime, "_run_cli_json", fake_cli)

    authorization = {
        "source_continuation_preflight_hash": runtime.EXPECTED_CONTINUATION_PREFLIGHT_HASH,
        "source_original_preflight_hash": runtime.EXPECTED_ORIGINAL_PREFLIGHT_HASH,
        "source_original_result_hash": runtime.EXPECTED_ORIGINAL_RESULT_HASH,
        "continuation_request_manifest_hash": runtime.EXPECTED_CONTINUATION_MANIFEST_HASH,
        "provider_dispatch_attempts_max": runtime.MAX_DISPATCH_ATTEMPTS,
        "model_calls_authorized": False,
        "broker_writes_authorized": False,
    }
    authorization["artifact_hash"] = canonical_sha256(authorization)

    result = runtime.execute_once(
        continuation_preflight={},
        original_preflight={},
        original_result={},
        authorization=authorization,
        journal_path=tmp_path / "journal.jsonl",
    )
    assert result["status"] == runtime.SUCCESS_STATUS
    assert [row["bundle_id"] for row in result["bundle_results"]] == list(runtime.BUNDLE_IDS)
    assert result["bundle_results"][0]["status"] == "PARTIAL_PAGINATION_BOUND"
    assert result["bundle_results"][1]["status"] == "PASS"
    assert result["bundle_results"][5]["status"] == "PASS"
    assert result["partial_bundle_ids"] == ["CR1_MSFT_NEWS_REFRESH"]
    assert cli_calls == [
        "CR3_CURRENT_PAPER_POSITIONS",
        "CR4_CURRENT_PORTFOLIO_EQUITY",
        "CR5_DYNAMIC_MARKET_CONTEXT",
    ]


def test_build_dry_binds_both_preflights_original_result_and_zero_authority(monkeypatch):
    monkeypatch.setattr(runtime, "verify_continuation_preflight", lambda _payload: runtime.EXPECTED_CONTINUATION_PREFLIGHT_HASH)
    monkeypatch.setattr(runtime, "verify_original_preflight", lambda _payload: runtime.EXPECTED_ORIGINAL_PREFLIGHT_HASH)
    monkeypatch.setattr(runtime, "verify_original_result", lambda _payload: {
        "result_artifact_hash": runtime.EXPECTED_ORIGINAL_RESULT_HASH,
        "retained_response_hash": runtime.EXPECTED_RETAINED_NVDA_EVIDENCE_HASH,
    })
    dry = runtime.build_dry(
        continuation_preflight={},
        original_preflight={},
        original_result={},
        code_commit_sha="f" * 40,
    )
    assert dry["source_continuation_preflight_hash"] == runtime.EXPECTED_CONTINUATION_PREFLIGHT_HASH
    assert dry["source_original_preflight_hash"] == runtime.EXPECTED_ORIGINAL_PREFLIGHT_HASH
    assert dry["source_original_result_hash"] == runtime.EXPECTED_ORIGINAL_RESULT_HASH
    assert dry["provider_dispatch_attempts_max"] == 11
    assert dry["provider_reads_authorized"] is False
    assert dry["model_calls_authorized"] is False
    assert dry["nvda_replay_retained_pages_allowed"] is False


def test_runner_dry_branch_precedes_any_authorization_or_provider_execution_surface():
    text = Path(
        "scripts/b3_research_reopen_execute_residual_external_read_continuation_v01.py"
    ).read_text(encoding="utf-8")
    dry_branch = text.index("if not args.execute_provider_reads:")
    auth_build = text.index("authorization = build_authorization(")
    execute_call = text.index("result = execute_once(")
    assert dry_branch < auth_build < execute_call
    assert "--execute-provider-reads" in text
    assert "--approve-continuation-preflight-hash" in text
    assert "--approve-continuation-request-manifest-hash" in text
