"""Fail-closed executor for an explicitly approved fresh nine-call Initial recovery."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
import re
from time import perf_counter_ns
from typing import Any, Callable, Mapping, Sequence

from aic.domain.canonical import canonical_sha256

from . import post_research_reopen_initial_paid_failure_recovery_preflight_v01 as forensic
from . import post_research_reopen_initial_production_dispatch_v01 as dispatch
from .initial_runtime_cost_v02 import actual_cost_usd, load_initial_runtime_pricing
from .post_research_reopen_initial_execute_production_v01 import (
    PostResearchInitialExecutionError,
    _replace_durable,
    _write_exclusive,
    build_raw_response_capture,
    frozen_initial_items,
    verify_context_admissibility,
    verify_raw_response_capture,
)
from .reopen_initial_runtime import process_reopen_initial_provider_response


RECOVERY_READINESS_VERSION = "B4_POST_RESEARCH_REOPEN_INITIAL_FULL_RECOVERY_READINESS_ZERO_CALL_v0_1"
RECOVERY_READINESS_STATUS = "B4_POST_RESEARCH_REOPEN_INITIAL_FULL_RECOVERY_READINESS_ZERO_CALL_PASS"
RECOVERY_OWNER_APPROVAL_VERSION = "B4_POST_RESEARCH_REOPEN_INITIAL_FULL_RECOVERY_OWNER_APPROVAL_v0_1"
RECOVERY_LEDGER_VERSION = "B4_POST_RESEARCH_REOPEN_INITIAL_FULL_RECOVERY_PAID_DISPATCH_LEDGER_v0_1"
RECOVERY_RESULT_VERSION = "B4_POST_RESEARCH_REOPEN_INITIAL_FULL_RECOVERY_COUNCIL_FREEZE_v0_1"
RECOVERY_RESULT_STATUS = "B4_POST_RESEARCH_REOPEN_INITIAL_FULL_RECOVERY_COUNCIL_FROZEN"
RECOVERY_MODE = "FULL_INITIAL_RECOVERY"
REPLACEMENT_KIND = "OWNER_AUTHORIZED_REPLACEMENT"
FIRST_DISPATCH_KIND = "FIRST_DISPATCH"
HISTORICAL_PAID_COST_USD = "UNKNOWN"
HISTORICAL_PAID_COST_MAX_USD = "0.636487"
RECOVERY_MAX_COST_USD = "5.726043"
TOTAL_LINEAGE_CONSERVATIVE_MAX_USD = "6.362530"
NEXT_GATE = "B4_POST_RESEARCH_REOPEN_INITIAL_FULL_RECOVERY_EXPLICIT_OWNER_APPROVAL_THEN_NINE_PAID_CALLS"


class FullInitialRecoveryError(RuntimeError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise FullInitialRecoveryError(message)


def _self_hash(payload: Mapping[str, Any], *, field: str = "artifact_hash") -> str:
    observed = payload.get(field)
    _need(isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None, f"{field} missing")
    _need(observed == canonical_sha256(payload, exclude_fields=(field,)), f"{field} self-hash mismatch")
    return observed


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _historical_inputs(
    *,
    historical_ledger: Mapping[str, Any],
    historical_ledger_file_sha256: str,
    recovery_preflight: Mapping[str, Any],
    cost_preflight: Mapping[str, Any],
    historical_raw_response_dir_exists: bool,
    historical_raw_response_file_count: int,
    fresh_initial_result_exists: bool,
) -> None:
    _need(historical_ledger_file_sha256 == forensic.EXPECTED_LEDGER_FILE_SHA256, "historical failed ledger file SHA-256 drift")
    _need(historical_raw_response_dir_exists is False and historical_raw_response_file_count == 0, "historical raw response unexpectedly available")
    _need(fresh_initial_result_exists is False, "fresh Initial result already exists")
    try:
        observed = forensic.verify_failure_recovery_preflight(
            recovery_preflight,
            ledger=historical_ledger,
            ledger_file_sha256=historical_ledger_file_sha256,
            cost_preflight=cost_preflight,
        )
    except forensic.PaidFailureRecoveryPreflightError as exc:
        raise FullInitialRecoveryError(str(exc)) from exc
    _need(observed == "acb859c8bfbe5ea4a6a34a316de509d027f9512bbd3697350f7532d4fcc9ec3c", "recovery preflight hash drift")
    _need(recovery_preflight.get("recovery_classification") == forensic.RECOVERY_CLASSIFICATION, "historical request #1 classification drift")
    request_1 = recovery_preflight.get("request_1")
    options = recovery_preflight.get("recovery_options")
    _need(isinstance(request_1, Mapping) and request_1.get("automatic_resend_permitted") is False, "historical request #1 resend policy drift")
    _need(isinstance(options, Mapping), "recovery options missing")
    full = options.get("option_b_full_initial_recovery")
    _need(isinstance(full, Mapping) and full.get("additional_provider_calls_required") == 9 and full.get("total_provider_call_lineage") == 10 and full.get("existing_approval_valid") is False, "full recovery authority facts drift")


def recovery_items(cost_preflight: Mapping[str, Any]) -> tuple[Any, ...]:
    try:
        items = frozen_initial_items(cost_preflight)
    except PostResearchInitialExecutionError as exc:
        raise FullInitialRecoveryError(str(exc)) from exc
    _need(len(items) == 9, "recovery request count drift")
    for index, item in enumerate(items, start=1):
        row = item.row
        _need(row.get("request_hash") == item.plan_item.request.request_hash, f"recovery request {index} hash drift")
        _need(row.get("request_payload_canonical_hash") == canonical_sha256(item.plan_item.request.request_payload), f"recovery request {index} payload hash drift")
        _need(row.get("model") == dispatch.EXPECTED_MODEL, f"recovery request {index} model drift")
        _need(row.get("reasoning_effort") == dispatch.EXPECTED_REASONING_EFFORT, f"recovery request {index} effort drift")
        _need(row.get("candidate") == item.plan_item.candidate_id and row.get("council_role") == item.plan_item.lane.value, f"recovery request {index} candidate/role drift")
        _need(row.get("maximum_output_tokens") == dispatch.EXPECTED_MAX_OUTPUT_TOKENS, f"recovery request {index} output cap drift")
    return items


def recovery_kinds(items: Sequence[Any]) -> list[str]:
    _need(len(items) == 9, "recovery kind request count drift")
    return [REPLACEMENT_KIND] + [FIRST_DISPATCH_KIND] * 8


def build_full_recovery_readiness(
    *,
    code_commit_sha: str,
    cost_preflight: Mapping[str, Any],
    context_capability: Mapping[str, Any],
    historical_ledger: Mapping[str, Any],
    historical_ledger_file_sha256: str,
    recovery_preflight: Mapping[str, Any],
    historical_raw_response_dir_exists: bool,
    historical_raw_response_file_count: int,
    fresh_initial_result_exists: bool,
) -> dict[str, Any]:
    _need(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha) is not None, "recovery executor code SHA invalid")
    _historical_inputs(
        historical_ledger=historical_ledger,
        historical_ledger_file_sha256=historical_ledger_file_sha256,
        recovery_preflight=recovery_preflight,
        cost_preflight=cost_preflight,
        historical_raw_response_dir_exists=historical_raw_response_dir_exists,
        historical_raw_response_file_count=historical_raw_response_file_count,
        fresh_initial_result_exists=fresh_initial_result_exists,
    )
    items = recovery_items(cost_preflight)
    try:
        verify_context_admissibility(items, context_capability)
    except PostResearchInitialExecutionError as exc:
        raise FullInitialRecoveryError(str(exc)) from exc
    artifact: dict[str, Any] = {
        "artifact_version": RECOVERY_READINESS_VERSION,
        "status": RECOVERY_READINESS_STATUS,
        "code_commit_sha": code_commit_sha,
        "recovery_mode": RECOVERY_MODE,
        "source_cost_preflight_hash": dispatch.EXPECTED_PREFLIGHT_HASH,
        "recovery_preflight_hash": recovery_preflight["artifact_hash"],
        "historical_failed_ledger_file_sha256": historical_ledger_file_sha256,
        "historical_failed_ledger_hash": forensic.EXPECTED_LEDGER_HASH,
        "historical_owner_approval_hash": forensic.EXPECTED_OWNER_APPROVAL_HASH,
        "historical_request_1_hash": items[0].plan_item.request.request_hash,
        "historical_request_1_failure_classification": forensic.RECOVERY_CLASSIFICATION,
        "historical_provider_calls_confirmed": 1,
        "new_paid_calls_planned": 9,
        "total_provider_call_lineage_if_complete": 10,
        "model": dispatch.EXPECTED_MODEL,
        "reasoning_effort": dispatch.EXPECTED_REASONING_EFFORT,
        "max_output_tokens_per_call": dispatch.EXPECTED_MAX_OUTPUT_TOKENS,
        "new_recovery_max_cost_usd": RECOVERY_MAX_COST_USD,
        "historical_paid_cost_usd": HISTORICAL_PAID_COST_USD,
        "historical_paid_cost_max_usd": HISTORICAL_PAID_COST_MAX_USD,
        "total_lineage_conservative_max_usd": TOTAL_LINEAGE_CONSERVATIVE_MAX_USD,
        "request_set_hash": cost_preflight["request_set_hash"],
        "request_hashes": [item.plan_item.request.request_hash for item in items],
        "recovery_kinds": recovery_kinds(items),
        "context_capability_hash": context_capability["capability_hash"],
        "context_admissibility": "PASS",
        "fresh_initial_result_exists": False,
        "owner_approval_required": True,
        "owner_approval_status": "NOT_GRANTED",
        "model_calls_authorized": False,
        "automatic_retries": 0,
        "partial_dispatch_fail_closed": True,
        "partial_dispatch_policy": "DURABLE_UNKNOWN_THEN_STOP_FAIL_CLOSED_NO_AUTOMATIC_RESEND",
        "model_calls_this_step": 0,
        "provider_reads_this_step": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "cost_usd_this_step": "0",
        "live_money": "PROHIBITED",
        "rebuttal_authorized": False,
        "judge_authorized": False,
        "final_decision_created": False,
        "b5_handoff_created": False,
        "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact, exclude_fields=("artifact_hash",))
    return artifact


def verify_full_recovery_readiness(
    payload: Mapping[str, Any],
    *,
    code_commit_sha: str,
    cost_preflight: Mapping[str, Any],
    context_capability: Mapping[str, Any],
    historical_ledger: Mapping[str, Any],
    historical_ledger_file_sha256: str,
    recovery_preflight: Mapping[str, Any],
) -> str:
    observed = _self_hash(payload)
    expected = build_full_recovery_readiness(
        code_commit_sha=code_commit_sha,
        cost_preflight=cost_preflight,
        context_capability=context_capability,
        historical_ledger=historical_ledger,
        historical_ledger_file_sha256=historical_ledger_file_sha256,
        recovery_preflight=recovery_preflight,
        historical_raw_response_dir_exists=False,
        historical_raw_response_file_count=0,
        fresh_initial_result_exists=False,
    )
    _need(dict(payload) == expected, "full recovery readiness drift")
    return observed


def build_full_recovery_owner_approval(
    *, code_commit_sha: str, readiness_hash: str, cost_preflight: Mapping[str, Any], owner_approval_id: str, owner_approval_at_utc: str
) -> dict[str, Any]:
    items = recovery_items(cost_preflight)
    _need(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha) is not None, "recovery approval code SHA invalid")
    _need(re.fullmatch(r"[0-9a-f]{64}", readiness_hash) is not None, "recovery readiness hash invalid")
    _need(bool(owner_approval_id.strip()) and bool(owner_approval_at_utc.strip()), "recovery owner approval identity missing")
    approval: dict[str, Any] = {
        "artifact_version": RECOVERY_OWNER_APPROVAL_VERSION,
        "owner_approval_granted": True,
        "owner_approval_id": owner_approval_id,
        "owner_approval_at_utc": owner_approval_at_utc,
        "approved_recovery_executor_code_commit_sha": code_commit_sha,
        "full_recovery_readiness_artifact_hash": readiness_hash,
        "recovery_preflight_hash": "acb859c8bfbe5ea4a6a34a316de509d027f9512bbd3697350f7532d4fcc9ec3c",
        "source_cost_preflight_hash": dispatch.EXPECTED_PREFLIGHT_HASH,
        "historical_failed_ledger_file_sha256": forensic.EXPECTED_LEDGER_FILE_SHA256,
        "historical_failed_ledger_hash": forensic.EXPECTED_LEDGER_HASH,
        "historical_owner_approval_hash": forensic.EXPECTED_OWNER_APPROVAL_HASH,
        "historical_request_1_hash": items[0].plan_item.request.request_hash,
        "historical_request_1_failure_classification": forensic.RECOVERY_CLASSIFICATION,
        "model": dispatch.EXPECTED_MODEL,
        "reasoning_effort": dispatch.EXPECTED_REASONING_EFFORT,
        "new_paid_call_count": 9,
        "new_paid_call_count_ceiling": 9,
        "max_output_tokens_per_call": dispatch.EXPECTED_MAX_OUTPUT_TOKENS,
        "approved_new_recovery_max_cost_usd": RECOVERY_MAX_COST_USD,
        "request_set_hash": cost_preflight["request_set_hash"],
        "request_hashes": [item.plan_item.request.request_hash for item in items],
        "recovery_kinds": recovery_kinds(items),
        "automatic_retries": 0,
    }
    approval["artifact_hash"] = canonical_sha256(approval, exclude_fields=("artifact_hash",))
    return approval


def verify_full_recovery_owner_approval(
    approval: Mapping[str, Any], *, code_commit_sha: str, readiness_hash: str, cost_preflight: Mapping[str, Any]
) -> str:
    observed = _self_hash(approval)
    expected = build_full_recovery_owner_approval(
        code_commit_sha=code_commit_sha,
        readiness_hash=readiness_hash,
        cost_preflight=cost_preflight,
        owner_approval_id=str(approval.get("owner_approval_id", "")),
        owner_approval_at_utc=str(approval.get("owner_approval_at_utc", "")),
    )
    _need(dict(approval) == expected, "full recovery owner approval drift")
    return observed


def _recovery_ledger(*, items: Sequence[Any], approval_hash: str) -> dict[str, Any]:
    ledger: dict[str, Any] = {
        "ledger_version": RECOVERY_LEDGER_VERSION,
        "historical_failed_ledger_sha256": forensic.EXPECTED_LEDGER_FILE_SHA256,
        "historical_failed_ledger_hash": forensic.EXPECTED_LEDGER_HASH,
        "historical_owner_approval_hash": forensic.EXPECTED_OWNER_APPROVAL_HASH,
        "historical_request_1_hash": items[0].plan_item.request.request_hash,
        "historical_request_1_failure_classification": forensic.RECOVERY_CLASSIFICATION,
        "recovery_owner_approval_hash": approval_hash,
        "entries": [
            {
                "recovery_dispatch_index": index,
                "original_frozen_request_hash": item.plan_item.request.request_hash,
                "candidate": item.plan_item.candidate_id,
                "council_role": item.plan_item.lane.value,
                "recovery_kind": REPLACEMENT_KIND if index == 1 else FIRST_DISPATCH_KIND,
                "state": dispatch.NOT_DISPATCHED,
                "automatic_retry_permitted": False,
            }
            for index, item in enumerate(items, start=1)
        ],
    }
    ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",))
    return ledger


def _store_recovery_ledger(path: Path, ledger: dict[str, Any], *, exclusive: bool) -> None:
    ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",))
    if exclusive:
        _write_exclusive(path, ledger)
    else:
        _replace_durable(path, ledger)


def _result(*, code_commit_sha: str, readiness_hash: str, approval_hash: str, recovery_ledger: Mapping[str, Any], cost_preflight: Mapping[str, Any], records: Sequence[Mapping[str, Any]], raw_response_hashes: Sequence[str], recovery_actual_cost: Decimal) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "artifact_version": RECOVERY_RESULT_VERSION,
        "status": RECOVERY_RESULT_STATUS,
        "code_commit_sha": code_commit_sha,
        "recovery_mode": RECOVERY_MODE,
        "recovery_owner_approval_hash": approval_hash,
        "recovery_readiness_hash": readiness_hash,
        "recovery_preflight_hash": "acb859c8bfbe5ea4a6a34a316de509d027f9512bbd3697350f7532d4fcc9ec3c",
        "source_cost_preflight_hash": dispatch.EXPECTED_PREFLIGHT_HASH,
        "historical_failed_ledger_file_sha256": forensic.EXPECTED_LEDGER_FILE_SHA256,
        "historical_failed_ledger_hash": forensic.EXPECTED_LEDGER_HASH,
        "recovery_ledger_hash": recovery_ledger["ledger_hash"],
        "request_set_hash": cost_preflight["request_set_hash"],
        "request_hashes": [record["request_hash"] for record in records],
        "recovery_kinds": recovery_kinds(records),
        "raw_response_hashes": list(raw_response_hashes),
        "processed_records": list(records),
        "historical_paid_cost_usd": HISTORICAL_PAID_COST_USD,
        "historical_paid_cost_max_usd": HISTORICAL_PAID_COST_MAX_USD,
        "recovery_actual_cost_usd": format(recovery_actual_cost, "f"),
        "recovery_approved_max_cost_usd": RECOVERY_MAX_COST_USD,
        "total_lineage_actual_cost_usd": "UNKNOWN",
        "total_lineage_conservative_max_usd": TOTAL_LINEAGE_CONSERVATIVE_MAX_USD,
        "model_calls_known_completed": 9,
        "automatic_retries": 0,
        "rebuttal_authorized": False,
        "judge_authorized": False,
        "final_decision_created": False,
        "b5_handoff_created": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact, exclude_fields=("artifact_hash",))
    return artifact


def verify_full_recovery_result(payload: Mapping[str, Any]) -> str:
    observed = _self_hash(payload)
    _need(payload.get("artifact_version") == RECOVERY_RESULT_VERSION and payload.get("status") == RECOVERY_RESULT_STATUS, "recovery result version/status drift")
    records = payload.get("processed_records")
    _need(isinstance(records, list) and len(records) == 9, "recovery result requires nine fresh records")
    _need(payload.get("model_calls_known_completed") == 9 and payload.get("automatic_retries") == 0, "recovery result call authority drift")
    _need(payload.get("historical_paid_cost_usd") == HISTORICAL_PAID_COST_USD and payload.get("total_lineage_actual_cost_usd") == "UNKNOWN", "historical paid-cost lineage drift")
    _need(all(isinstance(record, Mapping) and record.get("record_hash") == canonical_sha256(record, exclude_fields=("record_hash",)) for record in records), "recovery record hash drift")
    return observed


Transport = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def execute_paid_full_recovery(
    *,
    execute_paid_full_recovery: bool,
    branch: str,
    code_commit_sha: str,
    worktree_clean: bool,
    cost_preflight: Mapping[str, Any],
    recovery_readiness: Mapping[str, Any],
    recovery_preflight: Mapping[str, Any],
    historical_ledger: Mapping[str, Any],
    historical_ledger_file_sha256: str,
    historical_raw_response_dir_exists: bool,
    historical_raw_response_file_count: int,
    fresh_initial_result_exists: bool,
    approval: Mapping[str, Any] | None,
    context_capability: Mapping[str, Any],
    recovery_ledger_path: Path,
    raw_response_dir: Path,
    result_path: Path,
    transport_factory: Callable[[], Transport],
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Dispatch exactly nine fresh recovery requests only after every gate passes."""
    _need(execute_paid_full_recovery is True, "--execute-paid-full-recovery is required")
    _need(approval is not None, "exact full recovery owner approval artifact is required")
    _need(branch == dispatch.EXPECTED_BRANCH and worktree_clean is True, "full recovery checkout gate failed")
    _need(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha) is not None, "full recovery code SHA invalid")
    _historical_inputs(
        historical_ledger=historical_ledger,
        historical_ledger_file_sha256=historical_ledger_file_sha256,
        recovery_preflight=recovery_preflight,
        cost_preflight=cost_preflight,
        historical_raw_response_dir_exists=historical_raw_response_dir_exists,
        historical_raw_response_file_count=historical_raw_response_file_count,
        fresh_initial_result_exists=fresh_initial_result_exists or result_path.exists(),
    )
    readiness_hash = verify_full_recovery_readiness(
        recovery_readiness,
        code_commit_sha=code_commit_sha,
        cost_preflight=cost_preflight,
        context_capability=context_capability,
        historical_ledger=historical_ledger,
        historical_ledger_file_sha256=historical_ledger_file_sha256,
        recovery_preflight=recovery_preflight,
    )
    approval_hash = verify_full_recovery_owner_approval(approval, code_commit_sha=code_commit_sha, readiness_hash=readiness_hash, cost_preflight=cost_preflight)
    items = recovery_items(cost_preflight)
    try:
        verify_context_admissibility(items, context_capability)
    except PostResearchInitialExecutionError as exc:
        raise FullInitialRecoveryError(str(exc)) from exc
    _need(not recovery_ledger_path.exists() and not raw_response_dir.exists(), "existing recovery dispatch evidence requires a new owner decision")
    pricing = load_initial_runtime_pricing()
    _need(cost_preflight.get("pricing_hash") == pricing.get("pricing_hash"), "frozen pricing hash drift")
    ledger = _recovery_ledger(items=items, approval_hash=approval_hash)
    _store_recovery_ledger(recovery_ledger_path, ledger, exclusive=True)
    transport = transport_factory()
    cumulative = Decimal("0")
    records: list[Mapping[str, Any]] = []
    raw_hashes: list[str] = []
    for offset, item in enumerate(items):
        remaining = sum((Decimal(str(next_item.row["estimated_max_cost_usd"])) for next_item in items[offset:]), Decimal("0"))
        _need(cumulative + remaining <= Decimal(RECOVERY_MAX_COST_USD), "recovery remaining worst-case cost exceeds authority")
        entry = ledger["entries"][offset]
        _need(entry["state"] == dispatch.NOT_DISPATCHED, "recovery ledger is not safe for dispatch")
        entry["state"] = dispatch.DISPATCH_STARTED_UNKNOWN
        entry["dispatch_started_at_utc"] = now().astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        _store_recovery_ledger(recovery_ledger_path, ledger, exclusive=False)
        started = perf_counter_ns()
        try:
            raw = transport(item.plan_item.request.request_payload)
        except Exception as exc:
            entry["stop_reason"] = f"AMBIGUOUS_PROVIDER_OUTCOME:{type(exc).__name__}"
            _store_recovery_ledger(recovery_ledger_path, ledger, exclusive=False)
            raise FullInitialRecoveryError("ambiguous full recovery provider outcome; dispatch remains unknown") from exc
        _need(isinstance(raw, Mapping), "provider response must be object")
        capture = build_raw_response_capture(
            request_hash=item.plan_item.request.request_hash,
            provider_response=raw,
            dispatch_started_at_utc=entry["dispatch_started_at_utc"],
            captured_at_utc=_utc_now(),
        )
        raw_path = raw_response_dir / f"{item.index:02d}-{item.plan_item.request.request_hash}.json"
        _write_exclusive(raw_path, capture)
        capture_hash = verify_raw_response_capture(capture, request_hash=item.plan_item.request.request_hash)
        entry["raw_response_hash"] = capture_hash
        entry["raw_response_path"] = str(raw_path)
        entry["response_captured_at_utc"] = capture["captured_at_utc"]
        _store_recovery_ledger(recovery_ledger_path, ledger, exclusive=False)
        latency = max(0, (perf_counter_ns() - started) // 1_000_000)
        try:
            record = process_reopen_initial_provider_response(item.plan_item, raw_response=raw, latency_ms=latency, frozen_at=now(), pricing=pricing)
            call_cost = actual_cost_usd(raw, model=dispatch.EXPECTED_MODEL, pricing=pricing)
        except Exception as exc:
            entry["stop_reason"] = f"RESPONSE_CAPTURED_BUT_NOT_ACCEPTED:{type(exc).__name__}"
            _store_recovery_ledger(recovery_ledger_path, ledger, exclusive=False)
            raise FullInitialRecoveryError("captured recovery provider response failed validation; stop fail-closed") from exc
        cumulative += call_cost
        _need(cumulative <= Decimal(RECOVERY_MAX_COST_USD), "recovery actual cost exceeds approved ceiling")
        entry["state"] = dispatch.COMPLETED
        entry["processed_record_hash"] = record["record_hash"]
        entry["actual_cost_usd"] = format(call_cost, "f")
        _store_recovery_ledger(recovery_ledger_path, ledger, exclusive=False)
        records.append(record)
        raw_hashes.append(capture_hash)
    artifact = _result(
        code_commit_sha=code_commit_sha,
        readiness_hash=readiness_hash,
        approval_hash=approval_hash,
        recovery_ledger=ledger,
        cost_preflight=cost_preflight,
        records=records,
        raw_response_hashes=raw_hashes,
        recovery_actual_cost=cumulative,
    )
    verify_full_recovery_result(artifact)
    _write_exclusive(result_path, artifact)
    return artifact
