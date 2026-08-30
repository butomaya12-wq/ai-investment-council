from __future__ import annotations

from pathlib import Path

import pytest

from aic.research.reopen_remaining_gaps_scope import (
    EXPECTED_REASONS,
    PASS_STATUS,
    RemainingGapScopeError,
    _claim_view,
    _scope_group,
)


def _claim(*, claim_id: str, category: str, evidence_ids=(), computed_ids=()) -> dict:
    return {
        "claim_id": claim_id,
        "candidate_id": "NVDA",
        "category": category,
        "claim_text": f"Synthetic {category} claim",
        "claim_kind": "INFERENCE",
        "materiality": "SUPPORTING",
        "evidence_ids": list(evidence_ids),
        "computed_value_ids": list(computed_ids),
        "conflict_ids": [],
        "assumptions": [],
        "support_status": "SUPPORTED",
        "uncertainty_note": "Synthetic uncertainty.",
    }


def test_claim_view_preserves_support_lineage() -> None:
    raw = _claim(
        claim_id="C1",
        category="valuation_context",
        evidence_ids=("E1",),
        computed_ids=("V1",),
    )
    view = _claim_view(raw)
    assert view["claim_id"] == "C1"
    assert view["evidence_ids"] == ["E1"]
    assert view["computed_value_ids"] == ["V1"]
    assert view["support_status"] == "SUPPORTED"


def test_valuation_scope_detects_explicit_valuation_metric_signal() -> None:
    claim = _claim(claim_id="C1", category="valuation_context", computed_ids=("V1",))
    result = _scope_group(
        candidate="NVDA",
        category="valuation_context",
        group_ids=["C1"],
        claims_by_id={"C1": claim},
        evidence_by_id={},
        computed_by_id={
            "V1": {
                "computed_value_id": "V1",
                "metric_id": "price_to_earnings",
                "value": "31.0",
                "unit": "RATIO",
            }
        },
        shared_portfolio_context_refs=[],
    )
    assert result["support_lineage_resolved"] is True
    assert result["category_specific_reference_signal_detected"] is True
    assert result["gap_closed_by_this_inventory"] is False


def test_generic_growth_metric_does_not_fake_valuation_specific_signal() -> None:
    claim = _claim(claim_id="C1", category="valuation_context", computed_ids=("V1",))
    result = _scope_group(
        candidate="NVDA",
        category="valuation_context",
        group_ids=["C1"],
        claims_by_id={"C1": claim},
        evidence_by_id={},
        computed_by_id={
            "V1": {
                "computed_value_id": "V1",
                "metric_id": "annual_revenue_growth",
                "value": "0.25",
                "unit": "RATIO",
            }
        },
        shared_portfolio_context_refs=[],
    )
    assert result["category_specific_reference_signal_detected"] is False
    assert result["gap_closed_by_this_inventory"] is False


def test_portfolio_scope_detects_shared_portfolio_context_ref() -> None:
    claim = _claim(claim_id="P1", category="portfolio_interaction")
    result = _scope_group(
        candidate="NVDA",
        category="portfolio_interaction",
        group_ids=["P1"],
        claims_by_id={"P1": claim},
        evidence_by_id={},
        computed_by_id={},
        shared_portfolio_context_refs=["PORTFOLIO_EXPOSURE_SNAPSHOT_1"],
    )
    assert result["shared_portfolio_context_refs"] == ["PORTFOLIO_EXPOSURE_SNAPSHOT_1"]
    assert result["category_specific_reference_signal_detected"] is True
    assert result["gap_closed_by_this_inventory"] is False


def test_scope_fails_closed_on_unresolved_support_ref() -> None:
    claim = _claim(claim_id="C1", category="valuation_context", evidence_ids=("MISSING",))
    with pytest.raises(RemainingGapScopeError, match="support lineage cannot be resolved"):
        _scope_group(
            candidate="NVDA",
            category="valuation_context",
            group_ids=["C1"],
            claims_by_id={"C1": claim},
            evidence_by_id={},
            computed_by_id={},
            shared_portfolio_context_refs=[],
        )


def test_scope_fails_closed_on_claim_category_drift() -> None:
    claim = _claim(claim_id="C1", category="risk")
    with pytest.raises(RemainingGapScopeError, match="category/identity drift"):
        _scope_group(
            candidate="NVDA",
            category="valuation_context",
            group_ids=["C1"],
            claims_by_id={"C1": claim},
            evidence_by_id={},
            computed_by_id={},
            shared_portfolio_context_refs=[],
        )


def test_zero_call_runner_has_no_dispatch_surface() -> None:
    source = Path("scripts/b3_reopen_remaining_gaps_scope_zero_call_v01.py").read_text(encoding="utf-8")
    lowered = source.lower()
    assert "openai_api_key" not in lowered
    assert "alpaca data" not in lowered
    assert "urlopen" not in lowered
    assert "requests." not in lowered
    assert "execute-provider-read" not in lowered
    assert PASS_STATUS == "B3_REOPEN_REMAINING_GAPS_SCOPE_ZERO_CALL_PASS"
    assert EXPECTED_REASONS == (
        "VALUATION_SPECIFIC_EVIDENCE_MISSING",
        "PORTFOLIO_INTERACTION_EVIDENCE_MISSING",
    )
