from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from aic.council import reopen_lifecycle_plan as v01
from aic.council import reopen_lifecycle_plan_v02 as v02
from aic.domain.canonical import canonical_sha256


INITIAL_AUTHORITY_PATH = Path("config/event/b4_initial_selected_model_v1.json")


def _overlay(monkeypatch: pytest.MonkeyPatch) -> dict:
    closure_hash = "c" * 64
    payload = {
        "artifact_version": "B4_REOPEN_INPUT_OVERLAY_v0_1",
        "status": v01.EXPECTED_OVERLAY_STATUS,
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
    monkeypatch.setattr(v01, "EXPECTED_OVERLAY_HASH", payload["artifact_hash"])
    monkeypatch.setattr(v01, "EXPECTED_CLOSURE_HASH", closure_hash)
    return payload


def _real_initial_authority() -> dict:
    value = json.loads(INITIAL_AUTHORITY_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_v02_replays_real_initial_authority_through_typed_canonical_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _real_initial_authority()

    # Regression proof: the failed v0.1 path hashes raw JSON decimal strings,
    # which is not the contract used to create the selection_hash.
    assert canonical_sha256(
        raw,
        exclude_fields=("selection_hash",),
    ) != raw["selection_hash"]

    normalized = v02.normalize_initial_selected_model_authority(raw)
    assert canonical_sha256(
        normalized,
        exclude_fields=("selection_hash",),
    ) == raw["selection_hash"]

    artifact = v02.build_b4_reopen_lifecycle_plan_v02(
        code_commit_sha="a" * 40,
        overlay=_overlay(monkeypatch),
        initial_selected_model_authority=raw,
    )
    assert artifact["artifact_version"] == v02.ARTIFACT_VERSION
    assert artifact["status"] == v02.PASS_STATUS
    assert artifact["planned_model_eval_calls"] == 0
    assert artifact["planned_fresh_production_model_calls_max"] == 13
    assert artifact["planned_paid_calls_max"] == 13
    assert [
        row["fresh_model_calls_max"]
        for row in artifact["fresh_production_stages"]
    ] == [9, 3, 1]
    assert artifact["model_calls_authorized"] is False
    assert artifact["provider_reads_authorized"] is False
    assert artifact["initial_selected_model_authority_validation_contract"] == (
        v02.INITIAL_AUTHORITY_VALIDATION_CONTRACT
    )
    assert artifact["historical_v0_1_failure_class"] == (
        "RAW_JSON_DECIMAL_STRING_CANONICALIZATION_MISMATCH"
    )
    assert artifact["artifact_hash"] == canonical_sha256(
        artifact,
        exclude_fields=("artifact_hash",),
    )


def test_v02_rejects_tampered_real_initial_authority() -> None:
    raw = deepcopy(_real_initial_authority())
    raw["actual_paid_eval_cost_usd"] = "0.4515140"
    with pytest.raises(
        v02.B4ReopenLifecyclePlanError,
        match="typed validation failed",
    ):
        v02.normalize_initial_selected_model_authority(raw)


def test_v02_runner_has_no_model_or_provider_execution_surface() -> None:
    text = Path("scripts/b4_reopen_lifecycle_plan_zero_call_v02.py").read_text(
        encoding="utf-8"
    )
    lowered = text.lower()
    assert "load_openai_api_key" not in text
    assert "responsestransport" not in lowered
    assert "urllib" not in lowered
    assert "requests." not in lowered
    assert '["alpaca"' not in lowered
    assert "--execute" not in lowered
    assert "MODEL_CALLS=0" in text
    assert "PROVIDER_READS=0" in text
