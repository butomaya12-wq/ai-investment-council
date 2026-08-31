from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from aic.domain.canonical import canonical_sha256
from aic.council import reopen_rebuttal_auth_rejection_recovery as recovery
from aic.council import reopen_rebuttal_credential_probe as probe


def _recovery_plan():
    return recovery.build_recovery_plan(code_commit_sha="a" * 40)


def test_credential_probe_dry_is_zero_call_and_zero_read() -> None:
    plan = _recovery_plan()
    dry = probe.build_dry_artifact(code_commit_sha="b" * 40, recovery_plan=plan)
    assert dry["status"] == probe.DRY_STATUS
    assert dry["probe_http_method"] == "GET"
    assert dry["probe_endpoint"].endswith("/v1/models/gpt-5.6-sol")
    assert dry["provider_reads_max_if_later_approved"] == 1
    assert dry["provider_reads"] == 0
    assert dry["model_calls"] == 0
    assert dry["responses_generation_calls_max"] == 0
    assert dry["probe_provider_read_authorized"] is False
    assert dry["generation_dispatch_authorized"] is False
    assert dry["artifact_hash"] == canonical_sha256(dry, exclude_fields=("artifact_hash",))


def test_credential_probe_authorization_rejects_future_owner_approval() -> None:
    plan = _recovery_plan()
    dry = probe.build_dry_artifact(code_commit_sha="b" * 40, recovery_plan=plan)
    with pytest.raises(probe.B4ReopenRebuttalCredentialProbeError, match="cannot postdate"):
        probe.build_authorization(
            code_commit_sha="b" * 40,
            created_at_utc="2026-08-31T03:40:00Z",
            owner_approval_id="OWNER-PROBE-V01",
            owner_approval_at_utc="2026-08-31T03:40:01Z",
            approve_recovery_plan_hash=plan["artifact_hash"],
            approve_dry_hash=dry["artifact_hash"],
            recovery_plan=plan,
            dry_artifact=dry,
            journal_path="probe.jsonl",
        )


def test_probe_attempt_consumes_only_probe_authority() -> None:
    attempt = probe.build_attempt_event(
        authorization_hash="f" * 64,
        started_at_utc="2026-08-31T03:40:00Z",
    )
    assert attempt["event_type"] == "CREDENTIAL_PROBE_HTTP_ATTEMPT"
    assert attempt["provider_read_attempt"] == 1
    assert attempt["authorization_consumed_by_this_attempt"] is True
    assert attempt["model_calls"] == 0
    assert attempt["responses_generation_calls"] == 0
    assert attempt["generation_dispatch_authorized"] is False
    assert attempt["event_hash"] == canonical_sha256(attempt, exclude_fields=("event_hash",))


class _FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class _FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200, request_id: str = "req_probe") -> None:
        self._body = body
        self.status = status
        self.headers = _FakeHeaders({"x-request-id": request_id})

    def read(self, size: int | None = None) -> bytes:
        return self._body if size is None else self._body[:size]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_probe_success_requires_exact_model_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        probe,
        "urlopen",
        lambda request, timeout: _FakeResponse(b'{"id":"gpt-5.6-sol","object":"model"}'),
    )
    result = probe.probe_model_metadata(api_key="sk-test")
    assert result["http_response_received"] is True
    assert result["http_status_code"] == 200
    assert result["model_id"] == "gpt-5.6-sol"
    assert result["object"] == "model"
    assert result["validation_status"] == "PASS"


def test_probe_401_is_http_auth_rejection_not_network_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(request, timeout):
        raise HTTPError(
            probe.ENDPOINT,
            401,
            "Unauthorized",
            _FakeHeaders({"x-request-id": "req_bad_key"}),
            BytesIO(b'{"error":{"type":"invalid_request_error","code":"invalid_api_key"}}'),
        )

    monkeypatch.setattr(probe, "urlopen", _raise)
    result = probe.probe_model_metadata(api_key="sk-wrong")
    assert result["http_response_received"] is True
    assert result["http_status_code"] == 401
    assert result["request_id"] == "req_bad_key"
    assert result["error_type"] == "invalid_request_error"
    assert result["error_code"] == "invalid_api_key"
    assert result["validation_status"] == "FAIL"


def test_probe_url_error_is_distinct_network_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        probe,
        "urlopen",
        lambda request, timeout: (_ for _ in ()).throw(URLError("offline")),
    )
    result = probe.probe_model_metadata(api_key="sk-test")
    assert result["http_response_received"] is False
    assert result["http_status_code"] is None
    assert result["error_type"] == "NETWORK_URL_ERROR"
    assert result["validation_status"] == "FAIL"


def test_pass_receipt_and_final_never_authorize_generation() -> None:
    result = {
        "http_response_received": True,
        "http_status_code": 200,
        "request_id": "req_probe",
        "error_type": None,
        "error_code": None,
        "model_id": "gpt-5.6-sol",
        "object": "model",
        "validation_status": "PASS",
    }
    receipt = probe.build_result_receipt(
        authorization_hash="f" * 64,
        attempt_hash="e" * 64,
        finished_at_utc="2026-08-31T03:40:01Z",
        probe_result=result,
    )
    final = probe.build_final_artifact(
        code_commit_sha="b" * 40,
        recovery_plan_hash="a" * 64,
        dry_hash="c" * 64,
        authorization_hash="f" * 64,
        attempt_hash="e" * 64,
        receipt=receipt,
    )
    assert final["status"] == probe.PASS_STATUS
    assert final["provider_reads"] == 1
    assert final["model_calls"] == 0
    assert final["responses_generation_calls"] == 0
    assert final["fresh_generation_dispatch_authorized"] is False
    assert final["new_generation_owner_approval_required"] is True
    assert final["next_gate"] == probe.NEXT_GATE_PASS
    assert final["artifact_hash"] == canonical_sha256(final, exclude_fields=("artifact_hash",))
