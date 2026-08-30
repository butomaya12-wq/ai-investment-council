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

from aic.council.initial_runtime_cost_v02 import load_initial_runtime_pricing
from aic.council.judge_model_selection_v01 import (
    JUDGE_SELECTED_MODEL_AUTHORITY_VERSION,
    build_judge_selected_model_authority,
    verify_judge_selected_model_authority,
)
from aic.council.judge_production import (
    EXPECTED_MAX_OUTPUT_TOKENS,
    JUDGE_PRODUCTION_BLOCKED_STATUS,
    JUDGE_PRODUCTION_RUNTIME_VERSION,
    build_judge_production_context,
    build_judge_production_request,
    build_judge_production_success_artifact,
    execute_judge_production_once,
)
from aic.council.judge_production_preflight import (
    build_judge_production_cost_preflight,
    build_judge_production_request_preflight,
    verify_judge_production_cost_preflight,
    verify_judge_production_request_preflight,
)
from aic.council.models import CouncilInputFreezeArtifact
from aic.domain.canonical import canonical_sha256
from aic.research.handoff import load_real_event_handoff


RUNNER_DRY_VERSION = "B4_JUDGE_PRODUCTION_RUNNER_DRY_v0_1"
PAID_AUTHORIZATION_VERSION = "B4_JUDGE_PRODUCTION_PAID_AUTHORIZATION_v0_1"
PAID_RECEIPT_VERSION = "B4_JUDGE_PRODUCTION_PAID_CALL_RECEIPT_v0_1"

DEFAULT_EVAL = Path(".aic-runtime/b4_judge_model_eval_v0_1.json")
DEFAULT_EVAL_RECEIPTS = Path(".aic-runtime/b4_judge_model_eval_paid_receipts_v0_1.jsonl")
DEFAULT_SELECTION = Path(".aic-runtime/b4_judge_selected_model_authority_v0_1.json")
DEFAULT_INPUT_FREEZE = Path(".aic-runtime/b4_council_input_freeze.json")
DEFAULT_RECONCILIATION = Path(".aic-runtime/b3_selected_model_reconciliation.json")
DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_INITIAL_FREEZE = Path(".aic-runtime/b4_initial_council_freeze_v0_5.json")
DEFAULT_REBUTTAL_FREEZE = Path(".aic-runtime/b4_rebuttal_council_freeze_v0_1.json")
DEFAULT_JUDGE_ENTRY = Path(".aic-runtime/b4_judge_entry_preflight_v0_1.json")
DEFAULT_REQUEST_PREFLIGHT = Path(".aic-runtime/b4_judge_production_request_preflight_v0_1.json")
DEFAULT_COST_PREFLIGHT = Path(".aic-runtime/b4_judge_production_cost_preflight_v0_1.json")
DEFAULT_RUNNER_DRY = Path(".aic-runtime/b4_judge_production_runner_dry_v0_1.json")
DEFAULT_PAID_OUTPUT = Path(".aic-runtime/b4_judge_production_result_v0_1.json")
DEFAULT_AUTHORIZATION = Path(".aic-runtime/b4_judge_production_paid_authorization_v0_1.json")
DEFAULT_RECEIPTS = Path(".aic-runtime/b4_judge_production_paid_receipts_v0_1.jsonl")


class JudgeProductionAuthorizationError(ValueError):
    pass


