from __future__ import annotations

import json
from time import perf_counter_ns
from typing import Any, Mapping

from pydantic import model_validator

from aic.domain.canonical import canonical_sha256

from .models import B3Model
from .policy import ResearchPolicy
from .prompts import (
    SYNTHESIS_INSTRUCTIONS,
    SYNTHESIS_REPAIR_INSTRUCTIONS,
    SYNTHESIS_REPAIR_PROMPT_VERSION,
    synthesis_repair_prompt_hash,
)
from .runtime import (
    ResponsesCallResult,
    ResponsesRuntimeError,
    ResponsesTransport,
    StdlibResponsesTransport,
    parse_responses_payload,
    validate_openai_api_key,
)
from .synthesize import (
    CandidateSynthesisDraft,
    SynthesisInputEnvelope,
    SynthesisRequestEnvelope,
    parse_synthesis_output,
)
from .validate import CandidatePacketValidationError, validate_synthesis_draft


SYNTHESIS_REPAIR_REQUEST_VERSION = "B3_SYNTHESIS_REPAIR_REQUEST_v0_1"


class CandidateSynthesisRuntimeResult(B3Model):
    initial_call: ResponsesCallResult
    repair_call: ResponsesCallResult | None
    draft: CandidateSynthesisDraft
    repair_attempts: int
    validator_results: tuple[Mapping[str, object], ...]
    initial_validator_error: str | None

    @model_validator(mode="after")
    def _bounded_repair(self):
        if self.repair_attempts not in (0, 1):
            raise ValueError("B3 synthesis repair_attempts must be 0 or 1")
        if self.repair_attempts == 0:
            if self.repair_call is not None or self.initial_validator_error is not None:
                raise ValueError("zero-repair result cannot contain repair state")
        else:
            if self.repair_call is None or not self.initial_validator_error:
                raise ValueError("repaired result must record repair call and first validator error")
        return self


def _assert_synthesis_request_invariants(request: SynthesisRequestEnvelope) -> None:
    payload = request.request_payload
    if payload.get("store") is not False:
        raise ResponsesRuntimeError("synthesis runtime requires store=false")
    if payload.get("tools") != []:
        raise ResponsesRuntimeError("synthesis runtime requires an empty tools array")
    if payload.get("parallel_tool_calls") is not False:
        raise ResponsesRuntimeError("synthesis runtime requires parallel_tool_calls=false")
    text = payload.get("text")
    if not isinstance(text, Mapping):
        raise ResponsesRuntimeError("synthesis runtime requires text configuration")
    fmt = text.get("format")
    if not isinstance(fmt, Mapping):
        raise ResponsesRuntimeError("synthesis runtime requires structured output format")
    if fmt.get("type") != "json_schema" or fmt.get("strict") is not True:
        raise ResponsesRuntimeError("synthesis runtime requires strict json_schema output")


def _execute_call(
    *,
    request: SynthesisRequestEnvelope,
    api_key: str,
    transport: ResponsesTransport,
) -> ResponsesCallResult:
    _assert_synthesis_request_invariants(request)
    requested_model = request.request_payload.get("model")
    if not isinstance(requested_model, str) or not requested_model:
        raise ResponsesRuntimeError("synthesis request model must be a non-empty string")
    start = perf_counter_ns()
    raw_payload = transport.post(
        payload=request.request_payload,
        api_key=api_key,
    )
    latency_ms = max(0, (perf_counter_ns() - start) // 1_000_000)
    return parse_responses_payload(
        raw_payload,
        requested_model=requested_model,
        latency_ms=latency_ms,
    )


def _build_repair_request(
    *,
    original_request: SynthesisRequestEnvelope,
    synthesis_input: SynthesisInputEnvelope,
    invalid_draft: CandidateSynthesisDraft,
    validator_error: str,
) -> SynthesisRequestEnvelope:
    original_payload = dict(original_request.request_payload)
    original_text = original_payload.get("text")
    if not isinstance(original_text, Mapping):
        raise ResponsesRuntimeError("original synthesis request lost text configuration")
    repair_input = {
        "frozen_synthesis_input": synthesis_input.model_dump(mode="json"),
        "previous_invalid_draft": invalid_draft.model_dump(mode="json"),
        "validator_finding": validator_error,
    }
    repair_payload: dict[str, Any] = {
        **original_payload,
        "instructions": SYNTHESIS_INSTRUCTIONS + "\n\n" + SYNTHESIS_REPAIR_INSTRUCTIONS,
        "input": json.dumps(
            repair_input,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "text": dict(original_text),
    }
    envelope = {
        "request_version": SYNTHESIS_REPAIR_REQUEST_VERSION,
        "prompt_version": SYNTHESIS_REPAIR_PROMPT_VERSION,
        "prompt_hash": synthesis_repair_prompt_hash(),
        # The immutable input hash remains the hash of the exact same frozen evidence input.
        "input_hash": canonical_sha256(synthesis_input),
        "model_candidate_key": original_request.model_candidate_key,
        "request_payload": repair_payload,
    }
    return SynthesisRequestEnvelope(
        **envelope,
        request_hash=canonical_sha256(envelope),
    )


def execute_synthesis_runtime(
    *,
    request: SynthesisRequestEnvelope,
    synthesis_input: SynthesisInputEnvelope,
    research_policy: ResearchPolicy,
    api_key: str,
    transport: ResponsesTransport | None = None,
) -> CandidateSynthesisRuntimeResult:
    if research_policy.repair_attempt_limit != 1:
        raise ResponsesRuntimeError("B3 V1 synthesis runtime requires repair_attempt_limit=1")
    if request.input_hash != canonical_sha256(synthesis_input):
        raise ResponsesRuntimeError("synthesis request input_hash does not bind frozen input")
    key = validate_openai_api_key(api_key)
    runtime_transport = StdlibResponsesTransport() if transport is None else transport

    initial_call = _execute_call(
        request=request,
        api_key=key,
        transport=runtime_transport,
    )
    initial_draft = parse_synthesis_output(
        initial_call.output_text,
        synthesis_input=synthesis_input,
    )
    try:
        validator_results = validate_synthesis_draft(
            initial_draft,
            synthesis_input=synthesis_input,
        )
    except CandidatePacketValidationError as first_error:
        repair_request = _build_repair_request(
            original_request=request,
            synthesis_input=synthesis_input,
            invalid_draft=initial_draft,
            validator_error=str(first_error),
        )
        repair_call = _execute_call(
            request=repair_request,
            api_key=key,
            transport=runtime_transport,
        )
        repaired_draft = parse_synthesis_output(
            repair_call.output_text,
            synthesis_input=synthesis_input,
        )
        try:
            repaired_results = validate_synthesis_draft(
                repaired_draft,
                synthesis_input=synthesis_input,
            )
        except CandidatePacketValidationError as repair_error:
            raise CandidatePacketValidationError(
                "B3 synthesis repair exhausted after exactly one attempt: "
                + str(repair_error)
            ) from repair_error
        return CandidateSynthesisRuntimeResult(
            initial_call=initial_call,
            repair_call=repair_call,
            draft=repaired_draft,
            repair_attempts=1,
            validator_results=repaired_results,
            initial_validator_error=str(first_error),
        )

    return CandidateSynthesisRuntimeResult(
        initial_call=initial_call,
        repair_call=None,
        draft=initial_draft,
        repair_attempts=0,
        validator_results=validator_results,
        initial_validator_error=None,
    )
