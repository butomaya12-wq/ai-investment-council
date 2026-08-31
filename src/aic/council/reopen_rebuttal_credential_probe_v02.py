from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256
from aic.research.runtime import validate_openai_api_key

from . import reopen_rebuttal_credential_probe as v01


DRY_VERSION = "B4_REOPEN_REBUTTAL_CREDENTIAL_PROBE_DRY_v0_2"
DRY_STATUS = "READY_FOR_EXPLICIT_OWNER_B4_REOPEN_REBUTTAL_CREDENTIAL_PROBE_V02_AUTHORIZATION"
AUTH_VERSION = "B4_REOPEN_REBUTTAL_CREDENTIAL_PROBE_AUTHORIZATION_v0_2"
AUTH_STATUS = "AUTHORIZED_FOR_ONE_REBUTTAL_CREDENTIAL_PROBE_V02"
EVENT_VERSION = "B4_REOPEN_REBUTTAL_CREDENTIAL_PROBE_EVENT_v0_2"
RECEIPT_VERSION = "B4_REOPEN_REBUTTAL_CREDENTIAL_PROBE_RECEIPT_v0_2"
FINAL_VERSION = "B4_REOPEN_REBUTTAL_CREDENTIAL_PROBE_RESULT_v0_2"
PASS_STATUS = "B4_REOPEN_REBUTTAL_CREDENTIAL_PROBE_V02_PASS"
FAIL_STATUS = "B4_REOPEN_REBUTTAL_CREDENTIAL_PROBE_V02_FAIL"
NEXT_GATE_PASS = "B4_REOPEN_REBUTTAL_FRESH_GENERATION_RECOVERY_DRY_ZERO_CALL"
NEXT_GATE_FAIL = "REPLACE_OPENAI_CREDENTIAL_AND_CREATE_NEW_CREDENTIAL_PROBE_AUTHORITY"

MODEL_ID = v01.MODEL_ID
ENDPOINT = v01.ENDPOINT
HTTP_METHOD = "GET"
PROVIDER_READS_MAX = 1


class B4ReopenRebuttalCredentialProbeV02Error(ValueError):
    pass


