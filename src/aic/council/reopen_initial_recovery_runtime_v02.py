from __future__ import annotations

from typing import Any, Mapping, Sequence

from aic.domain.canonical import canonical_sha256

from . import reopen_initial_recovery_runtime as v01
from .reopen_initial_runtime import ReopenInitialRuntimePlanItem, _validate_processed_record

RECOVERY_PLAN_HASH = v01.RECOVERY_PLAN_HASH
RECOVERY_PLAN_SOURCE_HEAD = v01.RECOVERY_PLAN_SOURCE_HEAD
RUNTIME_VERSION = "B4_REOPEN_INITIAL_UNKNOWN_DISPATCH_RECOVERY_RUNTIME_v0_2"
DRY_VERSION = "B4_REOPEN_INITIAL_RECOVERY_RUNTIME_DRY_v0_2"
DRY_STATUS = v01.DRY_STATUS
AUTH_VERSION = "B4_REOPEN_INITIAL_RECOVERY_PAID_AUTHORIZATION_v0_2"
AUTH_STATUS = v01.AUTH_STATUS
EVENT_VERSION = "B4_REOPEN_INITIAL_RECOVERY_JOURNAL_EVENT_v0_2"
RECEIPT_VERSION = "B4_REOPEN_INITIAL_RECOVERY_PAID_CALL_RECEIPT_v0_2"
FREEZE_VERSION = "B4_REOPEN_INITIAL_COUNCIL_FREEZE_RECOVERED_v0_2"
FREEZE_STATUS = v01.FREEZE_STATUS
BLOCKED_VERSION = "B4_REOPEN_INITIAL_RECOVERY_BLOCKED_v0_2"
BLOCKED_STATUS = v01.BLOCKED_STATUS
NEXT_GATE = v01.NEXT_GATE

EXPECTED_UNKNOWN_REQUEST_HASH = v01.EXPECTED_UNKNOWN_REQUEST_HASH
EXPECTED_ONE_CALL_RECOVERY_CEILING_USD = v01.EXPECTED_ONE_CALL_RECOVERY_CEILING_USD
EXPECTED_POST_RECOVERY_AGGREGATE_SPEND_UPPER_USD = v01.EXPECTED_POST_RECOVERY_AGGREGATE_SPEND_UPPER_USD
EXPECTED_SELECTED_MODEL = v01.EXPECTED_SELECTED_MODEL
EXPECTED_MAX_OUTPUT_TOKENS = v01.EXPECTED_MAX_OUTPUT_TOKENS

B4ReopenInitialRecoveryRuntimeError = v01.B4ReopenInitialRecoveryRuntimeError
load_recovery_context = v01.load_recovery_context
append_jsonl_fsync = v01.append_jsonl_fsync
process_reopen_initial_provider_response = v01.process_reopen_initial_provider_response


def _rehash(payload: Mapping[str, Any], *, hash_field: str) -> dict[str, Any]:
    out = dict(payload)
    out.pop(hash_field, None)
    out[hash_field] = canonical_sha256(out)
    return out


def build_dry_artifact(
    *,
    code_commit_sha: str,
    recovery_plan: Mapping[str, Any],
    item: ReopenInitialRuntimePlanItem,
) -> dict[str, Any]:
    artifact = v01.build_dry_artifact(
        code_commit_sha=code_commit_sha,
        recovery_plan=recovery_plan,
        item=item,
    )
    artifact.update(
        {
            "artifact_version": DRY_VERSION,
            "runtime_version": RUNTIME_VERSION,
            "validated_processed_record_persisted_in_recovery_receipt": True,
            "crash_safe_local_finalize_supported": True,
            "local_finalize_requires_no_provider_dispatch": True,
        }
    )
    return _rehash(artifact, hash_field="artifact_hash")


