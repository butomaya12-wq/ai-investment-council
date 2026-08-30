from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256

from .initial_runtime_cost import verify_initial_runtime_cost_preflight
from .initial_runtime_preflight import EXPECTED_LOGICAL_CALLS, verify_initial_runtime_request_preflight
from .model_selection import InitialSelectedModelAuthority


INITIAL_RUNTIME_PAID_AUTHORIZATION_VERSION = "B4_INITIAL_RUNTIME_PAID_AUTHORIZATION_ARTIFACT_v0_1"
INITIAL_RUNTIME_PAID_AUTHORIZATION_RUN_CLASS = "B4_INITIAL_RUNTIME_PAID_PRE_DISPATCH_AUTHORIZATION"
INITIAL_RUNTIME_PAID_AUTHORIZATION_STATUS = "AUTHORIZED_FOR_EXACT_NINE_INITIAL_CALLS"
INITIAL_RUNTIME_PAID_RECEIPT_VERSION = "B4_INITIAL_RUNTIME_PAID_CALL_RECEIPT_v0_1"


class InitialRuntimeAuthorizationError(ValueError):
    pass


def _decimal(value: object, *, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise InitialRuntimeAuthorizationError(f"{field_name} must be decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise InitialRuntimeAuthorizationError(f"{field_name} invalid") from exc
    if not parsed.is_finite() or parsed < 0:
        raise InitialRuntimeAuthorizationError(f"{field_name} must be finite and non-negative")
    return parsed


def _approval_time(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise InitialRuntimeAuthorizationError("owner approval timestamp missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InitialRuntimeAuthorizationError("owner approval timestamp invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InitialRuntimeAuthorizationError("owner approval timestamp must be timezone-aware")
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_initial_runtime_paid_authorization(
    *,
    runtime_preflight: Mapping[str, Any],
    cost_preflight: Mapping[str, Any],
    authority: InitialSelectedModelAuthority,
    approve_cost_artifact_hash: str,
    approve_max_usd: str,
    owner_approval_id: str,
    owner_approval_at_utc: str,
    code_commit_sha: str,
    git_worktree_clean: bool,
    created_at_utc: str,
    run_id: str,
    receipt_journal_path: str,
) -> dict[str, Any]:
    runtime_hash = verify_initial_runtime_request_preflight(runtime_preflight)
    cost_hash = verify_initial_runtime_cost_preflight(cost_preflight)
    if cost_preflight.get("runtime_request_preflight_artifact_hash") != runtime_hash:
        raise InitialRuntimeAuthorizationError("cost preflight does not bind runtime request preflight")
    if cost_preflight.get("artifact_hash") != approve_cost_artifact_hash or cost_hash != approve_cost_artifact_hash:
        raise InitialRuntimeAuthorizationError("owner approval cost artifact hash mismatch")
    frozen_ceiling = _decimal(
        cost_preflight.get("total_initial_runtime_cost_upper_bound_usd"),
        field_name="cost preflight ceiling",
    )
    approved_ceiling = _decimal(approve_max_usd, field_name="approved max USD")
    if approved_ceiling != frozen_ceiling:
        raise InitialRuntimeAuthorizationError("approved max USD must exactly equal frozen cost ceiling")
    if cost_preflight.get("planned_paid_calls_max") != EXPECTED_LOGICAL_CALLS:
        raise InitialRuntimeAuthorizationError("paid Initial authorization requires exact nine-call ceiling")
    if cost_preflight.get("automatic_repair_calls_authorized") is not False:
        raise InitialRuntimeAuthorizationError("paid Initial authorization forbids automatic repair calls")
    if runtime_preflight.get("selected_model_authority_selection_hash") != authority.selection_hash:
        raise InitialRuntimeAuthorizationError("paid Initial authorization selected-model authority mismatch")
    if runtime_preflight.get("selected_model_eval_artifact_hash") != authority.model_eval_artifact_hash:
        raise InitialRuntimeAuthorizationError("paid Initial authorization model-eval authority mismatch")
    if code_commit_sha != runtime_preflight.get("code_commit_sha") or code_commit_sha != cost_preflight.get("code_commit_sha"):
        raise InitialRuntimeAuthorizationError("paid Initial authorization requires exact preflight git commit")
    if len(code_commit_sha) != 40 or any(ch not in "0123456789abcdef" for ch in code_commit_sha):
        raise InitialRuntimeAuthorizationError("paid Initial authorization git SHA invalid")
    if git_worktree_clean is not True:
        raise InitialRuntimeAuthorizationError("paid Initial authorization requires clean git worktree")
    if not isinstance(owner_approval_id, str) or not owner_approval_id or owner_approval_id != owner_approval_id.strip():
        raise InitialRuntimeAuthorizationError("owner approval ID missing")
    approval_at = _approval_time(owner_approval_at_utc)
    created_at = _approval_time(created_at_utc)
    if datetime.fromisoformat(approval_at.replace("Z", "+00:00")) > datetime.fromisoformat(created_at.replace("Z", "+00:00")):
        raise InitialRuntimeAuthorizationError("owner approval timestamp cannot be after authorization creation")
    if not isinstance(run_id, str) or not run_id or run_id != run_id.strip():
        raise InitialRuntimeAuthorizationError("paid Initial run_id missing")
    if not isinstance(receipt_journal_path, str) or not receipt_journal_path:
        raise InitialRuntimeAuthorizationError("paid Initial receipt journal path missing")

    artifact: dict[str, Any] = {
        "artifact_version": INITIAL_RUNTIME_PAID_AUTHORIZATION_VERSION,
        "run_class": INITIAL_RUNTIME_PAID_AUTHORIZATION_RUN_CLASS,
        "status": INITIAL_RUNTIME_PAID_AUTHORIZATION_STATUS,
        "run_id": run_id,
        "created_at_utc": created_at,
        "code_commit_sha": code_commit_sha,
        "git_worktree_clean": True,
        "runtime_request_preflight_artifact_hash": runtime_hash,
        "runtime_cost_preflight_artifact_hash": cost_hash,
        "b4_input_freeze_artifact_hash": runtime_preflight["b4_input_freeze_artifact_hash"],
        "selected_model_authority_selection_hash": authority.selection_hash,
        "selected_model_eval_artifact_hash": authority.model_eval_artifact_hash,
        "selected_candidate": dict(runtime_preflight["selected_candidate"]),
        "planned_paid_calls_max": EXPECTED_LOGICAL_CALLS,
        "automatic_repair_calls_authorized": False,
        "approved_cost_ceiling_usd": str(approved_ceiling),
        "owner_approval": {
            "owner_approval_id": owner_approval_id,
            "owner_approval_at_utc": approval_at,
            "approved_cost_artifact_hash": cost_hash,
            "approved_cost_ceiling_usd": str(approved_ceiling),
            "scope": "ONE_B4_INITIAL_PRODUCTION_RUN_EXACTLY_NINE_BASELINE_CALLS_ONLY",
            "rebuttal_authorized": False,
            "judge_authorized": False,
            "rerun_authorized": False,
        },
        "receipt_contract_version": INITIAL_RUNTIME_PAID_RECEIPT_VERSION,
        "receipt_journal_path": receipt_journal_path,
        "network_manifest": {
            "openai_responses_api": True,
            "hosted_tools": False,
            "general_web_search": False,
            "remote_mcp": False,
            "broker_api": False,
        },
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact
