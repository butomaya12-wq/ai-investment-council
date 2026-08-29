from __future__ import annotations

import json
import os
from dataclasses import dataclass
from time import perf_counter_ns
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import field_validator, model_validator

from aic.domain.canonical import canonical_sha256

from .models import B3Model, ResearchGapPlan
from .planner import PlannerInputEnvelope, PlannerRequestEnvelope, parse_planner_output
from .policy import ResearchPolicy


OPENAI_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"
RUNTIME_VERSION = "B3_RESPONSES_RUNTIME_v0_1"


class ResponsesRuntimeError(RuntimeError):
    pass


class ResponsesCredentialError(ResponsesRuntimeError):
    pass


class ResponsesProtocolError(ResponsesRuntimeError):
    pass


class ResponsesHttpError(ResponsesRuntimeError):
    """Safe, bounded HTTP failure metadata with no provider response message/body."""

    def __init__(
        self,
        *,
        status_code: int,
        diagnostics: Mapping[str, str | int | None],
    ) -> None:
        self.status_code = status_code
        self.diagnostics = dict(diagnostics)
        parts = [f"OpenAI Responses HTTP failure: {status_code}"]
        for key in (
            "error_type",
            "error_code",
            "request_id",
            "retry_after",
            "ratelimit_limit_requests",
            "ratelimit_remaining_requests",
            "ratelimit_reset_requests",
            "ratelimit_limit_tokens",
            "ratelimit_remaining_tokens",
            "ratelimit_reset_tokens",
        ):
            value = self.diagnostics.get(key)
            if value is not None:
                parts.append(f"{key}={value}")
        super().__init__("; ".join(parts))


class ResponsesTransport(Protocol):
    def post(
        self,
        *,
        payload: Mapping[str, Any],
        api_key: str,
    ) -> Mapping[str, Any]:
        ...


def _safe_provider_token(value: Any, *, max_len: int = 160) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip()
    if not token or len(token) > max_len:
        return None
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-/,;=+ ")
    if any(ch not in allowed for ch in token):
        return None
    return token