def verify_dry_artifact(
    dry: Mapping[str, Any],
    *,
    code_commit_sha: str,
    item: ReopenInitialRuntimePlanItem,
) -> str:
    observed = dry.get("artifact_hash")
    if not isinstance(observed, str) or observed != canonical_sha256(
        dry, exclude_fields=("artifact_hash",)
    ):
        raise B4ReopenInitialRecoveryRuntimeError("recovery v0.2 dry self-hash mismatch")
    expected = build_dry_artifact(
        code_commit_sha=code_commit_sha,
        recovery_plan={"artifact_hash": RECOVERY_PLAN_HASH},
        item=item,
    )
    if dict(dry) != expected:
        raise B4ReopenInitialRecoveryRuntimeError("recovery v0.2 dry artifact drift")
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
    dry_hash = verify_dry_artifact(
        dry_artifact, code_commit_sha=code_commit_sha, item=item
    )
    if approve_dry_hash != dry_hash:
        raise B4ReopenInitialRecoveryRuntimeError("owner approval v0.2 dry hash mismatch")

    # Reuse the proven v0.1 authorization validators against an internally rebuilt
    # v0.1 dry artifact, then rebind the resulting authority to the v0.2 dry hash.
    base_dry = v01.build_dry_artifact(
        code_commit_sha=code_commit_sha,
        recovery_plan={"artifact_hash": RECOVERY_PLAN_HASH},
        item=item,
    )
    base = v01.build_paid_authorization(
        code_commit_sha=code_commit_sha,
        dry_artifact=base_dry,
        item=item,
        owner_approval_id=owner_approval_id,
        owner_approval_at_utc=owner_approval_at_utc,
        approve_recovery_plan_hash=approve_recovery_plan_hash,
        approve_dry_hash=str(base_dry["artifact_hash"]),
        approve_max_usd=approve_max_usd,
        created_at_utc=created_at_utc,
        run_id=run_id,
        journal_path=journal_path,
    )
    approval = dict(base["owner_approval"])
    approval["approved_recovery_dry_artifact_hash"] = dry_hash
    base.update(
        {
            "artifact_version": AUTH_VERSION,
            "runtime_version": RUNTIME_VERSION,
            "owner_approval": approval,
            "receipt_version": RECEIPT_VERSION,
            "journal_event_version": EVENT_VERSION,
            "validated_processed_record_persisted_in_recovery_receipt": True,
            "crash_safe_local_finalize_supported": True,
        }
    )
    return _rehash(base, hash_field="artifact_hash")


def build_attempt_event(
    *,
    run_id: str,
    item: ReopenInitialRuntimePlanItem,
    authorization_hash: str,
    started_at_utc: str,
) -> dict[str, Any]:
    event = v01.build_attempt_event(
        run_id=run_id,
        item=item,
        authorization_hash=authorization_hash,
        started_at_utc=started_at_utc,
    )
    event["event_version"] = EVENT_VERSION
    return _rehash(event, hash_field="event_hash")


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
    receipt = v01.build_result_receipt(
        run_id=run_id,
        item=item,
        authorization_hash=authorization_hash,
        attempt_hash=attempt_hash,
        started_at_utc=started_at_utc,
        finished_at_utc=finished_at_utc,
        provider_response_received=provider_response_received,
        raw_response=raw_response,
        processed_record=processed_record,
        validation_error=validation_error,
    )
    receipt.update(
        {
            "receipt_version": RECEIPT_VERSION,
            "event_version": EVENT_VERSION,
            "processed_record": None
            if processed_record is None
            else dict(processed_record),
            "validated_processed_record_persisted": processed_record is not None,
            "local_finalize_replayable": processed_record is not None,
        }
    )
    return _rehash(receipt, hash_field="receipt_hash")


