from __future__ import annotations

import json
from time import perf_counter_ns
from typing import Any, Mapping

from pydantic import ValidationError, model_validator

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
    # Persist the exact first structured payload even when a custom Pydantic
    # invariant (for example duplicate claim_id values) prevents construction
    # of CandidateSynthesisDraft. This keeps bounded-repair evidence reconstructible.
    initial_draft: Mapping[str, Any]
    draft: CandidateSynthesisDraft
    repair_attempts: int
    repair_request_hash: str | None
    validator_results: tuple[Mapping[str, object], ...]
    initial_validator_error: str | None

    @model_validator(mode="after")
    def _bounded_repair(self):
        if self.repair_attempts not in (0, 1):
            raise ValueError("B3 synthesis repair_attempts must be 0 or 1")
        if self.repair_attempts == 0:
            if (
                self.repair_call is not None
                or self.repair_request_hash is not None
                or self.initial_validator_error is not None
            ):
                raise ValueError("zero-repair result cannot contain repair state")
            if canonical_sha256(self.initial_draft) != canonical_sha256(
                self.draft.model_dump(mode="json")
            ):
                raise ValueError("zero-repair result must preserve initial draft as final draft")
        else:
            if (
                self.repair_call is None
                or not self.repair_request_hash
                or not self.initial_validator_error
            ):
                raise ValueError(
                    "repaired result must record repair call, request hash and first validator error"
                )
            if (
                len(self.repair_request_hash) != 64
                or any(ch not in "0123456789abcdef" for ch in self.repair_request_hash)
            ):
                raise ValueError("repair_request_hash must be lowercase SHA-256")
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


def _raw_structured_payload(output_text: str) -> dict[str, Any]:
    try:
        payload = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise ResponsesRuntimeError("synthesis output is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ResponsesRuntimeError("synthesis structured output root must be an object")
    return payload


def _build_repair_request(
    *,
    original_request: SynthesisRequestEnvelope,
    synthesis_input: SynthesisInputEnvelope,
    invalid_draft: Mapping[str, Any],
    validator_error: str,
) -> SynthesisRequestEnvelope:
    original_payload = dict(original_request.request_payload)
    original_text = original_payload.get("text")
    if not isinstance(original_text, Mapping):
        raise ResponsesRuntimeError("original synthesis request lost text configuration")
    repair_input = {
        "frozen_synthesis_input": synthesis_input.model_dump(mode="json"),
        "previous_invalid_draft": dict(invalid_draft),
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


def _execute_repair(
    *,
    request: SynthesisRequestEnvelope,
    synthesis_input: SynthesisInputEnvelope,
    initial_call: ResponsesCallResult,
    invalid_payload: Mapping[str, Any],
    first_error: Exception,
    api_key: str,
    transport: ResponsesTransport,
) -> CandidateSynthesisRuntimeResult:
    repair_request = _build_repair_request(
        original_request=request,
        synthesis_input=synthesis_input,
        invalid_draft=invalid_payload,
        validator_error=str(first_error),
    )
    repair_call = _execute_call(
        request=repair_request,
        api_key=api_key,
        transport=transport,
    )
    try:
        repaired_draft = parse_synthesis_output(
            repair_call.output_text,
            synthesis_input=synthesis_input,
        )
        repaired_results = validate_synthesis_draft(
            repaired_draft,
            synthesis_input=synthesis_input,
        )
    except (ValidationError, CandidatePacketValidationError, ValueError) as repair_error:
        raise CandidatePacketValidationError(
            "B3 synthesis repair exhausted after exactly one attempt: "
            + str(repair_error)
        ) from repair_error

    return CandidateSynthesisRuntimeResult(
        initial_call=initial_call,
        repair_call=repair_call,
        initial_draft=dict(invalid_payload),
        draft=repaired_draft,
        repair_attempts=1,
        repair_request_hash=repair_request.request_hash,
        validator_results=repaired_results,
        initial_validator_error=str(first_error),
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
    try:
        initial_draft = parse_synthesis_output(
            initial_call.output_text,
            synthesis_input=synthesis_input,
        )
    except ValidationError as first_error:
        # Strict JSON Schema cannot express every application invariant (for example
        # uniqueness of a property across array items). Such a structured DTO failure
        # is an invalid synthesis result and receives the same single bounded repair.
        return _execute_repair(
            request=request,
            synthesis_input=synthesis_input,
            initial_call=initial_call,
            invalid_payload=_raw_structured_payload(initial_call.output_text),
            first_error=first_error,
            api_key=key,
            transport=runtime_transport,
        )

    initial_payload = initial_draft.model_dump(mode="json")
    try:
        validator_results = validate_synthesis_draft(
            initial_draft,
            synthesis_input=synthesis_input,
        )
    except CandidatePacketValidationError as first_error:
        return _execute_repair(
            request=request,
            synthesis_input=synthesis_input,
            initial_call=initial_call,
            invalid_payload=initial_payload,
            first_error=first_error,
            api_key=key,
            transport=runtime_transport,
        )

    return CandidateSynthesisRuntimeResult(
        initial_call=initial_call,
        repair_call=None,
        initial_draft=initial_payload,
        draft=initial_draft,
        repair_attempts=0,
        repair_request_hash=None,
        validator_results=validator_results,
        initial_validator_error=None,
    )
