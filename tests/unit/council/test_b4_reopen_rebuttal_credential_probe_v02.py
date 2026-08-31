from __future__ import annotations

import copy

import pytest

from aic.domain.canonical import canonical_sha256
from aic.council import reopen_rebuttal_auth_rejection_recovery as recovery
from aic.council import reopen_rebuttal_credential_probe as v01
from aic.council import reopen_rebuttal_credential_probe_v02 as v02


def _recovery_plan():
    return recovery.build_recovery_plan(code_commit_sha="a" * 40)


def _source_failed_result() -> dict:
    result = {
        "artifact_version": v01.FINAL_VERSION,
        "status": v01.FAIL_STATUS,
        "code_commit_sha": "b" * 40,
        "source_recovery_plan_artifact_hash": "a" * 64,
        "runner_dry_artifact_hash": "c" * 64,
        "probe_authorization_artifact_hash": "d" * 64,
        "attempt_event_hash": "e" * 64,
        "receipt_hash": "f" * 64,
        "probe_http_method": "GET",
        "probe_endpoint": v01.ENDPOINT,
        "probe_model_id": v01.MODEL_ID,
        "http_response_received": True,
        "http_status_code": 401,
        "request_id": "req_bad_key",
        "error_type": "invalid_request_error",
        "error_code": "invalid_api_key",
        "returned_model_id": None,
        "provider_reads": 1,
        "model_calls": 0,
        "responses_generation_calls": 0,
        "credential_probe_authority_consumed": True,
        "fresh_generation_dispatch_authorized": False,
        "new_generation_owner_approval_required": False,
        "automatic_retries": 0,
        "judge_authorized": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "next_gate": v01.NEXT_GATE_FAIL,
    }
    result["artifact_hash"] = canonical_sha256(result)
    return result


def test_v02_dry_binds_replacement_credential_without_persisting_secret() -> None:
    plan = _recovery_plan()
    source = _source_failed_result()
    key = "sk-" + "x" * 80
    dry = v02.build_dry_artifact(
        code_commit_sha="c" * 40,
        recovery_plan=plan,
        source_failed_result=source,
        expected_source_failed_result_hash=source["artifact_hash"],
        api_key=key,
    )
    assert dry["status"] == v02.DRY_STATUS
    assert dry["replacement_credential_fingerprint_sha256"] == v02.credential_fingerprint_sha256(key)
    assert dry["replacement_credential_secret_persisted"] is False
    assert key not in repr(dry)
    assert dry["provider_reads"] == 0
    assert dry["model_calls"] == 0
    assert dry["artifact_hash"] == canonical_sha256(dry, exclude_fields=("artifact_hash",))


def test_v02_dry_rejects_different_credential_at_execution_verification() -> None:
    plan = _recovery_plan()
    source = _source_failed_result()
    dry = v02.build_dry_artifact(
        code_commit_sha="c" * 40,
        recovery_plan=plan,
        source_failed_result=source,
        expected_source_failed_result_hash=source["artifact_hash"],
        api_key="sk-" + "x" * 80,
    )
    with pytest.raises(v02.B4ReopenRebuttalCredentialProbeV02Error, match="dry drift"):
        v02.verify_dry_artifact(
            dry,
            expected_code_commit_sha="c" * 40,
            recovery_plan=plan,
            source_failed_result=source,
            expected_source_failed_result_hash=source["artifact_hash"],
            api_key="sk-" + "y" * 80,
        )


def test_v02_rejects_source_result_that_does_not_prove_consumed_401() -> None:
    source = _source_failed_result()
    bad = copy.deepcopy(source)
    bad["credential_probe_authority_consumed"] = False
    bad["artifact_hash"] = canonical_sha256(bad, exclude_fields=("artifact_hash",))
    with pytest.raises(v02.B4ReopenRebuttalCredentialProbeV02Error, match="source V01 result drift"):
        v02.verify_source_failed_result(bad, expected_artifact_hash=bad["artifact_hash"])


def test_v02_authorization_binds_same_credential_fingerprint() -> None:
    plan = _recovery_plan()
    source = _source_failed_result()
    key = "sk-" + "z" * 80
    dry = v02.build_dry_artifact(
        code_commit_sha="c" * 40,
        recovery_plan=plan,
        source_failed_result=source,
        expected_source_failed_result_hash=source["artifact_hash"],
        api_key=key,
    )
    auth = v02.build_authorization(
        code_commit_sha="c" * 40,
        created_at_utc="2026-08-31T05:00:01Z",
        owner_approval_id="OWNER-PROBE-V02",
        owner_approval_at_utc="2026-08-31T05:00:00Z",
        approve_recovery_plan_hash=plan["artifact_hash"],
        approve_source_failed_result_hash=source["artifact_hash"],
        approve_dry_hash=dry["artifact_hash"],
        recovery_plan=plan,
        source_failed_result=source,
        dry_artifact=dry,
        api_key=key,
        journal_path="probe-v02.jsonl",
    )
    assert auth["replacement_credential_fingerprint_sha256"] == v02.credential_fingerprint_sha256(key)
    assert auth["provider_reads_max"] == 1
    assert auth["model_calls_max"] == 0
    assert auth["responses_generation_calls_max"] == 0
    assert auth["generation_dispatch_authorized"] is False
    assert auth["artifact_hash"] == canonical_sha256(auth, exclude_fields=("artifact_hash",))


def test_v02_attempt_and_receipt_preserve_credential_lineage() -> None:
    fingerprint = "a" * 64
    attempt = v02.build_attempt_event(
        authorization_hash="b" * 64,
        credential_fingerprint=fingerprint,
        started_at_utc="2026-08-31T05:00:00Z",
    )
    assert attempt["replacement_credential_fingerprint_sha256"] == fingerprint
    assert attempt["authorization_consumed_by_this_attempt"] is True
    assert attempt["provider_read_attempt"] == 1
    assert attempt["model_calls"] == 0

    receipt = v02.build_result_receipt(
        authorization_hash="b" * 64,
        attempt_hash=attempt["event_hash"],
        credential_fingerprint=fingerprint,
        finished_at_utc="2026-08-31T05:00:01Z",
        probe_result={
            "http_response_received": True,
            "http_status_code": 200,
            "request_id": "req_ok",
            "error_type": None,
            "error_code": None,
            "model_id": v02.MODEL_ID,
            "object": "model",
            "validation_status": "PASS",
        },
    )
    final = v02.build_final_artifact(
        code_commit_sha="c" * 40,
        recovery_plan_hash="d" * 64,
        source_failed_result_hash="e" * 64,
        dry_hash="f" * 64,
        authorization_hash="b" * 64,
        attempt_hash=attempt["event_hash"],
        receipt=receipt,
    )
    assert receipt["replacement_credential_fingerprint_sha256"] == fingerprint
    assert final["replacement_credential_fingerprint_sha256"] == fingerprint
    assert final["status"] == v02.PASS_STATUS
    assert final["next_gate"] == v02.NEXT_GATE_PASS
    assert final["fresh_generation_dispatch_authorized"] is False
    assert final["new_generation_owner_approval_required"] is True
