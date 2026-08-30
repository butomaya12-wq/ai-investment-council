from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from aic.council import reopen_lifecycle_plan as plan
from aic.domain.canonical import canonical_sha256


def _overlay(monkeypatch: pytest.MonkeyPatch) -> dict:
    closure_hash = "c" * 64
    payload = {
        "artifact_version": "B4_REOPEN_INPUT_OVERLAY_v0_1",
        "status": plan.EXPECTED_OVERLAY_STATUS,
        "source_b3_reopen_closure_hash": closure_hash,
        "effective_material_claim_count": 37,
        "legacy_material_claim_count": 34,
        "supplemental_claim_count": 3,
        "effective_gap_overlay": {
            "effective_unresolved_data_gap_refs": [],
            "effective_unresolved_reopen_reason_codes": [],
        },
        "historical_b4_frozen_outputs_reusable_as_new_model_outputs": False,
        "new_b4_decision_lifecycle_required": True,
        "historical_production_judge_rerun_authorized": False,
        "model_calls_authorized": False,
        "provider_reads_authorized": False,
    }
    payload["artifact_hash"] = canonical_sha256(payload)
    monkeypatch.setattr(plan, "EXPECTED_OVERLAY_HASH", payload["artifact_hash"])
    monkeypatch.setattr(plan, "EXPECTED_CLOSURE_HASH", closure_hash)
    return payload


def _initial_authority(monkeypatch: pytest.MonkeyPatch) -> dict:
    payload = {
        "artifact_version": "B4_INITIAL_SELECTED_MODEL_AUTHORITY_v0_1",
        "model_policy_version": plan.MODEL_POLICY_VERSION,
        "selection_status": "SELECTED",
        "selected_candidate": dict(plan.EXPECTED_INITIAL_SELECTED),
        "cost_receipt_status": "COMPLETE",
        "semantic_replay_receipts_complete": 36,
    }
    payload["selection_hash"] = canonical_sha256(payload)
    monkeypatch.setattr(plan, "EXPECTED_INITIAL_SELECTION_HASH", payload["selection_hash"])
    return payload


def test_lifecycle_plan_reuses_eval_selection_but_requires_13_fresh_production_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = plan.build_b4_reopen_lifecycle_plan(
        code_commit_sha="a" * 40,
        overlay=_overlay(monkeypatch),
        initial_selected_model_authority=_initial_authority(monkeypatch),
    )
    assert artifact["status"] == plan.PASS_STATUS
    assert artifact["model_eval_reruns_required"] is False
    assert artifact["planned_model_eval_calls"] == 0
    assert artifact["planned_fresh_production_model_calls_max"] == 13
    assert artifact["planned_paid_calls_max"] == 13
    assert [row["fresh_model_calls_max"] for row in artifact["fresh_production_stages"]] == [9, 3, 1]
    assert artifact["selected_model_authority_reuse"]["INITIAL"]["selected_model"]["candidate_key"] == "L2"
    assert artifact["selected_model_authority_reuse"]["REBUTTAL"]["selected_model"]["candidate_key"] == "R3"
    assert artifact["selected_model_authority_reuse"]["JUDGE"]["selected_model"]["candidate_key"] == "J1"
    assert artifact["post_reopen_judge_contract_required"] is True
    assert artifact["historical_reopen_restricted_judge_runtime_reusable"] is False
    assert artifact["model_calls_authorized"] is False
    assert artifact["owner_cost_approval_required"] is True
    assert artifact["provider_reads_authorized"] is False
    assert artifact["automatic_repair_calls_authorized"] == 0
    assert artifact["automatic_retries"] == 0
    assert artifact["final_decision_created"] is False
    assert artifact["b5_handoff_created"] is False
    assert artifact["next_gate"] == plan.NEXT_GATE
    assert artifact["artifact_hash"] == canonical_sha256(artifact, exclude_fields=("artifact_hash",))


def test_lifecycle_plan_rejects_any_effective_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    overlay = _overlay(monkeypatch)
    overlay["effective_gap_overlay"]["effective_unresolved_data_gap_refs"] = ["GAP"]
    overlay["artifact_hash"] = canonical_sha256(overlay, exclude_fields=("artifact_hash",))
    monkeypatch.setattr(plan, "EXPECTED_OVERLAY_HASH", overlay["artifact_hash"])
    with pytest.raises(plan.B4ReopenLifecyclePlanError, match="data gaps remain open"):
        plan.build_b4_reopen_lifecycle_plan(
            code_commit_sha="a" * 40,
            overlay=overlay,
            initial_selected_model_authority=_initial_authority(monkeypatch),
        )


def test_lifecycle_plan_rejects_reuse_of_historical_model_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    overlay = _overlay(monkeypatch)
    overlay["historical_b4_frozen_outputs_reusable_as_new_model_outputs"] = True
    overlay["artifact_hash"] = canonical_sha256(overlay, exclude_fields=("artifact_hash",))
    monkeypatch.setattr(plan, "EXPECTED_OVERLAY_HASH", overlay["artifact_hash"])
    with pytest.raises(plan.B4ReopenLifecyclePlanError, match="cannot be reusable"):
        plan.build_b4_reopen_lifecycle_plan(
            code_commit_sha="a" * 40,
            overlay=overlay,
            initial_selected_model_authority=_initial_authority(monkeypatch),
        )


def test_runner_has_no_model_or_provider_execution_surface() -> None:
    text = Path("scripts/b4_reopen_lifecycle_plan_zero_call_v01.py").read_text(encoding="utf-8")
    lowered = text.lower()
    assert "load_openai_api_key" not in text
    assert "responsestransport" not in lowered
    assert "urllib" not in lowered
    assert "requests." not in lowered
    assert '["alpaca"' not in lowered
    assert "--execute" not in lowered
    assert "MODEL_CALLS=0" in text
    assert "PROVIDER_READS=0" in text
