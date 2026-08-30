from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import COUNCIL_OPINION_V1
from aic.research.runtime import StdlibResponsesTransport

from .reopen_initial_runtime import (
    EXPECTED_EFFECTIVE_INPUT_MANIFEST_HASH,
    EXPECTED_MAX_OUTPUT_TOKENS,
    EXPECTED_REQUEST_MANIFEST_HASH,
    EXPECTED_SELECTED_MODEL,
    ReopenInitialRuntimePlanItem,
    _validate_processed_record,
    load_and_build_reopen_initial_runtime_plan,
    process_reopen_initial_provider_response,
)
from .reopen_initial_unknown_dispatch_recovery import (
    EXPECTED_BLOCKED_ARTIFACT_HASH,
    EXPECTED_KNOWN_COST_USD,
    EXPECTED_ONE_CALL_RECOVERY_CEILING_USD,
    EXPECTED_POST_RECOVERY_AGGREGATE_SPEND_UPPER_USD,
    EXPECTED_RECEIPT_MANIFEST_HASH,
    EXPECTED_SOURCE_AUTHORIZATION_HASH,
    EXPECTED_SOURCE_RUN_ID,
    EXPECTED_UNKNOWN_ATTEMPT_HASH,
    EXPECTED_UNKNOWN_RECEIPT_HASH,
    EXPECTED_UNKNOWN_REQUEST_HASH,
    load_and_build_recovery_plan_artifact,
)

RECOVERY_PLAN_HASH = "33a2ee7f26de5f395b53f7528d873d10305af993e2977113a5e2086290484c1d"
RECOVERY_PLAN_SOURCE_HEAD = "ac50ea45877d89c80d5f87ad2a3e16a8a4e768aa"
RUNTIME_VERSION = "B4_REOPEN_INITIAL_UNKNOWN_DISPATCH_RECOVERY_RUNTIME_v0_1"
DRY_VERSION = "B4_REOPEN_INITIAL_RECOVERY_RUNTIME_DRY_v0_1"
DRY_STATUS = "READY_FOR_EXPLICIT_OWNER_B4_REOPEN_INITIAL_RECOVERY_AUTHORIZATION"
AUTH_VERSION = "B4_REOPEN_INITIAL_RECOVERY_PAID_AUTHORIZATION_v0_1"
AUTH_STATUS = "AUTHORIZED_FOR_ONE_B4_REOPEN_INITIAL_RECOVERY_CALL"
EVENT_VERSION = "B4_REOPEN_INITIAL_RECOVERY_JOURNAL_EVENT_v0_1"
RECEIPT_VERSION = "B4_REOPEN_INITIAL_RECOVERY_PAID_CALL_RECEIPT_v0_1"
FREEZE_VERSION = "B4_REOPEN_INITIAL_COUNCIL_FREEZE_RECOVERED_v0_1"
FREEZE_STATUS = "B4_REOPEN_INITIAL_COUNCIL_FROZEN_AFTER_UNKNOWN_DISPATCH_RECOVERY"
BLOCKED_VERSION = "B4_REOPEN_INITIAL_RECOVERY_BLOCKED_v0_1"
BLOCKED_STATUS = "B4_REOPEN_INITIAL_RECOVERY_NOT_FROZEN"
NEXT_GATE = "B4_REOPEN_REBUTTAL_PRODUCTION_COST_PREFLIGHT_ZERO_CALL"


class B4ReopenInitialRecoveryRuntimeError(ValueError):
    pass


