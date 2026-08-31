from __future__ import annotations

from pathlib import Path

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_residual_external_read_continuation_plan_v01 as plan


HEAD = "a" * 40


def _reconciliation_fixture() -> dict:
    payload = {
        "status": "B3_RESEARCH_REOPEN_DURABLE_PROVIDER_READ_FAILURE_RECONCILIATION_V02_ZERO_CALL_PASS",
        "code_commit_sha": plan.EXPECTED_RECONCILIATION_CODE_SHA,
        "authority_consumed": True,
        "original_authority_reusable": False,
        "original_production_read_pass_rerun_allowed": False,
        "provider_dispatch_attempts_observed": 2,
        "provider_response_receipts_observed": 2,
        "retained_partial_evidence_hash": plan.EXPECTED_NVDA_RETAINED_HASH,
        "nvda_aggregate_payload_hash": plan.EXPECTED_NVDA_AGGREGATE_HASH,
        "nvda_retained_page_raw_payload_hashes": list(plan.EXPECTED_NVDA_PAGE_HASHES),
        "nvda_terminal_next_page_token": plan.EXPECTED_NVDA_TERMINAL_TOKEN,
        "nvda_retained_page_count": 2,
        "nvda_retained_article_count": 10,
        "nvda_continuation_must_start_from_terminal_token": True,
        "nvda_replay_of_retained_pages_allowed": False,
        "nvda_max_additional_pages_without_expanding_original_total_bound": 4,
        "residual_external_read_target_count": len(plan.RESIDUAL_TARGET_IDS),
        "residual_external_read_target_ids": list(plan.RESIDUAL_TARGET_IDS),
        "provider_reads_this_step": 0,
        "model_calls_this_step": 0,
    }
    payload["artifact_hash"] = canonical_sha256(payload)
    return payload


def test_build_plan_preserves_partial_evidence_and_moves_nvda_last(monkeypatch) -> None:
    monkeypatch.setattr(
        plan,
        "verify_reconciliation",
        lambda payload: plan.EXPECTED_RECONCILIATION_HASH,
    )
    artifact = plan.build_plan(reconciliation={}, code_commit_sha=HEAD)

    assert artifact["logical_provider_read_bundle_ids"] == list(plan.BUNDLE_IDS)
    assert artifact["logical_provider_read_bundle_ids"][-1] == "CR6_NVDA_NEWS_CONTINUATION"
    assert artifact["provider_dispatch_attempts_max"] == 11
    assert artifact["news_dispatch_attempts_max"] == 8
    assert artifact["non_news_dispatch_attempts_max"] == 3
    assert artifact["nvda_retained_page_count"] == 2
    assert artifact["nvda_retained_article_count"] == 10
    assert artifact["nvda_max_additional_pages"] == 4
    assert artifact["nvda_total_page_engineering_bound_including_retained"] == 6
    assert artifact["nvda_start_page_token"] == plan.EXPECTED_NVDA_TERMINAL_TOKEN
    assert artifact["nvda_replay_retained_pages_allowed"] is False
    assert artifact["pagination_incomplete_is_transport_error"] is False
    assert artifact["pagination_incomplete_continue_policy"] == "RETAIN_PARTIAL_AND_CONTINUE_TO_LATER_BUNDLES"
    assert artifact["transport_or_validation_error_policy"] == "STOP_IMMEDIATELY"
    assert artifact["provider_reads_authorized"] is False
    assert artifact["model_calls_authorized"] is False
    assert artifact["model_synthesis_authorized"] is False
    assert artifact["automatic_retries"] == 0
    assert artifact["automatic_followup_reads"] == 0
    assert artifact["next_gate"] == plan.NEXT_GATE
    assert artifact["artifact_hash"] == canonical_sha256(
        artifact, exclude_fields=("artifact_hash",)
    )


