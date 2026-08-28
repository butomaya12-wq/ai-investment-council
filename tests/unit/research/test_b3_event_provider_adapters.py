from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aic.research.handoff import load_real_event_handoff
from aic.research.provider_adapters import (
    AlpacaNewsRetrievalAdapter,
    EventRetrievalAdapterError,
    SecFilingRetrievalAdapter,
)
from aic.research.retrieve import (
    RetrievalAction,
    RetrievalExecutionStatus,
    RetrievalProvider,
    RetrievalRequest,
)


HANDOFF_PATH = Path("config/event/b2_real_event_handoff_v0_1.json")
CUTOFF = datetime(2026, 8, 28, 17, 34, tzinfo=UTC)


class _SecTransport:
    def get(self, *, url, user_agent, accept):
        if "submissions/CIK" in url:
            return 200, json.dumps(
                {
                    "filings": {
                        "recent": {
                            "accessionNumber": ["0001045810-26-000021"],
                            "acceptanceDateTime": ["2026-02-25T21:00:00Z"],
                        }
                    }
                }
            ).encode("utf-8")
        body = "Material risk disclosure and operating uncertainty. " * 30
        return 200, (
            "<html><body><h2>ITEM 1A. RISK FACTORS</h2><p>"
            + body
            + "</p><h2>ITEM 1B. UNRESOLVED STAFF COMMENTS</h2></body></html>"
        ).encode("utf-8")


class _NewsTransport:
    def get(self, *, endpoint, query, api_key_id, api_secret_key):
        payload = {
            "news": [
                {
                    "id": 321,
                    "headline": "Bounded NVDA news",
                    "author": "Reporter",
                    "created_at": "2026-08-28T15:00:00Z",
                    "updated_at": "2026-08-28T15:05:00Z",
                    "summary": "Summary",
                    "content": "Content",
                    "url": "https://example.com/provider-returned-news",
                    "symbols": ["NVDA"],
                    "source": "TestSource",
                }
            ],
            "next_page_token": "more",
        }
        return 200, json.dumps(payload).encode("utf-8")


def test_sec_adapter_uses_frozen_handoff_source_and_emits_bound_receipt() -> None:
    handoff = load_real_event_handoff(HANDOFF_PATH)
    candidate = handoff.candidate("NVDA")
    request = RetrievalRequest.build(
        dispatch_version="B3_RETRIEVAL_DISPATCH_v0_1",
        research_plan_id="plan-nvda",
        candidate_id="NVDA",
        need_id="need-sec",
        question_id="q1",
        provider=RetrievalProvider.SEC,
        action=RetrievalAction.GET_FILING_SECTIONS,
        parameters={
            "filing_accession": candidate.sec_accession,
            "sections": ("Risk Factors",),
        },
        max_items=1,
    )
    result = SecFilingRetrievalAdapter(
        handoff=handoff,
        user_agent="AIC test contact@example.com",
        transport=_SecTransport(),
    ).execute(request=request, research_cutoff=CUTOFF)

    assert result.status is RetrievalExecutionStatus.COMPLETE
    assert result.receipt.provider == "SEC"
    assert result.evidence_items[0].source_uri == candidate.sec_source_uri
    assert result.evidence_items[0].provider_read_receipt_id == result.receipt.provider_read_receipt_id


def test_sec_adapter_rejects_model_or_request_accession_drift_before_network() -> None:
    handoff = load_real_event_handoff(HANDOFF_PATH)
    request = RetrievalRequest.build(
        dispatch_version="B3_RETRIEVAL_DISPATCH_v0_1",
        research_plan_id="plan-nvda",
        candidate_id="NVDA",
        need_id="need-sec",
        question_id="q1",
        provider=RetrievalProvider.SEC,
        action=RetrievalAction.GET_FILING_SECTIONS,
        parameters={
            "filing_accession": "0000000000-00-000000",
            "sections": ("Risk Factors",),
        },
        max_items=1,
    )
    with pytest.raises(EventRetrievalAdapterError, match="frozen B2 event handoff"):
        SecFilingRetrievalAdapter(
            handoff=handoff,
            user_agent="AIC test contact@example.com",
            transport=_SecTransport(),
        ).execute(request=request, research_cutoff=CUTOFF)


def test_news_adapter_preserves_provider_url_as_evidence_and_partial_pagination() -> None:
    request = RetrievalRequest.build(
        dispatch_version="B3_RETRIEVAL_DISPATCH_v0_1",
        research_plan_id="plan-nvda",
        candidate_id="NVDA",
        need_id="need-news",
        question_id="q1",
        provider=RetrievalProvider.ALPACA,
        action=RetrievalAction.GET_NEWS_WINDOW,
        parameters={
            "candidate_id": "NVDA",
            "window_start": CUTOFF - timedelta(days=3),
            "window_end": CUTOFF,
        },
        max_items=1,
    )
    result = AlpacaNewsRetrievalAdapter(
        api_key_id="test-key",
        api_secret_key="test-secret",
        transport=_NewsTransport(),
    ).execute(request=request, research_cutoff=CUTOFF)

    assert result.status is RetrievalExecutionStatus.PARTIAL
    assert result.receipt.pagination_complete is False
    assert result.evidence_items[0].source_uri == "https://example.com/provider-returned-news"
    assert result.evidence_items[0].field_or_claim == "CURRENT_NEWS_CONTEXT"
