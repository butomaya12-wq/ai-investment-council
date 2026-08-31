from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from aic.domain.canonical import canonical_sha256
from aic.research.runtime import _http_error_diagnostics, validate_openai_api_key

from . import reopen_rebuttal_auth_rejection_recovery as recovery


DRY_VERSION = "B4_REOPEN_REBUTTAL_CREDENTIAL_PROBE_DRY_v0_1"
DRY_STATUS = "READY_FOR_EXPLICIT_OWNER_B4_REOPEN_REBUTTAL_CREDENTIAL_PROBE_AUTHORIZATION"
AUTH_VERSION = "B4_REOPEN_REBUTTAL_CREDENTIAL_PROBE_AUTHORIZATION_v0_1"
AUTH_STATUS = "AUTHORIZED_FOR_ONE_REBUTTAL_CREDENTIAL_PROBE"
EVENT_VERSION = "B4_REOPEN_REBUTTAL_CREDENTIAL_PROBE_EVENT_v0_1"
RECEIPT_VERSION = "B4_REOPEN_REBUTTAL_CREDENTIAL_PROBE_RECEIPT_v0_1"
FINAL_VERSION = "B4_REOPEN_REBUTTAL_CREDENTIAL_PROBE_RESULT_v0_1"
PASS_STATUS = "B4_REOPEN_REBUTTAL_CREDENTIAL_PROBE_PASS"
FAIL_STATUS = "B4_REOPEN_REBUTTAL_CREDENTIAL_PROBE_FAIL"
NEXT_GATE_PASS = "B4_REOPEN_REBUTTAL_FRESH_GENERATION_RECOVERY_DRY_ZERO_CALL"
NEXT_GATE_FAIL = "REPLACE_OPENAI_CREDENTIAL_AND_CREATE_NEW_CREDENTIAL_PROBE_AUTHORITY"

MODEL_ID = recovery.PROBE_MODEL_ID
ENDPOINT = recovery.PROBE_ENDPOINT
HTTP_METHOD = "GET"
PROVIDER_READS_MAX = 1


class B4ReopenRebuttalCredentialProbeError(ValueError):
    pass


