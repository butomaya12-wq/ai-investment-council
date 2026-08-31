from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_local_replay_v01 as replay
from aic.research import reopen_remaining_gaps_closure_v02 as closure_v02


HEAD = "a" * 40


def _inventory() -> dict:
    rows = []
    for target_id in replay.ALL_TARGET_IDS:
        rows.append(
            {
                "target_id": target_id,
                "inventory_status": "LOCAL_REPLAY_FIRST" if target_id in replay.LOCAL_TARGET_IDS else "RESIDUAL_EXTERNAL_READ_REQUIRED",
            }
        )
    return {"inventory_targets": rows}


def _closure() -> dict:
    return {
        "supplemental_evidence_units": [
            {
                "evidence_id": closure_v02.E_MSFT_VAL,
                "observed": {
                    "price_to_eps": "28.821727019499",
                    "price": "517.35",
                    "annual_gaap_diluted_eps": "17.95",
                },
            },
            {
                "evidence_id": closure_v02.E_META_VAL,
                "observed": {
                    "price_to_eps": "24.550021285653",
                    "price": "576.68",
                    "annual_gaap_diluted_eps": "23.49",
                },
            },
            {
                "evidence_id": closure_v02.E_META_PORT,
                "observed": {
                    "b2_cutoff_utc": "2026-08-27T20:00:00Z",
                    "portfolio_equity": "200000",
                    "meta_quantity": "0",
                    "meta_market_value": "0",
                    "meta_portfolio_weight": "0.000000000000",
                    "direct_position_exposure": "ZERO",
                },
            },
        ]
    }


def _metric(computed_value_id: str, metric_id: str, value: str) -> dict:
    return {
        "computed_value_id": computed_value_id,
        "metric_id": metric_id,
        "value": value,
        "unit": "RATIO",
    }


def _handoff() -> dict:
    return {
        "top3": ["NVDA", "MSFT", "META"],
        "candidates": [
            {"symbol": "NVDA", "metrics": []},
            {
                "symbol": "MSFT",
                "metrics": [
                    _metric("B2_MSFT_ANNUAL_REVENUE_GROWTH_20260827", "annual_revenue_growth", "0.177886867998466584316565148869106"),
                    _metric("B2_MSFT_ANNUAL_OPERATING_MARGIN_20260827", "annual_operating_margin", "0.4678081840892722073053498835278554"),
                ],
            },
            {
                "symbol": "META",
                "metrics": [
                    _metric("B2_META_ANNUAL_REVENUE_GROWTH_20260827", "annual_revenue_growth", "0.221670384982462112692324058820311"),
                    _metric("B2_META_ANNUAL_OPERATING_MARGIN_20260827", "annual_operating_margin", "0.4143785515957923230795258899515341"),
                ],
            },
        ],
    }


def _build(monkeypatch) -> dict:
    monkeypatch.setattr(replay, "verify_inventory", lambda payload: replay.EXPECTED_INVENTORY_HASH)
    monkeypatch.setattr(replay, "verify_historical_closure", lambda payload: replay.EXPECTED_HISTORICAL_CLOSURE_HASH)
    monkeypatch.setattr(replay, "verify_handoff", lambda payload: replay.EXPECTED_HANDOFF_HASH)
    monkeypatch.setattr(replay, "verify_judge", lambda payload: replay.EXPECTED_JUDGE_HASH)
    return replay.build_local_replay(
        inventory=_inventory(),
        historical_closure=_closure(),
        handoff=_handoff(),
        judge_result={},
        code_commit_sha=HEAD,
    )


