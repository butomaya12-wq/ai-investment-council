from __future__ import annotations

import io
from email.message import Message
from urllib.error import HTTPError

import pytest

from aic.research import runtime
from aic.research.runtime import ResponsesHttpError, StdlibResponsesTransport


def _http_error(*, body: bytes, headers: dict[str, str]) -> HTTPError:
    message = Message()
    for key, value in headers.items():
        message[key] = value
    return HTTPError(
        runtime.OPENAI_RESPONSES_ENDPOINT,
        429,
        "Too Many Requests",
        message,
        io.BytesIO(body),
    )


def test_http_429_surfaces_only_allowlisted_machine_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    secret_marker = "SECRET_PROMPT_TEXT_MUST_NEVER_SURFACE"
    exc = _http_error(
        body=(
            '{"error":{"message":"' + secret_marker + '",'
            '"type":"tokens","code":"rate_limit_exceeded"}}'
        ).encode("utf-8"),
        headers={
            "x-request-id": "req_safe_123",
            "retry-after": "2",
            "x-ratelimit-limit-tokens": "500000",
            "x-ratelimit-remaining-tokens": "12000",
            "x-ratelimit-reset-tokens": "1.5s",
        },
    )

    def fail(*args, **kwargs):  # noqa: ANN002, ANN003
        raise exc

    monkeypatch.setattr(runtime, "urlopen", fail)
    transport = StdlibResponsesTransport()

    with pytest.raises(ResponsesHttpError) as caught:
        transport.post(payload={"model": "gpt-5.6-sol", "input": secret_marker}, api_key="test-key")

    error = caught.value
    rendered = str(error)
    assert error.status_code == 429
    assert error.diagnostics["error_type"] == "tokens"
    assert error.diagnostics["error_code"] == "rate_limit_exceeded"
    assert error.diagnostics["request_id"] == "req_safe_123"
    assert error.diagnostics["retry_after"] == "2"
    assert error.diagnostics["ratelimit_limit_tokens"] == "500000"
    assert error.diagnostics["ratelimit_remaining_tokens"] == "12000"
    assert error.diagnostics["ratelimit_reset_tokens"] == "1.5s"
    assert secret_marker not in rendered
    assert secret_marker not in repr(error.diagnostics)


def test_http_429_billing_code_is_preserved_without_provider_message(monkeypatch: pytest.MonkeyPatch) -> None:
    exc = _http_error(
        body=b'{"error":{"message":"billing detail omitted","type":"insufficient_quota","code":"project_spend_limit_exceeded"}}',
        headers={"x-request-id": "req_billing_456"},
    )

    def fail(*args, **kwargs):  # noqa: ANN002, ANN003
        raise exc

    monkeypatch.setattr(runtime, "urlopen", fail)

    with pytest.raises(ResponsesHttpError) as caught:
        StdlibResponsesTransport().post(payload={"model": "gpt-5.6-sol"}, api_key="test-key")

    error = caught.value
    assert error.diagnostics["error_type"] == "insufficient_quota"
    assert error.diagnostics["error_code"] == "project_spend_limit_exceeded"
    assert "billing detail omitted" not in str(error)
