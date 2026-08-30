from __future__ import annotations

import json
from pathlib import Path

import pytest

from aic.council import reopen_input_overlay as mod
from aic.domain.canonical import canonical_sha256


def _selected() -> dict:
    specs = (("NVDA", 12), ("MSFT", 12), ("META", 10))
    payload = {
        "candidates": [
            {
                "candidate": candidate,
                "material_claims": [
                    {"claim_id": f"{candidate}_LEGACY_{index:03d}"}
                    for index in range(1, count + 1)
                ],
            }
            for candidate, count in specs
        ]
    }
    payload["artifact_hash"] = canonical_sha256(payload)
    return payload


def _judge() -> dict:
    payload = {
        "status": mod.EXPECTED_PRODUCTION_JUDGE_STATUS,
        "research_reopen_required": True,
        "research_reopen_request_hash": mod.EXPECTED_REOPEN_REQUEST_HASH,
        "research_reopen_request": {
            "reopen_request_id": mod.EXPECTED_REOPEN_REQUEST_ID,
        },
        "final_decision_created": False,
        "b5_handoff_created": False,
        "rerun_authorized": False,
    }
    payload["artifact_hash"] = canonical_sha256(payload)
    return payload


def _closure(*, selected_hash: str, judge_hash: str) -> dict:
    evidence = [
        {
            "candidate_id": "MSFT",
            "category": "valuation_context",
            "evidence_id": "B3_REOPEN_EVID_MSFT_VALUATION_20260828T173300Z",
        },
        {
            "candidate_id": "META",
            "category": "valuation_context",
            "evidence_id": "B3_REOPEN_EVID_META_VALUATION_20260828T173300Z",
        },
        {
            "candidate_id": "META",
            "category": "portfolio_interaction",
            "evidence_id": "B3_REOPEN_EVID_META_PORTFOLIO_20260827T200000Z",
        },
    ]
    claims = [
        {
            "candidate_id": "MSFT",
            "category": "valuation_context",
            "claim_id": "B3_REOPEN_SUPPLEMENTAL_MSFT_VALUATION_001",
            "claim_kind": "FACT",
            "claim_text": "MSFT valuation fact.",
            "evidence_ids": ["B3_REOPEN_EVID_MSFT_VALUATION_20260828T173300Z"],
            "support_status": "SUPPORTED",
        },
        {
            "candidate_id": "META",
            "category": "valuation_context",
            "claim_id": "B3_REOPEN_SUPPLEMENTAL_META_VALUATION_001",
            "claim_kind": "FACT",
            "claim_text": "META valuation fact.",
            "evidence_ids": ["B3_REOPEN_EVID_META_VALUATION_20260828T173300Z"],
            "support_status": "SUPPORTED",
        },
        {
            "candidate_id": "META",
            "category": "portfolio_interaction",
            "claim_id": "B3_REOPEN_SUPPLEMENTAL_META_PORTFOLIO_001",
            "claim_kind": "FACT",
            "claim_text": "META portfolio fact.",
            "evidence_ids": ["B3_REOPEN_EVID_META_PORTFOLIO_20260827T200000Z"],
            "support_status": "SUPPORTED",
        },
    ]
    payload = {
        "status": mod.EXPECTED_CLOSURE_STATUS,
        "overall_research_reopen_complete": True,
        "research_reopen_request_satisfied": True,
        "all_judge_conditions_satisfied": True,
        "remaining_reopen_reason_codes": [],
        "closed_reopen_reason_codes": list(mod.EXPECTED_CLOSED_REASONS),
        "legacy_frozen_artifacts_mutated": False,
        "legacy_material_claim_payloads_mutated": False,
        "reopen_overlay_is_additive": True,
        "historical_production_judge_rerun_authorized": False,
        "final_decision_created": False,
        "b5_handoff_created": False,
        "new_provider_dispatch_attempts": 0,
        "new_provider_reads": 0,
        "model_calls": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "source_selected_b3_reconciliation_hash": selected_hash,
        "source_production_judge_result_hash": judge_hash,
        "source_reopen_request_hash": mod.EXPECTED_REOPEN_REQUEST_HASH,
        "source_reopen_request_id": mod.EXPECTED_REOPEN_REQUEST_ID,
        "supplemental_claims": claims,
        "supplemental_evidence_units": evidence,
    }
    payload["artifact_hash"] = canonical_sha256(payload)
    return payload


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    selected = _selected()
    judge = _judge()
    closure = _closure(selected_hash=selected["artifact_hash"], judge_hash=judge["artifact_hash"])
    monkeypatch.setattr(mod, "EXPECTED_SELECTED_B3_HASH", selected["artifact_hash"])
    monkeypatch.setattr(mod, "EXPECTED_PRODUCTION_JUDGE_HASH", judge["artifact_hash"])
    monkeypatch.setattr(mod, "EXPECTED_CLOSURE_HASH", closure["artifact_hash"])
    selected_path = tmp_path / "selected.json"
    judge_path = tmp_path / "judge.json"
    closure_path = tmp_path / "closure.json"
    _write(selected_path, selected)
    _write(judge_path, judge)
    _write(closure_path, closure)
    return closure_path, selected_path, judge_path