def test_undispatched_templates_are_exactly_reused(monkeypatch) -> None:
    monkeypatch.setattr(
        plan,
        "verify_reconciliation",
        lambda payload: plan.EXPECTED_RECONCILIATION_HASH,
    )
    artifact = plan.build_plan(reconciliation={}, code_commit_sha=HEAD)
    rows = {row["bundle_id"]: row for row in artifact["provider_read_bundles"]}

    for bundle_id, expected_hash in plan.ORIGINAL_UNDISPATCHED_TEMPLATE_HASHES.items():
        assert rows[bundle_id]["request_template_reuse"] == "EXACT_ORIGINAL_UNDISPATCHED_TEMPLATE"
        assert rows[bundle_id]["source_request_template_hash"] == expected_hash
        assert rows[bundle_id]["provider_read_authorized"] is False


def test_nvda_continuation_is_bound_to_saved_token_and_no_replay(monkeypatch) -> None:
    monkeypatch.setattr(
        plan,
        "verify_reconciliation",
        lambda payload: plan.EXPECTED_RECONCILIATION_HASH,
    )
    artifact = plan.build_plan(reconciliation={}, code_commit_sha=HEAD)
    nvda = artifact["provider_read_bundles"][-1]
    request = nvda["request_contract"]

    assert nvda["bundle_id"] == "CR6_NVDA_NEWS_CONTINUATION"
    assert nvda["max_dispatch_attempts"] == 4
    assert request["start_page_token"] == plan.EXPECTED_NVDA_TERMINAL_TOKEN
    assert request["start_page_token_required"] is True
    assert request["replay_retained_pages"] is False
    assert request["retained_partial_evidence_hash"] == plan.EXPECTED_NVDA_RETAINED_HASH
    assert request["retained_aggregate_payload_hash"] == plan.EXPECTED_NVDA_AGGREGATE_HASH
    assert request["retained_page_raw_payload_hashes"] == list(plan.EXPECTED_NVDA_PAGE_HASHES)
    assert request["total_page_engineering_bound_including_retained"] == 6
    assert request["max_total_articles_including_retained"] == 30


def test_pagination_incomplete_does_not_starve_later_bundles(monkeypatch) -> None:
    monkeypatch.setattr(
        plan,
        "verify_reconciliation",
        lambda payload: plan.EXPECTED_RECONCILIATION_HASH,
    )
    artifact = plan.build_plan(reconciliation={}, code_commit_sha=HEAD)
    news_rows = [
        row for row in artifact["provider_read_bundles"]
        if "NEWS" in row["bundle_id"]
    ]
    assert news_rows
    assert all(
        row["bounded_pagination_incomplete_policy"].startswith("RETAIN_PARTIAL")
        for row in news_rows
    )
    assert all(
        row["transport_or_validation_error_policy"] == "STOP_IMMEDIATELY"
        for row in artifact["provider_read_bundles"]
    )


def test_verify_reconciliation_rejects_any_drift_before_plan() -> None:
    fixture = _reconciliation_fixture()
    # Fixture is intentionally not the production artifact hash; verifier must
    # fail closed instead of accepting a structurally similar substitute.
    with pytest.raises(
        plan.ResidualExternalReadContinuationPlanError,
        match="reconciliation hash drift",
    ):
        plan.verify_reconciliation(fixture)


def test_runner_contains_no_provider_or_model_execution_surface() -> None:
    source = Path(
        "scripts/b3_research_reopen_residual_external_read_continuation_plan_zero_call_v01.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "--execute-provider-reads",
        "alpaca data",
        "alpaca position",
        "alpaca account",
        "OPENAI_API_KEY",
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
        "submit_order",
        "order submit",
    )
    for token in forbidden:
        assert token not in source


def test_plan_requires_exact_code_sha(monkeypatch) -> None:
    with pytest.raises(
        plan.ResidualExternalReadContinuationPlanError,
        match="exact continuation-plan code SHA required",
    ):
        plan.build_plan(reconciliation={}, code_commit_sha="not-a-sha")