def test_local_replay_adds_relative_context_but_resolves_nothing(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    assert artifact["status"] == replay.PASS_STATUS
    assert artifact["local_replay_target_ids"] == list(replay.LOCAL_TARGET_IDS)
    assert artifact["local_replay_partial_target_count"] == 2
    assert artifact["local_replay_resolved_target_count"] == 0
    assert artifact["newly_escalated_external_read_target_ids"] == list(replay.LOCAL_TARGET_IDS)
    assert artifact["residual_external_read_target_count"] == 7
    assert artifact["residual_external_read_target_ids"] == list(replay.ALL_TARGET_IDS)
    assert all(row["resolved"] is False for row in artifact["local_replay_results"])
    assert all(row["external_read_required_after_local_replay"] is True for row in artifact["local_replay_results"])
    assert artifact["provider_reads_authorized"] is False
    assert artifact["model_calls_authorized"] is False
    assert artifact["next_gate"] == replay.NEXT_GATE
    assert replay.verify_local_replay(artifact, expected_code_commit_sha=HEAD) == artifact["artifact_hash"]


def test_local_replay_decimal_math_is_deterministic_and_bounded(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    comparison = artifact["deterministic_context"]["valuation_comparison"]
    assert comparison["msft"]["price_to_reported_annual_gaap_diluted_eps"] == "28.821727019499"
    assert comparison["msft"]["earnings_yield_from_same_multiple"] == "0.034696047163428540"
    assert comparison["meta"]["price_to_reported_annual_gaap_diluted_eps"] == "24.550021285653"
    assert comparison["meta"]["earnings_yield_from_same_multiple"] == "0.040733162239024154"
    assert comparison["derived_relative_view"]["msft_pe_premium_vs_meta_ratio"] == "0.174000082694118851"
    assert comparison["derived_relative_view"]["meta_pe_discount_vs_msft_ratio"] == "0.148211303609808940"
    assert "DOES_NOT_ESTABLISH VALUATION_ATTRACTIVENESS" in comparison["interpretive_boundary"]


def test_meta_condition_004_keeps_broader_portfolio_interaction_unresolved(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    rows = {row["target_id"]: row for row in artifact["local_replay_results"]}
    meta = rows["META_CONDITION_004"]
    portfolio = meta["derived_context"]["portfolio_context"]
    assert portfolio["direct_position_exposure"] == "ZERO"
    assert portfolio["meta_portfolio_weight"] == "0.000000000000"
    assert "DOES_NOT ESTABLISH CORRELATION" in portfolio["interpretive_boundary"]
    assert meta["resolved"] is False
    assert "correlation" in meta["residual_need"].lower()


def test_local_replay_rejects_historical_valuation_drift(monkeypatch) -> None:
    closure = _closure()
    closure["supplemental_evidence_units"][0]["observed"]["price_to_eps"] = "28.0"
    with pytest.raises(replay.JudgeReopenLocalReplayError, match="MSFT P/E drift"):
        replay._comparison_context(historical_closure=closure, handoff=_handoff())


def test_local_replay_verifier_rejects_silent_resolution(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    tampered = deepcopy(artifact)
    tampered["local_replay_resolved_target_count"] = 1
    tampered["local_replay_resolved_target_ids"] = ["MSFT_VALUATION_CONTEXT_DEPTH"]
    tampered["artifact_hash"] = canonical_sha256(tampered, exclude_fields=("artifact_hash",))
    with pytest.raises(replay.JudgeReopenLocalReplayError, match="cannot claim resolution"):
        replay.verify_local_replay(tampered, expected_code_commit_sha=HEAD)


def test_local_replay_verifier_rejects_provider_authority_tamper(monkeypatch) -> None:
    artifact = _build(monkeypatch)
    tampered = deepcopy(artifact)
    tampered["provider_reads_authorized"] = True
    tampered["artifact_hash"] = canonical_sha256(tampered, exclude_fields=("artifact_hash",))
    with pytest.raises(replay.JudgeReopenLocalReplayError, match="cannot authorize calls"):
        replay.verify_local_replay(tampered, expected_code_commit_sha=HEAD)


def test_local_replay_runner_has_no_external_execution_surface() -> None:
    source = Path("scripts/b3_research_reopen_local_replay_zero_call_v01.py").read_text(encoding="utf-8")
    forbidden = (
        "urlopen",
        "requests.",
        "httpx",
        "StdlibResponsesTransport",
        "execute-paid",
        "provider.post",
        "submit_order",
        "OPENAI_API_KEY",
    )
    for token in forbidden:
        assert token not in source
