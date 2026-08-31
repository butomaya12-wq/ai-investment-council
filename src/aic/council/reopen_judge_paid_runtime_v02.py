from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import json
from time import perf_counter_ns
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256

from .initial_runtime_cost_v02 import actual_cost_usd
from .judge_production import _usage_counts
from .proposal import FrozenJudgeDecisionProposal, JudgeDecisionProposalDraft, JudgeOutcome
from .request import CouncilRequestEnvelope, parse_council_responses_payload
from . import reopen_judge_production_v02 as gate


AUTH_VERSION = "B4_REOPEN_JUDGE_PRODUCTION_PAID_AUTHORIZATION_v0_2"
EVENT_VERSION = "B4_REOPEN_JUDGE_PRODUCTION_JOURNAL_EVENT_v0_2"
RECEIPT_VERSION = "B4_REOPEN_JUDGE_PRODUCTION_PAID_CALL_RECEIPT_v0_2"
RESULT_VERSION = "B4_REOPEN_JUDGE_PRODUCTION_RESULT_v0_2"
BLOCKED_VERSION = "B4_REOPEN_JUDGE_PRODUCTION_BLOCKED_v0_2"
SUCCESS_STATUS = "B4_REOPEN_JUDGE_PROPOSAL_FROZEN"
NEXT_GATE = "B4_REOPEN_JUDGE_PROPOSAL_POSTPROCESS_ZERO_CALL"
CONSUMPTION_RULE = "CONSUMED_ON_FIRST_DURABLE_JUDGE_PROVIDER_DISPATCH_ATTEMPT"


class ReopenJudgePaidRuntimeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class JudgeCallRun:
    response_received: bool
    response_id: str | None
    effective_model: str | None
    latency_ms: int
    input_tokens: int | None
    cached_tokens: int | None
    cache_write_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    actual_cost_usd: Decimal | None
    cost_receipt_status: str
    output_hash: str | None
    structured_output: Mapping[str, Any] | None
    structured_output_hash: str | None
    judge_proposal_hash: str | None
    validation_status: str
    validation_error: str | None
    model_calls: int


def _require(value: bool, message: str) -> None:
    if not value:
        raise ReopenJudgePaidRuntimeError(message)


def decimal_value(value: object, *, field: str) -> Decimal:
    _require(isinstance(value, str), f"{field} must be decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ReopenJudgePaidRuntimeError(f"{field} invalid") from exc
    _require(result.is_finite() and result >= 0, f"{field} invalid")
    return result


def decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def build_paid_authorization(
    *,
    run_id: str,
    created_at_utc: str,
    code_commit_sha: str,
    git_worktree_clean: bool,
    owner_approval_id: str,
    owner_approval_at_utc: str,
    selection_hash: str,
    entry_hash: str,
    request_preflight_hash: str,
    request_manifest_hash: str,
    request_hash: str,
    cost_preflight_hash: str,
    runner_dry_hash: str,
    approved_cost_ceiling_usd: Decimal,
    receipt_journal_path: str,
) -> dict[str, Any]:
    _require(git_worktree_clean is True, "Judge V02 paid authorization requires clean worktree")
    artifact = {
        "artifact_version": AUTH_VERSION,
        "status": "AUTHORIZED_FOR_ONE_B4_REOPEN_JUDGE_V02_RUN",
        "run_id": run_id,
        "created_at_utc": created_at_utc,
        "code_commit_sha": code_commit_sha,
        "git_worktree_clean": True,
        "owner_approval_id": owner_approval_id,
        "owner_approval_at_utc": owner_approval_at_utc,
        "rebuttal_council_freeze_artifact_hash": gate.EXPECTED_REBUTTAL_FREEZE_HASH,
        "judge_selected_model_authority_hash": selection_hash,
        "judge_entry_preflight_artifact_hash": entry_hash,
        "request_preflight_artifact_hash": request_preflight_hash,
        "request_manifest_hash": request_manifest_hash,
        "request_hash": request_hash,
        "cost_preflight_artifact_hash": cost_preflight_hash,
        "runner_dry_artifact_hash": runner_dry_hash,
        "approved_cost_ceiling_usd": decimal_text(approved_cost_ceiling_usd),
        "planned_paid_calls_max": 1,
        "model": "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "max_output_tokens_per_call": gate.EXPECTED_MAX_OUTPUT_TOKENS,
        "allowed_outcomes": [x.value for x in gate.EXPECTED_ALLOWED],
        "research_reopen_required_candidates": list(gate.EXPECTED_REOPEN),
        "invest_eligible_candidates": list(gate.EXPECTED_INVEST_ELIGIBLE),
        "invest_blocked_candidates": list(gate.EXPECTED_INVEST_BLOCKED),
        "source_successful_credential_probe_v02_result_artifact_hash": gate.EXPECTED_CREDENTIAL_PROBE_RESULT_HASH,
        "replacement_credential_fingerprint_sha256": gate.EXPECTED_CREDENTIAL_SHA256,
        "replacement_credential_secret_persisted": False,
        "receipt_journal_path": receipt_journal_path,
        "authorization_consumption_rule": CONSUMPTION_RULE,
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "rerun_authorized": False,
        "judge_execution_authority": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "network_manifest": {
            "openai_responses_api": True,
            "general_web_search": False,
            "hosted_tools": False,
            "remote_mcp": False,
            "broker_api": False,
        },
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def verify_paid_authorization(payload: Mapping[str, Any]) -> str:
    observed = payload.get("artifact_hash")
    _require(
        isinstance(observed, str)
        and observed == canonical_sha256(payload, exclude_fields=("artifact_hash",)),
        "Judge V02 paid authorization self-hash mismatch",
    )
    exact = {
        "artifact_version": AUTH_VERSION,
        "status": "AUTHORIZED_FOR_ONE_B4_REOPEN_JUDGE_V02_RUN",
        "rebuttal_council_freeze_artifact_hash": gate.EXPECTED_REBUTTAL_FREEZE_HASH,
        "planned_paid_calls_max": 1,
        "model": "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "max_output_tokens_per_call": gate.EXPECTED_MAX_OUTPUT_TOKENS,
        "allowed_outcomes": [x.value for x in gate.EXPECTED_ALLOWED],
        "research_reopen_required_candidates": list(gate.EXPECTED_REOPEN),
        "invest_eligible_candidates": list(gate.EXPECTED_INVEST_ELIGIBLE),
        "invest_blocked_candidates": list(gate.EXPECTED_INVEST_BLOCKED),
        "source_successful_credential_probe_v02_result_artifact_hash": gate.EXPECTED_CREDENTIAL_PROBE_RESULT_HASH,
        "replacement_credential_fingerprint_sha256": gate.EXPECTED_CREDENTIAL_SHA256,
        "replacement_credential_secret_persisted": False,
        "authorization_consumption_rule": CONSUMPTION_RULE,
        "automatic_repair_calls_authorized": 0,
        "automatic_retries": 0,
        "rerun_authorized": False,
        "judge_execution_authority": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    for key, expected in exact.items():
        _require(payload.get(key) == expected, f"Judge V02 paid authorization drift: {key}")
    _require(decimal_value(payload.get("approved_cost_ceiling_usd"), field="approved ceiling") > 0, "Judge V02 approved ceiling must be positive")
    return observed


def build_attempt_event(
    *,
    run_id: str,
    started_at_utc: str,
    authorization_hash: str,
    request_hash: str,
    request_manifest_hash: str,
) -> dict[str, Any]:
    event = {
        "event_version": EVENT_VERSION,
        "event_type": "JUDGE_PROVIDER_DISPATCH_ATTEMPT",
        "run_id": run_id,
        "dispatch_index": 1,
        "dispatch_started_at_utc": started_at_utc,
        "stage": "JUDGE",
        "requested_model": "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "max_output_tokens": gate.EXPECTED_MAX_OUTPUT_TOKENS,
        "request_hash": request_hash,
        "request_manifest_hash": request_manifest_hash,
        "paid_authorization_artifact_hash": authorization_hash,
        "authorization_consumed_by_this_attempt": True,
        "replacement_credential_fingerprint_sha256": gate.EXPECTED_CREDENTIAL_SHA256,
        "automatic_retry": False,
        "automatic_repair_attempted": False,
        "judge_execution_authority": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    event["event_hash"] = canonical_sha256(event)
    return event


def execute_once(
    *,
    request: CouncilRequestEnvelope,
    context: gate.ReopenJudgeContext,
    api_key: str,
    transport: Any,
    pricing: Mapping[str, Any],
) -> JudgeCallRun:
    started = perf_counter_ns()
    raw: Mapping[str, Any] | None = None
    response_id: str | None = None
    effective_model: str | None = None
    output_hash: str | None = None
    structured_output: Mapping[str, Any] | None = None
    structured_output_hash: str | None = None
    judge_proposal_hash: str | None = None
    usage: tuple[int, int, int, int, int] | None = None
    cost: Decimal | None = None
    cost_status = "INCOMPLETE"
    validation_error: str | None = None
    try:
        raw_value = transport.post(payload=request.request_payload, api_key=api_key)
        if not isinstance(raw_value, Mapping):
            raise ReopenJudgePaidRuntimeError("Responses payload must be an object")
        raw = raw_value
        latency_ms = max(0, (perf_counter_ns() - started) // 1_000_000)
        call, proposal = parse_council_responses_payload(raw, request=request, latency_ms=latency_ms)
        response_id = call.response_id
        effective_model = call.effective_model
        output_hash = call.output_hash
        if not isinstance(proposal, JudgeDecisionProposalDraft):
            raise ReopenJudgePaidRuntimeError("Judge V02 produced wrong DTO type")
        structured_output = proposal.model_dump(mode="json", exclude_none=False, warnings=False)
        structured_output_hash = canonical_sha256(structured_output)
        gate.validate_event_proposal(proposal, context=context)
        frozen = FrozenJudgeDecisionProposal.from_draft(proposal)
        judge_proposal_hash = frozen.judge_proposal_hash
        usage = _usage_counts(raw)
        model = request.request_payload.get("model")
        _require(isinstance(model, str) and model == "gpt-5.6-terra", "Judge V02 runtime model drift")
        cost = actual_cost_usd(raw, model=model, pricing=pricing)
        cost_status = "COMPLETE"
    except Exception as exc:
        latency_ms = max(0, (perf_counter_ns() - started) // 1_000_000)
        validation_error = f"{type(exc).__name__}: {exc}"
        if raw is not None and cost_status != "COMPLETE":
            try:
                usage = _usage_counts(raw)
                model = request.request_payload.get("model")
                _require(isinstance(model, str) and bool(model), "Judge V02 request model missing")
                cost = actual_cost_usd(raw, model=model, pricing=pricing)
                cost_status = "COMPLETE"
            except Exception as cost_exc:
                validation_error += f"; cost receipt: {type(cost_exc).__name__}: {cost_exc}"
    if usage is None:
        input_tokens = cached_tokens = cache_write_tokens = None
        output_tokens = reasoning_tokens = None
    else:
        input_tokens, cached_tokens, cache_write_tokens, output_tokens, reasoning_tokens = usage
    return JudgeCallRun(
        response_received=raw is not None,
        response_id=response_id,
        effective_model=effective_model,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        cached_tokens=cached_tokens,
        cache_write_tokens=cache_write_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        actual_cost_usd=cost,
        cost_receipt_status=cost_status,
        output_hash=output_hash,
        structured_output=structured_output,
        structured_output_hash=structured_output_hash,
        judge_proposal_hash=judge_proposal_hash,
        validation_status="PASS" if judge_proposal_hash is not None else "FAIL",
        validation_error=validation_error,
        model_calls=1 if raw is not None else 0,
    )


def build_result_receipt(
    *,
    run_id: str,
    started_at_utc: str,
    finished_at_utc: str,
    code_commit_sha: str,
    authorization_hash: str,
    attempt_event_hash: str,
    request_hash: str,
    request_manifest_hash: str,
    run: JudgeCallRun,
) -> dict[str, Any]:
    receipt = {
        "receipt_version": RECEIPT_VERSION,
        "event_type": "JUDGE_PROVIDER_DISPATCH_RESULT",
        "run_id": run_id,
        "dispatch_index": 1,
        "dispatch_started_at_utc": started_at_utc,
        "dispatch_finished_at_utc": finished_at_utc,
        "code_commit_sha": code_commit_sha,
        "paid_authorization_artifact_hash": authorization_hash,
        "dispatch_attempt_event_hash": attempt_event_hash,
        "request_hash": request_hash,
        "request_manifest_hash": request_manifest_hash,
        "provider_response_received": run.response_received,
        "provider_dispatch_state_unknown": not run.response_received,
        "response_id": run.response_id,
        "effective_model": run.effective_model,
        "latency_ms": run.latency_ms,
        "input_tokens": run.input_tokens,
        "cached_tokens": run.cached_tokens,
        "cache_write_tokens": run.cache_write_tokens,
        "output_tokens": run.output_tokens,
        "reasoning_tokens": run.reasoning_tokens,
        "actual_cost_usd": None if run.actual_cost_usd is None else decimal_text(run.actual_cost_usd),
        "cost_receipt_status": run.cost_receipt_status,
        "output_hash": run.output_hash,
        "structured_output_hash": run.structured_output_hash,
        "judge_proposal_hash": run.judge_proposal_hash,
        "structured_output": run.structured_output,
        "validation_status": run.validation_status,
        "validation_error": run.validation_error,
        "model_calls": run.model_calls,
        "replacement_credential_fingerprint_sha256": gate.EXPECTED_CREDENTIAL_SHA256,
        "raw_provider_response_persisted": False,
        "provider_output_text_persisted": False,
        "automatic_retry": False,
        "automatic_repair_attempted": False,
        "judge_execution_authority": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    receipt["receipt_hash"] = canonical_sha256(receipt)
    return receipt


def build_success_artifact(
    *,
    run_id: str,
    code_commit_sha: str,
    selection_hash: str,
    entry_hash: str,
    request_preflight_hash: str,
    request_manifest_hash: str,
    cost_preflight_hash: str,
    runner_dry_hash: str,
    authorization_hash: str,
    receipt_hash: str,
    approved_cost_ceiling_usd: Decimal,
    run: JudgeCallRun,
) -> dict[str, Any]:
    _require(run.validation_status == "PASS", "cannot freeze invalid Judge V02 proposal")
    _require(run.actual_cost_usd is not None and run.cost_receipt_status == "COMPLETE", "cannot freeze Judge V02 without complete cost receipt")
    _require(run.actual_cost_usd <= approved_cost_ceiling_usd, "Judge V02 actual cost exceeded approved ceiling")
    proposal = JudgeDecisionProposalDraft.model_validate(dict(run.structured_output or {}))
    artifact = {
        "artifact_version": RESULT_VERSION,
        "status": SUCCESS_STATUS,
        "run_id": run_id,
        "code_commit_sha": code_commit_sha,
        "rebuttal_council_freeze_artifact_hash": gate.EXPECTED_REBUTTAL_FREEZE_HASH,
        "judge_selected_model_authority_hash": selection_hash,
        "judge_entry_preflight_artifact_hash": entry_hash,
        "request_preflight_artifact_hash": request_preflight_hash,
        "request_manifest_hash": request_manifest_hash,
        "cost_preflight_artifact_hash": cost_preflight_hash,
        "runner_dry_artifact_hash": runner_dry_hash,
        "paid_authorization_artifact_hash": authorization_hash,
        "paid_call_receipt_hash": receipt_hash,
        "approved_cost_ceiling_usd": decimal_text(approved_cost_ceiling_usd),
        "actual_cost_usd": decimal_text(run.actual_cost_usd),
        "cost_receipt_status": "COMPLETE",
        "response_id": run.response_id,
        "effective_model": run.effective_model,
        "input_tokens": run.input_tokens,
        "cached_tokens": run.cached_tokens,
        "cache_write_tokens": run.cache_write_tokens,
        "output_tokens": run.output_tokens,
        "reasoning_tokens": run.reasoning_tokens,
        "latency_ms": run.latency_ms,
        "output_hash": run.output_hash,
        "structured_output_hash": run.structured_output_hash,
        "judge_proposal_hash": run.judge_proposal_hash,
        "judge_proposal": run.structured_output,
        "outcome": proposal.outcome.value,
        "primary_candidate_id": proposal.primary_candidate_id,
        "watch_candidate_ids": list(proposal.watch_candidate_ids),
        "research_reopen_required": proposal.research_reopen_required,
        "research_reopen_reason_codes": list(proposal.research_reopen_reason_codes),
        "next_directive": proposal.next_directive.value,
        "research_reopen_required_candidates_from_rebuttal": list(gate.EXPECTED_REOPEN),
        "invest_eligible_candidates": list(gate.EXPECTED_INVEST_ELIGIBLE),
        "invest_blocked_candidates": list(gate.EXPECTED_INVEST_BLOCKED),
        "judge_authorization_consumed": True,
        "model_calls": 1,
        "automatic_repair_calls": 0,
        "automatic_retries": 0,
        "rerun_authorized": False,
        "final_decision_created": False,
        "b5_handoff_created": False,
        "execution_authority": False,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "next_gate": NEXT_GATE,
        "replacement_credential_fingerprint_sha256": gate.EXPECTED_CREDENTIAL_SHA256,
        "replacement_credential_secret_persisted": False,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact


def build_blocked_artifact(
    *,
    status: str,
    reason: str,
    run_id: str,
    code_commit_sha: str,
    authorization_hash: str,
    attempt_event_hash: str,
    receipt_hash: str | None,
    runner_dry_hash: str,
    approved_cost_ceiling_usd: Decimal,
    run: JudgeCallRun | None,
) -> dict[str, Any]:
    known_cost = None if run is None else run.actual_cost_usd
    artifact = {
        "artifact_version": BLOCKED_VERSION,
        "status": status,
        "reason": reason,
        "run_id": run_id,
        "code_commit_sha": code_commit_sha,
        "rebuttal_council_freeze_artifact_hash": gate.EXPECTED_REBUTTAL_FREEZE_HASH,
        "runner_dry_artifact_hash": runner_dry_hash,
        "paid_authorization_artifact_hash": authorization_hash,
        "dispatch_attempt_event_hash": attempt_event_hash,
        "paid_call_receipt_hash": receipt_hash,
        "approved_cost_ceiling_usd": decimal_text(approved_cost_ceiling_usd),
        "known_cost_usd": None if known_cost is None else decimal_text(known_cost),
        "cost_receipt_status": "COMPLETE" if known_cost is not None else "INCOMPLETE",
        "provider_response_received": False if run is None else run.response_received,
        "validation_status": None if run is None else run.validation_status,
        "validation_error": None if run is None else run.validation_error,
        "judge_authorization_consumed": True,
        "dispatch_attempts": 1,
        "model_calls": 0 if run is None else run.model_calls,
        "automatic_repair_calls": 0,
        "automatic_retries": 0,
        "rerun_authorized": False,
        "final_decision_created": False,
        "b5_handoff_created": False,
        "execution_authority": False,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "replacement_credential_fingerprint_sha256": gate.EXPECTED_CREDENTIAL_SHA256,
        "replacement_credential_secret_persisted": False,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact
