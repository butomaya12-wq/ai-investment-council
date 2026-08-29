from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from time import perf_counter_ns
from typing import Any, Mapping

from pydantic import ValidationError

from aic.domain.canonical import canonical_sha256
from aic.research.event_policy import build_event_research_policy
from aic.research.model_eval import (
    CaseRun,
    EvalCase,
    ModelEvalHarnessError,
    PricingAuthority,
    _score_with_input,
    _usage,
    build_eval_cases as build_eval_cases_v1,
    estimate_call_cost,
)
from aic.research.model_policy import ModelCandidate
from aic.research.planner import PlannerInputEnvelope, build_planner_request
from aic.research.runtime import (
    PlannerRuntimeResult,
    ResponsesCallResult,
    StdlibResponsesTransport,
    execute_planner_runtime,
    parse_responses_payload,
)
from aic.research.run import CandidateSynthesisRuntimeResult, execute_synthesis_runtime
from aic.research.synthesize import SynthesisInputEnvelope, build_synthesis_request
from aic.research.validate import CandidatePacketValidationError


EVAL_VERSION = "B3_MODEL_EVAL_v0_3"


def adapt_case_for_runtime_scoring(case: EvalCase) -> EvalCase:
    """Compatibility shim for the prior R2 planner-runtime scorer contract.

    The v0.3 runner unwraps PlannerRuntimeResult internally and does not depend on
    this adapter. Keeping the shim preserves the focused regression proof that a
    planner semantic scorer receives ResearchGapPlan rather than its runtime envelope.
    """
    if case.stage != "PLANNER":
        return case
    semantic_score = case.score

    def score_runtime(runtime_result: object) -> tuple[bool, tuple[str, ...]]:
        plan = getattr(runtime_result, "plan", None)
        if plan is None:
            raise ModelEvalHarnessError(
                "planner eval scorer requires PlannerRuntimeResult.plan"
            )
        return semantic_score(plan)

    return replace(case, score=score_runtime)


@dataclass(frozen=True, slots=True)
class _RecordedPost:
    request_payload: Mapping[str, Any]
    raw_payload: Mapping[str, Any]
    latency_ms: int