class DispatchTrackingTransport:
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
            "Dry-run or execute exactly one owner-approved B4 production Judge call. "
            "Current frozen event permits WATCH/ABSTAIN research-reopen only."
        )
    )
    parser.add_argument("--eval", type=Path, default=DEFAULT_EVAL)
    parser.add_argument("--eval-receipts", type=Path, default=DEFAULT_EVAL_RECEIPTS)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--input-freeze", type=Path, default=DEFAULT_INPUT_FREEZE)
    parser.add_argument("--reconciliation", type=Path, default=DEFAULT_RECONCILIATION)
    parser.add_argument("--handoff", type=Path, default=DEFAULT_HANDOFF)
    parser.add_argument("--initial-freeze", type=Path, default=DEFAULT_INITIAL_FREEZE)
    parser.add_argument("--rebuttal-freeze", type=Path, default=DEFAULT_REBUTTAL_FREEZE)
    parser.add_argument("--judge-entry", type=Path, default=DEFAULT_JUDGE_ENTRY)
    parser.add_argument("--request-preflight", type=Path, default=DEFAULT_REQUEST_PREFLIGHT)
    parser.add_argument("--cost-preflight", type=Path, default=DEFAULT_COST_PREFLIGHT)
    parser.add_argument("--runner-dry", type=Path, default=DEFAULT_RUNNER_DRY)
    parser.add_argument("--paid-output", type=Path, default=DEFAULT_PAID_OUTPUT)
    parser.add_argument("--authorization-output", type=Path, default=DEFAULT_AUTHORIZATION)
    parser.add_argument("--receipt-journal", type=Path, default=DEFAULT_RECEIPTS)
    parser.add_argument("--execute-paid-judge", action="store_true")
    parser.add_argument("--approve-selection-hash")
    parser.add_argument("--approve-request-preflight-hash")
    parser.add_argument("--approve-request-manifest-hash")
    parser.add_argument("--approve-cost-artifact-hash")
    parser.add_argument("--approve-runner-dry-artifact-hash")
    parser.add_argument("--approve-max-usd")
    parser.add_argument("--owner-approval-id")
    parser.add_argument("--owner-approval-at-utc")
    return parser.parse_args()


