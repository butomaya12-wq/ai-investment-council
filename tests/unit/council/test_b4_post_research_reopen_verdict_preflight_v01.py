from __future__ import annotations

from pathlib import Path

import pytest

from aic.council import post_research_reopen_verdict_preflight_v01 as runtime
from aic.domain.canonical import canonical_sha256


def _closure() -> dict:
    return {
        "requirement_closures": [
            {
                "requirement_id": "NVDA_CURRENT_DEVELOPMENTS_Q4",
                "closure_status": "CLOSED_DECISION_USABLE_NONEXHAUSTIVE",
                "combined_unique_article_count": 15,
            },
            {
                "requirement_id": "MSFT_VALUATION_CONTEXT_DEPTH",
                "closure_status": "CLOSED_COMPARATIVE_CONTEXT_PRESENT_ATTRACTIVENESS_NOT_ESTABLISHED",
                "msft_point_in_time_pe": "28.821727019499",
                "meta_point_in_time_pe": "24.550021285653",
                "msft_pe_premium_vs_meta_ratio": "0.174000082694118851",
                "interpretive_boundary": "RELATIVE_POINT_IN_TIME_CONTEXT_ONLY; DOES_NOT_ESTABLISH VALUATION_ATTRACTIVENESS, FORWARD_EARNINGS_POWER, OR FAIR_VALUE",
            },
            {
                "requirement_id": "MSFT_AI_CLOUD_MONETIZATION_RETURN_DURABILITY",
                "closure_status": "CLOSED_AS_NOT_ESTABLISHED_MATERIAL_RISK",
                "decision_rule": "NO_POSITIVE_EXTRAPOLATION_FROM CURRENT GROWTH OR MARGIN; DURABILITY REMAINS A MATERIAL RISK INPUT FOR B4",
            },
        ]
    }


def _source() -> dict:
    return {
        "s00_hash": runtime.EXPECTED_S00_HASH,
        "local_replay_hash": runtime.EXPECTED_LOCAL_REPLAY_HASH,
        "original_result_hash": runtime.EXPECTED_ORIGINAL_RESULT_HASH,
        "wire_v02_result_hash": runtime.EXPECTED_WIRE_V02_RESULT_HASH,
        "repair_result_hash": runtime.EXPECTED_REPAIR_RESULT_HASH,
        "repair_authorization_hash": runtime.EXPECTED_REPAIR_AUTH_HASH,
        "salvaged_nvda_sha256": runtime.EXPECTED_SALVAGED_NVDA_SHA256,
        "nvda_article_ids": list(range(1, 16)),
        "msft_article_ids": list(range(101, 109)),
        "valuation": {},
        "meta_condition_ids": list(runtime.EXPECTED_META_CONDITION_IDS),
    }


