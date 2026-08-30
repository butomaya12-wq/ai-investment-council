from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_minimal_external_preflight as module
from aic.research.reopen_minimal_external_preflight import (
    PASS_STATUS,
    MinimalExternalReadPreflightError,
    _select_eps,
    build_minimal_external_read_preflight,
    inspect_alpaca_cli_help,
)


def _review(candidate: str) -> dict:
    if candidate == "MSFT":
        fragment = (
            "RESULTS OF OPERATIONS (In millions, except percentages and per share amounts) "
            "2026 2025 Percentage Change Revenue $331,839 $281,724 18% "
            "Diluted earnings per share 17.95 13.64 32% "
            "Adjusted diluted earnings per share (non-GAAP) 17.28 14.13 22%"
        )
        evidence_id = "B3_SEC_MSFT_N3_SEC_MDA_1"
    else:
        fragment = (
            "Net income was $60.46 billion, with diluted earnings per share (EPS) of $23.49 "
            "for the year ended December 31, 2025."
        )
        evidence_id = "B3_SEC_META_META_N3_SEC_MDA_1"
    return {
        "candidate_id": candidate,
        "diluted_eps_candidate_fragments": [
            {"evidence_id": evidence_id, "fragment": fragment}
        ],
    }


def _primitives_payload() -> dict:
    body = {
        "artifact_version": "B3_REOPEN_LOCAL_VALUATION_AND_PORTFOLIO_PRIMITIVES_v0_1",
        "status": module.EXPECTED_PRIMITIVES_STATUS,
        "code_commit_sha": "e" * 40,
        "source_evidence_plan_hash": module.EXPECTED_EVIDENCE_PLAN_HASH,
        "source_remaining_gaps_scope_hash": module.EXPECTED_SCOPE_HASH,
        "target_candidates": ["MSFT", "META"],
        "historical_portfolio_candidate_count": 0,
        "valuation_primitive_reviews": [_review("MSFT"), _review("META")],
        "external_need_summary": {
            "external_reads_authorized": False,
            "historical_portfolio_reconstruction_needed": True,
            "point_in_time_market_price_local_disambiguation_candidates": [],
            "point_in_time_market_price_read_candidates": ["MSFT", "META"],
            "primary_filing_denominator_read_candidates": [],
        },
        "provider_reads_authorized": False,
        "model_calls_authorized": False,
        "planned_provider_reads_at_this_gate": 0,
        "planned_model_calls_at_this_gate": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    body["artifact_hash"] = canonical_sha256(body)
    return body


def _help_runner(command, **kwargs):
    joined = " ".join(command)
    if "data multi-bars" in joined:
        text = "--symbols --start --end --timeframe --limit --feed --sort"
    elif "account activity list" in joined:
        text = "--after --until --direction --page-size --page-token"
    elif "account portfolio" in joined:
        text = "--start --end --timeframe --intraday-reporting"
    elif "position list" in joined:
        text = "List open positions"
    else:
        text = ""
    return subprocess.CompletedProcess(command, 0, stdout=text.encode(), stderr=b"")


def test_deterministic_eps_selector_uses_gaap_annual_values() -> None:
    msft = _select_eps("MSFT", _review("MSFT"))
    meta = _select_eps("META", _review("META"))
    assert msft["value"] == "17.95"
    assert msft["fiscal_period"] == "FY2026"
    assert meta["value"] == "23.49"
    assert meta["fiscal_period"] == "FY2025"
    assert "REJECT_ADJUSTED_NON_GAAP" in msft["selection_rule"]


def test_eps_selector_does_not_promote_adjustment_only_value() -> None:
    review = {
        "candidate_id": "MSFT",
        "diluted_eps_candidate_fragments": [
            {
                "evidence_id": "X",
                "fragment": "OpenAI gains increased diluted EPS by $0.67.",
            }
        ],
    }
    with pytest.raises(MinimalExternalReadPreflightError, match="GAAP annual EPS selection failed"):
        _select_eps("MSFT", review)


def test_cli_help_inspection_is_local_and_requires_exact_flags() -> None:
    inspected = inspect_alpaca_cli_help(
        which=lambda _: "/opt/homebrew/bin/alpaca",
        runner=_help_runner,
    )
    assert inspected["alpaca_cli_path"] == "/opt/homebrew/bin/alpaca"
    assert set(inspected["cli_help_checks"]) == {
        "market_multi_bars",
        "current_positions",
        "account_activities",
        "portfolio_history",
    }


def test_preflight_freezes_exact_four_read_plan(tmp_path: Path, monkeypatch) -> None:
    payload = _primitives_payload()
    source = tmp_path / "primitives.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(module, "EXPECTED_PRIMITIVES_HASH", payload["artifact_hash"])
    artifact = build_minimal_external_read_preflight(
        code_commit_sha="a" * 40,
        primitives_path=source,
        which=lambda _: "/opt/homebrew/bin/alpaca",
        runner=_help_runner,
    )
    assert artifact["status"] == PASS_STATUS
    assert artifact["planned_provider_reads_max"] == 4
    assert artifact["provider_reads_authorized"] is False
    assert [row["read_id"] for row in artifact["provider_read_plan"]] == [
        "R1_CURRENT_POSITIONS_ANCHOR",
        "R2_POST_CUTOFF_ACCOUNT_ACTIVITIES_FIRST_PAGE",
        "R3_B2_CUTOFF_PORTFOLIO_EQUITY",
        "R4_MSFT_META_POINT_IN_TIME_BARS",
    ]
    activities = artifact["provider_read_plan"][1]
    assert activities["page_size"] == 100
    assert activities["max_pages"] == 1
    assert activities["pagination_continuation_authorized"] is False
    bars = artifact["provider_read_plan"][3]
    assert bars["symbols"] == ["MSFT", "META"]
    assert bars["feed"] == "iex"
    assert bars["limit"] == 1000
    assert bars["max_pages"] == 1
    assert bars["pagination_continuation_authorized"] is False
    assert artifact["valuation_metric_contract"]["metric"] == "PRICE_TO_LATEST_REPORTED_ANNUAL_DILUTED_EPS"
    assert artifact["portfolio_reconstruction_contract"]["current_positions_are_not_a_cutoff_substitute"] is True


def test_primitives_with_local_market_price_scope_drift_is_rejected(tmp_path: Path, monkeypatch) -> None:
    payload = _primitives_payload()
    payload["external_need_summary"]["point_in_time_market_price_read_candidates"] = ["MSFT"]
    payload["artifact_hash"] = canonical_sha256(payload, exclude_fields=("artifact_hash",))
    source = tmp_path / "primitives.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(module, "EXPECTED_PRIMITIVES_HASH", payload["artifact_hash"])
    with pytest.raises(MinimalExternalReadPreflightError, match="market-price read candidate scope drift"):
        build_minimal_external_read_preflight(
            code_commit_sha="a" * 40,
            primitives_path=source,
            which=lambda _: "/opt/homebrew/bin/alpaca",
            runner=_help_runner,
        )


def test_zero_call_runner_has_no_external_execution_surface() -> None:
    source = Path("scripts/b3_reopen_minimal_external_read_preflight_zero_call_v01.py").read_text(
        encoding="utf-8"
    ).lower()
    assert "openai_api_key" not in source
    assert "execute-provider-read" not in source
    assert "urlopen" not in source
    assert "order submit" not in source
    assert "position list" not in source
    assert "account activity" not in source
    assert "data multi-bars" not in source