def processed_record_from_recovery_receipt(
    receipt: Mapping[str, Any],
    *,
    expected_authorization_hash: str,
    expected_attempt_hash: str,
) -> dict[str, Any]:
    observed_hash = receipt.get("receipt_hash")
    if not isinstance(observed_hash, str) or observed_hash != canonical_sha256(
        receipt, exclude_fields=("receipt_hash",)
    ):
        raise B4ReopenInitialRecoveryRuntimeError("recovery v0.2 receipt self-hash mismatch")
    if receipt.get("receipt_version") != RECEIPT_VERSION:
        raise B4ReopenInitialRecoveryRuntimeError("recovery v0.2 receipt version drift")
    if receipt.get("event_version") != EVENT_VERSION:
        raise B4ReopenInitialRecoveryRuntimeError("recovery v0.2 event version drift")
    if receipt.get("event_type") != "RECOVERY_PROVIDER_DISPATCH_RESULT":
        raise B4ReopenInitialRecoveryRuntimeError("recovery v0.2 result event missing")
    if receipt.get("paid_authorization_artifact_hash") != expected_authorization_hash:
        raise B4ReopenInitialRecoveryRuntimeError("recovery v0.2 receipt auth binding drift")
    if receipt.get("dispatch_attempt_event_hash") != expected_attempt_hash:
        raise B4ReopenInitialRecoveryRuntimeError("recovery v0.2 receipt attempt binding drift")
    if receipt.get("request_hash") != EXPECTED_UNKNOWN_REQUEST_HASH:
        raise B4ReopenInitialRecoveryRuntimeError("recovery v0.2 request hash drift")
    if receipt.get("candidate_id") != "META" or receipt.get("lane") != "RED_TEAM":
        raise B4ReopenInitialRecoveryRuntimeError("recovery v0.2 identity drift")
    if receipt.get("provider_response_received") is not True:
        raise B4ReopenInitialRecoveryRuntimeError("recovery v0.2 response not received")
    if receipt.get("validation_status") != "PASS":
        raise B4ReopenInitialRecoveryRuntimeError("recovery v0.2 validation not PASS")
    if receipt.get("cost_receipt_status") != "COMPLETE":
        raise B4ReopenInitialRecoveryRuntimeError("recovery v0.2 cost receipt incomplete")
    if receipt.get("validated_processed_record_persisted") is not True:
        raise B4ReopenInitialRecoveryRuntimeError("recovery v0.2 processed record not persisted")
    if receipt.get("local_finalize_replayable") is not True:
        raise B4ReopenInitialRecoveryRuntimeError("recovery v0.2 local finalize not replayable")
    record = receipt.get("processed_record")
    if not isinstance(record, Mapping):
        raise B4ReopenInitialRecoveryRuntimeError("recovery v0.2 processed record missing")
    record_dict = dict(record)
    record_hash = record_dict.get("record_hash")
    if not isinstance(record_hash, str) or receipt.get("processed_record_hash") != record_hash:
        raise B4ReopenInitialRecoveryRuntimeError("recovery v0.2 processed record hash binding drift")
    _validate_processed_record(record_dict)
    if receipt.get("actual_cost_usd") != record_dict.get("actual_cost_usd"):
        raise B4ReopenInitialRecoveryRuntimeError("recovery v0.2 cost binding drift")
    return record_dict


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
    artifact = v01.build_recovered_freeze(
        code_commit_sha=code_commit_sha,
        recovery_run_id=recovery_run_id,
        recovery_authorization_hash=recovery_authorization_hash,
        recovery_dry_hash=recovery_dry_hash,
        source_blocked=source_blocked,
        source_events=source_events,
        recovery_attempt_hash=recovery_attempt_hash,
        recovery_receipt_hash=recovery_receipt_hash,
        recovery_processed_record=recovery_processed_record,
    )
    artifact.update(
        {
            "artifact_version": FREEZE_VERSION,
            "runtime_version": RUNTIME_VERSION,
            "recovery_receipt_embeds_validated_processed_record": True,
            "crash_safe_local_finalize_supported": True,
        }
    )
    return _rehash(artifact, hash_field="artifact_hash")


def build_recovery_blocked(
    *,
    code_commit_sha: str,
    run_id: str,
    auth_hash: str,
    dry_hash: str,
    attempt_hash: str,
    receipt_hash: str,
    reason: str,
) -> dict[str, Any]:
    artifact = v01.build_recovery_blocked(
        code_commit_sha=code_commit_sha,
        run_id=run_id,
        auth_hash=auth_hash,
        dry_hash=dry_hash,
        attempt_hash=attempt_hash,
        receipt_hash=receipt_hash,
        reason=reason,
    )
    artifact.update(
        {
            "artifact_version": BLOCKED_VERSION,
            "runtime_version": RUNTIME_VERSION,
            "crash_safe_local_finalize_supported": True,
        }
    )
    return _rehash(artifact, hash_field="artifact_hash")
