from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
from time import perf_counter_ns
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256

from .bounded_request import build_bounded_judge_request
from .initial_runtime_cost_v02 import actual_cost_usd, load_initial_runtime_pricing
from .judge_eval_preflight import (
    JUDGE_EVAL_VERSION,
    JudgeEvalCase,
    build_judge_eval_cases,
    score_judge_eval_case,
)
from .model_policy import JUDGE_MODEL_LADDER, CouncilModelCandidate
from .proposal import JudgeDecisionProposalDraft
from .request import parse_council_responses_payload


JUDGE_EVAL_RUNTIME_VERSION = "B4_JUDGE_MODEL_EVAL_RUNTIME_v0_1"


@dataclass(frozen=True, slots=True)
class JudgeEvalCaseRun:
    case_id: str
    name: str
    critical_safety: bool
    passed: bool
    findings: tuple[str, ...]
    response_id: str | None
    requested_model: str
    effective_model: str | None
    model_calls: int
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
    result_hash: str


def build_judge_eval_case_request(
    case: JudgeEvalCase,
    model_candidate: CouncilModelCandidate,
):
    return build_bounded_judge_request(
        model_candidate=model_candidate,
        model_input=case.model_input,
        candidate_ids=case.candidate_ids,
        mandate_version=case.mandate_version,
        deep_comparison_id=case.deep_comparison_id,
        judge_input_hash=case.judge_input_hash,
        council_policy_version="COUNCIL_POLICY_vB4_0_1",
        judge_policy_version="JUDGE_POLICY_vB4_0_1",
        model_policy_version="MODEL_POLICY_vB4_0_1",
        model_run_ref=f"JUDGE_EVAL_{model_candidate.candidate_key}_{case.case_id}",
        allowed_claim_ids=case.allowed_claim_ids,
        allowed_dispute_refs=case.allowed_dispute_refs,
        allowed_conflict_refs=case.allowed_conflict_refs,
        allowed_unknown_refs=case.allowed_unknown_refs,
        allowed_condition_refs=case.allowed_condition_refs,
    )


def _usage_counts(raw: Mapping[str, Any]) -> tuple[int, int, int, int, int]:
    usage = raw.get("usage")
    if not isinstance(usage, Mapping):
        raise ValueError("provider response lacks usage")
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    input_details = usage.get("input_tokens_details")
    output_details = usage.get("output_tokens_details")
    if type(input_tokens) is not int or input_tokens < 0:
        raise ValueError("usage.input_tokens invalid")
    if type(output_tokens) is not int or output_tokens < 0:
        raise ValueError("usage.output_tokens invalid")
    if not isinstance(input_details, Mapping):
        raise ValueError("usage.input_tokens_details missing")
    cached_tokens = input_details.get("cached_tokens")
    cache_write_tokens = input_details.get("cache_write_tokens")
    if type(cached_tokens) is not int or cached_tokens < 0:
        raise ValueError("usage.cached_tokens invalid")
    if type(cache_write_tokens) is not int or cache_write_tokens < 0:
        raise ValueError("usage.cache_write_tokens invalid")
    if cached_tokens + cache_write_tokens > input_tokens:
        raise ValueError("cached + cache-write tokens exceed input tokens")
    reasoning_tokens = 0
    if isinstance(output_details, Mapping):
        value = output_details.get("reasoning_tokens")
        if value is not None:
            if type(value) is not int or value < 0:
                raise ValueError("usage.reasoning_tokens invalid")
            reasoning_tokens = value
    return (
        input_tokens,
        cached_tokens,
        cache_write_tokens,
        output_tokens,
        reasoning_tokens,
    )


