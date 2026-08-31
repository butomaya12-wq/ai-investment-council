from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from aic.council import post_research_reopen_initial_request_cost_preflight_v01 as runtime
from aic.domain.canonical import canonical_sha256


ROOT = Path(".")


def _artifact() -> dict:
    return runtime.load_and_build_initial_request_cost_preflight(
        code_commit_sha="a" * 40,
        source_verdict_preflight_path=ROOT / ".aic-runtime/b4_post_research_reopen_verdict_preflight_zero_call_v0_1.json",
        final_closure_path=ROOT / ".aic-runtime/b3_research_reopen_final_competition_closure_zero_call_v0_1.json",
        s00_path=ROOT / ".aic-runtime/b3_research_reopen_s00_scope_zero_call_v0_3.json",
        local_replay_path=ROOT / ".aic-runtime/b3_research_reopen_local_replay_zero_call_v0_1.json",
        original_result_path=ROOT / ".aic-runtime/b3_research_reopen_residual_external_read_result_v0_1.json",
        wire_v02_result_path=ROOT / ".aic-runtime/b3_research_reopen_continuation_wire_repair_result_v0_2.json",
        repair_result_path=ROOT / ".aic-runtime/b3_research_reopen_cr4_to_cr6_repair_result_v0_1.json",
        repair_authorization_path=ROOT / ".aic-runtime/b3_research_reopen_cr4_to_cr6_repair_authorization_v0_1.json",
        repair_raw_dir=ROOT / ".aic-runtime/b3_research_reopen_cr4_to_cr6_repair_raw_v0_1",
        freeze_path=ROOT / ".aic-runtime/b4_council_input_freeze.json",
        reconciliation_path=ROOT / ".aic-runtime/b3_selected_model_reconciliation.json",
        handoff_path=ROOT / "config/event/b2_real_event_handoff_v0_1.json",
        initial_authority_path=ROOT / "config/event/b4_initial_selected_model_v1.json",
        pricing_path=ROOT / "config/event/openai_text_pricing_2026_08_30.json",
    )


def test_real_saved_evidence_materializes_content_and_preserves_boundaries() -> None:
    artifact = _artifact()
    inputs = artifact["model_facing_inputs_by_candidate"]
    overlay = inputs["NVDA"]["post_research_reopen_overlay"]["saved_evidence_content"]
    assert artifact["source_verdict_preflight_hash"] == runtime.EXPECTED_SOURCE_VERDICT_HASH
    assert artifact["source_b3_closure_hash"] == runtime.EXPECTED_B3_CLOSURE_HASH
    assert artifact["model"] == "gpt-5.6-terra"
    assert artifact["reasoning_effort"] == "low"
    assert len(overlay["NVDA"]["retained_typed_current_articles"]) == 10
    assert len(overlay["NVDA"]["salvaged_raw_current_articles"]) == 5
    assert overlay["NVDA"]["combined_unique_article_count"] == 15
    assert all(row["content"] for row in overlay["NVDA"]["retained_typed_current_articles"])
    assert all(row["content"] for row in overlay["NVDA"]["salvaged_raw_current_articles"])
    assert overlay["NVDA"]["pagination_boundary"] == "NONEXHAUSTIVE_CURRENT_COVERAGE; TERMINAL_PAGINATION_NOT_CLAIMED"
    assert overlay["NVDA"]["directional_inference_from_closure_forbidden"] is True
    assert len(overlay["MSFT"]["typed_current_articles"]) == 8
    assert all(row["content"] for row in overlay["MSFT"]["typed_current_articles"])
    assert overlay["MSFT"]["valuation_context"]["msft_point_in_time_pe"] == "28.821727019499"
    assert overlay["MSFT"]["valuation_context"]["meta_point_in_time_pe"] == "24.550021285653"
    assert overlay["MSFT"]["valuation_context"]["msft_pe_premium_vs_meta_ratio"] == "0.174000082694118851"
    assert "DOES_NOT_ESTABLISH VALUATION_ATTRACTIVENESS" in overlay["MSFT"]["valuation_context"]["interpretive_boundary"]
    assert overlay["MSFT"]["durability_disposition"] == "CLOSED_AS_NOT_ESTABLISHED_MATERIAL_RISK"
    assert overlay["MSFT"]["positive_extrapolation_from_current_growth_or_margin_forbidden"] is True
    assert [row["condition_id"] for row in overlay["META"]["conditions_preserved_as_post_research_reopen_decision_context"]] == list(runtime.verdict_v01.EXPECTED_META_CONDITION_IDS)
    assert overlay["META"]["conditions_are_not_canonical_b3_reopen_requirements"] is True


