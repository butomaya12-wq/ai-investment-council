from __future__ import annotations

import argparse
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

from aic.council.initial_eval_runtime import (
    EXPECTED_INITIAL_CASE_IDS,
    INITIAL_EVAL_VERSION,
    build_case_request,
    build_initial_eval_cases,
    dry_run_manifest,
    execute_case_once,
    request_body_bytes,
)
from aic.council.model_policy import (
    INITIAL_MODEL_LADDER,
    MODEL_POLICY_VERSION,
    CouncilModelStage,
    StageModelEvalResult,
    select_stage_model_from_eval,
)
from aic.domain.canonical import canonical_sha256


ARTIFACT_VERSION = "B4_INITIAL_MODEL_EVAL_ARTIFACT_v0_2"
PAID_AUTHORIZATION_ARTIFACT_VERSION = "B4_INITIAL_PAID_AUTHORIZATION_ARTIFACT_v0_1"
PAID_CALL_RECEIPT_VERSION = "B4_INITIAL_PAID_CALL_RECEIPT_v0_1"
DEFAULT_COST_PREFLIGHT = Path(".aic-runtime/b4_initial_eval_cost_preflight.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b4_initial_model_eval.json")
DEFAULT_AUTHORIZATION_OUTPUT = Path(".aic-runtime/b4_initial_model_eval_paid_authorization.json")
DEFAULT_RECEIPT_JOURNAL = Path(".aic-runtime/b4_initial_model_eval_paid_receipts.jsonl")
EXPECTED_COST_STATUS = "REQUIRES_OWNER_COST_APPROVAL_BEFORE_INITIAL_MODEL_EVAL"
EXPECTED_COST_ARTIFACT_VERSION = "B4_INITIAL_EVAL_COST_PREFLIGHT_ARTIFACT_v0_1"
EXPECTED_PLANNED_CALLS = 36
EXPECTED_MAX_OUTPUT_TOKENS = 4096

# Exact UTC commit timestamp of the B4.3B pricing/cost-authority commit d760ea9...
# This is evidence capture metadata only; it does not change the frozen prices.
PRICING_AUTHORITY_CAPTURED_AT_UTC = "2026-08-29T18:45:42Z"
PRICING_CAPTURE_BASIS = (
    "B4.3B pricing/cost authority commit "
    "d760ea9c8e484b2679a0649f502cb35a08050d63 timestamp"
)


class B4InitialEvalAuthorizationError(ValueError):
    pass