def _http_error_diagnostics(exc: HTTPError) -> dict[str, str | int | None]:
    error_type: str | None = None
    error_code: str | None = None
    try:
        # The provider body is read only to extract two allowlisted machine fields.
        # The message/body itself is never persisted or surfaced because it may echo input.
        raw = exc.read(65_536)
        decoded = json.loads(raw.decode("utf-8"))
        if isinstance(decoded, Mapping):
            error = decoded.get("error")
            if isinstance(error, Mapping):
                error_type = _safe_provider_token(error.get("type"))
                error_code = _safe_provider_token(error.get("code"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        pass

    headers = exc.headers

    def header(name: str) -> str | None:
        if headers is None:
            return None
        return _safe_provider_token(headers.get(name))

    return {
        "status_code": exc.code,
        "error_type": error_type,
        "error_code": error_code,
        "request_id": header("x-request-id"),
        "retry_after": header("retry-after"),
        "ratelimit_limit_requests": header("x-ratelimit-limit-requests"),
        "ratelimit_remaining_requests": header("x-ratelimit-remaining-requests"),
        "ratelimit_reset_requests": header("x-ratelimit-reset-requests"),
        "ratelimit_limit_tokens": header("x-ratelimit-limit-tokens"),
        "ratelimit_remaining_tokens": header("x-ratelimit-remaining-tokens"),
        "ratelimit_reset_tokens": header("x-ratelimit-reset-tokens"),
    }


@dataclass(frozen=True, slots=True)
class StdlibResponsesTransport:
    endpoint: str = OPENAI_RESPONSES_ENDPOINT
    timeout_seconds: int = 60

    def __post_init__(self) -> None:
        if self.endpoint != OPENAI_RESPONSES_ENDPOINT:
            raise ValueError("B3 runtime endpoint is fixed to the OpenAI Responses API")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 120:
            raise ValueError("timeout_seconds must be within the bounded runtime range")

    def post(
        self,
        *,
        payload: Mapping[str, Any],
        api_key: str,
    ) -> Mapping[str, Any]:
        key = validate_openai_api_key(api_key)
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        request = Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            diagnostics = _http_error_diagnostics(exc)
            raise ResponsesHttpError(
                status_code=exc.code,
                diagnostics=diagnostics,
            ) from exc
        except URLError as exc:
            raise ResponsesRuntimeError("OpenAI Responses network failure") from exc

        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResponsesProtocolError("OpenAI Responses body is not valid UTF-8 JSON") from exc
        if not isinstance(decoded, Mapping):
            raise ResponsesProtocolError("OpenAI Responses body must be a JSON object")
        return decoded


class ResponsesUsage(B3Model):
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None

    @model_validator(mode="after")
    def _non_negative(self):
        for value in (
            self.input_tokens,
            self.output_tokens,
            self.reasoning_tokens,
            self.cached_tokens,
        ):
            if value is not None and value < 0:
                raise ValueError("response token counters must be non-negative")
        return self


class ResponsesCallResult(B3Model):
    runtime_version: str
    response_id: str
    requested_model: str
    effective_model: str
    output_text: str
    output_hash: str
    usage: ResponsesUsage
    latency_ms: int

    @field_validator(
        "response_id",
        "requested_model",
        "effective_model",
        "output_text",
    )
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("response string fields must be non-empty")
        return value

    @model_validator(mode="after")
    def _bind_output_hash(self):
        if self.runtime_version != RUNTIME_VERSION:
            raise ValueError("unexpected responses runtime version")
        if self.latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")
        expected = canonical_sha256({"output_text": self.output_text})
        if self.output_hash != expected:
            raise ValueError("output_hash does not bind output_text")
        return self


class PlannerRuntimeResult(B3Model):
    call: ResponsesCallResult
    plan: ResearchGapPlan
    plan_hash: str

    @model_validator(mode="after")
    def _bind_plan_hash(self):
        if self.plan_hash != canonical_sha256(self.plan):
            raise ValueError("plan_hash does not bind ResearchGapPlan")
        return self


def validate_openai_api_key(value: str) -> str:
    if not isinstance(value, str):
        raise ResponsesCredentialError("OPENAI_API_KEY must be a string")
    if not value or value != value.strip():
        raise ResponsesCredentialError("OPENAI_API_KEY must be non-empty and trimmed")
    if any(ch.isspace() for ch in value):
        raise ResponsesCredentialError("OPENAI_API_KEY must not contain whitespace")
    return value


def load_openai_api_key(
    env: Mapping[str, str] | None = None,
) -> str:
    source = os.environ if env is None else env
    value = source.get("OPENAI_API_KEY")
    if value is None:
        raise ResponsesCredentialError(
            "OPENAI_API_KEY is not present in the runtime environment"
        )
    return validate_openai_api_key(value)


def _optional_non_negative_int(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ResponsesProtocolError(f"{field} must be a non-negative integer or null")
    return value


def _extract_usage(payload: Mapping[str, Any]) -> ResponsesUsage:
    raw = payload.get("usage")
    if raw is None:
        return ResponsesUsage()
    if not isinstance(raw, Mapping):
        raise ResponsesProtocolError("usage must be an object or null")

    input_details = raw.get("input_tokens_details")
    if input_details is not None and not isinstance(input_details, Mapping):
        raise ResponsesProtocolError("input_tokens_details must be an object or null")
    output_details = raw.get("output_tokens_details")
    if output_details is not None and not isinstance(output_details, Mapping):
        raise ResponsesProtocolError("output_tokens_details must be an object or null")

    return ResponsesUsage(
        input_tokens=_optional_non_negative_int(
            raw.get("input_tokens"), field="usage.input_tokens"
        ),
        output_tokens=_optional_non_negative_int(
            raw.get("output_tokens"), field="usage.output_tokens"
        ),
        cached_tokens=_optional_non_negative_int(
            None if input_details is None else input_details.get("cached_tokens"),
            field="usage.input_tokens_details.cached_tokens",
        ),
        reasoning_tokens=_optional_non_negative_int(
            None if output_details is None else output_details.get("reasoning_tokens"),
            field="usage.output_tokens_details.reasoning_tokens",
        ),
    )


def _extract_output_text(payload: Mapping[str, Any]) -> str:
    output = payload.get("output")
    if not isinstance(output, list):
        raise ResponsesProtocolError("response.output must be an array")

    assistant_messages = 0
    text_parts: list[str] = []
    for item in output:
        if not isinstance(item, Mapping):
            raise ResponsesProtocolError("response.output items must be objects")
        item_type = item.get("type")

        # Reasoning items are expected for reasoning models and carry no executable authority.
        if item_type == "reasoning":
            continue
        if item_type != "message":
            raise ResponsesProtocolError(
                f"unexpected executable/non-message response output item: {item_type!r}"
            )
        if item.get("role") != "assistant":
            raise ResponsesProtocolError("response message role must be assistant")
        assistant_messages += 1
        content = item.get("content")
        if not isinstance(content, list):
            raise ResponsesProtocolError("assistant message content must be an array")
        for part in content:
            if not isinstance(part, Mapping):
                raise ResponsesProtocolError("assistant content items must be objects")
            part_type = part.get("type")
            if part_type == "refusal":
                raise ResponsesProtocolError("model returned a refusal instead of structured output")
            if part_type != "output_text":
                raise ResponsesProtocolError(
                    f"unexpected assistant content type: {part_type!r}"
                )
            text = part.get("text")
            if not isinstance(text, str) or not text:
                raise ResponsesProtocolError("output_text.text must be a non-empty string")
            text_parts.append(text)

    if assistant_messages != 1:
        raise ResponsesProtocolError("exactly one assistant message is required")
    if not text_parts:
        raise ResponsesProtocolError("structured response contains no output_text")
    return "".join(text_parts)


def _effective_model_matches(requested: str, effective: str) -> bool:
    return effective == requested or effective.startswith(requested + "-")


def parse_responses_payload(
    payload: Mapping[str, Any],
    *,
    requested_model: str,
    latency_ms: int,
) -> ResponsesCallResult:
    if not isinstance(payload, Mapping):
        raise ResponsesProtocolError("OpenAI Responses payload must be an object")
    if payload.get("status") != "completed":
        raise ResponsesProtocolError("OpenAI Responses status must be completed")
    if payload.get("error") is not None:
        raise ResponsesProtocolError("completed OpenAI response unexpectedly contains an error")

    response_id = payload.get("id")
    effective_model = payload.get("model")
    if not isinstance(response_id, str) or not response_id:
        raise ResponsesProtocolError("response.id must be a non-empty string")
    if not isinstance(effective_model, str) or not effective_model:
        raise ResponsesProtocolError("response.model must be a non-empty string")
    if not _effective_model_matches(requested_model, effective_model):
        raise ResponsesProtocolError("effective model does not match requested model family")

    if payload.get("store") is not False:
        raise ResponsesProtocolError("runtime response does not prove store=false")
    returned_tools = payload.get("tools")
    if returned_tools is not None and returned_tools != []:
        raise ResponsesProtocolError("runtime response unexpectedly reports enabled tools")

    output_text = _extract_output_text(payload)
    return ResponsesCallResult(
        runtime_version=RUNTIME_VERSION,
        response_id=response_id,
        requested_model=requested_model,
        effective_model=effective_model,
        output_text=output_text,
        output_hash=canonical_sha256({"output_text": output_text}),
        usage=_extract_usage(payload),
        latency_ms=latency_ms,
    )


def _assert_request_invariants(request: PlannerRequestEnvelope) -> None:
    payload = request.request_payload
    if payload.get("store") is not False:
        raise ResponsesRuntimeError("planner runtime requires store=false")
    if payload.get("tools") != []:
        raise ResponsesRuntimeError("planner runtime requires an empty tools array")
    if payload.get("parallel_tool_calls") is not False:
        raise ResponsesRuntimeError("planner runtime requires parallel_tool_calls=false")
    text = payload.get("text")
    if not isinstance(text, Mapping):
        raise ResponsesRuntimeError("planner runtime requires text configuration")
    fmt = text.get("format")
    if not isinstance(fmt, Mapping):
        raise ResponsesRuntimeError("planner runtime requires structured output format")
    if fmt.get("type") != "json_schema" or fmt.get("strict") is not True:
        raise ResponsesRuntimeError("planner runtime requires strict json_schema output")


def execute_planner_runtime(
    *,
    request: PlannerRequestEnvelope,
    planner_input: PlannerInputEnvelope,
    research_policy: ResearchPolicy,
    api_key: str,
    transport: ResponsesTransport | None = None,
) -> PlannerRuntimeResult:
    _assert_request_invariants(request)
    key = validate_openai_api_key(api_key)
    runtime_transport = StdlibResponsesTransport() if transport is None else transport

    start = perf_counter_ns()
    raw_payload = runtime_transport.post(payload=request.request_payload, api_key=key)
    latency_ms = max(0, (perf_counter_ns() - start) // 1_000_000)

    requested_model = request.request_payload.get("model")
    if not isinstance(requested_model, str) or not requested_model:
        raise ResponsesRuntimeError("planner request model must be a non-empty string")

    call = parse_responses_payload(
        raw_payload,
        requested_model=requested_model,
        latency_ms=latency_ms,
    )
    plan = parse_planner_output(
        call.output_text,
        planner_input=planner_input,
        research_policy=research_policy,
    )
    return PlannerRuntimeResult(
        call=call,
        plan=plan,
        plan_hash=canonical_sha256(plan),
    )