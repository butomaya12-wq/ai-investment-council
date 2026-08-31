from __future__ import annotations

from pathlib import Path

import pytest

from aic.domain.canonical import canonical_sha256
from aic.research import reopen_judge_durable_provider_read_failure_reconciliation_v01 as reconciliation


HEAD = "a" * 40


def test_build_reconciliation_retains_partial_nvda_without_resolving_targets(monkeypatch) -> None:
    monkeypatch.setattr(
        reconciliation,
        "verify_local_replay",
        lambda payload: reconciliation.EXPECTED_LOCAL_REPLAY_HASH,
    )
    monkeypatch.setattr(
        reconciliation,
        "verify_authorization",
        lambda payload: reconciliation.EXPECTED_AUTHORIZATION_HASH,
    )
    monkeypatch.setattr(
        reconciliation,
        "verify_journal",
        lambda rows: {
            "journal_event_count": 4,
            "provider_dispatch_attempt_count": 2,
            "provider_response_receipt_count": 2,
            "first_dispatch_event_hash": reconciliation.EXPECTED_FIRST_DISPATCH_EVENT_HASH,
            "last_dispatch_event_hash": reconciliation.EXPECTED_LAST_DISPATCH_EVENT_HASH,
        },
    )
    monkeypatch.setattr(
        reconciliation,
        "verify_result",
        lambda payload: {
            "result_artifact_hash": reconciliation.EXPECTED_RESULT_HASH,
            "partial_response_artifact_hash": reconciliation.EXPECTED_PARTIAL_RESPONSE_HASH,
            "nvda_aggregate_payload_hash": reconciliation.EXPECTED_NVDA_AGGREGATE_HASH,
            "nvda_page_raw_payload_hashes": list(reconciliation.EXPECTED_NVDA_PAGE_HASHES),
            "nvda_terminal_next_page_token": reconciliation.EXPECTED_NVDA_TERMINAL_NEXT_PAGE_TOKEN,
            "nvda_retained_article_count": 10,
        },
    )

    artifact = reconciliation.build_reconciliation(
        local_replay={},
        authorization={},
        journal_rows=[],
        result={},
        code_commit_sha=HEAD,
    )

    assert artifact["status"] == reconciliation.PASS_STATUS
    assert artifact["authority_consumed"] is True
    assert artifact["original_authority_reusable"] is False
    assert artifact["original_production_read_pass_rerun_allowed"] is False
    assert artifact["provider_dispatch_attempts_observed"] == 2
    assert artifact["provider_response_receipts_observed"] == 2
    assert artifact["partial_provider_bundle_count"] == 1
    assert artifact["completed_provider_bundle_count"] == 0
    assert artifact["undispatched_provider_bundle_ids"] == list(reconciliation.UNDISPATCHED_BUNDLE_IDS)
    assert artifact["retained_partial_evidence_usable"] is True
    assert artifact["retained_partial_evidence_complete"] is False
    assert artifact["nvda_retained_article_count"] == 10
    assert artifact["nvda_retained_page_count"] == 2
    assert artifact["nvda_continuation_must_start_from_terminal_token"] is True
    assert artifact["nvda_replay_of_retained_pages_allowed"] is False
    assert artifact["nvda_max_additional_pages_without_expanding_original_total_bound"] == 4
    assert artifact["residual_external_read_target_count"] == 7
    assert artifact["resolved_target_count_this_step"] == 0
    assert artifact["provider_reads_this_step"] == 0
    assert artifact["provider_reads_authorized_this_step"] is False
    assert artifact["model_calls_this_step"] == 0
    assert artifact["model_calls_authorized_this_step"] is False
    assert artifact["broker_writes"] == 0
    assert artifact["alpaca_orders"] == 0
    assert artifact["next_gate"] == reconciliation.NEXT_GATE
    assert artifact["artifact_hash"] == canonical_sha256(
        artifact,
        exclude_fields=("artifact_hash",),
    )


def test_build_reconciliation_requires_exact_commit_sha(monkeypatch) -> None:
    with pytest.raises(
        reconciliation.DurableProviderReadFailureReconciliationError,
        match="exact reconciliation code SHA required",
    ):
        reconciliation.build_reconciliation(
            local_replay={},
            authorization={},
            journal_rows=[],
            result={},
            code_commit_sha="not-a-sha",
        )


def test_verify_journal_rejects_any_event_count_other_than_exact_failure_shape() -> None:
    with pytest.raises(
        reconciliation.DurableProviderReadFailureReconciliationError,
        match="exactly four durable events",
    ):
        reconciliation.verify_journal([])


def test_verify_local_replay_rejects_noncanonical_artifact() -> None:
    with pytest.raises(
        reconciliation.DurableProviderReadFailureReconciliationError,
        match="artifact_hash missing",
    ):
        reconciliation.verify_local_replay({})


def test_verify_authorization_rejects_noncanonical_artifact() -> None:
    with pytest.raises(
        reconciliation.DurableProviderReadFailureReconciliationError,
        match="artifact_hash missing",
    ):
        reconciliation.verify_authorization({})


def test_verify_result_rejects_noncanonical_artifact() -> None:
    with pytest.raises(
        reconciliation.DurableProviderReadFailureReconciliationError,
        match="artifact_hash missing",
    ):
        reconciliation.verify_result({})


def test_runner_is_zero_call_and_contains_no_provider_execution_surface() -> None:
    source = Path(
        "scripts/b3_research_reopen_durable_provider_read_failure_reconciliation_zero_call_v01.py"
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


def test_reconciliation_never_shrinks_residual_set_on_partial_pagination(monkeypatch) -> None:
    monkeypatch.setattr(
        reconciliation,
        "verify_local_replay",
        lambda payload: reconciliation.EXPECTED_LOCAL_REPLAY_HASH,
    )
    monkeypatch.setattr(
        reconciliation,
        "verify_authorization",
        lambda payload: reconciliation.EXPECTED_AUTHORIZATION_HASH,
    )
    monkeypatch.setattr(
        reconciliation,
        "verify_journal",
        lambda rows: {
            "journal_event_count": 4,
            "provider_dispatch_attempt_count": 2,
            "provider_response_receipt_count": 2,
            "first_dispatch_event_hash": reconciliation.EXPECTED_FIRST_DISPATCH_EVENT_HASH,
            "last_dispatch_event_hash": reconciliation.EXPECTED_LAST_DISPATCH_EVENT_HASH,
        },
    )
    monkeypatch.setattr(
        reconciliation,
        "verify_result",
        lambda payload: {
            "result_artifact_hash": reconciliation.EXPECTED_RESULT_HASH,
            "partial_response_artifact_hash": reconciliation.EXPECTED_PARTIAL_RESPONSE_HASH,
            "nvda_aggregate_payload_hash": reconciliation.EXPECTED_NVDA_AGGREGATE_HASH,
            "nvda_page_raw_payload_hashes": list(reconciliation.EXPECTED_NVDA_PAGE_HASHES),
            "nvda_terminal_next_page_token": reconciliation.EXPECTED_NVDA_TERMINAL_NEXT_PAGE_TOKEN,
            "nvda_retained_article_count": 10,
        },
    )
    artifact = reconciliation.build_reconciliation(
        local_replay={},
        authorization={},
        journal_rows=[],
        result={},
        code_commit_sha=HEAD,
    )
    assert artifact["residual_external_read_target_ids"] == list(reconciliation.RESIDUAL_TARGET_IDS)
    assert artifact["resolved_target_count_this_step"] == 0