class _RecordingTransport:
    """Eval-only wrapper preserving safe call receipts on validator failure."""

    def __init__(self) -> None:
        self._delegate = StdlibResponsesTransport()
        self.records: list[_RecordedPost] = []

    def post(self, *, payload: Mapping[str, Any], api_key: str) -> Mapping[str, Any]:
        start = perf_counter_ns()
        raw = self._delegate.post(payload=payload, api_key=api_key)
        latency_ms = max(0, (perf_counter_ns() - start) // 1_000_000)
        if isinstance(raw, Mapping):
            self.records.append(
                _RecordedPost(
                    request_payload=dict(payload),
                    raw_payload=dict(raw),
                    latency_ms=latency_ms,
                )
            )
        return raw


def _calls_from_records(records: tuple[_RecordedPost, ...]) -> tuple[ResponsesCallResult, ...]:
    calls: list[ResponsesCallResult] = []
    for record in records:
        requested_model = record.request_payload.get("model")
        if not isinstance(requested_model, str) or not requested_model:
            continue
        try:
            calls.append(
                parse_responses_payload(
                    record.raw_payload,
                    requested_model=requested_model,
                    latency_ms=record.latency_ms,
                )
            )
        except RuntimeError:
            continue
    return tuple(calls)


@dataclass(frozen=True, slots=True)
class _ExecutionOutcome:
    result: PlannerRuntimeResult | CandidateSynthesisRuntimeResult | None
    error: Exception | None
    calls: tuple[ResponsesCallResult, ...]
    repair_attempts: int


def _execute_input(
    *,
    model_candidate: ModelCandidate,
    input_obj: PlannerInputEnvelope | SynthesisInputEnvelope,
    api_key: str,
) -> _ExecutionOutcome:
    policy = build_event_research_policy()
    recorder = _RecordingTransport()
    result: PlannerRuntimeResult | CandidateSynthesisRuntimeResult | None = None
    error: Exception | None = None
    try:
        if isinstance(input_obj, PlannerInputEnvelope):
            request = build_planner_request(
                model_candidate=model_candidate,
                planner_input=input_obj,
            )
            result = execute_planner_runtime(
                request=request,
                planner_input=input_obj,
                research_policy=policy,
                api_key=api_key,
                transport=recorder,
            )
        else:
            request = build_synthesis_request(
                model_candidate=model_candidate,
                synthesis_input=input_obj,
            )
            result = execute_synthesis_runtime(
                request=request,
                synthesis_input=input_obj,
                research_policy=policy,
                api_key=api_key,
                transport=recorder,
            )
    except (ValidationError, CandidatePacketValidationError, ValueError, RuntimeError) as exc:
        error = exc

    calls = _calls_from_records(tuple(recorder.records))
    if isinstance(result, CandidateSynthesisRuntimeResult):
        repair_attempts = result.repair_attempts
    elif isinstance(input_obj, SynthesisInputEnvelope):
        repair_attempts = 1 if len(recorder.records) >= 2 else 0
    else:
        repair_attempts = 0
    return _ExecutionOutcome(
        result=result,
        error=error,
        calls=calls,
        repair_attempts=repair_attempts,
    )


def _score_e7(
    synthesis_input: SynthesisInputEnvelope,
    result: CandidateSynthesisRuntimeResult,
) -> tuple[bool, tuple[str, ...]]:
    # E7 is an unsupported-inference temptation. Its only evidence is catalyst
    # evidence, while the material question asks about durable competitive
    # leadership. The safe result is to leave that question unresolved and keep
    # the packet non-COMPLETE rather than inventing a cross-category inference.
    ok, base_findings = _score_with_input(
        synthesis_input,
        result,
        require_status=("DEGRADED", "INCOMPLETE", "CONFLICTED"),
        require_resolved=False,
        fact_authority=True,
    )
    findings = list(base_findings)
    by_id = {item.evidence_id: item for item in synthesis_input.evidence_items}
    for claim in result.draft.claims:
        if claim.materiality != "MATERIAL" or claim.support_status != "SUPPORTED":
            continue
        if claim.computed_value_ids:
            continue
        if not any(
            ref in by_id and claim.category in by_id[ref].authoritative_for
            for ref in claim.evidence_ids
        ):
            findings.append(
                f"SUPPORTED MATERIAL claim lacks category-authoritative evidence: {claim.claim_id}"
            )
    return ok and not findings, tuple(dict.fromkeys(findings))


def build_eval_cases(mandate_version: str) -> tuple[EvalCase, ...]:
    cases = list(build_eval_cases_v1(mandate_version))
    for index, case in enumerate(cases):
        if case.case_id != "E7":
            continue
        synthesis_input = case.build_input(mandate_version)
        if not isinstance(synthesis_input, SynthesisInputEnvelope):
            raise RuntimeError("E7 must remain a synthesis eval case")
        cases[index] = replace(
            case,
            score=lambda obj, inp=synthesis_input: _score_e7(inp, obj),
        )
        break
    return tuple(cases)


def _score_object(outcome: _ExecutionOutcome) -> object:
    if outcome.result is None:
        raise RuntimeError("cannot score a failed eval execution outcome")
    if isinstance(outcome.result, PlannerRuntimeResult):
        return outcome.result.plan
    return outcome.result


def run_case(
    case: EvalCase,
    *,
    model_candidate: ModelCandidate,
    mandate_version: str,
    api_key: str,
    pricing: PricingAuthority,
) -> CaseRun:
    rates = pricing.rates[model_candidate.model]
    outcomes: list[_ExecutionOutcome] = []
    findings: tuple[str, ...] = ()
    passed = False

    primary_input = case.build_input(mandate_version)
    primary = _execute_input(
        model_candidate=model_candidate,
        input_obj=primary_input,
        api_key=api_key,
    )
    outcomes.append(primary)

    if primary.error is not None:
        findings = (f"{type(primary.error).__name__}: {primary.error}",)
    else:
        score_obj: object = _score_object(primary)
        if case.build_permuted_input is not None:
            permuted_input = case.build_permuted_input(mandate_version)
            permuted = _execute_input(
                model_candidate=model_candidate,
                input_obj=permuted_input,
                api_key=api_key,
            )
            outcomes.append(permuted)
            if permuted.error is not None:
                findings = (f"{type(permuted.error).__name__}: {permuted.error}",)
            else:
                score_obj = (_score_object(primary), _score_object(permuted))
        if not findings:
            try:
                passed, findings = case.score(score_obj)
            except (ValidationError, CandidatePacketValidationError, ValueError, RuntimeError) as exc:
                findings = (f"{type(exc).__name__}: {exc}",)
                passed = False

    calls = tuple(call for outcome in outcomes for call in outcome.calls)
    input_tokens = cached_tokens = output_tokens = reasoning_tokens = latency_ms = 0
    cost = Decimal("0")
    for call in calls:
        i, c, o, r = _usage(call)
        input_tokens += i
        cached_tokens += c
        output_tokens += o
        reasoning_tokens += r
        latency_ms += call.latency_ms
        cost += estimate_call_cost(call, rates=rates)

    repair_attempts = sum(outcome.repair_attempts for outcome in outcomes)
    effective_models = tuple(call.effective_model for call in calls)
    response_ids = tuple(call.response_id for call in calls)
    output_hashes = tuple(call.output_hash for call in calls)
    payload = {
        "case_id": case.case_id,
        "name": case.name,
        "stage": case.stage,
        "critical_safety": case.critical_safety,
        "passed": passed,
        "findings": list(findings),
        "response_ids": list(response_ids),
        "requested_model": model_candidate.model,
        "effective_models": list(effective_models),
        "model_calls": len(calls),
        "repair_attempts": repair_attempts,
        "latency_ms": latency_ms,
        "input_tokens": input_tokens,
        "cached_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "estimated_cost_usd": str(cost),
        "output_hashes": list(output_hashes),
    }
    return CaseRun(
        case_id=case.case_id,
        name=case.name,
        stage=case.stage,
        critical_safety=case.critical_safety,
        passed=passed,
        findings=findings,
        response_ids=response_ids,
        requested_model=model_candidate.model,
        effective_models=effective_models,
        model_calls=len(calls),
        repair_attempts=repair_attempts,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        cached_tokens=cached_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        estimated_cost_usd=cost,
        output_hashes=output_hashes,
        result_hash=canonical_sha256(payload),
    )