def test_requests_are_exactly_frozen_additive_and_bounded() -> None:
    artifact = _artifact()
    assert artifact["call_count_planned"] == 9
    assert artifact["call_count_ceiling"] == 9
    assert len(artifact["initial_requests"]) == 9
    assert artifact["estimated_input_tokens_upper_bound_total"] > 0
    assert artifact["estimated_max_cost_usd"] != "0"
    assert artifact["owner_approval_status"] == "NOT_GRANTED"
    assert artifact["owner_approval_granted"] is False
    assert artifact["model_calls_authorized"] is False
    assert artifact["provider_reads_authorized"] is False
    assert artifact["automatic_retries"] == 0
    assert artifact["broker_writes"] == 0
    assert artifact["alpaca_orders"] == 0
    assert artifact["live_money"] == "PROHIBITED"
    for candidate, body in artifact["model_facing_inputs_by_candidate"].items():
        assert body["candidate_id"] == candidate
        assert body["post_research_reopen_overlay"]["legacy_candidate_packet_and_material_claims_unchanged"] is True
        assert body["post_research_reopen_overlay"]["new_evidence_is_additive"] is True
        assert body["model_input_hash"] == canonical_sha256(body, exclude_fields=("model_input_hash",))
    for row in artifact["initial_requests"]:
        assert row["request_payload_canonical_hash"] == canonical_sha256(row["request_payload"])
        assert row["prompt_hash"]
        assert row["model_facing_input_hash"]
        assert row["maximum_output_tokens"] == 4096
        assert row["estimated_input_tokens_upper_bound"] > 0


def test_artifact_verification_fails_closed_on_tamper() -> None:
    artifact = _artifact()
    artifact["owner_approval_granted"] = True
    with pytest.raises(runtime.PostResearchReopenInitialRequestCostPreflightError, match="self-hash"):
        runtime.verify_initial_request_cost_preflight(artifact, expected_code_commit_sha="a" * 40)


def test_independent_verifier_accepts_sorted_json_key_order_round_trip() -> None:
    artifact = _artifact()
    persisted = json.loads(json.dumps(artifact, ensure_ascii=False, sort_keys=True))
    assert tuple(persisted["model_facing_inputs_by_candidate"]) != runtime.EXPECTED_CANDIDATES
    assert runtime.verify_initial_request_cost_preflight(
        persisted, expected_code_commit_sha="a" * 40
    ) == persisted["artifact_hash"]


def test_zero_call_runner_has_no_execution_surface_and_exclusive_output(tmp_path: Path) -> None:
    text = Path("scripts/b4_post_research_reopen_initial_request_cost_preflight_zero_call_v01.py").read_text(encoding="utf-8")
    lowered = text.lower()
    assert "responses.create" not in lowered
    assert "chat.completions" not in lowered
    assert "alpaca.trading" not in lowered
    assert "alpaca.data" not in lowered
    assert "--execute" not in lowered
    assert "os.O_EXCL" in text
    assert "MODEL_CALLS_THIS_STEP=0" in text
    assert "PROVIDER_READS_THIS_STEP=0" in text
    spec = importlib.util.spec_from_file_location("zero_call_runner", Path("scripts/b4_post_research_reopen_initial_request_cost_preflight_zero_call_v01.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / "exclusive.json"
    module._write_exclusive(path, {"status": "PASS"})
    with pytest.raises(runtime.PostResearchReopenInitialRequestCostPreflightError, match="already exists"):
        module._write_exclusive(path, {"status": "PASS"})
