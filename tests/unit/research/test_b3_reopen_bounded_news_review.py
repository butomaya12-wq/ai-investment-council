from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research.models import AlpacaNewsWindowParameters, ResearchNeedType
from aic.research.reopen_bounded_news_review import (
    EXPECTED_GAP,
    PASS_STATUS,
    REPLACEMENT_REF,
    ReopenBoundedNewsReviewError,
    _load_receipt_events,
    _news_need_rows,
    _review_candidate,
)


CUTOFF = datetime(2026, 8, 28, 17, 34, tzinfo=UTC)


def _frozen_news_row(candidate: str) -> dict:
    return {
        "candidate_id": candidate,
        "need_id": f"NEWS_{candidate}",
        "question_id": "Q4_RECENT_DEVELOPMENTS",
        "max_items": 5,
        "window_start": CUTOFF - timedelta(days=30),
        "window_end": CUTOFF,
        "research_cutoff": CUTOFF,
        "expected_evidence_role": "Bounded recent-development context through the research cutoff.",
    }


def _historical_candidate(candidate: str, *, count: int = 5, pagination_complete: bool = False) -> dict:
    receipt_id = f"B3_NEWS_{candidate}_R1"
    receipt = {
        "provider_read_receipt_id": receipt_id,
        "provider": "ALPACA",
        "endpoint_class": "GET_NEWS_WINDOW",
        "request_start": "2026-08-28T17:34:01Z",
        "response_received_at": "2026-08-28T17:34:02Z",
        "request_parameters_hash": "a" * 64,
        "pagination_complete": pagination_complete,
        "raw_payload_hash": "b" * 64,
        "record_count": count,
        "http_status": 200,
        "error": None,
    }
    evidence = []
    for index in range(count):
        evidence.append(
            {
                "evidence_id": f"B3_NEWS_{candidate}_{index + 1}",
                "provider": "ALPACA",
                "source_type": "ALPACA_NEWS",
                "source_uri": f"https://example.com/{candidate}/{index + 1}",
                "request_parameters_ref": "c" * 64,
                "entity_id": candidate,
                "field_or_claim": "CURRENT_NEWS_CONTEXT",
                "raw_value_or_record_ref": f"alpaca_news:{index + 1}",
                "normalized_value": "{}",
                "published_at": "2026-08-28T16:00:00Z",
                "observed_at": "2026-08-28T16:30:00Z",
                "retrieved_at": "2026-08-28T17:34:02Z",
                "as_of": "2026-08-28T16:30:00Z",
                "freshness_rule_id": "B3_ALPACA_NEWS_CUTOFF_V1",
                "knowable_at_cutoff": True,
                "authoritative_for": ["CURRENT_NEWS_CONTEXT"],
                "conflict_group": None,
                "provider_read_receipt_id": receipt_id,
                "raw_content_hash": f"{index + 1:064x}",
                "normalization_version": "B3_ALPACA_NEWS_v0_2",
            }
        )
    return {
        "candidate": candidate,
        "provider_receipts": [receipt],
        "research_evidence": {"evidence_items": evidence},
    }


def test_bounded_top_n_is_satisfied_without_provider_dataset_exhaustion() -> None:
    reviewed = _review_candidate(
        frozen=_frozen_news_row("NVDA"),
        historical=_historical_candidate("NVDA", count=5, pagination_complete=False),
    )
    assert reviewed["bounded_request_satisfied"] is True
    assert reviewed["provider_dataset_exhausted"] is False
    assert reviewed["historical_news_evidence_count"] == 5
    assert reviewed["completeness_semantics"] == "TOP_N_BOUND_SATISFIED_ADDITIONAL_PROVIDER_RECORDS_EXIST"


def test_bounded_top_n_fails_when_provider_returns_less_than_bound_and_more_pages_exist() -> None:
    with pytest.raises(ReopenBoundedNewsReviewError, match="not satisfied"):
        _review_candidate(
            frozen=_frozen_news_row("NVDA"),
            historical=_historical_candidate("NVDA", count=4, pagination_complete=False),
        )


def test_provider_exhaustion_with_fewer_records_still_satisfies_bounded_need() -> None:
    reviewed = _review_candidate(
        frozen=_frozen_news_row("META"),
        historical=_historical_candidate("META", count=3, pagination_complete=True),
    )
    assert reviewed["bounded_request_satisfied"] is True
    assert reviewed["provider_dataset_exhausted"] is True
    assert reviewed["completeness_semantics"] == "PROVIDER_EXHAUSTED"


def test_news_need_rows_preserve_exact_frozen_bound_and_role() -> None:
    results = []
    for candidate in ("NVDA", "MSFT", "META"):
        need = SimpleNamespace(
            need_id=f"NEWS_{candidate}",
            question_id="Q4_RECENT_DEVELOPMENTS",
            need_type=ResearchNeedType.NEED_ALPACA_NEWS_WINDOW,
            parameters=AlpacaNewsWindowParameters(
                window_start=CUTOFF - timedelta(days=30),
                window_end=CUTOFF,
            ),
            max_items=5,
            expected_evidence_role="Bounded recent-development context through the research cutoff.",
        )
        plan = SimpleNamespace(
            candidate_id=candidate,
            requested_needs=(need,),
            research_cutoff=CUTOFF,
        )
        results.append(SimpleNamespace(research_plan=plan))
    rows = _news_need_rows(SimpleNamespace(results=tuple(results)))
    assert [row["candidate_id"] for row in rows] == ["NVDA", "MSFT", "META"]
    assert all(row["max_items"] == 5 for row in rows)
    assert all("Bounded" in row["expected_evidence_role"] for row in rows)


def test_receipt_replay_requires_self_hashed_durable_events(tmp_path: Path) -> None:
    events = []
    for index, event_name in enumerate(("PROVIDER_DISPATCH_ATTEMPT", "PROVIDER_RESPONSE_RECEIVED"), start=1):
        event = {
            "receipt_event_version": "B3_REOPEN_PAGINATED_PROVIDER_READ_RECEIPT_EVENT_v0_1",
            "event": event_name,
            "authority_hash": "a" * 64,
            "preflight_hash": "b" * 64,
            "global_dispatch_attempt": 1,
            "candidate_dispatch_attempt": 1,
            "candidate_id": "NVDA",
            "sequence": index,
        }
        event["receipt_hash"] = canonical_sha256(event)
        events.append(event)
    path = tmp_path / "receipts.jsonl"
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    loaded, hashes = _load_receipt_events(path)
    assert len(loaded) == 2
    assert hashes == [event["receipt_hash"] for event in events]

    events[0]["candidate_id"] = "MSFT"
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    with pytest.raises(ReopenBoundedNewsReviewError, match="self-hash mismatch"):
        _load_receipt_events(path)


def test_zero_call_runner_has_no_provider_or_model_dispatch_surface() -> None:
    source = Path("scripts/b3_reopen_bounded_news_zero_call_v01.py").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" not in source
    assert "alpaca data news" not in source.lower()
    assert "read_alpaca_news" not in source
    assert "execute-provider-read" not in source
    assert "urlopen" not in source
    assert PASS_STATUS == "B3_REOPEN_BOUNDED_NEWS_ZERO_CALL_PASS"
    assert EXPECTED_GAP == "ALPACA_NEWS_PAGINATION_INCOMPLETE"
    assert REPLACEMENT_REF == "ALPACA_NEWS_BOUNDED_TOP_N_SATISFIED"
