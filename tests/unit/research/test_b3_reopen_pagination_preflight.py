from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research.models import AlpacaNewsWindowParameters, ResearchNeedType
from aic.research.reopen_pagination_preflight import (
    PREFLIGHT_STATUS,
    ReopenPaginationPreflightError,
    build_candidate_news_windows,
    inspect_alpaca_news_help,
    load_s00_artifact,
)


def _s00_payload() -> dict:
    payload = {
        "status": "B3_RESEARCH_REOPEN_S00_LINKED",
        "source_production_judge_result_hash": "a" * 64,
        "source_research_reopen_request_hash": "b" * 64,
        "required_source_ref_ids": ["ALPACA_NEWS_PAGINATION_INCOMPLETE"],
        "new_run_start_state": "S00",
        "next_lifecycle": "B3_RESEARCH_REOPEN_LINKED_S00",
        "next_gate": "B3_REOPEN_PAGINATION_ZERO_CALL_ENGINEERING",
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    payload["artifact_hash"] = canonical_sha256(payload)
    return payload


def test_s00_loader_accepts_only_bound_zero_call_artifact(tmp_path: Path) -> None:
    payload = _s00_payload()
    path = tmp_path / "s00.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_s00_artifact(path)
    assert loaded["artifact_hash"] == payload["artifact_hash"]

    payload["provider_reads"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ReopenPaginationPreflightError, match="self-hash mismatch"):
        load_s00_artifact(path)


def test_candidate_windows_preserve_exact_frozen_news_windows() -> None:
    cutoff = datetime(2026, 8, 28, 17, 34, tzinfo=UTC)
    results = []
    for candidate in ("NVDA", "MSFT", "META"):
        need = SimpleNamespace(
            need_id=f"NEWS_{candidate}",
            need_type=ResearchNeedType.NEED_ALPACA_NEWS_WINDOW,
            parameters=AlpacaNewsWindowParameters(
                window_start=cutoff - timedelta(days=7),
                window_end=cutoff,
            ),
            max_items=5,
        )
        plan = SimpleNamespace(
            candidate_id=candidate,
            requested_needs=(need,),
            research_cutoff=cutoff,
        )
        results.append(SimpleNamespace(research_plan=plan))
    rows = build_candidate_news_windows(SimpleNamespace(results=tuple(results)))
    assert [row["candidate_id"] for row in rows] == ["NVDA", "MSFT", "META"]
    assert all(row["page_size"] == 5 for row in rows)
    assert all(row["max_pages"] == 6 for row in rows)
    assert all(row["planned_provider_reads_max"] == 6 for row in rows)


def test_cli_help_is_zero_call_capability_check_and_requires_page_token() -> None:
    help_text = " ".join(
        (
            "--symbols",
            "--start",
            "--end",
            "--limit",
            "--include-content",
            "--exclude-contentless",
            "--page-token",
        )
    ).encode("utf-8")

    def runner(command, **kwargs):
        assert command == ["/usr/local/bin/alpaca", "data", "news", "--help"]
        return subprocess.CompletedProcess(command, 0, stdout=help_text, stderr=b"")

    result = inspect_alpaca_news_help(
        which=lambda executable: "/usr/local/bin/alpaca",
        runner=runner,
    )
    assert result["page_token_flag_present"] is True
    assert len(result["alpaca_news_help_sha256"]) == 64

    def bad_runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=help_text.replace(b"--page-token", b"--other-token"),
            stderr=b"",
        )

    with pytest.raises(ReopenPaginationPreflightError, match="--page-token"):
        inspect_alpaca_news_help(
            which=lambda executable: "/usr/local/bin/alpaca",
            runner=bad_runner,
        )


def test_zero_call_script_has_no_provider_or_model_dispatch_surface() -> None:
    source = Path("scripts/b3_reopen_pagination_zero_call_v01.py").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" not in source
    assert "urlopen" not in source
    assert "read_alpaca_news_window_for_reopen" not in source
    assert "execute_paid" not in source
    assert "order submit" not in source.lower()
    assert PREFLIGHT_STATUS == "B3_REOPEN_PAGINATION_ZERO_CALL_ENGINEERING_PASS"
