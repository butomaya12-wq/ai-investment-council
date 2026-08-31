from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from aic.data.providers.alpaca_news import AlpacaNewsArticle
from aic.data.providers.alpaca_news_reopen import (
    ALPACA_NEWS_REOPEN_PAGINATION_VERSION,
    AlpacaNewsReopenRead,
)
from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_durable_provider_read_failure_reconciliation_v01 as v01
from aic.research import reopen_judge_durable_provider_read_failure_reconciliation_v02 as v02


HEAD = "b" * 40


def _synthetic_reopen_read() -> AlpacaNewsReopenRead:
    ts = datetime(2026, 8, 31, 8, 58, 17, tzinfo=UTC)
    article = AlpacaNewsArticle(
        article_id=1,
        headline="Synthetic NVDA headline",
        summary="Synthetic summary",
        content="Synthetic content",
        author="Synthetic Author",
        source="synthetic",
        url="https://example.com/nvda",
        symbols=("NVDA",),
        created_at=ts,
        updated_at=ts,
        content_hash="0" * 64,
    )
    return AlpacaNewsReopenRead.build(
        pagination_version=ALPACA_NEWS_REOPEN_PAGINATION_VERSION,
        symbol="NVDA",
        window_start=datetime(2026, 8, 28, 17, 34, tzinfo=UTC),
        window_end=ts,
        retrieved_at=datetime(2026, 8, 31, 9, 31, 21, 488444, tzinfo=UTC),
        page_size=5,
        page_count=2,
        max_pages=2,
        articles=(article,),
        page_raw_payload_hashes=("1" * 64, "2" * 64),
        terminal_next_page_token="token",
        pagination_complete=False,
    )


def test_v02_regression_uses_typed_model_hash_surface_not_json_dict_rehash() -> None:
    read = _synthetic_reopen_read()
    serialized = read.model_dump(mode="json")

    # This is the exact class of false negative in V01: canonical hashing of a
    # JSON-mode dict is not equivalent to canonical hashing of the typed model,
    # because datetime values have already been converted to strings.
    assert canonical_sha256(
        serialized,
        exclude_fields=("aggregate_payload_hash",),
    ) != read.aggregate_payload_hash

    reparsed = AlpacaNewsReopenRead.model_validate(serialized)
    assert reparsed.aggregate_payload_hash == read.aggregate_payload_hash


def test_v02_build_retains_partial_evidence_without_resolving_targets(monkeypatch) -> None:
    monkeypatch.setattr(
        v01,
        "verify_local_replay",
        lambda payload: v01.EXPECTED_LOCAL_REPLAY_HASH,
    )
    monkeypatch.setattr(
        v01,
        "verify_authorization",
        lambda payload: v01.EXPECTED_AUTHORIZATION_HASH,
    )
    monkeypatch.setattr(
        v01,
        "verify_journal",
        lambda rows: {
            "journal_event_count": 4,
            "provider_dispatch_attempt_count": 2,
            "provider_response_receipt_count": 2,
            "first_dispatch_event_hash": v01.EXPECTED_FIRST_DISPATCH_EVENT_HASH,
            "last_dispatch_event_hash": v01.EXPECTED_LAST_DISPATCH_EVENT_HASH,
        },
    )
    monkeypatch.setattr(
        v02,
        "verify_result_v02",
        lambda payload: {
            "result_artifact_hash": v01.EXPECTED_RESULT_HASH,
            "partial_response_artifact_hash": v01.EXPECTED_PARTIAL_RESPONSE_HASH,
            "nvda_aggregate_payload_hash": v01.EXPECTED_NVDA_AGGREGATE_HASH,
            "nvda_page_raw_payload_hashes": list(v01.EXPECTED_NVDA_PAGE_HASHES),
            "nvda_terminal_next_page_token": v01.EXPECTED_NVDA_TERMINAL_NEXT_PAGE_TOKEN,
            "nvda_retained_article_count": 10,
            "nvda_aggregate_validation_surface": "ALPACA_NEWS_REOPEN_TYPED_MODEL_VALIDATOR",
        },
    )

    artifact = v02.build_reconciliation_v02(
        local_replay={},
        authorization={},
        journal_rows=[],
        result={},
        code_commit_sha=HEAD,
    )

    assert artifact["status"] == v02.PASS_STATUS
    assert artifact["authority_consumed"] is True
    assert artifact["original_authority_reusable"] is False
    assert artifact["original_production_read_pass_rerun_allowed"] is False
    assert artifact["provider_dispatch_attempts_observed"] == 2
    assert artifact["provider_response_receipts_observed"] == 2
    assert artifact["retained_partial_evidence_usable"] is True
    assert artifact["retained_partial_evidence_complete"] is False
    assert artifact["nvda_aggregate_validation_surface"] == "ALPACA_NEWS_REOPEN_TYPED_MODEL_VALIDATOR"
    assert artifact["nvda_continuation_must_start_from_terminal_token"] is True
    assert artifact["nvda_replay_of_retained_pages_allowed"] is False
    assert artifact["nvda_max_additional_pages_without_expanding_original_total_bound"] == 4
    assert artifact["residual_external_read_target_count"] == 7
    assert artifact["resolved_target_count_this_step"] == 0
    assert artifact["provider_reads_this_step"] == 0
    assert artifact["provider_reads_authorized_this_step"] is False
    assert artifact["model_calls_this_step"] == 0
    assert artifact["model_calls_authorized_this_step"] is False
    assert artifact["broker_writes"] == 0
    assert artifact["alpaca_orders"] == 0
    assert artifact["next_gate"] == v02.NEXT_GATE
    assert artifact["artifact_hash"] == canonical_sha256(
        artifact,
        exclude_fields=("artifact_hash",),
    )


def test_v02_runner_is_zero_call_and_has_no_provider_execution_surface() -> None:
    source = Path(
        "scripts/b3_research_reopen_durable_provider_read_failure_reconciliation_zero_call_v02.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "--execute-provider-reads",
        "alpaca data",
        "alpaca position",
        "alpaca account",
        "OPENAI_API_KEY",
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
        "submit_order",
        "order submit",
    )
    for token in forbidden:
        assert token not in source


def test_v02_source_does_not_repeat_v01_json_dict_aggregate_rehash() -> None:
    source = Path(
        "src/aic/research/reopen_judge_durable_provider_read_failure_reconciliation_v02.py"
    ).read_text(encoding="utf-8")
    bad = 'canonical_sha256(response, exclude_fields=("aggregate_payload_hash",))'
    assert bad not in source
    assert "AlpacaNewsReopenRead.model_validate" in source
