from __future__ import annotations

from decimal import Decimal

import pytest

from aic.domain.canonical import canonical_sha256
from aic.council import reopen_rebuttal_auth_rejection_recovery as rr


def test_recovery_plan_preserves_consumed_authority_and_requires_probe() -> None:
    artifact = rr.build_recovery_plan(code_commit_sha="a" * 40)
    assert artifact["status"] == rr.PASS_STATUS
    assert artifact["source_authority_consumed"] is True
    assert artifact["source_authority_rerun_authorized"] is False
    assert artifact["source_durable_dispatch_attempts"] == 1
    assert artifact["source_model_calls_known_completed"] == 0
    assert artifact["source_successful_rebuttal_processed_records"] == 0
    assert artifact["forensic_classification"] == "HTTP_AUTHENTICATION_REJECTION_INVALID_API_KEY"
    assert artifact["forensic_http_status_code"] == 401
    assert artifact["forensic_error_code"] == "invalid_api_key"
    assert artifact["historical_receipt_provider_dispatch_state_unknown"] is True
    assert artifact["reconciled_transport_outcome_unknown"] is False
    assert artifact["rejected_attempt_billing_resolution"] == "NOT_PROVEN_FROM_USAGE_RECEIPT"
    assert artifact["credential_probe_required"] is True
    assert artifact["credential_probe_provider_read_authorized"] is False
    assert artifact["generation_dispatch_authorized"] is False
    assert artifact["model_calls"] == 0
    assert artifact["provider_reads"] == 0
    assert artifact["judge_authorized"] is False
    assert artifact["artifact_hash"] == canonical_sha256(artifact, exclude_fields=("artifact_hash",))


def test_recovery_plan_requires_all_three_fresh_outputs_and_preserves_requests() -> None:
    artifact = rr.build_recovery_plan(code_commit_sha="b" * 40)
    assert artifact["fresh_rebuttal_outputs_required"] == 3
    assert artifact["candidate_order"] == ["NVDA", "MSFT", "META"]
    assert [row["request_hash"] for row in artifact["fresh_generation_request_rows"]] == [
        rr.REQUEST_HASHES["NVDA"],
        rr.REQUEST_HASHES["MSFT"],
        rr.REQUEST_HASHES["META"],
    ]
    assert artifact["fresh_generation_request_manifest_hash"] == rr.SOURCE_REQUEST_MANIFEST_HASH
    assert artifact["fresh_generation_cost_ceiling_usd_if_later_approved"] == "1.73851"


def test_recovery_conservative_spend_math_keeps_rejected_attempt_unresolved() -> None:
    assert rr.SOURCE_REJECTED_ATTEMPT_COST_UPPER_USD == Decimal("0.55782")
    assert rr.REBUTTAL_STAGE_UPPER_AFTER_FRESH_RECOVERY_USD == Decimal("2.29633")
    assert rr.AGGREGATE_INITIAL_PLUS_REBUTTAL_UPPER_AFTER_RECOVERY_USD == Decimal("2.7926325")
    artifact = rr.build_recovery_plan(code_commit_sha="c" * 40)
    assert artifact["conservative_rebuttal_stage_spend_upper_after_fresh_recovery_usd"] == "2.29633"
    assert artifact["conservative_initial_plus_rebuttal_spend_upper_after_fresh_recovery_usd"] == "2.7926325"


def test_hash_bound_verifier_fails_closed() -> None:
    raw = {"value": 1}
    raw["artifact_hash"] = canonical_sha256(raw)
    with pytest.raises(rr.B4ReopenRebuttalAuthRejectionRecoveryError, match="drift"):
        rr._verify_hash_bound(raw, field="artifact_hash", expected="f" * 64, label="fixture")


def test_recovery_plan_rejects_invalid_code_sha() -> None:
    with pytest.raises(rr.B4ReopenRebuttalAuthRejectionRecoveryError, match="exact lowercase"):
        rr.build_recovery_plan(code_commit_sha="not-a-sha")
