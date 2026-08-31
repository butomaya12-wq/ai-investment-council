"""Fail-closed paid executor for the frozen post-research Initial request set.

No code in this module constructs a transport until explicit, hash-bound owner
authority and every local gate have passed.  Tests inject a transport callable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import re
from time import perf_counter_ns
from typing import Any, Callable, Mapping, Sequence

from aic.domain.canonical import canonical_sha256

from . import post_research_reopen_initial_production_dispatch_v01 as dispatch
from .initial_runtime_cost_v02 import actual_cost_usd, load_initial_runtime_pricing
from .initial_schema_repair_v05 import INITIAL_SCHEMA_VERSION
from .models import CouncilInputBundle, CouncilLane
from .request import CouncilRequestEnvelope, CouncilRequestStage
from .reopen_initial_runtime import ReopenInitialRuntimePlanItem, process_reopen_initial_provider_response


READINESS_VERSION = "B4_POST_RESEARCH_REOPEN_INITIAL_PRODUCTION_DISPATCH_ZERO_CALL_PREFLIGHT_v0_2"
READINESS_STATUS = "B4_POST_RESEARCH_REOPEN_INITIAL_PRODUCTION_DISPATCH_ZERO_CALL_PREFLIGHT_V02_PASS"
OWNER_APPROVAL_VERSION = "B4_POST_RESEARCH_REOPEN_INITIAL_OWNER_APPROVAL_v0_2"
LEDGER_VERSION = "B4_POST_RESEARCH_REOPEN_INITIAL_PAID_DISPATCH_LEDGER_v0_1"
RESULT_VERSION = "B4_POST_RESEARCH_REOPEN_INITIAL_COUNCIL_FREEZE_v0_1"
RESULT_STATUS = "B4_POST_RESEARCH_REOPEN_INITIAL_COUNCIL_FROZEN"
CONTEXT_CAPABILITY_PATH = Path("config/event/openai_gpt_5_6_terra_context_capability_2026_08_31.json")
NEXT_GATE = "B4_POST_RESEARCH_REOPEN_INITIAL_EXPLICIT_OWNER_APPROVAL_THEN_ONE_PAID_INITIAL_EXECUTION"


class PostResearchInitialExecutionError(RuntimeError):
    pass


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise PostResearchInitialExecutionError(message)


def _self_hash(payload: Mapping[str, Any], field: str = "artifact_hash") -> str:
    observed = payload.get(field)
    _need(isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None, f"{field} missing")
    _need(observed == canonical_sha256(payload, exclude_fields=(field,)), f"{field} self-hash mismatch")
    return observed


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PostResearchInitialExecutionError(f"unable to read {label}") from exc
    _need(isinstance(value, dict), f"{label} root must be object")
    return value


def _write_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    _need(not path.exists(), f"exclusive output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _replace_durable(path: Path, payload: Mapping[str, Any]) -> None:
    _need(path.is_file(), f"durable ledger missing: {path}")
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_context_capability(path: Path = CONTEXT_CAPABILITY_PATH) -> dict[str, Any]:
    payload = _read_object(path, "frozen model context capability")
    observed = payload.get("capability_hash")
    _need(isinstance(observed, str) and re.fullmatch(r"[0-9a-f]{64}", observed) is not None, "context capability hash missing")
    _need(observed == canonical_sha256(payload, exclude_fields=("capability_hash",)), "context capability hash mismatch")
    _need(payload.get("artifact_version") == "OPENAI_GPT_5_6_TERRA_CONTEXT_CAPABILITY_2026_08_31_v0_1", "context capability version drift")
    _need(payload.get("model") == dispatch.EXPECTED_MODEL, "context capability model drift")
    _need(payload.get("context_window_tokens") == 1_050_000, "context window drift")
    _need(payload.get("max_output_tokens") == 128_000, "context output maximum drift")
    _need(payload.get("source_kind") == "OPENAI_OFFICIAL_MODEL_DOCUMENTATION", "context capability provenance drift")
    _need(payload.get("source_url") == "https://developers.openai.com/api/docs/models/gpt-5.6-terra", "context capability source drift")
    return payload


@dataclass(frozen=True)
class FrozenInitialItem:
    index: int
    row: Mapping[str, Any]
    plan_item: ReopenInitialRuntimePlanItem


def frozen_initial_items(cost_preflight: Mapping[str, Any]) -> tuple[FrozenInitialItem, ...]:
    request_hashes = dispatch.verify_cost_preflight_for_dispatch(cost_preflight)
    inputs = cost_preflight.get("model_facing_inputs_by_candidate")
    rows = cost_preflight.get("initial_requests")
    _need(isinstance(inputs, Mapping) and isinstance(rows, list), "frozen request surface missing")
    result: list[FrozenInitialItem] = []
    for index, (row, request_hash) in enumerate(zip(rows, request_hashes, strict=True), start=1):
        _need(isinstance(row, Mapping), f"frozen request {index} malformed")
        payload = row.get("request_payload")
        candidate = row.get("candidate")
        role = row.get("council_role")
        stage_text = row.get("stage")
        _need(isinstance(payload, Mapping) and isinstance(candidate, str) and isinstance(role, str) and isinstance(stage_text, str), f"frozen request {index} identity missing")
        _need(row.get("request_hash") == request_hash, f"frozen request {index} hash drift")
        _need(row.get("request_payload_canonical_hash") == canonical_sha256(payload), f"frozen request {index} payload hash drift")
        _need(row.get("model") == dispatch.EXPECTED_MODEL and payload.get("model") == dispatch.EXPECTED_MODEL, f"frozen request {index} model drift")
        _need(row.get("reasoning_effort") == dispatch.EXPECTED_REASONING_EFFORT and payload.get("reasoning") == {"effort": "low"}, f"frozen request {index} effort drift")
        _need(row.get("maximum_output_tokens") == dispatch.EXPECTED_MAX_OUTPUT_TOKENS and payload.get("max_output_tokens") == dispatch.EXPECTED_MAX_OUTPUT_TOKENS, f"frozen request {index} output cap drift")
        model_input = inputs.get(candidate)
        _need(isinstance(model_input, Mapping) and model_input.get("model_input_hash") == row.get("model_facing_input_hash"), f"frozen request {index} model input drift")
        bundle_raw = model_input.get("council_input_bundle")
        _need(isinstance(bundle_raw, Mapping), f"frozen request {index} bundle missing")
        try:
            input_envelope = json.loads(payload["input"])
            _need(isinstance(input_envelope, Mapping), f"frozen request {index} input envelope invalid")
            request = CouncilRequestEnvelope(
                request_version="B4_RESPONSES_REQUEST_v0_1", prompt_contract_version=row["prompt_contract_version"],
                stage=CouncilRequestStage(stage_text), prompt_version=row["prompt_version"], prompt_hash=row["prompt_hash"],
                schema_version=INITIAL_SCHEMA_VERSION, input_hash=canonical_sha256(input_envelope),
                model_candidate_key="L2", request_payload=payload, request_hash=request_hash,
            )
            plan_item = ReopenInitialRuntimePlanItem(index, candidate, CouncilLane(role), CouncilRequestStage(stage_text), CouncilInputBundle.model_validate(bundle_raw), model_input, request, int(row["estimated_input_tokens_upper_bound"]), row)
        except Exception as exc:
            raise PostResearchInitialExecutionError(f"frozen request {index} cannot be reconstructed") from exc
        result.append(FrozenInitialItem(index, row, plan_item))
    _need(len(result) == dispatch.EXPECTED_CALL_COUNT, "frozen request count drift")
    return tuple(result)


def verify_context_admissibility(items: Sequence[FrozenInitialItem], capability: Mapping[str, Any]) -> None:
    context = capability.get("context_window_tokens")
    maximum = capability.get("max_output_tokens")
    _need(type(context) is int and type(maximum) is int, "context capability numeric fields invalid")
    for item in items:
        input_upper = item.row.get("estimated_input_tokens_upper_bound")
        _need(type(input_upper) is int and input_upper > 0, f"request {item.index} input token bound invalid")
        _need(dispatch.EXPECTED_MAX_OUTPUT_TOKENS <= maximum, f"request {item.index} exceeds model max output")
        _need(input_upper + dispatch.EXPECTED_MAX_OUTPUT_TOKENS <= context, f"request {item.index} exceeds context window")


def build_owner_approval(*, code_commit_sha: str, readiness_hash: str, cost_preflight: Mapping[str, Any], owner_approval_id: str, owner_approval_at_utc: str) -> dict[str, Any]:
    hashes = list(dispatch.verify_cost_preflight_for_dispatch(cost_preflight))
    _need(re.fullmatch(r"[0-9a-f]{40}", code_commit_sha) is not None, "approval code SHA invalid")
    _need(re.fullmatch(r"[0-9a-f]{64}", readiness_hash) is not None, "approval readiness hash invalid")
    approval: dict[str, Any] = {
        "artifact_version": OWNER_APPROVAL_VERSION, "owner_approval_granted": True,
        "owner_approval_id": owner_approval_id, "owner_approval_at_utc": owner_approval_at_utc,
        "cost_preflight_artifact_hash": dispatch.EXPECTED_PREFLIGHT_HASH,
        "dispatch_readiness_artifact_hash": readiness_hash, "approved_dispatch_code_commit_sha": code_commit_sha,
        "model": dispatch.EXPECTED_MODEL, "reasoning_effort": dispatch.EXPECTED_REASONING_EFFORT,
        "planned_call_count": dispatch.EXPECTED_CALL_COUNT, "call_count_ceiling": dispatch.EXPECTED_CALL_COUNT,
        "max_output_tokens_per_call": dispatch.EXPECTED_MAX_OUTPUT_TOKENS, "approved_max_estimated_cost_usd": dispatch.EXPECTED_MAX_COST_USD,
        "request_set_hash": cost_preflight["request_set_hash"], "request_hashes": hashes, "automatic_retries": 0,
    }
    approval["artifact_hash"] = canonical_sha256(approval, exclude_fields=("artifact_hash",))
    return approval


def verify_owner_approval_v02(approval: Mapping[str, Any], *, code_commit_sha: str, readiness_hash: str, cost_preflight: Mapping[str, Any]) -> str:
    _self_hash(approval)
    _need(approval.get("artifact_version") == OWNER_APPROVAL_VERSION, "owner approval version drift")
    _need(approval.get("owner_approval_granted") is True, "owner approval is not explicitly granted")
    _need(approval.get("dispatch_readiness_artifact_hash") == readiness_hash, "owner approval readiness hash drift")
    legacy = dict(approval)
    legacy["artifact_version"] = dispatch.OWNER_APPROVAL_VERSION
    legacy.pop("dispatch_readiness_artifact_hash", None)
    legacy["artifact_hash"] = canonical_sha256(legacy, exclude_fields=("artifact_hash",))
    try:
        return dispatch.verify_owner_approval(legacy, cost_preflight=cost_preflight, dispatch_code_commit_sha=code_commit_sha)
    except dispatch.PostResearchReopenInitialProductionDispatchError as exc:
        raise PostResearchInitialExecutionError(str(exc)) from exc


def build_readiness(*, code_commit_sha: str, cost_preflight: Mapping[str, Any], context_capability: Mapping[str, Any]) -> dict[str, Any]:
    items = frozen_initial_items(cost_preflight)
    verify_context_admissibility(items, context_capability)
    artifact: dict[str, Any] = {
        "artifact_version": READINESS_VERSION, "status": READINESS_STATUS, "code_commit_sha": code_commit_sha,
        "source_cost_preflight_hash": dispatch.EXPECTED_PREFLIGHT_HASH, "request_set_hash": cost_preflight["request_set_hash"],
        "request_hashes": [item.plan_item.request.request_hash for item in items], "model": dispatch.EXPECTED_MODEL,
        "reasoning_effort": dispatch.EXPECTED_REASONING_EFFORT, "call_count": 9, "call_count_ceiling": 9,
        "max_output_tokens_per_call": 4096, "approved_max_estimated_cost_usd_required": dispatch.EXPECTED_MAX_COST_USD,
        "context_capability_hash": context_capability["capability_hash"], "context_admissibility": "PASS",
        "paid_executor_exists": True, "explicit_paid_flag_required": True, "owner_approval_required": True,
        "owner_approval_status": "NOT_GRANTED", "owner_approval_binds_exact_readiness_hash": True,
        "owner_approval_binds_exact_code_sha": True, "automatic_retries": 0,
        "partial_dispatch_policy": "DURABLE_UNKNOWN_THEN_STOP_FAIL_CLOSED_NO_RESEND",
        "fresh_initial_result_exists": False, "provider_reads_authorized": False, "model_calls_authorized": False,
        "model_calls_this_step": 0, "provider_reads_this_step": 0, "broker_writes": 0, "alpaca_orders": 0,
        "cost_usd_this_step": "0", "live_money": "PROHIBITED", "rebuttal_authorized": False,
        "judge_authorized": False, "b5_handoff_created": False, "next_gate": NEXT_GATE,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact, exclude_fields=("artifact_hash",))
    return artifact


def verify_readiness(payload: Mapping[str, Any], *, code_commit_sha: str, cost_preflight: Mapping[str, Any], context_capability: Mapping[str, Any]) -> str:
    observed = _self_hash(payload)
    expected = build_readiness(code_commit_sha=code_commit_sha, cost_preflight=cost_preflight, context_capability=context_capability)
    _need(dict(payload) == expected, "readiness artifact drift")
    return observed


def _ledger(items: Sequence[FrozenInitialItem], approval_hash: str) -> dict[str, Any]:
    ledger: dict[str, Any] = {"ledger_version": LEDGER_VERSION, "owner_approval_hash": approval_hash, "entries": [
        {"dispatch_index": item.index, "request_hash": item.plan_item.request.request_hash, "state": dispatch.NOT_DISPATCHED} for item in items
    ]}
    ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",))
    return ledger


def _store_ledger(path: Path, ledger: dict[str, Any], *, exclusive: bool) -> None:
    ledger["ledger_hash"] = canonical_sha256(ledger, exclude_fields=("ledger_hash",))
    if exclusive:
        _write_exclusive(path, ledger)
    else:
        _replace_durable(path, ledger)


def _result(*, code_commit_sha: str, approval_hash: str, readiness_hash: str, cost_preflight: Mapping[str, Any], ledger: Mapping[str, Any], records: Sequence[Mapping[str, Any]], raw_response_hashes: Sequence[str], total_cost: Decimal) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "artifact_version": RESULT_VERSION, "status": RESULT_STATUS, "code_commit_sha": code_commit_sha,
        "owner_approval_hash": approval_hash, "dispatch_readiness_artifact_hash": readiness_hash,
        "source_cost_preflight_hash": dispatch.EXPECTED_PREFLIGHT_HASH, "request_set_hash": cost_preflight["request_set_hash"],
        "request_hashes": [record["request_hash"] for record in records], "ledger_hash": ledger["ledger_hash"],
        "raw_response_hashes": list(raw_response_hashes), "processed_records": list(records),
        "actual_cost_usd": format(total_cost, "f"), "model_calls_known_completed": 9,
        "automatic_retries": 0, "rebuttal_authorized": False, "judge_authorized": False,
        "final_decision_created": False, "b5_handoff_created": False, "broker_writes": 0,
        "alpaca_orders": 0, "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact, exclude_fields=("artifact_hash",))
    return artifact


def verify_result(payload: Mapping[str, Any]) -> str:
    observed = _self_hash(payload)
    _need(payload.get("artifact_version") == RESULT_VERSION and payload.get("status") == RESULT_STATUS, "result version/status drift")
    rows = payload.get("processed_records")
    _need(isinstance(rows, list) and len(rows) == 9, "result requires exact nine processed records")
    _need(payload.get("model_calls_known_completed") == 9 and payload.get("automatic_retries") == 0, "result dispatch authority drift")
    _need(all(isinstance(row, Mapping) and row.get("record_hash") == canonical_sha256(row, exclude_fields=("record_hash",)) for row in rows), "result record hash drift")
    return observed


Transport = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def execute_paid_initial(*, execute_paid_initial: bool, branch: str, code_commit_sha: str, worktree_clean: bool, cost_preflight: Mapping[str, Any], readiness: Mapping[str, Any], approval: Mapping[str, Any] | None, context_capability: Mapping[str, Any], ledger_path: Path, raw_response_dir: Path, result_path: Path, transport_factory: Callable[[], Transport], now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> dict[str, Any]:
    """Run exactly once only after all non-I/O authority gates pass."""
    _need(execute_paid_initial is True, "--execute-paid-initial is required")
    _need(approval is not None, "exact owner approval artifact is required")
    _need(branch == dispatch.EXPECTED_BRANCH and worktree_clean, "paid execution checkout gate failed")
    readiness_hash = verify_readiness(readiness, code_commit_sha=code_commit_sha, cost_preflight=cost_preflight, context_capability=context_capability)
    approval_hash = verify_owner_approval_v02(approval, code_commit_sha=code_commit_sha, readiness_hash=readiness_hash, cost_preflight=cost_preflight)
    items = frozen_initial_items(cost_preflight)
    verify_context_admissibility(items, context_capability)
    _need(not result_path.exists() and not ledger_path.exists(), "fresh result or prior dispatch ledger exists; recovery decision required")
    ledger = _ledger(items, approval_hash)
    _store_ledger(ledger_path, ledger, exclusive=True)
    # This is deliberately after every authority/input/ledger gate.
    transport = transport_factory()
    pricing = load_initial_runtime_pricing()
    _need(cost_preflight.get("pricing_hash") == pricing.get("pricing_hash"), "frozen pricing hash drift")
    cumulative = Decimal("0")
    records: list[Mapping[str, Any]] = []
    raw_hashes: list[str] = []
    for offset, item in enumerate(items):
        remaining = sum(Decimal(str(next_item.row["estimated_max_cost_usd"])) for next_item in items[offset:])
        _need(cumulative + remaining <= Decimal(dispatch.EXPECTED_MAX_COST_USD), "remaining worst-case cost exceeds authority")
        entry = ledger["entries"][offset]
        _need(entry["state"] == dispatch.NOT_DISPATCHED, "ledger is not safe for dispatch")
        entry["state"] = dispatch.DISPATCH_STARTED_UNKNOWN
        entry["dispatch_started_at_utc"] = now().astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        _store_ledger(ledger_path, ledger, exclusive=False)
        started = perf_counter_ns()
        try:
            raw = transport(item.plan_item.request.request_payload)
        except Exception as exc:
            entry["stop_reason"] = f"AMBIGUOUS_PROVIDER_OUTCOME:{type(exc).__name__}"
            _store_ledger(ledger_path, ledger, exclusive=False)
            raise PostResearchInitialExecutionError("ambiguous provider outcome; dispatch remains unknown") from exc
        _need(isinstance(raw, Mapping), "provider response must be object")
        latency = max(0, (perf_counter_ns() - started) // 1_000_000)
        try:
            record = process_reopen_initial_provider_response(item.plan_item, raw_response=raw, latency_ms=latency, frozen_at=now(), pricing=pricing)
            call_cost = actual_cost_usd(raw, model=dispatch.EXPECTED_MODEL, pricing=pricing)
        except Exception as exc:
            entry["stop_reason"] = f"RESPONSE_CAPTURED_BUT_NOT_ACCEPTED:{type(exc).__name__}"
            _store_ledger(ledger_path, ledger, exclusive=False)
            raise PostResearchInitialExecutionError("captured provider response failed validation; stop fail-closed") from exc
        raw_payload = {"request_hash": item.plan_item.request.request_hash, "provider_response_id": raw.get("id"), "dispatch_started_at_utc": entry["dispatch_started_at_utc"], "dispatch_finished_at_utc": _utc_now(), "raw_response": dict(raw), "actual_cost_usd": format(call_cost, "f")}
        raw_payload["raw_response_hash"] = canonical_sha256(raw_payload, exclude_fields=("raw_response_hash",))
        _write_exclusive(raw_response_dir / f"{item.index:02d}-{item.plan_item.request.request_hash}.json", raw_payload)
        cumulative += call_cost
        _need(cumulative <= Decimal(dispatch.EXPECTED_MAX_COST_USD), "actual cost exceeds approved ceiling")
        entry["state"] = dispatch.COMPLETED
        entry["raw_response_hash"] = raw_payload["raw_response_hash"]
        entry["processed_record_hash"] = record["record_hash"]
        _store_ledger(ledger_path, ledger, exclusive=False)
        records.append(record)
        raw_hashes.append(raw_payload["raw_response_hash"])
    artifact = _result(code_commit_sha=code_commit_sha, approval_hash=approval_hash, readiness_hash=readiness_hash, cost_preflight=cost_preflight, ledger=ledger, records=records, raw_response_hashes=raw_hashes, total_cost=cumulative)
    verify_result(artifact)
    _write_exclusive(result_path, artifact)
    return artifact