def test_verdict_preflight_freezes_closed_b3_and_staged_fresh_b4(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(runtime, "_verify_final_closure", lambda _p: runtime.EXPECTED_FINAL_CLOSURE_HASH)
    monkeypatch.setattr(runtime, "_verify_source_lineage", lambda **_kwargs: _source())
    monkeypatch.setattr(
        runtime,
        "_verify_initial_selected_model_authority",
        lambda _p: runtime.EXPECTED_INITIAL_SELECTION_HASH,
    )

    artifact = runtime.build_verdict_preflight(
        code_commit_sha="a" * 40,
        final_closure=_closure(),
        s00={},
        local_replay={},
        original_result={},
        wire_v02_result={},
        repair_result={},
        repair_authorization={},
        repair_raw_dir=tmp_path,
        initial_selected_model_authority={},
    )

    assert artifact["status"] == runtime.PASS_STATUS
    assert artifact["source_b3_final_closure_hash"] == runtime.EXPECTED_FINAL_CLOSURE_HASH
    assert artifact["canonical_research_reopen_closed"] is True
    assert artifact["remaining_canonical_reopen_requirement_ids"] == []
    assert artifact["additional_provider_read_required_before_b4"] is False
    assert artifact["candidate_order"] == ["NVDA", "MSFT", "META"]
    assert artifact["planned_model_eval_calls"] == 0
    assert artifact["planned_fresh_production_model_calls_max"] == 13
    assert [row["fresh_model_calls_max"] for row in artifact["fresh_production_stages"]] == [9, 3, 1]
    assert artifact["historical_b4_frozen_outputs_reusable_as_new_model_outputs"] is False
    assert artifact["historical_reopen_restricted_judge_runtime_reusable"] is False
    assert artifact["new_post_research_reopen_judge_contract_required"] is True
    assert artifact["initial_model_facing_materialization_required"] is True
    assert artifact["initial_model_facing_materialization_contract"]["must_materialize_saved_evidence_content_not_only_ids"] is True
    assert artifact["initial_model_facing_materialization_contract"]["provider_read_for_materialization_allowed"] is False
    assert artifact["model_calls_authorized"] is False
    assert artifact["provider_reads_authorized"] is False
    assert artifact["owner_cost_approval_required"] is True
    assert artifact["final_decision_created"] is False
    assert artifact["b5_handoff_created"] is False
    assert artifact["next_gate"] == runtime.NEXT_GATE
    assert artifact["artifact_hash"] == canonical_sha256(artifact, exclude_fields=("artifact_hash",))
    assert runtime.verify_verdict_preflight(artifact, expected_code_commit_sha="a" * 40) == artifact["artifact_hash"]


def test_verdict_preflight_preserves_negative_epistemic_boundaries(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(runtime, "_verify_final_closure", lambda _p: runtime.EXPECTED_FINAL_CLOSURE_HASH)
    monkeypatch.setattr(runtime, "_verify_source_lineage", lambda **_kwargs: _source())
    monkeypatch.setattr(runtime, "_verify_initial_selected_model_authority", lambda _p: runtime.EXPECTED_INITIAL_SELECTION_HASH)

    artifact = runtime.build_verdict_preflight(
        code_commit_sha="b" * 40,
        final_closure=_closure(),
        s00={}, local_replay={}, original_result={}, wire_v02_result={},
        repair_result={}, repair_authorization={}, repair_raw_dir=tmp_path,
        initial_selected_model_authority={},
    )
    context = artifact["post_research_reopen_decision_context"]
    assert context["NVDA"]["directional_inference_from_closure_forbidden"] is True
    assert context["NVDA"]["current_article_count"] == 15
    assert context["MSFT"]["durability_disposition"] == "CLOSED_AS_NOT_ESTABLISHED_MATERIAL_RISK"
    assert context["MSFT"]["positive_extrapolation_forbidden"] is True
    assert context["META"]["judge_change_condition_ids"] == list(runtime.EXPECTED_META_CONDITION_IDS)
    assert context["META"]["conditions_are_not_canonical_b3_reopen_requirements"] is True
    assert all(row["canonical_b3_blocker"] is False for row in artifact["known_transport_limitations"])


def test_verify_rejects_paid_authority_tamper(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(runtime, "_verify_final_closure", lambda _p: runtime.EXPECTED_FINAL_CLOSURE_HASH)
    monkeypatch.setattr(runtime, "_verify_source_lineage", lambda **_kwargs: _source())
    monkeypatch.setattr(runtime, "_verify_initial_selected_model_authority", lambda _p: runtime.EXPECTED_INITIAL_SELECTION_HASH)
    artifact = runtime.build_verdict_preflight(
        code_commit_sha="c" * 40,
        final_closure=_closure(),
        s00={}, local_replay={}, original_result={}, wire_v02_result={},
        repair_result={}, repair_authorization={}, repair_raw_dir=tmp_path,
        initial_selected_model_authority={},
    )
    artifact["model_calls_authorized"] = True
    artifact["artifact_hash"] = canonical_sha256(artifact, exclude_fields=("artifact_hash",))
    with pytest.raises(runtime.PostResearchReopenVerdictPreflightError, match="model authority"):
        runtime.verify_verdict_preflight(artifact, expected_code_commit_sha="c" * 40)


def test_zero_call_runner_has_no_paid_execution_switch():
    text = Path("scripts/b4_post_research_reopen_verdict_preflight_zero_call_v01.py").read_text(encoding="utf-8")
    lowered = text.lower()
    assert "--execute" not in lowered
    assert "responses.create" not in lowered
    assert "chat.completions" not in lowered
    assert "data news" not in lowered
    assert "multi-bars" not in lowered
    assert "account portfolio" not in lowered
    assert 'print("PROVIDER_READS=0")' in text
    assert 'print("MODEL_CALLS=0")' in text
