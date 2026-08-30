from __future__ import annotations

import json
from pathlib import Path

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research.reopen_paginated_read import (
    BLOCKED_STATUS,
    PARTIAL_STATUS,
    SUCCESS_STATUS,
    ReopenPaginatedReadError,
    execute_paginated_provider_reads,
    load_approved_preflight,
    load_read_authority,
)


AUTHORITY_PATH = Path("config/event/b3_reopen_paginated_read_authority_v1.json")


def _preflight(authority, *, page_size: int = 2) -> dict:
    rows = []
    for candidate in authority.approved_candidate_ids:
        rows.append(
            {
                "candidate_id": candidate,
                "need_id": f"{candidate}_NEWS",
                "window_start": "2026-08-01T00:00:00Z",
                "window_end": "2026-08-28T17:34:00Z",
                "research_cutoff": "2026-08-28T17:34:00Z",
                "page_size": page_size,
                "max_pages": 6,
                "planned_provider_reads_max": 6,
            }
        )
    payload = {
        "artifact_version": "B3_REOPEN_PAGINATION_ZERO_CALL_PREFLIGHT_v0_1",
        "status": "B3_REOPEN_PAGINATION_ZERO_CALL_ENGINEERING_PASS",
        "code_commit_sha": "a" * 40,
        "source_s00_artifact_hash": authority.source_s00_artifact_hash,
        "source_production_judge_result_hash": authority.source_production_judge_result_hash,
        "source_research_reopen_request_hash": authority.source_research_reopen_request_hash,
        "required_source_ref_ids": list(authority.required_source_ref_ids),
        "frozen_planner_artifact_hash": "b" * 64,
        "candidate_news_windows": rows,
        "pagination_engineering_version": "B3_ALPACA_NEWS_REOPEN_PAGINATION_v0_1",
        "max_pages_per_candidate": 6,
        "planned_provider_reads_max": 18,
        "provider_reads_authorized": False,
        "next_gate": "B3_REOPEN_PAGINATED_PROVIDER_READ_OWNER_APPROVAL",
        "alpaca_cli_path": "/opt/homebrew/bin/alpaca",
        "alpaca_news_help_sha256": "c" * 64,
        "required_news_flags": [
            "--symbols",
            "--start",
            "--end",
            "--limit",
            "--include-content",
            "--exclude-contentless",
            "--page-token",
        ],
        "page_token_flag_present": True,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    payload["artifact_hash"] = canonical_sha256(payload)
    return payload


class _FakeTransport:
    def __init__(self, candidate: str, *, pages: int = 2, fail_on: int | None = None):
        self.candidate = candidate
        self.pages = pages
        self.fail_on = fail_on
        self.calls = 0

    def get(self, *, endpoint, query, api_key_id, api_secret_key):
        self.calls += 1
        if self.fail_on == self.calls:
            raise RuntimeError("synthetic provider failure")
        article_id = {"NVDA": 100, "MSFT": 200, "META": 300}[self.candidate] + self.calls
        next_token = f"{self.candidate}-p{self.calls + 1}" if self.calls < self.pages else None
        payload = {
            "news": [
                {
                    "id": article_id,
                    "headline": f"{self.candidate} synthetic page {self.calls}",
                    "summary": "Summary",
                    "content": "Content",
                    "author": "Reporter",
                    "source": "Synthetic",
                    "url": f"https://example.com/{self.candidate}/{article_id}",
                    "symbols": [self.candidate],
                    "created_at": "2026-08-28T16:00:00Z",
                    "updated_at": "2026-08-28T16:30:00Z",
                }
            ],
            "next_page_token": next_token,
        }
        return 200, json.dumps(payload).encode("utf-8")


def test_event_authority_matches_owner_approved_scope() -> None:
    authority = load_read_authority(AUTHORITY_PATH)
    assert authority.source_zero_call_preflight_hash == (
        "b490dcef9b1248ed1cf79444f89d024bd50bbe59ed7945e2b69068b3565614c2"
    )
    assert authority.approved_candidate_ids == ("NVDA", "MSFT", "META")
    assert authority.approved_provider_dispatch_attempts_max == 18
    assert authority.approved_auth_mode == "CLI_PROFILE:paper"
    assert authority.model_calls_authorized == 0
    assert authority.broker_writes_authorized == 0
    assert authority.alpaca_orders_authorized == 0
    assert authority.live_money == "PROHIBITED"


def test_preflight_loader_rejects_unapproved_hash(tmp_path: Path) -> None:
    authority = load_read_authority(AUTHORITY_PATH)
    preflight = _preflight(authority)
    path = tmp_path / "preflight.json"
    path.write_text(json.dumps(preflight), encoding="utf-8")
    with pytest.raises(ReopenPaginatedReadError, match="not owner-approved"):
        load_approved_preflight(path, authority=authority)


def test_execute_complete_writes_durable_receipts_and_stays_within_budget(tmp_path: Path) -> None:
    authority = load_read_authority(AUTHORITY_PATH)
    preflight = _preflight(authority)
    authority = authority.model_copy(update={"source_zero_call_preflight_hash": preflight["artifact_hash"]})
    transports = {}

    def factory(candidate):
        transports[candidate] = _FakeTransport(candidate, pages=2)
        return transports[candidate]

    result = execute_paginated_provider_reads(
        authority=authority,
        preflight=preflight,
        code_commit_sha="d" * 40,
        authorization_path=tmp_path / "authorization.json",
        receipts_path=tmp_path / "receipts.jsonl",
        result_path=tmp_path / "result.json",
        base_transport_factory=factory,
    )
    assert result["status"] == SUCCESS_STATUS
    assert result["dispatch_attempts"] == 6
    assert result["provider_reads"] == 6
    assert result["provider_read_authorization_consumed"] is True
    assert result["gap_closed"] is True
    assert result["model_calls"] == 0
    assert result["broker_writes"] == 0
    assert result["alpaca_orders"] == 0
    lines = (tmp_path / "receipts.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 12
    events = [json.loads(line) for line in lines]
    assert sum(event["event"] == "PROVIDER_DISPATCH_ATTEMPT" for event in events) == 6
    assert sum(event["event"] == "PROVIDER_RESPONSE_RECEIVED" for event in events) == 6


def test_execute_partial_never_exceeds_eighteen_provider_attempts(tmp_path: Path) -> None:
    authority = load_read_authority(AUTHORITY_PATH)
    preflight = _preflight(authority, page_size=1)
    authority = authority.model_copy(update={"source_zero_call_preflight_hash": preflight["artifact_hash"]})

    result = execute_paginated_provider_reads(
        authority=authority,
        preflight=preflight,
        code_commit_sha="e" * 40,
        authorization_path=tmp_path / "authorization.json",
        receipts_path=tmp_path / "receipts.jsonl",
        result_path=tmp_path / "result.json",
        base_transport_factory=lambda candidate: _FakeTransport(candidate, pages=9),
    )
    assert result["status"] == PARTIAL_STATUS
    assert result["dispatch_attempts"] == 18
    assert result["gap_closed"] is False
    assert all(row["page_count"] == 6 for row in result["candidate_results"])
    assert all(row["pagination_complete"] is False for row in result["candidate_results"])


def test_first_provider_failure_blocks_without_retrying(tmp_path: Path) -> None:
    authority = load_read_authority(AUTHORITY_PATH)
    preflight = _preflight(authority)
    authority = authority.model_copy(update={"source_zero_call_preflight_hash": preflight["artifact_hash"]})
    created = {}

    def factory(candidate):
        transport = _FakeTransport(candidate, pages=2, fail_on=1)
        created[candidate] = transport
        return transport

    result = execute_paginated_provider_reads(
        authority=authority,
        preflight=preflight,
        code_commit_sha="f" * 40,
        authorization_path=tmp_path / "authorization.json",
        receipts_path=tmp_path / "receipts.jsonl",
        result_path=tmp_path / "result.json",
        base_transport_factory=factory,
    )
    assert result["status"] == BLOCKED_STATUS
    assert result["dispatch_attempts"] == 1
    assert result["provider_read_authorization_consumed"] is True
    assert created["NVDA"].calls == 1
    assert "MSFT" not in created
    assert result["rerun_authorized"] is False


def test_existing_evidence_path_blocks_before_dispatch(tmp_path: Path) -> None:
    authority = load_read_authority(AUTHORITY_PATH)
    preflight = _preflight(authority)
    authority = authority.model_copy(update={"source_zero_call_preflight_hash": preflight["artifact_hash"]})
    existing = tmp_path / "authorization.json"
    existing.write_text("{}", encoding="utf-8")
    with pytest.raises(ReopenPaginatedReadError, match="must be fresh"):
        execute_paginated_provider_reads(
            authority=authority,
            preflight=preflight,
            code_commit_sha="1" * 40,
            authorization_path=existing,
            receipts_path=tmp_path / "receipts.jsonl",
            result_path=tmp_path / "result.json",
            base_transport_factory=lambda candidate: _FakeTransport(candidate),
        )


def test_provider_read_script_has_no_model_or_trading_execution_surface() -> None:
    source = Path("scripts/b3_reopen_paginated_provider_read_v01.py").read_text(encoding="utf-8").lower()
    assert "openai" not in source
    assert "responses.create" not in source
    assert "submit_order" not in source
    assert "place_order" not in source
    assert "alpaca.trading" not in source
    assert "tradingclient" not in source
