from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_s00_scope_v01 as v01
from aic.research import reopen_judge_s00_scope_v03 as scope


HEAD = "a" * 40


def _initial_claim(claim_id: str, *, category: str, evidence_ids: list[str], computed_value_ids: list[str]) -> dict:
    return {
        "candidate_id": "MSFT",
        "claim_id": claim_id,
        "category": category,
        "support_status": "SUPPORTED",
        "materiality": "MATERIAL",
        "claim_text": "valuation" if category == "INTEGRITY_FINDING" else "durability",
        "evidence_ids": evidence_ids,
        "computed_value_ids": computed_value_ids,
    }


def _recovered_initial() -> dict:
    valuation_id = v01.EXPECTED_REOPEN_REASONS[1]
    durability_id = v01.EXPECTED_REOPEN_REASONS[2]
    rows = []
    for candidate in ("NVDA", "MSFT", "META"):
        for lane in ("BULL", "BEAR", "RED_TEAM"):
            row = {
                "candidate_id": candidate,
                "lane": lane,
                "stage": f"{lane}_INITIAL",
                "material_claims": [],
                "claim_metadata": [],
            }
            if candidate == "MSFT" and lane == "RED_TEAM":
                row["material_claims"] = [
                    _initial_claim(
                        valuation_id,
                        category="INTEGRITY_FINDING",
                        evidence_ids=["B3_REOPEN_EVID_MSFT_VALUATION_20260828T173300Z"],
                        computed_value_ids=[],
                    ),
                    _initial_claim(
                        durability_id,
                        category="ASSUMPTION",
                        evidence_ids=["B3_SEC_MSFT_N3_SEC_MDA_1", "B3_SEC_MSFT_N2_SEC_RISK_FACTORS_1", "B3_SEC_MSFT_N1_SEC_BUSINESS_1"],
                        computed_value_ids=["B2_MSFT_ANNUAL_REVENUE_GROWTH_20260827", "B2_MSFT_ANNUAL_OPERATING_MARGIN_20260827"],
                    ),
                ]
                row["claim_metadata"] = [
                    {"material_claim_id": valuation_id, "council_claim_type": "INTEGRITY_FINDING", "lane": "RED_TEAM"},
                    {"material_claim_id": durability_id, "council_claim_type": "ASSUMPTION", "lane": "RED_TEAM"},
                ]
            rows.append(row)
    payload = {
        "artifact_version": "B4_REOPEN_INITIAL_COUNCIL_FREEZE_RECOVERED_v0_2",
        "status": "B4_REOPEN_INITIAL_COUNCIL_FROZEN_AFTER_UNKNOWN_DISPATCH_RECOVERY",
        "candidate_order": ["NVDA", "MSFT", "META"],
        "initial_opinion_count": 9,
        "processed_records": rows,
    }
    payload["artifact_hash"] = canonical_sha256(payload)
    return payload


def _rebuttal() -> dict:
    records = [
        {"candidate_id": "NVDA", "research_reopen_required": True, "research_reopen_reason_codes": [v01.EXPECTED_REOPEN_REASONS[0]]},
        {"candidate_id": "MSFT", "research_reopen_required": True, "research_reopen_reason_codes": [v01.EXPECTED_REOPEN_REASONS[1], v01.EXPECTED_REOPEN_REASONS[2]]},
        {"candidate_id": "META", "research_reopen_required": False, "research_reopen_reason_codes": []},
    ]
    payload = {
        "artifact_version": "B4_REOPEN_REBUTTAL_COUNCIL_FREEZE_v0_3",
        "status": "B4_REOPEN_REBUTTAL_COUNCIL_FROZEN",
        "candidate_order": ["NVDA", "MSFT", "META"],
        "processed_records": records,
        "research_reopen_required_candidates": ["NVDA", "MSFT"],
        "rebuttal_rerun_authorized": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    payload["artifact_hash"] = canonical_sha256(payload)
    return payload


def test_v03_reads_msft_reopen_claims_from_recovered_initial_not_rebuttal(monkeypatch) -> None:
    initial = _recovered_initial()
    rebuttal = _rebuttal()
    monkeypatch.setattr(scope, "EXPECTED_INITIAL_FREEZE_HASH", initial["artifact_hash"])
    monkeypatch.setattr(v01, "EXPECTED_REBUTTAL_FREEZE_HASH", rebuttal["artifact_hash"])
    observed, valuation, durability = scope.verify_recovered_initial_freeze(initial, expected_hash=initial["artifact_hash"])
    assert observed == initial["artifact_hash"]
    assert valuation["claim_id"] == v01.EXPECTED_REOPEN_REASONS[1]
    assert durability["claim_id"] == v01.EXPECTED_REOPEN_REASONS[2]
    assert scope.verify_rebuttal_reason_lineage(rebuttal, expected_hash=rebuttal["artifact_hash"]) == rebuttal["artifact_hash"]


def test_v03_fails_if_initial_claim_missing_even_when_rebuttal_reason_is_present(monkeypatch) -> None:
    initial = _recovered_initial()
    initial = deepcopy(initial)
    initial["processed_records"][5]["material_claims"] = initial["processed_records"][5]["material_claims"][:1]
    initial["processed_records"][5]["claim_metadata"] = initial["processed_records"][5]["claim_metadata"][:1]
    initial["artifact_hash"] = canonical_sha256(initial, exclude_fields=("artifact_hash",))
    with pytest.raises(scope.JudgeReopenS00ScopeV03Error, match="durability reopen claim missing"):
        scope.verify_recovered_initial_freeze(initial, expected_hash=initial["artifact_hash"])


def test_v03_fails_if_rebuttal_reason_lineage_drops_initial_claim(monkeypatch) -> None:
    rebuttal = _rebuttal()
    rebuttal = deepcopy(rebuttal)
    rebuttal["processed_records"][1]["research_reopen_reason_codes"] = [v01.EXPECTED_REOPEN_REASONS[1]]
    rebuttal["artifact_hash"] = canonical_sha256(rebuttal, exclude_fields=("artifact_hash",))
    with pytest.raises(scope.JudgeReopenS00ScopeV03Error, match="MSFT Rebuttal reopen reason drift"):
        scope.verify_rebuttal_reason_lineage(rebuttal, expected_hash=rebuttal["artifact_hash"])


def test_v03_runner_has_no_provider_or_model_execution_surface() -> None:
    source = Path("scripts/b3_research_reopen_s00_scope_zero_call_v03.py").read_text(encoding="utf-8")
    for token in ("OPENAI_API_KEY", "urlopen", "requests.", "httpx", "provider.post", "alpaca data", "execute_paid"):
        assert token not in source
