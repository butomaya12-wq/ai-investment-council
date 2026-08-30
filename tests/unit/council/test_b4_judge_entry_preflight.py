from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from aic.council import judge_entry_preflight as gate
from aic.domain.canonical import canonical_sha256


def _freeze() -> dict:
    raw = {
        "artifact_version": "B4_REBUTTAL_COUNCIL_FREEZE_ARTIFACT_v0_1",
        "runtime_version": "B4_REBUTTAL_PRODUCTION_RUNTIME_v0_1",
        "run_class": "B4_REAL_SELECTED_MODEL_REBUTTAL_COUNCIL",
        "status": "REBUTTAL_COUNCIL_FROZEN",
        "run_id": gate.EXPECTED_REBUTTAL_RUN_ID,
        "paid_authorization_artifact_hash": gate.EXPECTED_PAID_AUTHORIZATION_HASH,
        "receipt_manifest_hash": gate.EXPECTED_RECEIPT_MANIFEST_HASH,
        "candidate_order": list(gate.EXPECTED_CANDIDATE_ORDER),
        "rebuttal_bundle_count": 3,
        "rebuttal_bundle_ids": ["REB_NVDA", "REB_MSFT", "REB_META"],
        "rebuttal_bundle_hashes": list(gate.EXPECTED_REBUTTAL_BUNDLE_HASHES),
        "processed_records": [{"candidate_id": c} for c in gate.EXPECTED_CANDIDATE_ORDER],
        "research_reopen_required_candidates": list(gate.EXPECTED_RESEARCH_REOPEN_CANDIDATES),
        "dispatch_attempts": 3,
        "model_calls": 3,
        "automatic_repair_calls": 0,
        "judge_model_calls": 0,
        "actual_cost_usd": "0.556601",
        "cost_receipt_status": "COMPLETE",
        "rebuttal_freeze_barrier": True,
        "production_rebuttal_authorization_consumed": True,
        "judge_authorized": False,
        "rerun_authorized": False,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    raw["artifact_hash"] = gate.EXPECTED_REBUTTAL_FREEZE_HASH
    return raw


def _patch_freeze_verifier(monkeypatch) -> None:
    monkeypatch.setattr(
        gate,
        "verify_rebuttal_council_freeze_artifact",
        lambda _: gate.EXPECTED_REBUTTAL_FREEZE_HASH,
    )


def test_judge_entry_passes_only_after_exact_frozen_rebuttal_and_blocks_invest(monkeypatch) -> None:
    _patch_freeze_verifier(monkeypatch)
    artifact = gate.build_judge_entry_preflight(
        _freeze(),
        code_commit_sha="a" * 40,
    )
    observed = gate.verify_judge_entry_preflight(artifact)
    assert observed == artifact["artifact_hash"]
    assert artifact["judge_entry_barrier_satisfied"] is True
    assert artifact["judge_model_selection_required"] is True
    assert artifact["judge_execution_authorized"] is False
    assert artifact["invest_eligible_candidates"] == []
    assert artifact["invest_persistence_allowed"] is False
    assert artifact["allowed_judge_outcomes_for_current_frozen_run"] == ["WATCH", "ABSTAIN"]
    assert artifact["research_reopen_required_candidates"] == ["NVDA", "MSFT", "META"]
    assert artifact["research_reopen_must_remain_visible_to_judge"] is True
    assert artifact["b3_reopen_is_separate_lifecycle"] is True
    assert artifact["new_research_inside_b4_allowed"] is False
    assert artifact["model_calls"] == 0
    assert artifact["provider_reads"] == 0
    assert artifact["broker_writes"] == 0
    assert artifact["alpaca_orders"] == 0
    assert artifact["live_money"] == "PROHIBITED"
    assert artifact["judge_authorized"] is False
    assert artifact["rerun_authorized"] is False


def test_judge_entry_rejects_research_reopen_candidate_drift(monkeypatch) -> None:
    _patch_freeze_verifier(monkeypatch)
    raw = _freeze()
    raw["research_reopen_required_candidates"] = ["NVDA", "MSFT"]
    with pytest.raises(gate.JudgeEntryPreflightError, match="research-reopen candidate set"):
        gate.build_judge_entry_preflight(raw, code_commit_sha="b" * 40)


def test_judge_entry_rejects_frozen_bundle_hash_drift(monkeypatch) -> None:
    _patch_freeze_verifier(monkeypatch)
    raw = _freeze()
    raw["rebuttal_bundle_hashes"][1] = "0" * 64
    with pytest.raises(gate.JudgeEntryPreflightError, match="bundle hashes drift"):
        gate.build_judge_entry_preflight(raw, code_commit_sha="c" * 40)


def test_judge_entry_rejects_unconsumed_rebuttal_authorization(monkeypatch) -> None:
    _patch_freeze_verifier(monkeypatch)
    raw = _freeze()
    raw["production_rebuttal_authorization_consumed"] = False
    with pytest.raises(gate.JudgeEntryPreflightError, match="authorization is not consumed"):
        gate.build_judge_entry_preflight(raw, code_commit_sha="d" * 40)


def test_judge_entry_verifier_rejects_tampered_outcome_surface(monkeypatch) -> None:
    _patch_freeze_verifier(monkeypatch)
    artifact = gate.build_judge_entry_preflight(_freeze(), code_commit_sha="e" * 40)
    changed = deepcopy(artifact)
    changed["allowed_judge_outcomes_for_current_frozen_run"] = ["INVEST", "WATCH", "ABSTAIN"]
    changed["artifact_hash"] = canonical_sha256(changed, exclude_fields=("artifact_hash",))
    with pytest.raises(gate.JudgeEntryPreflightError, match="outcome surface drift"):
        gate.verify_judge_entry_preflight(changed)


def test_judge_entry_script_is_zero_call_and_cannot_authorize_judge() -> None:
    text = Path("scripts/b4_judge_entry_preflight_v01.py").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" not in text
    assert "StdlibResponsesTransport" not in text
    assert "--execute" not in text
    assert '"model_calls": 0' in text
    assert '"provider_reads": 0' in text
    assert '"judge_execution_authorized": False' in text
    assert '"judge_authorized": False' in text
    assert '"new_research_inside_b4_allowed": False' in text