def _read_object(path: str | Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise B4ReopenInitialRecoveryRuntimeError(f"unable to read {label}") from exc
    if not isinstance(value, dict):
        raise B4ReopenInitialRecoveryRuntimeError(f"{label} root must be object")
    return value


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise B4ReopenInitialRecoveryRuntimeError("unable to read source receipt journal") from exc
    out: list[dict[str, Any]] = []
    for line in lines:
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise B4ReopenInitialRecoveryRuntimeError("receipt journal event must be object")
            out.append(value)
    return out


def _decimal(value: object, *, field: str) -> Decimal:
    if not isinstance(value, str):
        raise B4ReopenInitialRecoveryRuntimeError(f"{field} must be decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise B4ReopenInitialRecoveryRuntimeError(f"{field} invalid") from exc
    if not parsed.is_finite() or parsed < 0:
        raise B4ReopenInitialRecoveryRuntimeError(f"{field} invalid")
    return parsed


def _utc(value: str, *, field: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise B4ReopenInitialRecoveryRuntimeError(f"{field} invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise B4ReopenInitialRecoveryRuntimeError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _self_hash(payload: Mapping[str, Any], *, expected: str, label: str) -> None:
    if payload.get("artifact_hash") != expected:
        raise B4ReopenInitialRecoveryRuntimeError(f"{label} hash drift")
    if expected != canonical_sha256(payload, exclude_fields=("artifact_hash",)):
        raise B4ReopenInitialRecoveryRuntimeError(f"{label} self-hash mismatch")


def load_recovery_context(
    *,
    code_commit_sha: str,
    recovery_plan_path: str | Path,
    source_blocked_path: str | Path,
    source_journal_path: str | Path,
    cost_preflight_path: str | Path,
    lifecycle_path: str | Path,
    overlay_path: str | Path,
    closure_path: str | Path,
    freeze_path: str | Path,
    reconciliation_path: str | Path,
    handoff_path: str | Path,
    initial_authority_path: str | Path,
    pricing_path: str | Path,
    source_authorization_path: str | Path,
) -> tuple[dict[str, Any], dict[str, Any], tuple[ReopenInitialRuntimePlanItem, ...], dict[str, Any], list[dict[str, Any]]]:
    recovery_plan = _read_object(recovery_plan_path, label="recovery plan")
    _self_hash(recovery_plan, expected=RECOVERY_PLAN_HASH, label="recovery plan")
    if recovery_plan.get("status") != "B4_REOPEN_INITIAL_UNKNOWN_DISPATCH_RECOVERY_ZERO_CALL_PASS":
        raise B4ReopenInitialRecoveryRuntimeError("recovery plan is not PASS")
    if recovery_plan.get("code_commit_sha") != RECOVERY_PLAN_SOURCE_HEAD:
        raise B4ReopenInitialRecoveryRuntimeError("recovery plan source HEAD drift")
    if recovery_plan.get("recovery_paid_dispatch_authorized") is not False:
        raise B4ReopenInitialRecoveryRuntimeError("recovery plan unexpectedly authorizes paid dispatch")
    if recovery_plan.get("recovery_paid_calls_max") != 1:
        raise B4ReopenInitialRecoveryRuntimeError("recovery plan call ceiling drift")
    if _decimal(recovery_plan.get("recovery_cost_ceiling_usd"), field="recovery ceiling") != EXPECTED_ONE_CALL_RECOVERY_CEILING_USD:
        raise B4ReopenInitialRecoveryRuntimeError("recovery ceiling drift")

    cost, plan, _authority, pricing = load_and_build_reopen_initial_runtime_plan(
        cost_preflight_path=cost_preflight_path,
        lifecycle_path=lifecycle_path,
        overlay_path=overlay_path,
        closure_path=closure_path,
        freeze_path=freeze_path,
        reconciliation_path=reconciliation_path,
        handoff_path=handoff_path,
        initial_authority_path=initial_authority_path,
        pricing_path=pricing_path,
    )
    recomputed = load_and_build_recovery_plan_artifact(
        code_commit_sha=RECOVERY_PLAN_SOURCE_HEAD,
        cost_preflight_path=cost_preflight_path,
        source_authorization_path=source_authorization_path,
        blocked_artifact_path=source_blocked_path,
        receipt_journal_path=source_journal_path,
        runtime_plan=plan,
    )
    if recomputed != recovery_plan:
        raise B4ReopenInitialRecoveryRuntimeError("current immutable evidence does not reproduce recovery plan")
    missing = plan[8]
    if missing.request.request_hash != EXPECTED_UNKNOWN_REQUEST_HASH:
        raise B4ReopenInitialRecoveryRuntimeError("recovery request hash drift")
    source_blocked = _read_object(source_blocked_path, label="source blocked artifact")
    _self_hash(source_blocked, expected=EXPECTED_BLOCKED_ARTIFACT_HASH, label="source blocked artifact")
    source_events = _read_jsonl(source_journal_path)
    if len(source_events) != 18:
        raise B4ReopenInitialRecoveryRuntimeError("source journal must remain exact 18 events")
    return recovery_plan, source_blocked, plan, pricing, source_events


def build_dry_artifact(*, code_commit_sha: str, recovery_plan: Mapping[str, Any], item: ReopenInitialRuntimePlanItem) -> dict[str, Any]:
    if recovery_plan.get("artifact_hash") != RECOVERY_PLAN_HASH:
        raise B4ReopenInitialRecoveryRuntimeError("dry recovery-plan binding drift")
    artifact: dict[str, Any] = {
        "artifact_version": DRY_VERSION,
        "runtime_version": RUNTIME_VERSION,
        "status": DRY_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_recovery_plan_artifact_hash": RECOVERY_PLAN_HASH,
        "source_blocked_artifact_hash": EXPECTED_BLOCKED_ARTIFACT_HASH,
        "source_receipt_manifest_hash": EXPECTED_RECEIPT_MANIFEST_HASH,
        "source_paid_authorization_artifact_hash": EXPECTED_SOURCE_AUTHORIZATION_HASH,
        "source_authority_consumed": True,
        "source_authority_rerun_authorized": False,
        "reusable_processed_opinion_count": 8,
        "recovery_candidate_id": item.candidate_id,
        "recovery_lane": item.lane.value,
        "recovery_request_hash": item.request.request_hash,
        "recovery_request_body_utf8_bytes": item.request_body_utf8_bytes,
        "selected_model": dict(EXPECTED_SELECTED_MODEL),
        "max_output_tokens": EXPECTED_MAX_OUTPUT_TOKENS,
        "recovery_paid_calls_max": 1,
        "recovery_cost_ceiling_usd": str(EXPECTED_ONE_CALL_RECOVERY_CEILING_USD),
        "aggregate_initial_spend_upper_bound_after_one_recovery_usd": str(EXPECTED_POST_RECOVERY_AGGREGATE_SPEND_UPPER_USD),
        "owner_approval_required": True,
        "recovery_paid_dispatch_authorized": False,
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "recovery_rerun_authorized": False,
        "rebuttal_authorized": False,
        "judge_authorized": False,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_dry_artifact(dry: Mapping[str, Any], *, code_commit_sha: str, item: ReopenInitialRuntimePlanItem) -> str:
    observed = dry.get("artifact_hash")
    if not isinstance(observed, str) or observed != canonical_sha256(dry, exclude_fields=("artifact_hash",)):
        raise B4ReopenInitialRecoveryRuntimeError("recovery dry self-hash mismatch")
    expected = build_dry_artifact(code_commit_sha=code_commit_sha, recovery_plan={"artifact_hash": RECOVERY_PLAN_HASH}, item=item)
    if dry != expected:
        raise B4ReopenInitialRecoveryRuntimeError("recovery dry artifact drift")
    return observed


def build_paid_authorization(
    *,
    code_commit_sha: str,
    dry_artifact: Mapping[str, Any],
    item: ReopenInitialRuntimePlanItem,
    owner_approval_id: str,
    owner_approval_at_utc: str,
    approve_recovery_plan_hash: str,
    approve_dry_hash: str,
    approve_max_usd: str,
    created_at_utc: str,
    run_id: str,
    journal_path: str,
) -> dict[str, Any]:
    dry_hash = verify_dry_artifact(dry_artifact, code_commit_sha=code_commit_sha, item=item)
    if approve_recovery_plan_hash != RECOVERY_PLAN_HASH or approve_dry_hash != dry_hash:
        raise B4ReopenInitialRecoveryRuntimeError("owner approval artifact binding mismatch")
    if _decimal(approve_max_usd, field="approved max USD") != EXPECTED_ONE_CALL_RECOVERY_CEILING_USD:
        raise B4ReopenInitialRecoveryRuntimeError("owner approval ceiling mismatch")
    approval_at = _utc(owner_approval_at_utc, field="owner approval timestamp")
    created_at = _utc(created_at_utc, field="authorization creation timestamp")
    if datetime.fromisoformat(approval_at.replace("Z", "+00:00")) > datetime.fromisoformat(created_at.replace("Z", "+00:00")):
        raise B4ReopenInitialRecoveryRuntimeError("owner approval cannot postdate authorization")
    if not owner_approval_id or owner_approval_id != owner_approval_id.strip():
        raise B4ReopenInitialRecoveryRuntimeError("owner approval id missing")
    artifact: dict[str, Any] = {
        "artifact_version": AUTH_VERSION,
        "runtime_version": RUNTIME_VERSION,
        "status": AUTH_STATUS,
        "run_id": run_id,
        "created_at_utc": created_at,
        "runner_code_commit_sha": code_commit_sha,
        "source_recovery_plan_artifact_hash": RECOVERY_PLAN_HASH,
        "source_blocked_artifact_hash": EXPECTED_BLOCKED_ARTIFACT_HASH,
        "source_receipt_manifest_hash": EXPECTED_RECEIPT_MANIFEST_HASH,
        "source_paid_authorization_artifact_hash": EXPECTED_SOURCE_AUTHORIZATION_HASH,
        "source_authority_consumed": True,
        "selected_model": dict(EXPECTED_SELECTED_MODEL),
        "recovery_request_hash": item.request.request_hash,
        "recovery_paid_calls_max": 1,
        "max_output_tokens": EXPECTED_MAX_OUTPUT_TOKENS,
        "recovery_cost_ceiling_usd": str(EXPECTED_ONE_CALL_RECOVERY_CEILING_USD),
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_RECOVERY_PROVIDER_DISPATCH_ATTEMPT",
        "authorization_consumed_before_dispatch": False,
        "owner_approval": {
            "owner_approval_id": owner_approval_id,
            "owner_approval_at_utc": approval_at,
            "approved_recovery_plan_artifact_hash": RECOVERY_PLAN_HASH,
            "approved_recovery_dry_artifact_hash": dry_hash,
            "approved_recovery_request_hash": item.request.request_hash,
            "approved_cost_ceiling_usd": str(EXPECTED_ONE_CALL_RECOVERY_CEILING_USD),
            "scope": "ONE_FRESH_META_RED_TEAM_INITIAL_RECOVERY_CALL_ONLY",
            "recovery_rerun_authorized": False,
            "rebuttal_authorized": False,
            "judge_authorized": False,
        },
        "journal_path": journal_path,
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def build_attempt_event(*, run_id: str, item: ReopenInitialRuntimePlanItem, authorization_hash: str, started_at_utc: str) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_version": EVENT_VERSION,
        "event_type": "RECOVERY_PROVIDER_DISPATCH_ATTEMPT",
        "run_id": run_id,
        "source_run_id": EXPECTED_SOURCE_RUN_ID,
        "dispatch_index": 1,
        "replaces_unknown_source_dispatch_index": 9,
        "dispatch_started_at_utc": _utc(started_at_utc, field="dispatch start"),
        "candidate_id": "META",
        "lane": "RED_TEAM",
        "stage": "RED_TEAM_INITIAL",
        "request_hash": item.request.request_hash,
        "request_body_utf8_bytes": item.request_body_utf8_bytes,
        "requested_model": "gpt-5.6-terra",
        "reasoning_effort": "low",
        "max_output_tokens": EXPECTED_MAX_OUTPUT_TOKENS,
        "source_recovery_plan_artifact_hash": RECOVERY_PLAN_HASH,
        "paid_authorization_artifact_hash": authorization_hash,
        "authorization_consumed_by_this_attempt": True,
        "automatic_repair_attempted": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    event["event_hash"] = canonical_sha256(event)
    return event


def build_result_receipt(
    *,
    run_id: str,
    item: ReopenInitialRuntimePlanItem,
    authorization_hash: str,
    attempt_hash: str,
    started_at_utc: str,
    finished_at_utc: str,
    provider_response_received: bool,
    raw_response: Mapping[str, Any] | None,
    processed_record: Mapping[str, Any] | None,
    validation_error: str | None,
) -> dict[str, Any]:
    actual_cost = None if processed_record is None else processed_record.get("actual_cost_usd")
    receipt: dict[str, Any] = {
        "receipt_version": RECEIPT_VERSION,
        "event_version": EVENT_VERSION,
        "event_type": "RECOVERY_PROVIDER_DISPATCH_RESULT",
        "run_id": run_id,
        "source_run_id": EXPECTED_SOURCE_RUN_ID,
        "dispatch_index": 1,
        "replaces_unknown_source_dispatch_index": 9,
        "dispatch_started_at_utc": _utc(started_at_utc, field="dispatch start"),
        "dispatch_finished_at_utc": _utc(finished_at_utc, field="dispatch finish"),
        "candidate_id": "META",
        "lane": "RED_TEAM",
        "stage": "RED_TEAM_INITIAL",
        "request_hash": item.request.request_hash,
        "paid_authorization_artifact_hash": authorization_hash,
        "dispatch_attempt_event_hash": attempt_hash,
        "provider_response_received": provider_response_received,
        "provider_dispatch_state_unknown": not provider_response_received,
        "validation_status": "PASS" if processed_record is not None else "FAIL",
        "validation_error": validation_error,
        "processed_record_hash": None if processed_record is None else processed_record.get("record_hash"),
        "actual_cost_usd": actual_cost,
        "cost_receipt_status": "COMPLETE" if actual_cost is not None else "INCOMPLETE_UNKNOWN_PROVIDER_DISPATCH",
        "raw_provider_response_persisted": False,
        "provider_output_text_persisted": False,
        "automatic_repair_attempted": False,
        "recovery_rerun_authorized": False,
        "rebuttal_authorized": False,
        "judge_authorized": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    if provider_response_received and isinstance(raw_response, Mapping):
        usage = raw_response.get("usage")
        if isinstance(usage, Mapping):
            receipt["input_tokens"] = usage.get("input_tokens")
            receipt["output_tokens"] = usage.get("output_tokens")
    receipt["receipt_hash"] = canonical_sha256(receipt)
    return receipt


def build_recovered_freeze(
    *,
    code_commit_sha: str,
    recovery_run_id: str,
    recovery_authorization_hash: str,
    recovery_dry_hash: str,
    source_blocked: Mapping[str, Any],
    source_events: Sequence[Mapping[str, Any]],
    recovery_attempt_hash: str,
    recovery_receipt_hash: str,
    recovery_processed_record: Mapping[str, Any],
) -> dict[str, Any]:
    source_records = source_blocked.get("processed_records")
    if not isinstance(source_records, list) or len(source_records) != 8:
        raise B4ReopenInitialRecoveryRuntimeError("source reusable records missing")
    records = [dict(item) for item in source_records] + [dict(recovery_processed_record)]
    expected_identity = [
        ("NVDA", "BULL"), ("NVDA", "BEAR"), ("NVDA", "RED_TEAM"),
        ("MSFT", "BULL"), ("MSFT", "BEAR"), ("MSFT", "RED_TEAM"),
        ("META", "BULL"), ("META", "BEAR"), ("META", "RED_TEAM"),
    ]
    opinion_ids: list[str] = []
    opinion_hashes: list[str] = []
    for record, identity in zip(records, expected_identity, strict=True):
        opinion_id, opinion_hash = _validate_processed_record(record)
        if (record.get("candidate_id"), record.get("lane")) != identity:
            raise B4ReopenInitialRecoveryRuntimeError("recovered opinion identity/order drift")
        opinion_ids.append(opinion_id)
        opinion_hashes.append(opinion_hash)
    if len(set(opinion_ids)) != 9 or len(set(opinion_hashes)) != 9:
        raise B4ReopenInitialRecoveryRuntimeError("recovered opinions must be unique")
    recovery_cost = _decimal(recovery_processed_record.get("actual_cost_usd"), field="recovery actual cost")
    if recovery_cost > EXPECTED_ONE_CALL_RECOVERY_CEILING_USD:
        raise B4ReopenInitialRecoveryRuntimeError("recovery actual cost exceeds approved ceiling")
    known_cost = EXPECTED_KNOWN_COST_USD + recovery_cost
    aggregate_upper = known_cost + EXPECTED_ONE_CALL_RECOVERY_CEILING_USD
    source_attempts = source_blocked.get("dispatch_attempt_hashes")
    source_receipts = source_blocked.get("paid_call_receipt_hashes")
    if not isinstance(source_attempts, list) or not isinstance(source_receipts, list):
        raise B4ReopenInitialRecoveryRuntimeError("source receipt hashes missing")
    artifact: dict[str, Any] = {
        "artifact_version": FREEZE_VERSION,
        "runtime_version": RUNTIME_VERSION,
        "status": FREEZE_STATUS,
        "code_commit_sha": code_commit_sha,
        "source_run_id": EXPECTED_SOURCE_RUN_ID,
        "recovery_run_id": recovery_run_id,
        "source_blocked_artifact_hash": EXPECTED_BLOCKED_ARTIFACT_HASH,
        "source_paid_authorization_artifact_hash": EXPECTED_SOURCE_AUTHORIZATION_HASH,
        "source_receipt_manifest_hash": EXPECTED_RECEIPT_MANIFEST_HASH,
        "source_unknown_dispatch_attempt_hash": EXPECTED_UNKNOWN_ATTEMPT_HASH,
        "source_unknown_dispatch_receipt_hash": EXPECTED_UNKNOWN_RECEIPT_HASH,
        "source_unknown_dispatch_cost_status": "INCOMPLETE_UNKNOWN_PROVIDER_DISPATCH",
        "source_unknown_dispatch_cost_remains_unknown": True,
        "recovery_plan_artifact_hash": RECOVERY_PLAN_HASH,
        "recovery_dry_artifact_hash": recovery_dry_hash,
        "recovery_paid_authorization_artifact_hash": recovery_authorization_hash,
        "recovery_attempt_hash": recovery_attempt_hash,
        "recovery_receipt_hash": recovery_receipt_hash,
        "request_manifest_hash": EXPECTED_REQUEST_MANIFEST_HASH,
        "effective_input_manifest_hash": EXPECTED_EFFECTIVE_INPUT_MANIFEST_HASH,
        "selected_model": dict(EXPECTED_SELECTED_MODEL),
        "candidate_order": ["NVDA", "MSFT", "META"],
        "initial_opinion_count": 9,
        "initial_opinion_ids": opinion_ids,
        "initial_opinion_hashes": opinion_hashes,
        "processed_records": records,
        "reused_source_processed_opinion_count": 8,
        "fresh_recovery_processed_opinion_count": 1,
        "source_provider_dispatch_attempts": 9,
        "recovery_provider_dispatch_attempts": 1,
        "aggregate_provider_dispatch_attempts": 10,
        "model_calls_known_completed": 9,
        "known_actual_cost_usd": str(known_cost),
        "aggregate_initial_spend_upper_bound_usd": str(aggregate_upper),
        "aggregate_cost_receipt_status": "PARTIAL_UNKNOWN_HISTORICAL_DISPATCH",
        "initial_freeze_barrier": True,
        "rebuttal_cost_requires_this_fresh_initial_freeze": True,
        "rebuttal_authorized": False,
        "judge_authorized": False,
        "recovery_rerun_authorized": False,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def build_recovery_blocked(*, code_commit_sha: str, run_id: str, auth_hash: str, dry_hash: str, attempt_hash: str, receipt_hash: str, reason: str) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "artifact_version": BLOCKED_VERSION,
        "runtime_version": RUNTIME_VERSION,
        "status": BLOCKED_STATUS,
        "code_commit_sha": code_commit_sha,
        "recovery_run_id": run_id,
        "source_blocked_artifact_hash": EXPECTED_BLOCKED_ARTIFACT_HASH,
        "recovery_plan_artifact_hash": RECOVERY_PLAN_HASH,
        "recovery_dry_artifact_hash": dry_hash,
        "recovery_paid_authorization_artifact_hash": auth_hash,
        "authorization_consumed": True,
        "recovery_provider_dispatch_attempts": 1,
        "recovery_attempt_hash": attempt_hash,
        "recovery_receipt_hash": receipt_hash,
        "blocked_reason": reason,
        "initial_freeze_barrier": False,
        "recovery_rerun_authorized": False,
        "rebuttal_authorized": False,
        "judge_authorized": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def append_jsonl_fsync(path: str | Path, payload: Mapping[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
