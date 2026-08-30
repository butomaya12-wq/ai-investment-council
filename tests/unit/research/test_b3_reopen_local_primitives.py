from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from aic.research.reopen_local_primitives import (
    PASS_STATUS,
    _portfolio_discoveries,
    _valuation_primitives,
)


RESEARCH_CUTOFF = datetime(2026, 8, 28, 17, 34, tzinfo=UTC)
B2_CUTOFF = datetime(2026, 8, 27, 20, 0, tzinfo=UTC)


def _retrieval_row(*rows: dict) -> dict:
    return {"research_evidence": {"evidence_items": list(rows)}}


def test_sec_price_wording_is_disqualified_but_eps_fragment_is_retained() -> None:
    sec = {
        "evidence_id": "B3_SEC_MSFT_MDA_1",
        "provider": "SEC",
        "source_type": "SEC_FILING_SECTION",
        "source_uri": "https://www.sec.gov/Archives/edgar/data/789019/example.htm",
        "field_or_claim": "MD&A",
        "normalized_value": (
            "Diluted earnings per share was $13.64 for the year. "
            "The market price of our common stock may fluctuate significantly."
        ),
        "raw_value_or_record_ref": "section:MD&A",
        "authoritative_for": ["B3_QUALITATIVE_SEC_RESEARCH"],
        "as_of": "2026-07-30T20:00:00Z",
    }
    reviewed = _valuation_primitives("MSFT", _retrieval_row(sec), research_cutoff=RESEARCH_CUTOFF)
    assert reviewed["diluted_eps_candidate_fragment_count"] >= 1
    assert reviewed["eligible_local_market_price_candidate_count"] == 0
    assert reviewed["disqualified_sec_price_fragment_count"] >= 1
    assert all(
        row["classification"] == "SEC_TEXT_NOT_CUTOFF_MARKET_FEED"
        for row in reviewed["disqualified_sec_price_fragments"]
    )


def test_non_sec_market_bar_can_supply_local_price_candidate() -> None:
    bar = {
        "evidence_id": "B2_MARKET_MSFT_CLOSE_20260827",
        "provider": "ALPACA",
        "source_type": "ALPACA_BAR",
        "source_uri": "alpaca://bars/MSFT",
        "field_or_claim": "close",
        "normalized_value": "512.34",
        "raw_value_or_record_ref": "bar:2026-08-27",
        "authoritative_for": ["POINT_IN_TIME_MARKET_PRICE"],
        "as_of": "2026-08-27T20:00:00Z",
    }
    reviewed = _valuation_primitives("MSFT", _retrieval_row(bar), research_cutoff=RESEARCH_CUTOFF)
    assert reviewed["eligible_local_market_price_candidate_count"] == 1
    assert reviewed["local_market_price_status"] == "LOCAL_MARKET_PRICE_RESOLVED"


def test_market_evidence_after_research_cutoff_is_rejected() -> None:
    bar = {
        "evidence_id": "FUTURE_BAR",
        "provider": "ALPACA",
        "source_type": "ALPACA_BAR",
        "field_or_claim": "close",
        "normalized_value": "999.00",
        "raw_value_or_record_ref": "bar:future",
        "authoritative_for": ["POINT_IN_TIME_MARKET_PRICE"],
        "as_of": "2026-08-29T20:00:00Z",
    }
    reviewed = _valuation_primitives("META", _retrieval_row(bar), research_cutoff=RESEARCH_CUTOFF)
    assert reviewed["eligible_local_market_price_candidate_count"] == 0


def test_portfolio_discovery_distinguishes_historical_from_current(tmp_path: Path) -> None:
    historical = {
        "portfolio_snapshot_ref": "PORTFOLIO_20260827",
        "snapshot_as_of": "2026-08-27T19:55:00Z",
        "equity": "100000",
        "positions": [
            {"symbol": "META", "qty": "2", "market_value": "1500"},
            {"symbol": "MSFT", "qty": "1", "market_value": "500"},
        ],
    }
    current = {
        "portfolio_snapshot_ref": "PORTFOLIO_20260830",
        "snapshot_as_of": "2026-08-30T12:00:00Z",
        "positions": [{"symbol": "META", "qty": "10"}],
    }
    (tmp_path / "historical.json").write_text(json.dumps(historical), encoding="utf-8")
    (tmp_path / "current.json").write_text(json.dumps(current), encoding="utf-8")
    found = _portfolio_discoveries(roots=(tmp_path,), b2_cutoff=B2_CUTOFF)
    assert len(found) == 2
    by_ref = {row["portfolio_snapshot_ref"]: row for row in found}
    assert by_ref["PORTFOLIO_20260827"]["at_or_before_b2_cutoff"] is True
    assert by_ref["PORTFOLIO_20260827"]["meta_position_present"] is True
    assert by_ref["PORTFOLIO_20260830"]["at_or_before_b2_cutoff"] is False


def test_zero_call_runner_exposes_no_provider_or_model_execution_surface() -> None:
    source = Path("scripts/b3_reopen_local_valuation_and_portfolio_primitives_zero_call_v01.py").read_text(
        encoding="utf-8"
    )
    lowered = source.lower()
    assert "openai_api_key" not in lowered
    assert "execute-provider-read" not in lowered
    assert "urlopen" not in lowered
    assert "alpaca data" not in lowered
    assert "curl " not in lowered
    assert "submit_order" not in lowered
    assert "create_order" not in lowered
    assert "orders.create" not in lowered
    assert PASS_STATUS == "B3_REOPEN_LOCAL_VALUATION_AND_PORTFOLIO_PRIMITIVES_ZERO_CALL_PASS"
