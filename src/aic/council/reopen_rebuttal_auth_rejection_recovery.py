from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from aic.domain.canonical import canonical_sha256


ARTIFACT_VERSION = "B4_REOPEN_REBUTTAL_AUTH_REJECTION_RECOVERY_PLAN_ZERO_CALL_v0_1"
PASS_STATUS = "B4_REOPEN_REBUTTAL_AUTH_REJECTION_RECOVERY_ZERO_CALL_PASS"
NEXT_GATE = "B4_REOPEN_REBUTTAL_CREDENTIAL_PROBE_DRY_ZERO_CALL"

SOURCE_CODE_SHA = "209a329da78aa3390962a4ebe336c9a1c888d271"
SOURCE_RUN_ID = "AIC-B4-REOPEN-REBUTTAL-20260831T034228396218Z-144584082ad5"
SOURCE_COST_HASH = "7213763ddf0c0a5f6622819d278de194685a796abce39095440e6534217d8838"
SOURCE_REQUEST_MANIFEST_HASH = "ff423f97dc2398befa25dd8bedbfd92bc46562e56c302caa67ddb2e1c8f50693"
SOURCE_DRY_HASH = "e1bab83ec9e609f83bb92975b1756f5f4cc723e80cae5542e73f80363eaf8956"
SOURCE_AUTH_HASH = "05ec4a12eb4cd5e3fade831a714b5595c16125f113f69d79609e756437804c7d"
SOURCE_RECEIPT_HASH = "ba91a49b4dc7dfde1141e5ce8dc54af5faf5d7f87823889ca6d86ddb53d82097"
SOURCE_BLOCKED_HASH = "ba72c2ecb5c0c9da62c2a02e81c8eef83a2f4f7e15ffdcfbd90a06047e54de74"
SOURCE_OWNER_APPROVAL_ID = "OWNER-B4-REOPEN-REBUTTAL-PRODUCTION-V01"
SOURCE_OWNER_APPROVAL_AT_UTC = "2026-08-31T03:35:18Z"

EXPECTED_AUTH_VERSION = "B4_REOPEN_REBUTTAL_RUNTIME_PAID_AUTHORIZATION_v0_2"
EXPECTED_AUTH_STATUS = "AUTHORIZED_FOR_ONE_B4_REOPEN_REBUTTAL_RUN"
EXPECTED_EVENT_VERSION = "B4_REOPEN_REBUTTAL_RUNTIME_JOURNAL_EVENT_v0_2"
EXPECTED_RECEIPT_VERSION = "B4_REOPEN_REBUTTAL_RUNTIME_PAID_CALL_RECEIPT_v0_2"
EXPECTED_BLOCKED_VERSION = "B4_REOPEN_REBUTTAL_COUNCIL_BLOCKED_v0_2"
EXPECTED_BLOCKED_STATUS = "B4_REOPEN_REBUTTAL_COUNCIL_NOT_FROZEN"

CANDIDATE_ORDER = ("NVDA", "MSFT", "META")
REQUEST_HASHES = {
    "NVDA": "727284829f590352d2c1e6a87dabfc221eb58e54d616a5c7b483e6ffe864c80f",
    "MSFT": "6f75cb3fb0abb797f9edba2bcde550922cd68cfebd8a6b3d10382586b0877a13",
    "META": "4a8c140c1e49d70058389f5e2e56538d1fe4ef2d4f133cfd81ed96ecadbd2be7",
}
REQUEST_BODY_BYTES = {"NVDA": 86988, "MSFT": 93140, "META": 93846}
PER_CALL_COST_UPPER_USD = {
    "NVDA": Decimal("0.55782"),
    "MSFT": Decimal("0.58858"),
    "META": Decimal("0.59211"),
}
REBUTTAL_FRESH_GENERATION_COST_CEILING_USD = Decimal("1.73851")
SOURCE_REJECTED_ATTEMPT_COST_UPPER_USD = PER_CALL_COST_UPPER_USD["NVDA"]
REBUTTAL_STAGE_UPPER_AFTER_FRESH_RECOVERY_USD = (
    SOURCE_REJECTED_ATTEMPT_COST_UPPER_USD + REBUTTAL_FRESH_GENERATION_COST_CEILING_USD
)
INITIAL_SPEND_UPPER_BOUND_USD = Decimal("0.4963025")
AGGREGATE_INITIAL_PLUS_REBUTTAL_UPPER_AFTER_RECOVERY_USD = (
    INITIAL_SPEND_UPPER_BOUND_USD + REBUTTAL_STAGE_UPPER_AFTER_FRESH_RECOVERY_USD
)