def test_overlay_builds_37_claim_effective_surface_without_mutating_legacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closure_path, selected_path, judge_path = _inputs(tmp_path, monkeypatch)
    artifact = mod.build_b4_reopen_input_overlay(
        code_commit_sha="a" * 40,
        closure_path=closure_path,
        selected_b3_path=selected_path,
        production_judge_path=judge_path,
    )
    assert artifact["status"] == mod.PASS_STATUS
    assert artifact["legacy_material_claim_count"] == 34
    assert artifact["supplemental_claim_count"] == 3
    assert artifact["effective_material_claim_count"] == 37
    surfaces = {row["candidate_id"]: row for row in artifact["effective_candidate_surfaces"]}
    assert surfaces["NVDA"]["effective_material_claim_count"] == 12
    assert surfaces["MSFT"]["effective_material_claim_count"] == 13
    assert surfaces["META"]["effective_material_claim_count"] == 12
    assert surfaces["META"]["supplemental_portfolio_context_refs"] == [
        "B3_REOPEN_EVID_META_PORTFOLIO_20260827T200000Z"
    ]
    assert artifact["effective_gap_overlay"]["effective_unresolved_data_gap_refs"] == []
    assert artifact["legacy_b3_artifacts_mutated"] is False
    assert artifact["historical_b4_frozen_outputs_reusable_as_new_model_outputs"] is False
    assert artifact["new_b4_decision_lifecycle_required"] is True
    assert artifact["planned_model_calls"] == 0
    assert artifact["planned_provider_reads"] == 0
    assert artifact["final_decision_created"] is False
    assert artifact["b5_handoff_created"] is False
    assert artifact["artifact_hash"] == canonical_sha256(artifact, exclude_fields=("artifact_hash",))


def test_overlay_rejects_supplemental_category_drift_even_if_rehashed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closure_path, selected_path, judge_path = _inputs(tmp_path, monkeypatch)
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    closure["supplemental_claims"][0]["category"] = "market_context"
    closure["artifact_hash"] = canonical_sha256(closure, exclude_fields=("artifact_hash",))
    monkeypatch.setattr(mod, "EXPECTED_CLOSURE_HASH", closure["artifact_hash"])
    _write(closure_path, closure)
    with pytest.raises(mod.B4ReopenInputOverlayError, match="supplemental claim identity/category surface drift"):
        mod.build_b4_reopen_input_overlay(
            code_commit_sha="b" * 40,
            closure_path=closure_path,
            selected_b3_path=selected_path,
            production_judge_path=judge_path,
        )


def test_overlay_rejects_legacy_claim_count_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closure_path, selected_path, judge_path = _inputs(tmp_path, monkeypatch)
    selected = json.loads(selected_path.read_text(encoding="utf-8"))
    selected["candidates"][0]["material_claims"].pop()
    selected["artifact_hash"] = canonical_sha256(selected, exclude_fields=("artifact_hash",))
    monkeypatch.setattr(mod, "EXPECTED_SELECTED_B3_HASH", selected["artifact_hash"])
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    closure["source_selected_b3_reconciliation_hash"] = selected["artifact_hash"]
    closure["artifact_hash"] = canonical_sha256(closure, exclude_fields=("artifact_hash",))
    monkeypatch.setattr(mod, "EXPECTED_CLOSURE_HASH", closure["artifact_hash"])
    _write(selected_path, selected)
    _write(closure_path, closure)
    with pytest.raises(mod.B4ReopenInputOverlayError, match="legacy B3 claim-count surface drift"):
        mod.build_b4_reopen_input_overlay(
            code_commit_sha="c" * 40,
            closure_path=closure_path,
            selected_b3_path=selected_path,
            production_judge_path=judge_path,
        )


def test_runner_has_no_provider_or_model_execution_surface() -> None:
    text = Path("scripts/b4_reopen_input_overlay_zero_call_v01.py").read_text(encoding="utf-8")
    assert "--execute" not in text
    assert "requests.post" not in text
    assert "transport.post" not in text
    assert "alpaca\",\n" not in text
    assert "load_openai_api_key" not in text
    assert "MODEL_CALLS=0" in text
    assert "PROVIDER_READS=0" in text