class DispatchTrackingTransport:
    """Track whether a provider POST was attempted and whether a JSON response returned."""

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.dispatch_attempts = 0
        self.provider_responses = 0

    def post(self, *, payload: Mapping[str, Any], api_key: str) -> Mapping[str, Any]:
        self.dispatch_attempts += 1
        result = self.delegate.post(payload=payload, api_key=api_key)
        self.provider_responses += 1
        return result


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run or execute the frozen B4 Initial-stage model eval. "
            "Paid execution is impossible without exact owner cost-artifact approval "
            "and a durable owner-approval record."
        )
    )
    parser.add_argument("--cost-preflight", type=Path, default=DEFAULT_COST_PREFLIGHT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--authorization-output", type=Path, default=DEFAULT_AUTHORIZATION_OUTPUT)
    parser.add_argument("--receipt-journal", type=Path, default=DEFAULT_RECEIPT_JOURNAL)
    parser.add_argument("--execute-paid-eval", action="store_true")
    parser.add_argument("--approve-cost-artifact-hash")
    parser.add_argument("--approve-max-usd")
    parser.add_argument("--owner-approval-id")
    parser.add_argument("--owner-approval-at-utc")
    return parser.parse_args()


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise B4InitialEvalAuthorizationError(f"unable to read cost preflight: {path}") from exc
    if not isinstance(value, dict):
        raise B4InitialEvalAuthorizationError("cost preflight root must be an object")
    return value


def _verify_cost_preflight(cost: Mapping[str, Any]) -> None:
    actual_hash = cost.get("artifact_hash")
    if not isinstance(actual_hash, str) or len(actual_hash) != 64:
        raise B4InitialEvalAuthorizationError("cost preflight artifact_hash missing")
    expected_hash = canonical_sha256(cost, exclude_fields=("artifact_hash",))
    if actual_hash != expected_hash:
        raise B4InitialEvalAuthorizationError("cost preflight artifact_hash mismatch")
    if cost.get("artifact_version") != EXPECTED_COST_ARTIFACT_VERSION:
        raise B4InitialEvalAuthorizationError("unexpected cost preflight artifact version")
    if cost.get("status") != EXPECTED_COST_STATUS:
        raise B4InitialEvalAuthorizationError("cost preflight is not owner-approval ready")
    if cost.get("planned_paid_calls_max") != EXPECTED_PLANNED_CALLS:
        raise B4InitialEvalAuthorizationError("Initial paid-call ceiling drift")
    if cost.get("max_output_tokens_per_call") != EXPECTED_MAX_OUTPUT_TOKENS:
        raise B4InitialEvalAuthorizationError("Initial output-token ceiling drift")
    if tuple(cost.get("eval_case_ids", ())) != EXPECTED_INITIAL_CASE_IDS:
        raise B4InitialEvalAuthorizationError("Initial eval case surface drift")
    if cost.get("owner_cost_approval_required") is not True:
        raise B4InitialEvalAuthorizationError("cost preflight must require owner approval")
    for field in ("model_calls", "provider_reads", "broker_writes", "alpaca_orders"):
        if cost.get(field) != 0:
            raise B4InitialEvalAuthorizationError(f"cost preflight {field} must be zero")
    if cost.get("live_money") != "PROHIBITED":
        raise B4InitialEvalAuthorizationError("live money must remain prohibited")


def _decimal_text(value: object, *, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise B4InitialEvalAuthorizationError(f"{field_name} must be decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise B4InitialEvalAuthorizationError(f"{field_name} invalid decimal") from exc
    if not result.is_finite() or result < 0:
        raise B4InitialEvalAuthorizationError(f"{field_name} invalid")
    return result


def validate_paid_execution_authorization(
    cost: Mapping[str, Any],
    *,
    approve_cost_artifact_hash: str | None,
    approve_max_usd: str | None,
) -> Decimal:
    _verify_cost_preflight(cost)
    artifact_hash = cost["artifact_hash"]
    ceiling = _decimal_text(
        cost.get("total_initial_model_eval_cost_upper_bound_usd"),
        field_name="total_initial_model_eval_cost_upper_bound_usd",
    )
    if approve_cost_artifact_hash != artifact_hash:
        raise B4InitialEvalAuthorizationError(
            "paid Initial eval requires exact approved cost artifact hash"
        )
    approved = _decimal_text(approve_max_usd, field_name="approve_max_usd")
    if approved != ceiling:
        raise B4InitialEvalAuthorizationError(
            "paid Initial eval requires exact approval of frozen cost ceiling"
        )
    return ceiling


def _canonical_owner_approval_time(value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise B4InitialEvalAuthorizationError(
            "paid Initial eval requires owner_approval_at_utc"
        )
    text = value.strip()
    parse_value = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(parse_value)
    except ValueError as exc:
        raise B4InitialEvalAuthorizationError(
            "owner_approval_at_utc must be RFC3339 UTC"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise B4InitialEvalAuthorizationError(
            "owner_approval_at_utc must use UTC timezone"
        )
    if parsed > datetime.now(UTC):
        raise B4InitialEvalAuthorizationError("owner approval timestamp cannot be future")
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate_owner_approval_record(
    *,
    owner_approval_id: str | None,
    owner_approval_at_utc: str | None,
) -> tuple[str, str]:
    if not isinstance(owner_approval_id, str) or not owner_approval_id.strip():
        raise B4InitialEvalAuthorizationError(
            "paid Initial eval requires non-empty owner_approval_id"
        )
    approval_id = owner_approval_id.strip()
    if len(approval_id) > 160 or any(ch.isspace() for ch in approval_id):
        raise B4InitialEvalAuthorizationError(
            "owner_approval_id must be <=160 characters and contain no whitespace"
        )
    return approval_id, _canonical_owner_approval_time(owner_approval_at_utc)


def _pricing_metadata(cost: Mapping[str, Any]) -> dict[str, Any]:
    pricing_version = cost.get("pricing_version")
    pricing_hash = cost.get("pricing_hash")
    pricing_as_of_date = cost.get("pricing_as_of_date")
    if not isinstance(pricing_version, str) or not pricing_version:
        raise B4InitialEvalAuthorizationError("cost preflight pricing_version missing")
    if not isinstance(pricing_hash, str) or len(pricing_hash) != 64:
        raise B4InitialEvalAuthorizationError("cost preflight pricing_hash missing")
    if not isinstance(pricing_as_of_date, str) or not pricing_as_of_date:
        raise B4InitialEvalAuthorizationError("cost preflight pricing_as_of_date missing")
    return {
        "pricing_version": pricing_version,
        "pricing_hash": pricing_hash,
        "pricing_as_of_date": pricing_as_of_date,
        "pricing_captured_at_utc": PRICING_AUTHORITY_CAPTURED_AT_UTC,
        "pricing_capture_basis": PRICING_CAPTURE_BASIS,
    }


def _dry_run(cost: Mapping[str, Any]) -> dict[str, Any]:
    _verify_cost_preflight(cost)
    _pricing_metadata(cost)
    manifest = dry_run_manifest()
    request_bound = cost.get("eval_request_body_utf8_bytes_upper_bound")
    if not isinstance(request_bound, int) or request_bound <= 0:
        raise B4InitialEvalAuthorizationError("eval request byte bound invalid")
    if manifest["request_count"] != EXPECTED_PLANNED_CALLS:
        raise B4InitialEvalAuthorizationError("dry-run request count mismatch")
    if max(item["request_body_utf8_bytes"] for item in manifest["requests"]) > request_bound:
        raise B4InitialEvalAuthorizationError("frozen Initial eval request exceeds approved byte bound")
    if any(item["max_output_tokens"] != EXPECTED_MAX_OUTPUT_TOKENS for item in manifest["requests"]):
        raise B4InitialEvalAuthorizationError("dry-run request lacks exact output-token cap")
    return manifest


def _case_record(run) -> dict[str, Any]:
    return {
        "case_id": run.case_id,
        "name": run.name,
        "lane": run.lane,
        "critical_safety": run.critical_safety,
        "passed": run.passed,
        "findings": list(run.findings),
        "response_id": run.response_id,
        "requested_model": run.requested_model,
        "effective_model": run.effective_model,
        "model_calls": run.model_calls,
        "latency_ms": run.latency_ms,
        "input_tokens": run.input_tokens,
        "cached_tokens": run.cached_tokens,
        "output_tokens": run.output_tokens,
        "reasoning_tokens": run.reasoning_tokens,
        "estimated_cost_usd": str(run.estimated_cost_usd),
        "output_hash": run.output_hash,
        "result_hash": run.result_hash,
    }


def _candidate_record(candidate, runs) -> tuple[dict[str, Any], StageModelEvalResult]:
    passed = all(run.passed for run in runs)
    critical_failures = sum(1 for run in runs if run.critical_safety and not run.passed)
    cost = sum((run.estimated_cost_usd for run in runs), Decimal("0"))
    latency = sum(run.latency_ms for run in runs)
    total_tokens = sum(run.input_tokens + run.output_tokens for run in runs)
    eval_result = StageModelEvalResult(
        candidate_key=candidate.candidate_key,
        all_required_checks_passed=passed,
        critical_safety_failures=critical_failures,
        estimated_cost_usd=cost,
        latency_ms=latency,
        total_tokens=total_tokens,
    )
    record = {
        "candidate_key": candidate.candidate_key,
        "model": candidate.model,
        "reasoning_effort": candidate.reasoning_effort,
        "ladder_position": candidate.ladder_position,
        "cases": [_case_record(run) for run in runs],
        "passed_cases": sum(1 for run in runs if run.passed),
        "required_cases": len(runs),
        "all_required_checks_passed": passed,
        "critical_safety_failures": critical_failures,
        "estimated_cost_usd": str(cost),
        "latency_ms": latency,
        "total_tokens": total_tokens,
    }
    record["record_hash"] = canonical_sha256(record)
    return record, eval_result


def _write(output: Path, artifact: Mapping[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_durable(output: Path, artifact: Mapping[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _append_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _git_execution_context() -> dict[str, Any]:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise B4InitialEvalAuthorizationError(
            "unable to prove local git execution context"
        ) from exc
    if len(head) != 40 or any(ch not in "0123456789abcdef" for ch in head):
        raise B4InitialEvalAuthorizationError("local git HEAD is not a canonical SHA")
    if status.strip():
        raise B4InitialEvalAuthorizationError(
            "paid Initial eval requires a clean git working tree"
        )
    return {"code_commit_sha": head, "git_worktree_clean": True}


def _require_fresh_paid_paths(*paths: Path) -> None:
    for path in paths:
        if path.exists():
            raise B4InitialEvalAuthorizationError(
                f"paid evidence path already exists; refusing overwrite: {path}"
            )


def _utc_now_text() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _build_run_id(*, started_at_utc: str, cost_hash: str, dry_hash: str) -> str:
    suffix = canonical_sha256(
        {
            "started_at_utc": started_at_utc,
            "cost_preflight_artifact_hash": cost_hash,
            "dry_run_manifest_hash": dry_hash,
        }
    )[:12]
    compact = started_at_utc.replace("-", "").replace(":", "").replace(".", "")
    return f"AIC-B4-INITIAL-EVAL-{compact}-{suffix}"


def _build_paid_authorization_artifact(
    *,
    args: argparse.Namespace,
    cost: Mapping[str, Any],
    dry: Mapping[str, Any],
    approved_ceiling: Decimal,
    owner_approval_id: str,
    owner_approval_at_utc: str,
    git_context: Mapping[str, Any],
    run_started_at_utc: str,
    run_id: str,
) -> dict[str, Any]:
    pricing = _pricing_metadata(cost)
    artifact: dict[str, Any] = {
        "artifact_version": PAID_AUTHORIZATION_ARTIFACT_VERSION,
        "run_class": "B4_INITIAL_MODEL_EVAL_PAID_PRE_DISPATCH_AUTHORIZATION",
        "status": "AUTHORIZED_FOR_EXACT_PAID_INITIAL_EVAL",
        "run_id": run_id,
        "created_at_utc": run_started_at_utc,
        "code_commit_sha": git_context["code_commit_sha"],
        "git_worktree_clean": git_context["git_worktree_clean"],
        "eval_version": INITIAL_EVAL_VERSION,
        "model_policy_version": MODEL_POLICY_VERSION,
        "cost_preflight_artifact_hash": cost["artifact_hash"],
        "approved_cost_ceiling_usd": str(approved_ceiling),
        "dry_run_manifest_hash": dry["manifest_hash"],
        "planned_paid_calls_max": EXPECTED_PLANNED_CALLS,
        "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
        **pricing,
        "owner_approval": {
            "owner_approval_id": owner_approval_id,
            "owner_approval_at_utc": owner_approval_at_utc,
            "approved_cost_artifact_hash": cost["artifact_hash"],
            "approved_cost_ceiling_usd": str(approved_ceiling),
        },
        "command_argv": list(sys.argv),
        "receipt_contract_version": PAID_CALL_RECEIPT_VERSION,
        "receipt_journal_path": str(args.receipt_journal),
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


def _request_manifest_lookup(dry: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    requests = dry.get("requests")
    if not isinstance(requests, list):
        raise B4InitialEvalAuthorizationError("dry-run request manifest missing")
    result: dict[tuple[str, str], Mapping[str, Any]] = {}
    for item in requests:
        if not isinstance(item, Mapping):
            raise B4InitialEvalAuthorizationError("dry-run request record invalid")
        key = (item.get("candidate_key"), item.get("case_id"))
        if not all(isinstance(part, str) for part in key):
            raise B4InitialEvalAuthorizationError("dry-run request key invalid")
        if key in result:
            raise B4InitialEvalAuthorizationError("duplicate dry-run request key")
        result[key] = item
    if len(result) != EXPECTED_PLANNED_CALLS:
        raise B4InitialEvalAuthorizationError("dry-run request lookup count mismatch")
    return result


def _build_paid_call_receipt(
    *,
    run_id: str,
    dispatch_index: int,
    dispatch_started_at_utc: str,
    dispatch_finished_at_utc: str,
    authorization_artifact_hash: str,
    cost: Mapping[str, Any],
    approved_ceiling: Decimal,
    owner_approval_id: str,
    owner_approval_at_utc: str,
    code_commit_sha: str,
    candidate: Any,
    case: Any,
    request: Any,
    run: Any,
    tracker: DispatchTrackingTransport,
) -> dict[str, Any]:
    pricing = _pricing_metadata(cost)
    response_received = tracker.provider_responses == 1 and run.model_calls == 1
    cost_status = "COMPLETE" if response_received else "INCOMPLETE"
    result_status = (
        "PASS"
        if response_received and run.passed
        else "FAIL"
        if response_received
        else "BLOCKED_UNKNOWN_PROVIDER_DISPATCH"
    )
    receipt: dict[str, Any] = {
        "receipt_version": PAID_CALL_RECEIPT_VERSION,
        "run_id": run_id,
        "dispatch_index": dispatch_index,
        "dispatch_started_at_utc": dispatch_started_at_utc,
        "dispatch_finished_at_utc": dispatch_finished_at_utc,
        "code_commit_sha": code_commit_sha,
        "stage": "INITIAL",
        "case_id": case.case_id,
        "case_name": case.name,
        "lane": case.lane.value,
        "critical_safety": case.critical_safety,
        "candidate_key": candidate.candidate_key,
        "requested_model": candidate.model,
        "effective_model": run.effective_model,
        "reasoning_effort": candidate.reasoning_effort,
        "prompt_version": request.prompt_version,
        "prompt_hash": request.prompt_hash,
        "schema_version": request.schema_version,
        "input_hash": request.input_hash,
        "request_hash": request.request_hash,
        "request_body_utf8_bytes": request_body_bytes(request.request_payload),
        "max_output_tokens": request.request_payload["max_output_tokens"],
        "cost_preflight_artifact_hash": cost["artifact_hash"],
        "paid_authorization_artifact_hash": authorization_artifact_hash,
        "approved_cost_ceiling_usd": str(approved_ceiling),
        "owner_approval_id": owner_approval_id,
        "owner_approval_at_utc": owner_approval_at_utc,
        **pricing,
        "dispatch_attempted": tracker.dispatch_attempts == 1,
        "provider_response_received": response_received,
        "response_id": run.response_id,
        "input_tokens": run.input_tokens if response_received else None,
        "cached_tokens": run.cached_tokens if response_received else None,
        "output_tokens": run.output_tokens if response_received else None,
        "reasoning_tokens": run.reasoning_tokens if response_received else None,
        "latency_ms": run.latency_ms,
        "actual_cost_usd": str(run.estimated_cost_usd) if response_received else None,
        "cost_receipt_status": cost_status,
        "case_result": result_status,
        "findings": list(run.findings),
        "output_hash": run.output_hash,
        "result_hash": run.result_hash,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    receipt["receipt_hash"] = canonical_sha256(receipt)
    return receipt


def _partial_blocked_artifact(
    *,
    cost: Mapping[str, Any],
    approved_ceiling: Decimal,
    dry: Mapping[str, Any],
    run_id: str,
    authorization_artifact_hash: str,
    receipt_hashes: list[str],
    receipt_journal: Path,
    dispatch_attempts: int,
    completed_model_responses: int,
    known_cost: Decimal,
    reason: str,
) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "run_class": "B4_INITIAL_REAL_MODEL_EVAL",
        "status": "COST_RECEIPT_INCOMPLETE_PROVIDER_DISPATCH_UNKNOWN",
        "run_id": run_id,
        "eval_version": INITIAL_EVAL_VERSION,
        "model_policy_version": MODEL_POLICY_VERSION,
        "cost_preflight_artifact_hash": cost["artifact_hash"],
        "paid_authorization_artifact_hash": authorization_artifact_hash,
        "approved_cost_ceiling_usd": str(approved_ceiling),
        "dry_run_manifest_hash": dry["manifest_hash"],
        "provider_blocked_reason": reason,
        "dispatch_attempts": dispatch_attempts,
        "model_calls": completed_model_responses,
        "known_cost_usd": str(known_cost),
        "actual_cost_usd": None,
        "cost_receipt_status": "INCOMPLETE",
        "paid_call_receipt_hashes": receipt_hashes,
        "receipt_manifest_hash": canonical_sha256({"receipt_hashes": receipt_hashes}),
        "receipt_journal_path": str(receipt_journal),
        "provider_reads": 0,
        "external_writes": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def main() -> int:
    args = _args()
    try:
        cost = _read_object(args.cost_preflight)
        dry = _dry_run(cost)
        pricing = _pricing_metadata(cost)
        if not args.execute_paid_eval:
            artifact: dict[str, Any] = {
                "artifact_version": ARTIFACT_VERSION,
                "run_class": "B4_INITIAL_MODEL_EVAL_DRY_RUN",
                "status": "READY_FOR_OWNER_PAID_INITIAL_EVAL_AUTHORIZATION",
                "eval_version": INITIAL_EVAL_VERSION,
                "model_policy_version": MODEL_POLICY_VERSION,
                "cost_preflight_artifact_hash": cost["artifact_hash"],
                "approved_cost_ceiling_usd": None,
                "dry_run_manifest_hash": dry["manifest_hash"],
                "case_ids": list(EXPECTED_INITIAL_CASE_IDS),
                "candidate_keys": [item.candidate_key for item in INITIAL_MODEL_LADDER],
                "planned_paid_calls_max": EXPECTED_PLANNED_CALLS,
                "request_count": dry["request_count"],
                "max_request_body_utf8_bytes": max(
                    item["request_body_utf8_bytes"] for item in dry["requests"]
                ),
                "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
                **pricing,
                "paid_authorization_artifact_version": PAID_AUTHORIZATION_ARTIFACT_VERSION,
                "paid_call_receipt_version": PAID_CALL_RECEIPT_VERSION,
                "paid_receipt_journal_required": True,
                "unknown_dispatch_fail_closed": True,
                "paid_owner_approval_fields_required": [
                    "owner_approval_id",
                    "owner_approval_at_utc",
                    "approve_cost_artifact_hash",
                    "approve_max_usd",
                ],
                "model_calls": 0,
                "provider_reads": 0,
                "broker_writes": 0,
                "alpaca_orders": 0,
                "live_money": "PROHIBITED",
            }
            artifact["artifact_hash"] = canonical_sha256(artifact)
            _write(args.output, artifact)
            print(json.dumps(artifact, ensure_ascii=False, indent=2))
            return 0

        approved_ceiling = validate_paid_execution_authorization(
            cost,
            approve_cost_artifact_hash=args.approve_cost_artifact_hash,
            approve_max_usd=args.approve_max_usd,
        )
        owner_approval_id, owner_approval_at_utc = validate_owner_approval_record(
            owner_approval_id=args.owner_approval_id,
            owner_approval_at_utc=args.owner_approval_at_utc,
        )
        git_context = _git_execution_context()
        _require_fresh_paid_paths(
            args.output,
            args.authorization_output,
            args.receipt_journal,
        )
        request_manifest = _request_manifest_lookup(dry)
        run_started_at_utc = _utc_now_text()
        run_id = _build_run_id(
            started_at_utc=run_started_at_utc,
            cost_hash=cost["artifact_hash"],
            dry_hash=dry["manifest_hash"],
        )
        authorization = _build_paid_authorization_artifact(
            args=args,
            cost=cost,
            dry=dry,
            approved_ceiling=approved_ceiling,
            owner_approval_id=owner_approval_id,
            owner_approval_at_utc=owner_approval_at_utc,
            git_context=git_context,
            run_started_at_utc=run_started_at_utc,
            run_id=run_id,
        )
        _write_durable(args.authorization_output, authorization)

        # Import/load the secret only after all deterministic approval, git,
        # request, evidence-path and durable authorization gates pass.
        from aic.research.runtime import StdlibResponsesTransport, load_openai_api_key

        api_key = load_openai_api_key()
        cases = build_initial_eval_cases()
        candidate_records = []
        eval_results = []
        cumulative_cost = Decimal("0")
        completed_model_responses = 0
        dispatch_attempts = 0
        receipt_hashes: list[str] = []

        for candidate in INITIAL_MODEL_LADDER:
            runs = []
            for case in cases:
                if dispatch_attempts >= EXPECTED_PLANNED_CALLS:
                    raise B4InitialEvalAuthorizationError(
                        "paid Initial eval dispatch ceiling exhausted"
                    )
                request = build_case_request(case, candidate)
                frozen_request = request_manifest[(candidate.candidate_key, case.case_id)]
                if frozen_request.get("request_hash") != request.request_hash:
                    raise B4InitialEvalAuthorizationError(
                        "paid request hash differs from validated dry-run request"
                    )
                if frozen_request.get("request_body_utf8_bytes") != request_body_bytes(
                    request.request_payload
                ):
                    raise B4InitialEvalAuthorizationError(
                        "paid request byte size differs from validated dry-run request"
                    )
                print(
                    f"[B4 INITIAL EVAL] {candidate.candidate_key} {case.case_id} "
                    f"{candidate.model}/{candidate.reasoning_effort}",
                    file=sys.stderr,
                    flush=True,
                )
                dispatch_started_at_utc = _utc_now_text()
                tracker = DispatchTrackingTransport(StdlibResponsesTransport())
                run = execute_case_once(
                    case,
                    model_candidate=candidate,
                    api_key=api_key,
                    transport=tracker,
                )
                dispatch_finished_at_utc = _utc_now_text()
                if tracker.dispatch_attempts != 1:
                    raise B4InitialEvalAuthorizationError(
                        "each Initial eval case must attempt exactly one provider dispatch"
                    )
                dispatch_attempts += tracker.dispatch_attempts
                completed_model_responses += run.model_calls

                receipt = _build_paid_call_receipt(
                    run_id=run_id,
                    dispatch_index=dispatch_attempts,
                    dispatch_started_at_utc=dispatch_started_at_utc,
                    dispatch_finished_at_utc=dispatch_finished_at_utc,
                    authorization_artifact_hash=authorization["artifact_hash"],
                    cost=cost,
                    approved_ceiling=approved_ceiling,
                    owner_approval_id=owner_approval_id,
                    owner_approval_at_utc=owner_approval_at_utc,
                    code_commit_sha=git_context["code_commit_sha"],
                    candidate=candidate,
                    case=case,
                    request=request,
                    run=run,
                    tracker=tracker,
                )
                _append_receipt(args.receipt_journal, receipt)
                receipt_hashes.append(receipt["receipt_hash"])

                if tracker.provider_responses != 1 or run.model_calls != 1:
                    reason = run.findings[0] if run.findings else "provider dispatch receipt unavailable"
                    artifact = _partial_blocked_artifact(
                        cost=cost,
                        approved_ceiling=approved_ceiling,
                        dry=dry,
                        run_id=run_id,
                        authorization_artifact_hash=authorization["artifact_hash"],
                        receipt_hashes=receipt_hashes,
                        receipt_journal=args.receipt_journal,
                        dispatch_attempts=dispatch_attempts,
                        completed_model_responses=completed_model_responses,
                        known_cost=cumulative_cost,
                        reason=reason,
                    )
                    _write(args.output, artifact)
                    print(json.dumps(artifact, ensure_ascii=False, indent=2))
                    return 2

                runs.append(run)
                cumulative_cost += run.estimated_cost_usd
                if cumulative_cost > approved_ceiling:
                    raise B4InitialEvalAuthorizationError(
                        "actual eval cost exceeded approved ceiling"
                    )

            if len(runs) != len(cases):
                raise B4InitialEvalAuthorizationError(
                    "candidate eval did not cover full case set"
                )
            record, eval_result = _candidate_record(candidate, tuple(runs))
            candidate_records.append(record)
            eval_results.append(eval_result)

        if dispatch_attempts != EXPECTED_PLANNED_CALLS:
            raise B4InitialEvalAuthorizationError(
                "paid Initial eval must contain exactly 36 dispatch attempts"
            )
        if completed_model_responses != EXPECTED_PLANNED_CALLS:
            raise B4InitialEvalAuthorizationError(
                "paid Initial eval must contain exactly 36 completed provider responses"
            )
        if len(receipt_hashes) != EXPECTED_PLANNED_CALLS:
            raise B4InitialEvalAuthorizationError(
                "paid Initial eval must durably persist exactly 36 call receipts"
            )
        if len(eval_results) != len(INITIAL_MODEL_LADDER):
            raise B4InitialEvalAuthorizationError(
                "paid Initial eval did not cover full frozen ladder"
            )

        selection = select_stage_model_from_eval(
            CouncilModelStage.INITIAL, tuple(eval_results)
        )
        selected = (
            None
            if selection.selected_candidate is None
            else {
                "candidate_key": selection.selected_candidate.candidate_key,
                "model": selection.selected_candidate.model,
                "reasoning_effort": selection.selected_candidate.reasoning_effort,
                "ladder_position": selection.selected_candidate.ladder_position,
            }
        )
        artifact = {
            "artifact_version": ARTIFACT_VERSION,
            "run_class": "B4_INITIAL_REAL_MODEL_EVAL",
            "status": "PASS_SELECTED" if selected is not None else "BLOCKED_NO_PASSING_MODEL",
            "run_id": run_id,
            "code_commit_sha": git_context["code_commit_sha"],
            "eval_version": INITIAL_EVAL_VERSION,
            "model_policy_version": MODEL_POLICY_VERSION,
            "cost_preflight_artifact_hash": cost["artifact_hash"],
            "paid_authorization_artifact_hash": authorization["artifact_hash"],
            "approved_cost_ceiling_usd": str(approved_ceiling),
            "dry_run_manifest_hash": dry["manifest_hash"],
            **pricing,
            "case_ids": list(EXPECTED_INITIAL_CASE_IDS),
            "candidate_records": candidate_records,
            "selection": {
                "status": selection.status.value,
                "selected_candidate": selected,
                "reason_code": selection.reason_code,
            },
            "dispatch_attempts": dispatch_attempts,
            "model_calls": completed_model_responses,
            "actual_cost_usd": str(cumulative_cost),
            "cost_receipt_status": "COMPLETE",
            "paid_call_receipt_hashes": receipt_hashes,
            "receipt_manifest_hash": canonical_sha256(
                {"receipt_hashes": receipt_hashes}
            ),
            "receipt_journal_path": str(args.receipt_journal),
            "network_manifest": {
                "openai_responses_api": True,
                "hosted_tools": False,
                "general_web_search": False,
                "remote_mcp": False,
                "broker_api": False,
            },
            "provider_reads": 0,
            "external_writes": 0,
            "broker_writes": 0,
            "alpaca_orders": 0,
            "live_money": "PROHIBITED",
        }
        artifact["artifact_hash"] = canonical_sha256(artifact)
        _write(args.output, artifact)
        print(
            json.dumps(
                {
                    "artifact_version": artifact["artifact_version"],
                    "status": artifact["status"],
                    "run_id": artifact["run_id"],
                    "selection": artifact["selection"],
                    "dispatch_attempts": artifact["dispatch_attempts"],
                    "model_calls": artifact["model_calls"],
                    "actual_cost_usd": artifact["actual_cost_usd"],
                    "cost_receipt_status": artifact["cost_receipt_status"],
                    "approved_cost_ceiling_usd": artifact[
                        "approved_cost_ceiling_usd"
                    ],
                    "paid_authorization_artifact_hash": artifact[
                        "paid_authorization_artifact_hash"
                    ],
                    "receipt_manifest_hash": artifact["receipt_manifest_hash"],
                    "broker_writes": 0,
                    "alpaca_orders": 0,
                    "live_money": "PROHIBITED",
                    "artifact_hash": artifact["artifact_hash"],
                    "output_path": str(args.output),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if selected is not None else 1
    except Exception as exc:
        print(
            f"B4 Initial model eval failed closed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