def execute_judge_eval_case_once(
    case: JudgeEvalCase,
    *,
    model_candidate: CouncilModelCandidate,
    api_key: str,
    transport: Any,
) -> JudgeEvalCaseRun:
    request = build_judge_eval_case_request(case, model_candidate)
    started = perf_counter_ns()
    raw: Mapping[str, Any] | None = None
    findings: list[str] = []
    passed = False
    response_id: str | None = None
    effective_model: str | None = None
    output_hash: str | None = None
    structured_output: Mapping[str, Any] | None = None
    structured_output_hash: str | None = None

    try:
        raw_value = transport.post(payload=request.request_payload, api_key=api_key)
        if not isinstance(raw_value, Mapping):
            raise RuntimeError("Responses payload must be an object")
        raw = raw_value
        latency_ms = max(0, (perf_counter_ns() - started) // 1_000_000)
        call, proposal = parse_council_responses_payload(
            raw,
            request=request,
            latency_ms=latency_ms,
        )
        response_id = call.response_id
        effective_model = call.effective_model
        output_hash = call.output_hash
        if not isinstance(proposal, JudgeDecisionProposalDraft):
            raise RuntimeError("Judge eval produced wrong DTO type")
        structured_output = proposal.model_dump(mode="json", exclude_none=False)
        structured_output_hash = canonical_sha256(structured_output)
        passed, scored_findings = score_judge_eval_case(proposal, case=case)
        findings.extend(scored_findings)
    except Exception as exc:
        latency_ms = max(0, (perf_counter_ns() - started) // 1_000_000)
        findings.append(f"{type(exc).__name__}: {exc}")

    input_tokens = None
    cached_tokens = None
    cache_write_tokens = None
    output_tokens = None
    reasoning_tokens = None
    cost: Decimal | None = None
    cost_status = "INCOMPLETE"
    if raw is not None:
        try:
            (
                input_tokens,
                cached_tokens,
                cache_write_tokens,
                output_tokens,
                reasoning_tokens,
            ) = _usage_counts(raw)
            cost = actual_cost_usd(
                raw,
                model=model_candidate.model,
                pricing=load_initial_runtime_pricing(),
            )
            cost_status = "COMPLETE"
        except Exception as exc:
            findings.append(f"cost receipt: {type(exc).__name__}: {exc}")

    final_passed = passed and cost_status == "COMPLETE"
    final_findings = tuple(dict.fromkeys(findings))
    result_payload = {
        "eval_version": JUDGE_EVAL_VERSION,
        "runtime_version": JUDGE_EVAL_RUNTIME_VERSION,
        "case_id": case.case_id,
        "name": case.name,
        "critical_safety": case.critical_safety,
        "passed": final_passed,
        "findings": list(final_findings),
        "response_id": response_id,
        "requested_model": model_candidate.model,
        "effective_model": effective_model,
        "model_calls": 1 if raw is not None else 0,
        "latency_ms": latency_ms,
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "cache_write_tokens": cache_write_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "actual_cost_usd": None if cost is None else str(cost),
        "cost_receipt_status": cost_status,
        "output_hash": output_hash,
        "structured_output_hash": structured_output_hash,
    }
    return JudgeEvalCaseRun(
        case_id=case.case_id,
        name=case.name,
        critical_safety=case.critical_safety,
        passed=final_passed,
        findings=final_findings,
        response_id=response_id,
        requested_model=model_candidate.model,
        effective_model=effective_model,
        model_calls=result_payload["model_calls"],
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
        result_hash=canonical_sha256(result_payload),
    )


def dry_run_manifest() -> dict[str, Any]:
    cases = build_judge_eval_cases()
    requests: list[dict[str, Any]] = []
    for candidate in JUDGE_MODEL_LADDER:
        for case in cases:
            request = build_judge_eval_case_request(case, candidate)
            body_bytes = len(
                json.dumps(
                    request.request_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            requests.append(
                {
                    "candidate_key": candidate.candidate_key,
                    "model": candidate.model,
                    "reasoning_effort": candidate.reasoning_effort,
                    "case_id": case.case_id,
                    "request_hash": request.request_hash,
                    "request_body_utf8_bytes": body_bytes,
                    "max_output_tokens": request.request_payload["max_output_tokens"],
                }
            )
    manifest: dict[str, Any] = {
        "eval_version": JUDGE_EVAL_VERSION,
        "runtime_version": JUDGE_EVAL_RUNTIME_VERSION,
        "candidate_keys": [item.candidate_key for item in JUDGE_MODEL_LADDER],
        "case_ids": [case.case_id for case in cases],
        "request_count": len(requests),
        "requests": requests,
    }
    manifest["manifest_hash"] = canonical_sha256(manifest)
    return manifest
