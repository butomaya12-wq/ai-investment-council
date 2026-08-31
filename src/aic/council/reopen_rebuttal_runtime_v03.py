from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from aic.domain.canonical import canonical_sha256
from aic.research.runtime import load_openai_api_key

from . import reopen_rebuttal_credential_probe_v02 as probe_v02
from . import reopen_rebuttal_runtime as base
from . import reopen_rebuttal_runtime_v02 as v02


RUNTIME_VERSION = "B4_REOPEN_REBUTTAL_PRODUCTION_RUNTIME_v0_3"
DRY_VERSION = "B4_REOPEN_REBUTTAL_RUNTIME_DRY_v0_3"
AUTH_VERSION = "B4_REOPEN_REBUTTAL_RUNTIME_PAID_AUTHORIZATION_v0_3"
EVENT_VERSION = "B4_REOPEN_REBUTTAL_RUNTIME_JOURNAL_EVENT_v0_3"
RECEIPT_VERSION = "B4_REOPEN_REBUTTAL_RUNTIME_PAID_CALL_RECEIPT_v0_3"
FREEZE_VERSION = "B4_REOPEN_REBUTTAL_COUNCIL_FREEZE_v0_3"
BLOCKED_VERSION = "B4_REOPEN_REBUTTAL_COUNCIL_BLOCKED_v0_3"

SOURCE_FRESH_DRY_PATH = Path(
    ".aic-runtime/b4_reopen_rebuttal_fresh_generation_recovery_dry_v0_1.json"
)
SOURCE_FRESH_DRY_HASH = "e56873e099b3c79d65c89095ab04f5011133b7d94be076b61091f83f6d75417c"
SOURCE_FRESH_DRY_HEAD = "aeea20684d750302c318f3761b3e2fa495aa9ef1"

SOURCE_PROBE_RESULT_PATH = Path(
    ".aic-runtime/b4_reopen_rebuttal_credential_probe_result_v0_2.json"
)
SOURCE_PROBE_RESULT_HASH = "219b234df60fbcd9d0ae5cd2c8ef23f2da495051cfb7db4641d55e62ae01eb1b"

CREDENTIAL_LINEAGE_CONTRACT = (
    "REBUTTAL_GENERATION_AUTHORITY_BOUND_TO_VALIDATED_CREDENTIAL_SHA256_v0_3"
)

B4ReopenRebuttalRuntimeV03Error = base.B4ReopenRebuttalRuntimeError

_BASE_BUILD_DRY = base.build_dry_artifact
_BASE_BUILD_RESULT_RECEIPT = base.build_result_receipt
_BASE_BUILD_FREEZE = base.build_freeze_artifact
_BASE_BUILD_BLOCKED = base.build_blocked_artifact
_BASE_DURABLE_FINALIZE = base.durable_finalize_inputs_from_journal

_ACTIVE_CREDENTIAL_FINGERPRINT: str | None = None


def _read_object(path: str | Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise B4ReopenRebuttalRuntimeV03Error(f"unable to read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise B4ReopenRebuttalRuntimeV03Error(f"{label} root must be object")
    return value


def verify_source_fresh_recovery_dry(
    dry: Mapping[str, Any], *, expected_hash: str = SOURCE_FRESH_DRY_HASH
) -> str:
    observed = dry.get("artifact_hash")
    if not isinstance(observed, str) or observed != canonical_sha256(
        dry, exclude_fields=("artifact_hash",)
    ):
        raise B4ReopenRebuttalRuntimeV03Error("source fresh-generation dry self-hash mismatch")
    if observed != expected_hash:
        raise B4ReopenRebuttalRuntimeV03Error("source fresh-generation dry hash mismatch")
    exact = {
        "artifact_version": v02.DRY_VERSION,
        "runtime_version": v02.RUNTIME_VERSION,
        "status": base.DRY_STATUS,
        "code_commit_sha": SOURCE_FRESH_DRY_HEAD,
        "source_cost_preflight_artifact_hash": base.EXPECTED_COST_PREFLIGHT_HASH,
        "request_manifest_hash": base.EXPECTED_REQUEST_MANIFEST_HASH,
        "planned_paid_calls_max": base.EXPECTED_CALLS,
        "max_output_tokens_per_call": base.EXPECTED_MAX_OUTPUT_TOKENS,
        "cost_ceiling_usd": str(base.EXPECTED_COST_CEILING_USD),
        "owner_approval_required": True,
        "paid_dispatch_authorized": False,
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "judge_authorized": False,
        "rebuttal_rerun_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    for field, expected in exact.items():
        if dry.get(field) != expected:
            raise B4ReopenRebuttalRuntimeV03Error(
                f"source fresh-generation dry drift: {field}"
            )
    return observed


def verify_successful_probe_result(
    result: Mapping[str, Any], *, expected_hash: str = SOURCE_PROBE_RESULT_HASH
) -> str:
    observed = result.get("artifact_hash")
    if not isinstance(observed, str) or observed != canonical_sha256(
        result, exclude_fields=("artifact_hash",)
    ):
        raise B4ReopenRebuttalRuntimeV03Error("credential-probe V02 result self-hash mismatch")
    if observed != expected_hash:
        raise B4ReopenRebuttalRuntimeV03Error("credential-probe V02 result hash mismatch")
    exact = {
        "artifact_version": probe_v02.FINAL_VERSION,
        "status": probe_v02.PASS_STATUS,
        "probe_model_id": probe_v02.MODEL_ID,
        "http_response_received": True,
        "http_status_code": 200,
        "error_type": None,
        "error_code": None,
        "returned_model_id": probe_v02.MODEL_ID,
        "provider_reads": 1,
        "model_calls": 0,
        "responses_generation_calls": 0,
        "credential_probe_authority_consumed": True,
        "fresh_generation_dispatch_authorized": False,
        "new_generation_owner_approval_required": True,
        "automatic_retries": 0,
        "judge_authorized": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "next_gate": probe_v02.NEXT_GATE_PASS,
    }
    for field, expected in exact.items():
        if result.get(field) != expected:
            raise B4ReopenRebuttalRuntimeV03Error(f"credential-probe V02 drift: {field}")
    fingerprint = result.get("replacement_credential_fingerprint_sha256")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise B4ReopenRebuttalRuntimeV03Error("credential-probe fingerprint missing")
    return observed


def _fingerprint_for_key(api_key: str) -> str:
    return probe_v02.credential_fingerprint_sha256(api_key)


def _bind_current_credential(probe_result: Mapping[str, Any], *, api_key: str) -> str:
    global _ACTIVE_CREDENTIAL_FINGERPRINT
    fingerprint = _fingerprint_for_key(api_key)
    if probe_result.get("replacement_credential_fingerprint_sha256") != fingerprint:
        raise B4ReopenRebuttalRuntimeV03Error(
            "current OPENAI_API_KEY differs from successful credential-probe V02 credential"
        )
    _ACTIVE_CREDENTIAL_FINGERPRINT = fingerprint
    return fingerprint


def _require_active_fingerprint() -> str:
    fingerprint = _ACTIVE_CREDENTIAL_FINGERPRINT
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise B4ReopenRebuttalRuntimeV03Error(
            "credential fingerprint was not established before Rebuttal authority/dispatch"
        )
    return fingerprint


def add_credential_lineage_to_dry(
    dry: Mapping[str, Any],
    *,
    source_fresh_dry: Mapping[str, Any],
    probe_result: Mapping[str, Any],
    api_key: str,
    expected_source_dry_hash: str = SOURCE_FRESH_DRY_HASH,
    expected_probe_result_hash: str = SOURCE_PROBE_RESULT_HASH,
) -> dict[str, Any]:
    source_hash = verify_source_fresh_recovery_dry(
        source_fresh_dry, expected_hash=expected_source_dry_hash
    )
    probe_hash = verify_successful_probe_result(
        probe_result, expected_hash=expected_probe_result_hash
    )
    fingerprint = _bind_current_credential(probe_result, api_key=api_key)
    artifact = dict(dry)
    artifact.update(
        {
            "artifact_version": DRY_VERSION,
            "runtime_version": RUNTIME_VERSION,
            "credential_lineage_contract": CREDENTIAL_LINEAGE_CONTRACT,
            "source_fresh_generation_recovery_dry_artifact_hash": source_hash,
            "source_successful_credential_probe_v02_result_artifact_hash": probe_hash,
            "replacement_credential_fingerprint_sha256": fingerprint,
            "replacement_credential_secret_persisted": False,
            "credential_lineage_enforced_before_paid_dispatch": True,
        }
    )
    artifact["artifact_hash"] = canonical_sha256(
        artifact, exclude_fields=("artifact_hash",)
    )
    return artifact


def _source_lineage() -> tuple[dict[str, Any], dict[str, Any]]:
    source_dry = _read_object(SOURCE_FRESH_DRY_PATH, label="source fresh-generation dry")
    probe = _read_object(SOURCE_PROBE_RESULT_PATH, label="successful credential-probe V02 result")
    verify_source_fresh_recovery_dry(source_dry)
    verify_successful_probe_result(probe)
    return source_dry, probe


def build_dry_artifact(*, code_commit_sha: str, bound: base.BoundReopenRebuttalRuntime) -> dict[str, Any]:
    source_dry, probe = _source_lineage()
    api_key = load_openai_api_key()
    base_dry = _BASE_BUILD_DRY(code_commit_sha=code_commit_sha, bound=bound)
    return add_credential_lineage_to_dry(
        base_dry,
        source_fresh_dry=source_dry,
        probe_result=probe,
        api_key=api_key,
    )


def verify_dry_artifact(
    dry: Mapping[str, Any],
    *,
    expected_code_commit_sha: str,
    bound: base.BoundReopenRebuttalRuntime,
) -> str:
    observed = dry.get("artifact_hash")
    if not isinstance(observed, str) or observed != canonical_sha256(
        dry, exclude_fields=("artifact_hash",)
    ):
        raise B4ReopenRebuttalRuntimeV03Error("Rebuttal V03 dry self-hash mismatch")
    source_dry, probe = _source_lineage()
    api_key = load_openai_api_key()
    fingerprint = _bind_current_credential(probe, api_key=api_key)
    exact = {
        "artifact_version": DRY_VERSION,
        "runtime_version": RUNTIME_VERSION,
        "status": base.DRY_STATUS,
        "code_commit_sha": expected_code_commit_sha,
        "source_cost_preflight_artifact_hash": base.EXPECTED_COST_PREFLIGHT_HASH,
        "source_recovered_initial_freeze_hash": base.EXPECTED_RECOVERED_INITIAL_FREEZE_HASH,
        "selected_model_authority_selection_hash": base.EXPECTED_SELECTION_HASH,
        "request_manifest_hash": base.EXPECTED_REQUEST_MANIFEST_HASH,
        "planned_paid_calls_max": base.EXPECTED_CALLS,
        "max_output_tokens_per_call": base.EXPECTED_MAX_OUTPUT_TOKENS,
        "cost_ceiling_usd": str(base.EXPECTED_COST_CEILING_USD),
        "owner_approval_required": True,
        "paid_dispatch_authorized": False,
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "judge_authorized": False,
        "rebuttal_rerun_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "credential_lineage_contract": CREDENTIAL_LINEAGE_CONTRACT,
        "source_fresh_generation_recovery_dry_artifact_hash": source_dry["artifact_hash"],
        "source_successful_credential_probe_v02_result_artifact_hash": probe["artifact_hash"],
        "replacement_credential_fingerprint_sha256": fingerprint,
        "replacement_credential_secret_persisted": False,
        "credential_lineage_enforced_before_paid_dispatch": True,
    }
    for field, expected in exact.items():
        if dry.get(field) != expected:
            raise B4ReopenRebuttalRuntimeV03Error(f"Rebuttal V03 dry drift: {field}")
    expected_rows = [
        {
            "dispatch_index": item.dispatch_index,
            "candidate_id": item.candidate_id,
            "context_hash": item.context_hash,
            "request_hash": item.request.request_hash,
            "request_body_utf8_bytes": item.request_body_utf8_bytes,
            "model": item.request.request_payload["model"],
            "reasoning_effort": item.request.request_payload["reasoning"]["effort"],
            "max_output_tokens": item.request.request_payload["max_output_tokens"],
        }
        for item in bound.plan
    ]
    if dry.get("request_rows") != expected_rows:
        raise B4ReopenRebuttalRuntimeV03Error("Rebuttal V03 dry request rows drift")
    return observed


def build_paid_authorization(
    *,
    code_commit_sha: str,
    git_worktree_clean: bool,
    created_at_utc: str,
    run_id: str,
    owner_approval_id: str,
    owner_approval_at_utc: str,
    approve_cost_artifact_hash: str,
    approve_request_manifest_hash: str,
    approve_dry_artifact_hash: str,
    approve_max_usd: str,
    dry_artifact: Mapping[str, Any],
    bound: base.BoundReopenRebuttalRuntime,
    receipt_journal_path: str,
) -> dict[str, Any]:
    if approve_cost_artifact_hash != base.EXPECTED_COST_PREFLIGHT_HASH:
        raise B4ReopenRebuttalRuntimeV03Error("owner approval cost artifact hash mismatch")
    if approve_request_manifest_hash != base.EXPECTED_REQUEST_MANIFEST_HASH:
        raise B4ReopenRebuttalRuntimeV03Error("owner approval request manifest mismatch")
    dry_hash = verify_dry_artifact(
        dry_artifact, expected_code_commit_sha=code_commit_sha, bound=bound
    )
    if approve_dry_artifact_hash != dry_hash:
        raise B4ReopenRebuttalRuntimeV03Error("owner approval V03 dry artifact hash mismatch")
    if base._decimal(approve_max_usd, field="approved max USD") != base.EXPECTED_COST_CEILING_USD:
        raise B4ReopenRebuttalRuntimeV03Error("approved max USD must equal frozen Rebuttal ceiling")
    if (
        not isinstance(owner_approval_id, str)
        or not owner_approval_id
        or owner_approval_id != owner_approval_id.strip()
        or any(ch.isspace() for ch in owner_approval_id)
    ):
        raise B4ReopenRebuttalRuntimeV03Error("owner approval ID invalid")
    created = base._utc(created_at_utc, field="authorization created_at")
    owner_at = base._utc(owner_approval_at_utc, field="owner approval time")
    if datetime.fromisoformat(owner_at.replace("Z", "+00:00")) > datetime.fromisoformat(
        created.replace("Z", "+00:00")
    ):
        raise B4ReopenRebuttalRuntimeV03Error("owner approval cannot postdate authorization")
    fingerprint = _require_active_fingerprint()
    artifact: dict[str, Any] = {
        "artifact_version": AUTH_VERSION,
        "runtime_version": RUNTIME_VERSION,
        "status": base.AUTH_STATUS,
        "run_id": run_id,
        "created_at_utc": created,
        "code_commit_sha": code_commit_sha,
        "git_worktree_clean": git_worktree_clean,
        "owner_approval_id": owner_approval_id,
        "owner_approval_at_utc": owner_at,
        "source_cost_preflight_artifact_hash": base.EXPECTED_COST_PREFLIGHT_HASH,
        "source_recovered_initial_freeze_hash": base.EXPECTED_RECOVERED_INITIAL_FREEZE_HASH,
        "selected_model_authority_selection_hash": base.EXPECTED_SELECTION_HASH,
        "request_manifest_hash": base.EXPECTED_REQUEST_MANIFEST_HASH,
        "runner_dry_artifact_hash": dry_hash,
        "credential_lineage_contract": CREDENTIAL_LINEAGE_CONTRACT,
        "source_successful_credential_probe_v02_result_artifact_hash": SOURCE_PROBE_RESULT_HASH,
        "replacement_credential_fingerprint_sha256": fingerprint,
        "replacement_credential_secret_persisted": False,
        "selected_rebuttal_model": dict(base.cost_v01.EXPECTED_REBUTTAL_SELECTED),
        "planned_paid_calls_max": base.EXPECTED_CALLS,
        "max_output_tokens_per_call": base.EXPECTED_MAX_OUTPUT_TOKENS,
        "approved_cost_ceiling_usd": str(base.EXPECTED_COST_CEILING_USD),
        "authorization_consumption_rule": "CONSUMED_ON_FIRST_REBUTTAL_PROVIDER_DISPATCH_ATTEMPT",
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "rebuttal_rerun_authorized": False,
        "judge_authorized": False,
        "receipt_journal_path": receipt_journal_path,
        "network_manifest": {
            "openai_responses_api": True,
            "hosted_tools": False,
            "general_web_search": False,
            "remote_mcp": False,
            "broker_api": False,
        },
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def build_attempt_event(*, run_id: str, item: Any, authorization_hash: str, started_at_utc: str) -> dict[str, Any]:
    fingerprint = _require_active_fingerprint()
    event = _BASE_BUILD_DRY  # keep captured base functions strongly referenced
    del event
    raw = base.build_attempt_event.__wrapped__ if hasattr(base.build_attempt_event, "__wrapped__") else None
    del raw
    artifact: dict[str, Any] = {
        "event_version": EVENT_VERSION,
        "event_type": "REBUTTAL_PROVIDER_DISPATCH_ATTEMPT",
        "run_id": run_id,
        "dispatch_index": item.dispatch_index,
        "dispatch_started_at_utc": base._utc(started_at_utc, field="dispatch start"),
        "candidate_id": item.candidate_id,
        "stage": "REBUTTAL",
        "context_hash": item.context_hash,
        "request_hash": item.request.request_hash,
        "request_body_utf8_bytes": item.request_body_utf8_bytes,
        "requested_model": item.request.request_payload["model"],
        "reasoning_effort": item.request.request_payload["reasoning"]["effort"],
        "max_output_tokens": base.EXPECTED_MAX_OUTPUT_TOKENS,
        "source_cost_preflight_artifact_hash": base.EXPECTED_COST_PREFLIGHT_HASH,
        "request_manifest_hash": base.EXPECTED_REQUEST_MANIFEST_HASH,
        "paid_authorization_artifact_hash": authorization_hash,
        "credential_lineage_contract": CREDENTIAL_LINEAGE_CONTRACT,
        "replacement_credential_fingerprint_sha256": fingerprint,
        "authorization_consumed_by_this_attempt": True,
        "automatic_repair_attempted": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["event_hash"] = canonical_sha256(artifact)
    return artifact


def build_result_receipt(**kwargs: Any) -> dict[str, Any]:
    fingerprint = _require_active_fingerprint()
    receipt = _BASE_BUILD_RESULT_RECEIPT(**kwargs)
    receipt.update(
        {
            "receipt_version": RECEIPT_VERSION,
            "event_version": EVENT_VERSION,
            "credential_lineage_contract": CREDENTIAL_LINEAGE_CONTRACT,
            "replacement_credential_fingerprint_sha256": fingerprint,
        }
    )
    receipt["receipt_hash"] = canonical_sha256(
        receipt, exclude_fields=("receipt_hash",)
    )
    return receipt


def _verify_journal_credential_lineage(
    *, events: Sequence[Mapping[str, Any]], authorization_hash: str
) -> None:
    fingerprint = _require_active_fingerprint()
    for row in events:
        if row.get("paid_authorization_artifact_hash") != authorization_hash:
            raise B4ReopenRebuttalRuntimeV03Error("Rebuttal V03 journal authorization drift")
        if row.get("replacement_credential_fingerprint_sha256") != fingerprint:
            raise B4ReopenRebuttalRuntimeV03Error("Rebuttal V03 journal credential drift")
        if row.get("credential_lineage_contract") != CREDENTIAL_LINEAGE_CONTRACT:
            raise B4ReopenRebuttalRuntimeV03Error("Rebuttal V03 journal credential contract drift")


def durable_finalize_inputs_from_journal(
    *,
    events: Sequence[Mapping[str, Any]],
    bound: base.BoundReopenRebuttalRuntime,
    authorization_hash: str,
) -> tuple[list[str], list[Mapping[str, Any]], Decimal] | None:
    if events:
        _verify_journal_credential_lineage(
            events=events, authorization_hash=authorization_hash
        )
    return _BASE_DURABLE_FINALIZE(
        events=events, bound=bound, authorization_hash=authorization_hash
    )


def build_freeze_artifact(**kwargs: Any) -> dict[str, Any]:
    fingerprint = _require_active_fingerprint()
    artifact = _BASE_BUILD_FREEZE(**kwargs)
    artifact.update(
        {
            "artifact_version": FREEZE_VERSION,
            "runtime_version": RUNTIME_VERSION,
            "credential_lineage_contract": CREDENTIAL_LINEAGE_CONTRACT,
            "source_successful_credential_probe_v02_result_artifact_hash": SOURCE_PROBE_RESULT_HASH,
            "replacement_credential_fingerprint_sha256": fingerprint,
            "replacement_credential_secret_persisted": False,
        }
    )
    artifact["artifact_hash"] = canonical_sha256(
        artifact, exclude_fields=("artifact_hash",)
    )
    return artifact


def build_blocked_artifact(**kwargs: Any) -> dict[str, Any]:
    fingerprint = _require_active_fingerprint()
    artifact = _BASE_BUILD_BLOCKED(**kwargs)
    artifact.update(
        {
            "artifact_version": BLOCKED_VERSION,
            "runtime_version": RUNTIME_VERSION,
            "credential_lineage_contract": CREDENTIAL_LINEAGE_CONTRACT,
            "source_successful_credential_probe_v02_result_artifact_hash": SOURCE_PROBE_RESULT_HASH,
            "replacement_credential_fingerprint_sha256": fingerprint,
            "replacement_credential_secret_persisted": False,
        }
    )
    artifact["artifact_hash"] = canonical_sha256(
        artifact, exclude_fields=("artifact_hash",)
    )
    return artifact


load_and_build_reopen_rebuttal_runtime_plan = v02.load_and_build_reopen_rebuttal_runtime_plan