def _utc(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise B4ReopenRebuttalCredentialProbeV02Error(f"{field} missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise B4ReopenRebuttalCredentialProbeV02Error(f"{field} invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise B4ReopenRebuttalCredentialProbeV02Error(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def credential_fingerprint_sha256(api_key: str) -> str:
    key = validate_openai_api_key(api_key)
    if not key.startswith("sk-"):
        raise B4ReopenRebuttalCredentialProbeV02Error("OPENAI_API_KEY must start with sk-")
    if "*" in key or "…" in key or "..." in key:
        raise B4ReopenRebuttalCredentialProbeV02Error("OPENAI_API_KEY appears masked or abbreviated")
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in key):
        raise B4ReopenRebuttalCredentialProbeV02Error("OPENAI_API_KEY must contain printable ASCII only")
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def verify_source_failed_result(
    result: Mapping[str, Any], *, expected_artifact_hash: str
) -> str:
    observed = result.get("artifact_hash")
    if not isinstance(observed, str) or observed != canonical_sha256(result, exclude_fields=("artifact_hash",)):
        raise B4ReopenRebuttalCredentialProbeV02Error("source V01 result self-hash mismatch")
    if observed != expected_artifact_hash:
        raise B4ReopenRebuttalCredentialProbeV02Error("source V01 result hash mismatch")
    exact = {
        "artifact_version": v01.FINAL_VERSION,
        "status": v01.FAIL_STATUS,
        "probe_http_method": HTTP_METHOD,
        "probe_endpoint": ENDPOINT,
        "probe_model_id": MODEL_ID,
        "http_response_received": True,
        "http_status_code": 401,
        "error_type": "invalid_request_error",
        "error_code": "invalid_api_key",
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
    for field, expected in exact.items():
        if result.get(field) != expected:
            raise B4ReopenRebuttalCredentialProbeV02Error(f"source V01 result drift: {field}")
    return observed


def build_dry_artifact(
    *,
    code_commit_sha: str,
    recovery_plan: Mapping[str, Any],
    source_failed_result: Mapping[str, Any],
    expected_source_failed_result_hash: str,
    api_key: str,
) -> dict[str, Any]:
    recovery_hash = v01.verify_recovery_plan(recovery_plan)
    source_hash = verify_source_failed_result(
        source_failed_result,
        expected_artifact_hash=expected_source_failed_result_hash,
    )
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise B4ReopenRebuttalCredentialProbeV02Error("exact lowercase git SHA required")
    fingerprint = credential_fingerprint_sha256(api_key)
    artifact: dict[str, Any] = {
        "artifact_version": DRY_VERSION,
        "status": DRY_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_recovery_plan_artifact_hash": recovery_hash,
        "source_failed_v01_result_artifact_hash": source_hash,
        "source_failed_v01_authority_consumed": True,
        "replacement_credential_fingerprint_sha256": fingerprint,
        "replacement_credential_secret_persisted": False,
        "credential_hygiene_status": "PASS",
        "probe_http_method": HTTP_METHOD,
        "probe_endpoint": ENDPOINT,
        "probe_model_id": MODEL_ID,
        "provider_reads_max_if_later_approved": PROVIDER_READS_MAX,
        "model_calls_max": 0,
        "responses_generation_calls_max": 0,
        "owner_approval_required": True,
        "probe_provider_read_authorized": False,
        "generation_dispatch_authorized": False,
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_DURABLE_CREDENTIAL_PROBE_HTTP_ATTEMPT",
        "automatic_retries": 0,
        "automatic_repair_calls_authorized": 0,
        "judge_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_dry_artifact(
    dry: Mapping[str, Any],
    *,
    expected_code_commit_sha: str,
    recovery_plan: Mapping[str, Any],
    source_failed_result: Mapping[str, Any],
    expected_source_failed_result_hash: str,
    api_key: str,
) -> str:
    recovery_hash = v01.verify_recovery_plan(recovery_plan)
    source_hash = verify_source_failed_result(
        source_failed_result,
        expected_artifact_hash=expected_source_failed_result_hash,
    )
    fingerprint = credential_fingerprint_sha256(api_key)
    observed = dry.get("artifact_hash")
    if not isinstance(observed, str) or observed != canonical_sha256(dry, exclude_fields=("artifact_hash",)):
        raise B4ReopenRebuttalCredentialProbeV02Error("credential probe V02 dry self-hash mismatch")
    exact = {
        "artifact_version": DRY_VERSION,
        "status": DRY_STATUS,
        "code_commit_sha": expected_code_commit_sha,
        "source_recovery_plan_artifact_hash": recovery_hash,
        "source_failed_v01_result_artifact_hash": source_hash,
        "source_failed_v01_authority_consumed": True,
        "replacement_credential_fingerprint_sha256": fingerprint,
        "replacement_credential_secret_persisted": False,
        "credential_hygiene_status": "PASS",
        "probe_http_method": HTTP_METHOD,
        "probe_endpoint": ENDPOINT,
        "probe_model_id": MODEL_ID,
        "provider_reads_max_if_later_approved": 1,
        "model_calls_max": 0,
        "responses_generation_calls_max": 0,
        "owner_approval_required": True,
        "probe_provider_read_authorized": False,
        "generation_dispatch_authorized": False,
        "automatic_retries": 0,
        "automatic_repair_calls_authorized": 0,
        "judge_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    for field, expected in exact.items():
        if dry.get(field) != expected:
            raise B4ReopenRebuttalCredentialProbeV02Error(f"credential probe V02 dry drift: {field}")
    return observed


def build_authorization(
    *,
    code_commit_sha: str,
    created_at_utc: str,
    owner_approval_id: str,
    owner_approval_at_utc: str,
    approve_recovery_plan_hash: str,
    approve_source_failed_result_hash: str,
    approve_dry_hash: str,
    recovery_plan: Mapping[str, Any],
    source_failed_result: Mapping[str, Any],
    dry_artifact: Mapping[str, Any],
    api_key: str,
    journal_path: str,
) -> dict[str, Any]:
    recovery_hash = v01.verify_recovery_plan(recovery_plan)
    source_hash = verify_source_failed_result(
        source_failed_result,
        expected_artifact_hash=approve_source_failed_result_hash,
    )
    dry_hash = verify_dry_artifact(
        dry_artifact,
        expected_code_commit_sha=code_commit_sha,
        recovery_plan=recovery_plan,
        source_failed_result=source_failed_result,
        expected_source_failed_result_hash=source_hash,
        api_key=api_key,
    )
    if approve_recovery_plan_hash != recovery_hash:
        raise B4ReopenRebuttalCredentialProbeV02Error("owner approval recovery-plan hash mismatch")
    if approve_dry_hash != dry_hash:
        raise B4ReopenRebuttalCredentialProbeV02Error("owner approval credential-probe V02 dry hash mismatch")
    if not isinstance(owner_approval_id, str) or not owner_approval_id or owner_approval_id != owner_approval_id.strip() or any(ch.isspace() for ch in owner_approval_id):
        raise B4ReopenRebuttalCredentialProbeV02Error("owner approval ID invalid")
    created = _utc(created_at_utc, field="probe V02 authorization created_at")
    owner_at = _utc(owner_approval_at_utc, field="probe V02 owner approval time")
    if datetime.fromisoformat(owner_at.replace("Z", "+00:00")) > datetime.fromisoformat(created.replace("Z", "+00:00")):
        raise B4ReopenRebuttalCredentialProbeV02Error("owner approval cannot postdate probe V02 authorization")
    fingerprint = credential_fingerprint_sha256(api_key)
    artifact: dict[str, Any] = {
        "artifact_version": AUTH_VERSION,
        "status": AUTH_STATUS,
        "code_commit_sha": code_commit_sha,
        "created_at_utc": created,
        "owner_approval_id": owner_approval_id,
        "owner_approval_at_utc": owner_at,
        "source_recovery_plan_artifact_hash": recovery_hash,
        "source_failed_v01_result_artifact_hash": source_hash,
        "runner_dry_artifact_hash": dry_hash,
        "replacement_credential_fingerprint_sha256": fingerprint,
        "replacement_credential_secret_persisted": False,
        "probe_http_method": HTTP_METHOD,
        "probe_endpoint": ENDPOINT,
        "probe_model_id": MODEL_ID,
        "provider_reads_max": 1,
        "model_calls_max": 0,
        "responses_generation_calls_max": 0,
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_DURABLE_CREDENTIAL_PROBE_HTTP_ATTEMPT",
        "journal_path": journal_path,
        "automatic_retries": 0,
        "generation_dispatch_authorized": False,
        "judge_authorized": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def build_attempt_event(
    *, authorization_hash: str, credential_fingerprint: str, started_at_utc: str
) -> dict[str, Any]:
    if len(credential_fingerprint) != 64 or any(ch not in "0123456789abcdef" for ch in credential_fingerprint):
        raise B4ReopenRebuttalCredentialProbeV02Error("credential fingerprint must be lowercase SHA-256")
    event: dict[str, Any] = {
        "event_version": EVENT_VERSION,
        "event_type": "CREDENTIAL_PROBE_HTTP_ATTEMPT",
        "started_at_utc": _utc(started_at_utc, field="probe V02 attempt start"),
        "paid_authorization_artifact_hash": authorization_hash,
        "replacement_credential_fingerprint_sha256": credential_fingerprint,
        "http_method": HTTP_METHOD,
        "endpoint": ENDPOINT,
        "model_id": MODEL_ID,
        "provider_read_attempt": 1,
        "model_calls": 0,
        "responses_generation_calls": 0,
        "authorization_consumed_by_this_attempt": True,
        "automatic_retry": False,
        "generation_dispatch_authorized": False,
        "judge_authorized": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    event["event_hash"] = canonical_sha256(event)
    return event


def build_result_receipt(
    *,
    authorization_hash: str,
    attempt_hash: str,
    credential_fingerprint: str,
    finished_at_utc: str,
    probe_result: Mapping[str, Any],
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "receipt_version": RECEIPT_VERSION,
        "event_version": EVENT_VERSION,
        "event_type": "CREDENTIAL_PROBE_HTTP_RESULT",
        "finished_at_utc": _utc(finished_at_utc, field="probe V02 result finish"),
        "paid_authorization_artifact_hash": authorization_hash,
        "attempt_event_hash": attempt_hash,
        "replacement_credential_fingerprint_sha256": credential_fingerprint,
        "http_method": HTTP_METHOD,
        "endpoint": ENDPOINT,
        "expected_model_id": MODEL_ID,
        "http_response_received": probe_result.get("http_response_received"),
        "http_status_code": probe_result.get("http_status_code"),
        "request_id": probe_result.get("request_id"),
        "error_type": probe_result.get("error_type"),
        "error_code": probe_result.get("error_code"),
        "returned_model_id": probe_result.get("model_id"),
        "returned_object": probe_result.get("object"),
        "validation_status": probe_result.get("validation_status"),
        "provider_reads": 1,
        "model_calls": 0,
        "responses_generation_calls": 0,
        "raw_provider_response_persisted": False,
        "generation_dispatch_authorized": False,
        "automatic_retry": False,
        "judge_authorized": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    receipt["receipt_hash"] = canonical_sha256(receipt)
    return receipt


def build_final_artifact(
    *,
    code_commit_sha: str,
    recovery_plan_hash: str,
    source_failed_result_hash: str,
    dry_hash: str,
    authorization_hash: str,
    attempt_hash: str,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    receipt_hash = receipt.get("receipt_hash")
    if not isinstance(receipt_hash, str) or receipt_hash != canonical_sha256(receipt, exclude_fields=("receipt_hash",)):
        raise B4ReopenRebuttalCredentialProbeV02Error("credential probe V02 receipt self-hash mismatch")
    passed = receipt.get("validation_status") == "PASS"
    artifact: dict[str, Any] = {
        "artifact_version": FINAL_VERSION,
        "status": PASS_STATUS if passed else FAIL_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_recovery_plan_artifact_hash": recovery_plan_hash,
        "source_failed_v01_result_artifact_hash": source_failed_result_hash,
        "runner_dry_artifact_hash": dry_hash,
        "probe_authorization_artifact_hash": authorization_hash,
        "attempt_event_hash": attempt_hash,
        "receipt_hash": receipt_hash,
        "replacement_credential_fingerprint_sha256": receipt.get("replacement_credential_fingerprint_sha256"),
        "probe_http_method": HTTP_METHOD,
        "probe_endpoint": ENDPOINT,
        "probe_model_id": MODEL_ID,
        "http_response_received": receipt.get("http_response_received"),
        "http_status_code": receipt.get("http_status_code"),
        "request_id": receipt.get("request_id"),
        "error_type": receipt.get("error_type"),
        "error_code": receipt.get("error_code"),
        "returned_model_id": receipt.get("returned_model_id"),
        "provider_reads": 1,
        "model_calls": 0,
        "responses_generation_calls": 0,
        "credential_probe_authority_consumed": True,
        "fresh_generation_dispatch_authorized": False,
        "new_generation_owner_approval_required": passed,
        "automatic_retries": 0,
        "judge_authorized": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "next_gate": NEXT_GATE_PASS if passed else NEXT_GATE_FAIL,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def probe_model_metadata(*, api_key: str, timeout_seconds: int = 30) -> dict[str, Any]:
    return v01.probe_model_metadata(api_key=api_key, timeout_seconds=timeout_seconds)