def _utc(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise B4ReopenRebuttalCredentialProbeError(f"{field} missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise B4ReopenRebuttalCredentialProbeError(f"{field} invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise B4ReopenRebuttalCredentialProbeError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_object(path: str | Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise B4ReopenRebuttalCredentialProbeError(f"unable to read {label}") from exc
    if not isinstance(value, dict):
        raise B4ReopenRebuttalCredentialProbeError(f"{label} root must be object")
    return value


def verify_recovery_plan(plan: Mapping[str, Any]) -> str:
    observed = plan.get("artifact_hash")
    if not isinstance(observed, str) or observed != canonical_sha256(plan, exclude_fields=("artifact_hash",)):
        raise B4ReopenRebuttalCredentialProbeError("recovery plan self-hash mismatch")
    exact = {
        "artifact_version": recovery.ARTIFACT_VERSION,
        "status": recovery.PASS_STATUS,
        "source_paid_authorization_artifact_hash": recovery.SOURCE_AUTH_HASH,
        "source_receipt_hash": recovery.SOURCE_RECEIPT_HASH,
        "source_blocked_artifact_hash": recovery.SOURCE_BLOCKED_HASH,
        "source_authority_consumed": True,
        "source_authority_rerun_authorized": False,
        "source_model_calls_known_completed": 0,
        "source_successful_rebuttal_processed_records": 0,
        "forensic_classification": "HTTP_AUTHENTICATION_REJECTION_INVALID_API_KEY",
        "credential_probe_required": True,
        "credential_probe_endpoint": ENDPOINT,
        "credential_probe_model_id": MODEL_ID,
        "credential_probe_provider_reads_max_if_later_approved": 1,
        "credential_probe_provider_read_authorized": False,
        "generation_dispatch_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "judge_authorized": False,
        "next_gate": recovery.NEXT_GATE,
    }
    for field, expected in exact.items():
        if plan.get(field) != expected:
            raise B4ReopenRebuttalCredentialProbeError(f"recovery plan drift: {field}")
    return observed


def build_dry_artifact(*, code_commit_sha: str, recovery_plan: Mapping[str, Any]) -> dict[str, Any]:
    plan_hash = verify_recovery_plan(recovery_plan)
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise B4ReopenRebuttalCredentialProbeError("exact lowercase git SHA required")
    artifact: dict[str, Any] = {
        "artifact_version": DRY_VERSION,
        "status": DRY_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_recovery_plan_artifact_hash": plan_hash,
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
    dry: Mapping[str, Any], *, expected_code_commit_sha: str, recovery_plan: Mapping[str, Any]
) -> str:
    plan_hash = verify_recovery_plan(recovery_plan)
    observed = dry.get("artifact_hash")
    if not isinstance(observed, str) or observed != canonical_sha256(dry, exclude_fields=("artifact_hash",)):
        raise B4ReopenRebuttalCredentialProbeError("credential probe dry self-hash mismatch")
    exact = {
        "artifact_version": DRY_VERSION,
        "status": DRY_STATUS,
        "code_commit_sha": expected_code_commit_sha,
        "source_recovery_plan_artifact_hash": plan_hash,
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
            raise B4ReopenRebuttalCredentialProbeError(f"credential probe dry drift: {field}")
    return observed


def build_authorization(
    *,
    code_commit_sha: str,
    created_at_utc: str,
    owner_approval_id: str,
    owner_approval_at_utc: str,
    approve_recovery_plan_hash: str,
    approve_dry_hash: str,
    recovery_plan: Mapping[str, Any],
    dry_artifact: Mapping[str, Any],
    journal_path: str,
) -> dict[str, Any]:
    plan_hash = verify_recovery_plan(recovery_plan)
    dry_hash = verify_dry_artifact(
        dry_artifact,
        expected_code_commit_sha=code_commit_sha,
        recovery_plan=recovery_plan,
    )
    if approve_recovery_plan_hash != plan_hash:
        raise B4ReopenRebuttalCredentialProbeError("owner approval recovery-plan hash mismatch")
    if approve_dry_hash != dry_hash:
        raise B4ReopenRebuttalCredentialProbeError("owner approval credential-probe dry hash mismatch")
    if not isinstance(owner_approval_id, str) or not owner_approval_id or owner_approval_id != owner_approval_id.strip() or any(ch.isspace() for ch in owner_approval_id):
        raise B4ReopenRebuttalCredentialProbeError("owner approval ID invalid")
    created = _utc(created_at_utc, field="probe authorization created_at")
    owner_at = _utc(owner_approval_at_utc, field="probe owner approval time")
    if datetime.fromisoformat(owner_at.replace("Z", "+00:00")) > datetime.fromisoformat(created.replace("Z", "+00:00")):
        raise B4ReopenRebuttalCredentialProbeError("owner approval cannot postdate probe authorization")
    artifact: dict[str, Any] = {
        "artifact_version": AUTH_VERSION,
        "status": AUTH_STATUS,
        "code_commit_sha": code_commit_sha,
        "created_at_utc": created,
        "owner_approval_id": owner_approval_id,
        "owner_approval_at_utc": owner_at,
        "source_recovery_plan_artifact_hash": plan_hash,
        "runner_dry_artifact_hash": dry_hash,
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


def build_attempt_event(*, authorization_hash: str, started_at_utc: str) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_version": EVENT_VERSION,
        "event_type": "CREDENTIAL_PROBE_HTTP_ATTEMPT",
        "started_at_utc": _utc(started_at_utc, field="probe attempt start"),
        "paid_authorization_artifact_hash": authorization_hash,
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


def probe_model_metadata(*, api_key: str, timeout_seconds: int = 30) -> dict[str, Any]:
    key = validate_openai_api_key(api_key)
    request = Request(
        ENDPOINT,
        method=HTTP_METHOD,
        headers={"Authorization": f"Bearer {key}"},
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(65_536)
            status = getattr(response, "status", 200)
            request_id = None if response.headers is None else response.headers.get("x-request-id")
    except HTTPError as exc:
        diagnostics = _http_error_diagnostics(exc)
        return {
            "http_response_received": True,
            "http_status_code": exc.code,
            "request_id": diagnostics.get("request_id"),
            "error_type": diagnostics.get("error_type"),
            "error_code": diagnostics.get("error_code"),
            "model_id": None,
            "object": None,
            "validation_status": "FAIL",
        }
    except URLError:
        return {
            "http_response_received": False,
            "http_status_code": None,
            "request_id": None,
            "error_type": "NETWORK_URL_ERROR",
            "error_code": None,
            "model_id": None,
            "object": None,
            "validation_status": "FAIL",
        }
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {
            "http_response_received": True,
            "http_status_code": status,
            "request_id": request_id,
            "error_type": "INVALID_JSON_RESPONSE",
            "error_code": None,
            "model_id": None,
            "object": None,
            "validation_status": "FAIL",
        }
    if not isinstance(decoded, Mapping):
        return {
            "http_response_received": True,
            "http_status_code": status,
            "request_id": request_id,
            "error_type": "INVALID_RESPONSE_SHAPE",
            "error_code": None,
            "model_id": None,
            "object": None,
            "validation_status": "FAIL",
        }
    model_id = decoded.get("id")
    object_type = decoded.get("object")
    valid = status == 200 and model_id == MODEL_ID and object_type == "model"
    return {
        "http_response_received": True,
        "http_status_code": status,
        "request_id": request_id,
        "error_type": None if valid else "MODEL_METADATA_MISMATCH",
        "error_code": None,
        "model_id": model_id if isinstance(model_id, str) else None,
        "object": object_type if isinstance(object_type, str) else None,
        "validation_status": "PASS" if valid else "FAIL",
    }


def build_result_receipt(
    *,
    authorization_hash: str,
    attempt_hash: str,
    finished_at_utc: str,
    probe_result: Mapping[str, Any],
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "receipt_version": RECEIPT_VERSION,
        "event_version": EVENT_VERSION,
        "event_type": "CREDENTIAL_PROBE_HTTP_RESULT",
        "finished_at_utc": _utc(finished_at_utc, field="probe result finish"),
        "paid_authorization_artifact_hash": authorization_hash,
        "attempt_event_hash": attempt_hash,
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
    dry_hash: str,
    authorization_hash: str,
    attempt_hash: str,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    receipt_hash = receipt.get("receipt_hash")
    if not isinstance(receipt_hash, str) or receipt_hash != canonical_sha256(receipt, exclude_fields=("receipt_hash",)):
        raise B4ReopenRebuttalCredentialProbeError("credential probe receipt self-hash mismatch")
    passed = receipt.get("validation_status") == "PASS"
    if passed:
        if receipt.get("http_status_code") != 200 or receipt.get("returned_model_id") != MODEL_ID or receipt.get("returned_object") != "model":
            raise B4ReopenRebuttalCredentialProbeError("PASS credential probe receipt metadata drift")
    artifact: dict[str, Any] = {
        "artifact_version": FINAL_VERSION,
        "status": PASS_STATUS if passed else FAIL_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_recovery_plan_artifact_hash": recovery_plan_hash,
        "runner_dry_artifact_hash": dry_hash,
        "probe_authorization_artifact_hash": authorization_hash,
        "attempt_event_hash": attempt_hash,
        "receipt_hash": receipt_hash,
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


def load_recovery_plan(path: str | Path) -> dict[str, Any]:
    plan = _read_object(path, label="Rebuttal auth-rejection recovery plan")
    verify_recovery_plan(plan)
    return plan