PROBE_MODEL_ID = "gpt-5.6-sol"
PROBE_ENDPOINT = f"https://api.openai.com/v1/models/{PROBE_MODEL_ID}"
EXPECTED_VALIDATION_ERROR = (
    "ResponsesHttpError: OpenAI Responses HTTP failure: 401; "
    "error_type=invalid_request_error; error_code=invalid_api_key; "
    "request_id=req_2839cd27ae75438096528fad61e4c12a"
)
EXPECTED_REQUEST_ID = "req_2839cd27ae75438096528fad61e4c12a"


class B4ReopenRebuttalAuthRejectionRecoveryError(ValueError):
    pass


def _read_object(path: str | Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise B4ReopenRebuttalAuthRejectionRecoveryError(f"unable to read {label}") from exc
    if not isinstance(value, dict):
        raise B4ReopenRebuttalAuthRejectionRecoveryError(f"{label} root must be object")
    return value


def _read_jsonl(path: str | Path, *, label: str) -> list[dict[str, Any]]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise B4ReopenRebuttalAuthRejectionRecoveryError(f"unable to read {label}") from exc
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise B4ReopenRebuttalAuthRejectionRecoveryError(f"{label} contains invalid JSON") from exc
        if not isinstance(value, dict):
            raise B4ReopenRebuttalAuthRejectionRecoveryError(f"{label} row must be object")
        rows.append(value)
    return rows


def _verify_hash_bound(
    raw: Mapping[str, Any], *, field: str, expected: str, label: str
) -> str:
    observed = raw.get(field)
    if observed != expected:
        raise B4ReopenRebuttalAuthRejectionRecoveryError(f"{label} {field} drift")
    if observed != canonical_sha256(raw, exclude_fields=(field,)):
        raise B4ReopenRebuttalAuthRejectionRecoveryError(f"{label} self-hash mismatch")
    return expected


def verify_source_cost(cost: Mapping[str, Any]) -> str:
    _verify_hash_bound(cost, field="artifact_hash", expected=SOURCE_COST_HASH, label="cost preflight")
    if cost.get("request_manifest_hash") != SOURCE_REQUEST_MANIFEST_HASH:
        raise B4ReopenRebuttalAuthRejectionRecoveryError("cost request manifest drift")
    if cost.get("planned_paid_calls_max") != 3 or cost.get("max_output_tokens_per_call") != 6144:
        raise B4ReopenRebuttalAuthRejectionRecoveryError("cost call/token ceiling drift")
    if cost.get("rebuttal_exact_cost_upper_bound_usd") != "1.73851":
        raise B4ReopenRebuttalAuthRejectionRecoveryError("cost ceiling drift")
    rows = cost.get("request_rows")
    if not isinstance(rows, list) or len(rows) != 3:
        raise B4ReopenRebuttalAuthRejectionRecoveryError("cost request rows malformed")
    for row, candidate in zip(rows, CANDIDATE_ORDER, strict=True):
        if not isinstance(row, Mapping):
            raise B4ReopenRebuttalAuthRejectionRecoveryError("cost request row malformed")
        if row.get("candidate_id") != candidate:
            raise B4ReopenRebuttalAuthRejectionRecoveryError("cost candidate order drift")
        if row.get("request_hash") != REQUEST_HASHES[candidate]:
            raise B4ReopenRebuttalAuthRejectionRecoveryError(f"{candidate} request hash drift")
        if row.get("request_body_utf8_bytes") != REQUEST_BODY_BYTES[candidate]:
            raise B4ReopenRebuttalAuthRejectionRecoveryError(f"{candidate} request byte count drift")
    return SOURCE_COST_HASH


def verify_source_dry(dry: Mapping[str, Any]) -> str:
    _verify_hash_bound(dry, field="artifact_hash", expected=SOURCE_DRY_HASH, label="runtime dry")
    if dry.get("code_commit_sha") != SOURCE_CODE_SHA:
        raise B4ReopenRebuttalAuthRejectionRecoveryError("runtime dry code SHA drift")
    if dry.get("source_cost_preflight_artifact_hash") != SOURCE_COST_HASH:
        raise B4ReopenRebuttalAuthRejectionRecoveryError("runtime dry cost lineage drift")
    if dry.get("request_manifest_hash") != SOURCE_REQUEST_MANIFEST_HASH:
        raise B4ReopenRebuttalAuthRejectionRecoveryError("runtime dry request manifest drift")
    if dry.get("paid_dispatch_authorized") is not False or dry.get("judge_authorized") is not False:
        raise B4ReopenRebuttalAuthRejectionRecoveryError("runtime dry authority boundary drift")
    return SOURCE_DRY_HASH


def verify_source_authorization(auth: Mapping[str, Any]) -> str:
    _verify_hash_bound(auth, field="artifact_hash", expected=SOURCE_AUTH_HASH, label="paid authorization")
    exact = {
        "artifact_version": EXPECTED_AUTH_VERSION,
        "status": EXPECTED_AUTH_STATUS,
        "run_id": SOURCE_RUN_ID,
        "code_commit_sha": SOURCE_CODE_SHA,
        "owner_approval_id": SOURCE_OWNER_APPROVAL_ID,
        "owner_approval_at_utc": SOURCE_OWNER_APPROVAL_AT_UTC,
        "source_cost_preflight_artifact_hash": SOURCE_COST_HASH,
        "request_manifest_hash": SOURCE_REQUEST_MANIFEST_HASH,
        "runner_dry_artifact_hash": SOURCE_DRY_HASH,
        "planned_paid_calls_max": 3,
        "max_output_tokens_per_call": 6144,
        "approved_cost_ceiling_usd": "1.73851",
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_REBUTTAL_PROVIDER_DISPATCH_ATTEMPT",
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "rebuttal_rerun_authorized": False,
        "judge_authorized": False,
        "live_money": "PROHIBITED",
    }
    for field, expected in exact.items():
        if auth.get(field) != expected:
            raise B4ReopenRebuttalAuthRejectionRecoveryError(f"paid authorization drift: {field}")
    return SOURCE_AUTH_HASH


def verify_source_journal(events: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    if len(events) != 2:
        raise B4ReopenRebuttalAuthRejectionRecoveryError("source journal must contain exactly two events")
    attempt, result = events
    attempt_hash = attempt.get("event_hash")
    if not isinstance(attempt_hash, str) or attempt_hash != canonical_sha256(attempt, exclude_fields=("event_hash",)):
        raise B4ReopenRebuttalAuthRejectionRecoveryError("source attempt self-hash mismatch")
    attempt_exact = {
        "event_version": EXPECTED_EVENT_VERSION,
        "event_type": "REBUTTAL_PROVIDER_DISPATCH_ATTEMPT",
        "run_id": SOURCE_RUN_ID,
        "dispatch_index": 1,
        "candidate_id": "NVDA",
        "stage": "REBUTTAL",
        "request_hash": REQUEST_HASHES["NVDA"],
        "request_body_utf8_bytes": REQUEST_BODY_BYTES["NVDA"],
        "requested_model": PROBE_MODEL_ID,
        "reasoning_effort": "medium",
        "max_output_tokens": 6144,
        "source_cost_preflight_artifact_hash": SOURCE_COST_HASH,
        "request_manifest_hash": SOURCE_REQUEST_MANIFEST_HASH,
        "paid_authorization_artifact_hash": SOURCE_AUTH_HASH,
        "authorization_consumed_by_this_attempt": True,
        "automatic_repair_attempted": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    for field, expected in attempt_exact.items():
        if attempt.get(field) != expected:
            raise B4ReopenRebuttalAuthRejectionRecoveryError(f"source attempt drift: {field}")

    _verify_hash_bound(result, field="receipt_hash", expected=SOURCE_RECEIPT_HASH, label="source result receipt")
    result_exact = {
        "receipt_version": EXPECTED_RECEIPT_VERSION,
        "event_version": EXPECTED_EVENT_VERSION,
        "event_type": "REBUTTAL_PROVIDER_DISPATCH_RESULT",
        "run_id": SOURCE_RUN_ID,
        "dispatch_index": 1,
        "candidate_id": "NVDA",
        "stage": "REBUTTAL",
        "request_hash": REQUEST_HASHES["NVDA"],
        "paid_authorization_artifact_hash": SOURCE_AUTH_HASH,
        "dispatch_attempt_event_hash": attempt_hash,
        "provider_response_received": False,
        "provider_dispatch_state_unknown": True,
        "response_id": None,
        "effective_model": None,
        "actual_cost_usd": None,
        "cost_receipt_status": "INCOMPLETE_UNKNOWN_PROVIDER_DISPATCH",
        "validation_status": "FAIL",
        "validation_error": EXPECTED_VALIDATION_ERROR,
        "output_hash": None,
        "structured_output_hash": None,
        "processed_record_hash": None,
        "processed_record": None,
        "validated_processed_record_persisted": False,
        "local_finalize_replayable": False,
        "raw_provider_response_persisted": False,
        "provider_output_text_persisted": False,
        "automatic_repair_attempted": False,
        "rebuttal_rerun_authorized": False,
        "judge_authorized": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    for field, expected in result_exact.items():
        if result.get(field) != expected:
            raise B4ReopenRebuttalAuthRejectionRecoveryError(f"source result drift: {field}")
    return str(attempt_hash), SOURCE_RECEIPT_HASH


def verify_source_blocked(blocked: Mapping[str, Any]) -> str:
    _verify_hash_bound(blocked, field="artifact_hash", expected=SOURCE_BLOCKED_HASH, label="blocked artifact")
    exact = {
        "artifact_version": EXPECTED_BLOCKED_VERSION,
        "status": EXPECTED_BLOCKED_STATUS,
        "run_id": SOURCE_RUN_ID,
        "code_commit_sha": SOURCE_CODE_SHA,
        "source_cost_preflight_artifact_hash": SOURCE_COST_HASH,
        "request_manifest_hash": SOURCE_REQUEST_MANIFEST_HASH,
        "runner_dry_artifact_hash": SOURCE_DRY_HASH,
        "paid_authorization_artifact_hash": SOURCE_AUTH_HASH,
        "authorization_consumed": True,
        "dispatch_attempts": 1,
        "model_calls_known_completed": 0,
        "known_rebuttal_cost_usd": "0",
        "receipt_hashes": [SOURCE_RECEIPT_HASH],
        "successful_processed_records": [],
        "blocked_reason": "INCOMPLETE_UNKNOWN_PROVIDER_DISPATCH; " + EXPECTED_VALIDATION_ERROR,
        "rebuttal_freeze_barrier": False,
        "automatic_repair_calls": 0,
        "automatic_retries": 0,
        "rebuttal_rerun_authorized": False,
        "judge_authorized": False,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    for field, expected in exact.items():
        if blocked.get(field) != expected:
            raise B4ReopenRebuttalAuthRejectionRecoveryError(f"blocked artifact drift: {field}")
    return SOURCE_BLOCKED_HASH


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def build_recovery_plan(*, code_commit_sha: str) -> dict[str, Any]:
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise B4ReopenRebuttalAuthRejectionRecoveryError("exact lowercase git SHA required")
    request_rows = [
        {
            "candidate_id": candidate,
            "request_hash": REQUEST_HASHES[candidate],
            "request_body_utf8_bytes": REQUEST_BODY_BYTES[candidate],
            "max_output_tokens": 6144,
        }
        for candidate in CANDIDATE_ORDER
    ]
    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "status": PASS_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_runtime_code_sha": SOURCE_CODE_SHA,
        "source_run_id": SOURCE_RUN_ID,
        "source_cost_preflight_artifact_hash": SOURCE_COST_HASH,
        "source_request_manifest_hash": SOURCE_REQUEST_MANIFEST_HASH,
        "source_runtime_dry_artifact_hash": SOURCE_DRY_HASH,
        "source_paid_authorization_artifact_hash": SOURCE_AUTH_HASH,
        "source_receipt_hash": SOURCE_RECEIPT_HASH,
        "source_blocked_artifact_hash": SOURCE_BLOCKED_HASH,
        "source_authority_consumed": True,
        "source_authority_rerun_authorized": False,
        "source_durable_dispatch_attempts": 1,
        "source_durable_result_events": 1,
        "source_model_calls_known_completed": 0,
        "source_successful_rebuttal_processed_records": 0,
        "historical_receipt_provider_response_received": False,
        "historical_receipt_provider_dispatch_state_unknown": True,
        "historical_receipt_cost_status": "INCOMPLETE_UNKNOWN_PROVIDER_DISPATCH",
        "historical_receipt_actual_cost_usd": None,
        "forensic_classification": "HTTP_AUTHENTICATION_REJECTION_INVALID_API_KEY",
        "forensic_http_status_code": 401,
        "forensic_error_type": "invalid_request_error",
        "forensic_error_code": "invalid_api_key",
        "forensic_request_id": EXPECTED_REQUEST_ID,
        "forensic_transport_class": "HTTP_ERROR_NOT_URL_ERROR",
        "reconciled_transport_outcome_unknown": False,
        "successful_responses_payload_observed": False,
        "provider_generation_execution_proven": False,
        "rejected_attempt_billing_resolution": "NOT_PROVEN_FROM_USAGE_RECEIPT",
        "known_rebuttal_cost_usd": "0",
        "source_rejected_attempt_cost_upper_bound_usd": _decimal_text(SOURCE_REJECTED_ATTEMPT_COST_UPPER_USD),
        "fresh_rebuttal_outputs_required": 3,
        "candidate_order": list(CANDIDATE_ORDER),
        "fresh_generation_request_rows": request_rows,
        "fresh_generation_request_manifest_hash": SOURCE_REQUEST_MANIFEST_HASH,
        "fresh_generation_model": {
            "candidate_key": "R3",
            "model": PROBE_MODEL_ID,
            "reasoning_effort": "medium",
        },
        "fresh_generation_paid_calls_max_if_later_approved": 3,
        "fresh_generation_cost_ceiling_usd_if_later_approved": _decimal_text(REBUTTAL_FRESH_GENERATION_COST_CEILING_USD),
        "conservative_rebuttal_stage_spend_upper_after_fresh_recovery_usd": _decimal_text(REBUTTAL_STAGE_UPPER_AFTER_FRESH_RECOVERY_USD),
        "conservative_initial_plus_rebuttal_spend_upper_after_fresh_recovery_usd": _decimal_text(AGGREGATE_INITIAL_PLUS_REBUTTAL_UPPER_AFTER_RECOVERY_USD),
        "credential_probe_required": True,
        "credential_probe_endpoint": PROBE_ENDPOINT,
        "credential_probe_model_id": PROBE_MODEL_ID,
        "credential_probe_provider_reads_max_if_later_approved": 1,
        "credential_probe_model_calls": 0,
        "generation_dispatch_authorized": False,
        "credential_probe_provider_read_authorized": False,
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "judge_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def load_and_build_recovery_plan(
    *,
    code_commit_sha: str,
    cost_path: str | Path,
    dry_path: str | Path,
    authorization_path: str | Path,
    journal_path: str | Path,
    blocked_path: str | Path,
) -> dict[str, Any]:
    verify_source_cost(_read_object(cost_path, label="Rebuttal cost preflight"))
    verify_source_dry(_read_object(dry_path, label="Rebuttal runtime dry"))
    verify_source_authorization(_read_object(authorization_path, label="Rebuttal paid authorization"))
    verify_source_journal(_read_jsonl(journal_path, label="Rebuttal paid journal"))
    verify_source_blocked(_read_object(blocked_path, label="Rebuttal blocked artifact"))
    return build_recovery_plan(code_commit_sha=code_commit_sha)