def _read(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise JudgeProductionAuthorizationError(f"unable to read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise JudgeProductionAuthorizationError(f"{label} root must be object")
    return value


def _read_receipts(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise JudgeProductionAuthorizationError("unable to read Judge eval receipts") from exc
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise JudgeProductionAuthorizationError("Judge eval receipt line must be object")
        rows.append(value)
    return rows


def _decimal(value: object, *, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise JudgeProductionAuthorizationError(f"{field_name} must be decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise JudgeProductionAuthorizationError(f"{field_name} invalid decimal") from exc
    if not result.is_finite() or result < 0:
        raise JudgeProductionAuthorizationError(f"{field_name} invalid")
    return result


def _head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git_execution_context(expected_head: str) -> dict[str, Any]:
    head = _head()
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if head != expected_head:
        raise JudgeProductionAuthorizationError("local HEAD differs from approved production Judge HEAD")
    if status.strip():
        raise JudgeProductionAuthorizationError("production Judge requires clean git worktree")
    return {"code_commit_sha": head, "git_worktree_clean": True}


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_durable_new(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _append_receipt(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _require_fresh_paid_paths(*paths: Path) -> None:
    for path in paths:
        if path.exists():
            raise JudgeProductionAuthorizationError(
                f"paid production Judge evidence already exists; refusing overwrite: {path}"
            )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_owner_time(value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise JudgeProductionAuthorizationError("owner approval timestamp required")
    text = value.strip()
    parse_value = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(parse_value)
    except ValueError as exc:
        raise JudgeProductionAuthorizationError("owner approval timestamp must be RFC3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise JudgeProductionAuthorizationError("owner approval timestamp must be UTC")
    if parsed > datetime.now(UTC):
        raise JudgeProductionAuthorizationError("owner approval timestamp cannot be future")
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _owner_record(owner_id: str | None, owner_at: str | None) -> tuple[str, str]:
    if not isinstance(owner_id, str) or not owner_id.strip():
        raise JudgeProductionAuthorizationError("owner approval ID required")
    value = owner_id.strip()
    if len(value) > 160 or any(ch.isspace() for ch in value):
        raise JudgeProductionAuthorizationError("owner approval ID invalid")
    return value, _canonical_owner_time(owner_at)


def _source_objects(args: argparse.Namespace) -> dict[str, Any]:
    eval_artifact = _read(args.eval, label="Judge eval artifact")
    eval_receipts = _read_receipts(args.eval_receipts)
    selection = _read(args.selection, label="Judge selected-model authority")
    rebuilt_selection = build_judge_selected_model_authority(eval_artifact, eval_receipts)
    if selection != rebuilt_selection:
        raise JudgeProductionAuthorizationError(
            "on-disk Judge selected-model authority differs from durable paid-eval replay"
        )
    verify_judge_selected_model_authority(selection)
    input_freeze = CouncilInputFreezeArtifact.model_validate(
        _read(args.input_freeze, label="B4 input freeze")
    )
    reconciliation = _read(args.reconciliation, label="B3 reconciliation")
    handoff = load_real_event_handoff(args.handoff)
    initial_freeze = _read(args.initial_freeze, label="Initial Council freeze")
    rebuttal_freeze = _read(args.rebuttal_freeze, label="Rebuttal Council freeze")
    judge_entry = _read(args.judge_entry, label="Judge entry")
    context = build_judge_production_context(
        input_freeze=input_freeze,
        reconciliation=reconciliation,
        handoff=handoff,
        initial_freeze=initial_freeze,
        rebuttal_freeze=rebuttal_freeze,
        judge_entry=judge_entry,
        selected_model_authority=selection,
    )
    return {
        "selection": selection,
        "context": context,
    }


def _deterministic_dry(args: argparse.Namespace) -> dict[str, Any]:
    sources = _source_objects(args)
    selection = sources["selection"]
    context = sources["context"]
    request_preflight = _read(args.request_preflight, label="production Judge request preflight")
    cost_preflight = _read(args.cost_preflight, label="production Judge cost preflight")
    rebuilt_request = build_judge_production_request_preflight(
        code_commit_sha=_head(),
        context=context,
        selected_model_authority=selection,
    )
    if request_preflight != rebuilt_request:
        raise JudgeProductionAuthorizationError("production Judge request preflight differs from deterministic rebuild")
    rebuilt_cost = build_judge_production_cost_preflight(request_preflight)
    if cost_preflight != rebuilt_cost:
        raise JudgeProductionAuthorizationError("production Judge cost preflight differs from deterministic rebuild")
    request_hash = verify_judge_production_request_preflight(request_preflight)
    cost_hash = verify_judge_production_cost_preflight(cost_preflight)
    request = build_judge_production_request(context, selection)
    if request.request_hash != request_preflight["request_hash"]:
        raise JudgeProductionAuthorizationError("production Judge runtime request differs from preflight")
    return {
        "selection": selection,
        "context": context,
        "request": request,
        "request_preflight": request_preflight,
        "request_preflight_hash": request_hash,
        "cost_preflight": cost_preflight,
        "cost_preflight_hash": cost_hash,
        "cost_ceiling_usd": _decimal(
            cost_preflight["production_judge_cost_upper_bound_usd"],
            field_name="production_judge_cost_upper_bound_usd",
        ),
    }


def _runner_dry_artifact(dry: Mapping[str, Any]) -> dict[str, Any]:
    request_preflight = dry["request_preflight"]
    cost_preflight = dry["cost_preflight"]
    selection = dry["selection"]
    artifact: dict[str, Any] = {
        "artifact_version": RUNNER_DRY_VERSION,
        "status": "READY_FOR_EXPLICIT_OWNER_B4_PRODUCTION_JUDGE_AUTHORIZATION",
        "code_commit_sha": request_preflight["code_commit_sha"],
        "runtime_version": JUDGE_PRODUCTION_RUNTIME_VERSION,
        "judge_selected_model_authority_version": JUDGE_SELECTED_MODEL_AUTHORITY_VERSION,
        "judge_selected_model_authority_hash": selection["artifact_hash"],
        "selected_candidate": dict(selection["selected_candidate"]),
        "judge_input_hash": request_preflight["judge_input_hash"],
        "judge_context_hash": request_preflight["judge_context_hash"],
        "request_preflight_artifact_hash": dry["request_preflight_hash"],
        "request_manifest_hash": request_preflight["request_manifest_hash"],
        "request_hash": request_preflight["request_hash"],
        "cost_preflight_artifact_hash": dry["cost_preflight_hash"],
        "cost_ceiling_usd": str(dry["cost_ceiling_usd"]),
        "planned_paid_calls_max": 1,
        "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
        "allowed_outcomes": list(request_preflight["allowed_outcomes"]),
        "required_unknown_refs": list(request_preflight["required_unknown_refs"]),
        "required_research_reopen": True,
        "required_next_directive": "RESEARCH_REOPEN_REQUEST",
        "final_decision_creation_allowed_for_current_frozen_run": False,
        "b5_handoff_allowed_for_current_frozen_run": False,
        "research_reopen_request_persistence_required": True,
        "paid_authorization_artifact_version": PAID_AUTHORIZATION_VERSION,
        "paid_call_receipt_version": PAID_RECEIPT_VERSION,
        "consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "unknown_dispatch_fail_closed": True,
        "stop_on_incomplete_cost_receipt": True,
        "stop_on_validation_failure": True,
        "automatic_repair_calls_authorized": False,
        "rerun_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "production_judge_authorized": False,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def validate_paid_authorization(
    *,
    dry: Mapping[str, Any],
    runner_dry: Mapping[str, Any],
    approve_selection_hash: str | None,
    approve_request_hash: str | None,
    approve_manifest_hash: str | None,
    approve_cost_hash: str | None,
    approve_runner_dry_hash: str | None,
    approve_max_usd: str | None,
) -> Decimal:
    expected_runner = _runner_dry_artifact(dry)
    if runner_dry != expected_runner:
        raise JudgeProductionAuthorizationError("production Judge runner dry artifact drift")
    if approve_selection_hash != dry["selection"]["artifact_hash"]:
        raise JudgeProductionAuthorizationError("production Judge requires exact selected-model authority approval")
    if approve_request_hash != dry["request_preflight_hash"]:
        raise JudgeProductionAuthorizationError("production Judge requires exact request-preflight approval")
    if approve_manifest_hash != dry["request_preflight"]["request_manifest_hash"]:
        raise JudgeProductionAuthorizationError("production Judge requires exact request-manifest approval")
    if approve_cost_hash != dry["cost_preflight_hash"]:
        raise JudgeProductionAuthorizationError("production Judge requires exact cost-preflight approval")
    if approve_runner_dry_hash != runner_dry.get("artifact_hash"):
        raise JudgeProductionAuthorizationError("production Judge requires exact runner-dry approval")
    if _decimal(approve_max_usd, field_name="approve_max_usd") != dry["cost_ceiling_usd"]:
        raise JudgeProductionAuthorizationError("production Judge requires exact cost-ceiling approval")
    return dry["cost_ceiling_usd"]


def _run_id(started: str, request_hash: str) -> str:
    seed = canonical_sha256({"started_at": started, "request_hash": request_hash})[:12]
    compact = started.replace("-", "").replace(":", "").replace(".", "")
    return f"AIC-B4-JUDGE-PRODUCTION-{compact}-{seed}"


def _authorization_artifact(
    *,
    run_id: str,
    created_at: str,
    git_context: Mapping[str, Any],
    dry: Mapping[str, Any],
    runner_dry: Mapping[str, Any],
    ceiling: Decimal,
    owner_id: str,
    owner_at: str,
    receipt_journal: Path,
) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "artifact_version": PAID_AUTHORIZATION_VERSION,
        "run_class": "B4_ONE_PRODUCTION_JUDGE_CALL",
        "status": "AUTHORIZED_UNCONSUMED_BEFORE_DISPATCH",
        "consumption_rule": "CONSUMED_ON_FIRST_PROVIDER_DISPATCH_ATTEMPT",
        "run_id": run_id,
        "created_at_utc": created_at,
        "code_commit_sha": git_context["code_commit_sha"],
        "git_worktree_clean": git_context["git_worktree_clean"],
        "judge_selected_model_authority_hash": dry["selection"]["artifact_hash"],
        "selected_candidate": dict(dry["selection"]["selected_candidate"]),
        "judge_input_hash": dry["context"].judge_input_hash,
        "judge_context_hash": dry["context"].context_hash,
        "request_preflight_artifact_hash": dry["request_preflight_hash"],
        "request_manifest_hash": dry["request_preflight"]["request_manifest_hash"],
        "request_hash": dry["request"].request_hash,
        "cost_preflight_artifact_hash": dry["cost_preflight_hash"],
        "runner_dry_artifact_hash": runner_dry["artifact_hash"],
        "approved_cost_ceiling_usd": str(ceiling),
        "planned_paid_calls_max": 1,
        "max_output_tokens_per_call": EXPECTED_MAX_OUTPUT_TOKENS,
        "owner_approval": {
            "owner_approval_id": owner_id,
            "owner_approval_at_utc": owner_at,
            "approved_selection_hash": dry["selection"]["artifact_hash"],
            "approved_request_preflight_hash": dry["request_preflight_hash"],
            "approved_request_manifest_hash": dry["request_preflight"]["request_manifest_hash"],
            "approved_cost_preflight_hash": dry["cost_preflight_hash"],
            "approved_runner_dry_hash": runner_dry["artifact_hash"],
            "approved_cost_ceiling_usd": str(ceiling),
        },
        "receipt_contract_version": PAID_RECEIPT_VERSION,
        "receipt_journal_path": str(receipt_journal),
        "automatic_repair_calls_authorized": False,
        "research_reopen_request_persistence_required": True,
        "final_decision_creation_allowed": False,
        "b5_handoff_allowed": False,
        "rerun_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "production_judge_authorized": True,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def _receipt(
    *,
    run_id: str,
    started_at: str,
    finished_at: str,
    code_commit_sha: str,
    authorization: Mapping[str, Any],
    dry: Mapping[str, Any],
    runner_dry: Mapping[str, Any],
    owner_id: str,
    owner_at: str,
    tracker: DispatchTrackingTransport,
    run: Any,
) -> dict[str, Any]:
    provider_received = tracker.provider_responses == 1 and run.model_calls == 1
    if not provider_received:
        result = "BLOCKED_UNKNOWN_PROVIDER_DISPATCH"
    elif run.cost_receipt_status != "COMPLETE":
        result = "BLOCKED_INCOMPLETE_COST_RECEIPT"
    elif run.validation_status != "PASS":
        result = "BLOCKED_JUDGE_VALIDATION_FAILED"
    else:
        result = "PASS_RESEARCH_REOPEN_REQUESTED"
    receipt: dict[str, Any] = {
        "receipt_version": PAID_RECEIPT_VERSION,
        "run_id": run_id,
        "dispatch_index": 1,
        "dispatch_started_at_utc": started_at,
        "dispatch_finished_at_utc": finished_at,
        "code_commit_sha": code_commit_sha,
        "stage": "JUDGE",
        "run_class": "PRODUCTION",
        "candidate_key": "J1",
        "requested_model": "gpt-5.6-terra",
        "effective_model": run.effective_model,
        "reasoning_effort": "medium",
        "request_hash": dry["request"].request_hash,
        "request_body_utf8_bytes": dry["request_preflight"]["request_body_utf8_bytes"],
        "max_output_tokens": EXPECTED_MAX_OUTPUT_TOKENS,
        "judge_input_hash": dry["context"].judge_input_hash,
        "judge_context_hash": dry["context"].context_hash,
        "judge_selected_model_authority_hash": dry["selection"]["artifact_hash"],
        "request_preflight_artifact_hash": dry["request_preflight_hash"],
        "request_manifest_hash": dry["request_preflight"]["request_manifest_hash"],
        "cost_preflight_artifact_hash": dry["cost_preflight_hash"],
        "runner_dry_artifact_hash": runner_dry["artifact_hash"],
        "paid_authorization_artifact_hash": authorization["artifact_hash"],
        "approved_cost_ceiling_usd": authorization["approved_cost_ceiling_usd"],
        "owner_approval_id": owner_id,
        "owner_approval_at_utc": owner_at,
        "dispatch_attempted": tracker.dispatch_attempts == 1,
        "provider_response_received": provider_received,
        "response_id": run.response_id,
        "latency_ms": run.latency_ms,
        "input_tokens": run.input_tokens,
        "cached_tokens": run.cached_tokens,
        "cache_write_tokens": run.cache_write_tokens,
        "output_tokens": run.output_tokens,
        "reasoning_tokens": run.reasoning_tokens,
        "actual_cost_usd": None if run.actual_cost_usd is None else str(run.actual_cost_usd),
        "cost_receipt_status": run.cost_receipt_status,
        "validation_status": run.validation_status,
        "validation_error": run.validation_error,
        "call_result": result,
        "output_hash": run.output_hash,
        "structured_output": None if run.structured_output is None else dict(run.structured_output),
        "structured_output_hash": run.structured_output_hash,
        "judge_proposal_hash": run.judge_proposal_hash,
        "research_reopen_request": None if run.research_reopen_request is None else dict(run.research_reopen_request),
        "research_reopen_request_hash": run.research_reopen_request_hash,
        "automatic_repair_attempted": False,
        "rerun_authorized": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    receipt["receipt_hash"] = canonical_sha256(receipt)
    return receipt


def _blocked_artifact(
    *,
    status: str,
    reason: str,
    run_id: str,
    code_commit_sha: str,
    dry: Mapping[str, Any],
    runner_dry: Mapping[str, Any],
    authorization_hash: str,
    receipt_hash: str,
    ceiling: Decimal,
    known_cost: Decimal | None,
) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "artifact_version": "B4_JUDGE_PRODUCTION_BLOCKED_v0_1",
        "runtime_version": JUDGE_PRODUCTION_RUNTIME_VERSION,
        "status": status,
        "reason": reason,
        "run_id": run_id,
        "code_commit_sha": code_commit_sha,
        "judge_selected_model_authority_hash": dry["selection"]["artifact_hash"],
        "request_preflight_artifact_hash": dry["request_preflight_hash"],
        "request_manifest_hash": dry["request_preflight"]["request_manifest_hash"],
        "cost_preflight_artifact_hash": dry["cost_preflight_hash"],
        "runner_dry_artifact_hash": runner_dry["artifact_hash"],
        "paid_authorization_artifact_hash": authorization_hash,
        "paid_call_receipt_hash": receipt_hash,
        "approved_cost_ceiling_usd": str(ceiling),
        "known_cost_usd": None if known_cost is None else str(known_cost),
        "cost_receipt_status": "COMPLETE" if known_cost is not None else "INCOMPLETE",
        "dispatch_attempts": 1,
        "model_calls": 1 if known_cost is not None else 0,
        "judge_authorization_consumed": True,
        "automatic_repair_calls": 0,
        "b4_complete": False,
        "final_decision_created": False,
        "b5_handoff_created": False,
        "rerun_authorized": False,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def main() -> int:
    args = _args()
    try:
        dry = _deterministic_dry(args)
        runner_dry = _runner_dry_artifact(dry)
        if not args.execute_paid_judge:
            _write(args.runner_dry, runner_dry)
            print(json.dumps(runner_dry, ensure_ascii=False, indent=2))
            return 0

        on_disk_runner_dry = _read(args.runner_dry, label="production Judge runner dry")
        ceiling = validate_paid_authorization(
            dry=dry,
            runner_dry=on_disk_runner_dry,
            approve_selection_hash=args.approve_selection_hash,
            approve_request_hash=args.approve_request_preflight_hash,
            approve_manifest_hash=args.approve_request_manifest_hash,
            approve_cost_hash=args.approve_cost_artifact_hash,
            approve_runner_dry_hash=args.approve_runner_dry_artifact_hash,
            approve_max_usd=args.approve_max_usd,
        )
        owner_id, owner_at = _owner_record(args.owner_approval_id, args.owner_approval_at_utc)
        git_context = _git_execution_context(dry["request_preflight"]["code_commit_sha"])
        _require_fresh_paid_paths(args.paid_output, args.authorization_output, args.receipt_journal)

        started = _utc_now()
        run_id = _run_id(started, dry["request"].request_hash)
        authorization = _authorization_artifact(
            run_id=run_id,
            created_at=started,
            git_context=git_context,
            dry=dry,
            runner_dry=on_disk_runner_dry,
            ceiling=ceiling,
            owner_id=owner_id,
            owner_at=owner_at,
            receipt_journal=args.receipt_journal,
        )
        _write_durable_new(args.authorization_output, authorization)

        from aic.research.runtime import StdlibResponsesTransport, load_openai_api_key

        api_key = load_openai_api_key()
        print("[B4 PRODUCTION JUDGE] J1 gpt-5.6-terra/medium", file=sys.stderr, flush=True)
        dispatch_started = _utc_now()
        tracker = DispatchTrackingTransport(StdlibResponsesTransport())
        run = execute_judge_production_once(
            request=dry["request"],
            context=dry["context"],
            api_key=api_key,
            transport=tracker,
            pricing=load_initial_runtime_pricing(),
            parent_run_id=run_id,
        )
        dispatch_finished = _utc_now()
        if tracker.dispatch_attempts != 1:
            raise JudgeProductionAuthorizationError("production Judge must attempt exactly one provider dispatch")
        receipt = _receipt(
            run_id=run_id,
            started_at=dispatch_started,
            finished_at=dispatch_finished,
            code_commit_sha=git_context["code_commit_sha"],
            authorization=authorization,
            dry=dry,
            runner_dry=on_disk_runner_dry,
            owner_id=owner_id,
            owner_at=owner_at,
            tracker=tracker,
            run=run,
        )
        _append_receipt(args.receipt_journal, receipt)
        receipt_manifest_hash = canonical_sha256({"receipt_hashes": [receipt["receipt_hash"]]})

        if tracker.provider_responses != 1 or run.model_calls != 1:
            artifact = _blocked_artifact(
                status="BLOCKED_UNKNOWN_PROVIDER_DISPATCH",
                reason=run.validation_error or "provider response unavailable",
                run_id=run_id,
                code_commit_sha=git_context["code_commit_sha"],
                dry=dry,
                runner_dry=on_disk_runner_dry,
                authorization_hash=authorization["artifact_hash"],
                receipt_hash=receipt["receipt_hash"],
                ceiling=ceiling,
                known_cost=None,
            )
            _write(args.paid_output, artifact)
            print(json.dumps(artifact, ensure_ascii=False, indent=2))
            return 2

        if run.cost_receipt_status != "COMPLETE" or run.actual_cost_usd is None:
            artifact = _blocked_artifact(
                status="BLOCKED_INCOMPLETE_COST_RECEIPT",
                reason=run.validation_error or "production Judge cost receipt incomplete",
                run_id=run_id,
                code_commit_sha=git_context["code_commit_sha"],
                dry=dry,
                runner_dry=on_disk_runner_dry,
                authorization_hash=authorization["artifact_hash"],
                receipt_hash=receipt["receipt_hash"],
                ceiling=ceiling,
                known_cost=None,
            )
            _write(args.paid_output, artifact)
            print(json.dumps(artifact, ensure_ascii=False, indent=2))
            return 2

        if run.actual_cost_usd > ceiling:
            artifact = _blocked_artifact(
                status="BLOCKED_APPROVED_COST_CEILING_EXCEEDED",
                reason="actual production Judge cost exceeded approved ceiling",
                run_id=run_id,
                code_commit_sha=git_context["code_commit_sha"],
                dry=dry,
                runner_dry=on_disk_runner_dry,
                authorization_hash=authorization["artifact_hash"],
                receipt_hash=receipt["receipt_hash"],
                ceiling=ceiling,
                known_cost=run.actual_cost_usd,
            )
            _write(args.paid_output, artifact)
            print(json.dumps(artifact, ensure_ascii=False, indent=2))
            return 2

        if run.validation_status != "PASS":
            artifact = _blocked_artifact(
                status="BLOCKED_JUDGE_VALIDATION_FAILED",
                reason=run.validation_error or "production Judge deterministic validation failed",
                run_id=run_id,
                code_commit_sha=git_context["code_commit_sha"],
                dry=dry,
                runner_dry=on_disk_runner_dry,
                authorization_hash=authorization["artifact_hash"],
                receipt_hash=receipt["receipt_hash"],
                ceiling=ceiling,
                known_cost=run.actual_cost_usd,
            )
            _write(args.paid_output, artifact)
            print(json.dumps(artifact, ensure_ascii=False, indent=2))
            return 2

        artifact = build_judge_production_success_artifact(
            run_id=run_id,
            code_commit_sha=git_context["code_commit_sha"],
            context=dry["context"],
            request_preflight_hash=dry["request_preflight_hash"],
            request_manifest_hash=dry["request_preflight"]["request_manifest_hash"],
            cost_preflight_hash=dry["cost_preflight_hash"],
            runner_dry_hash=on_disk_runner_dry["artifact_hash"],
            selected_model_authority_hash=dry["selection"]["artifact_hash"],
            paid_authorization_hash=authorization["artifact_hash"],
            paid_receipt_hash=receipt["receipt_hash"],
            receipt_manifest_hash=receipt_manifest_hash,
            approved_cost_ceiling_usd=ceiling,
            run=run,
        )
        _write(args.paid_output, artifact)
        print(json.dumps(artifact, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
