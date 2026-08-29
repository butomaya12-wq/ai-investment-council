from __future__ import annotations

import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path("scripts/b4_initial_model_eval.py")


def _module():
    spec = spec_from_file_location("b4_initial_model_eval_paid_receipt_test_module", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _SuccessTransport:
    def post(self, *, payload, api_key):
        return {"id": "resp_test"}


class _FailureTransport:
    def post(self, *, payload, api_key):
        raise RuntimeError("synthetic network failure")


def _cost_artifact():
    return {
        "artifact_hash": "c" * 64,
        "pricing_version": "OPENAI_TEXT_PRICING_2026_08_29",
        "pricing_hash": "5" * 64,
        "pricing_as_of_date": "2026-08-29",
    }


def _request():
    return SimpleNamespace(
        prompt_version="BULL_INITIAL_vB4_0_1",
        prompt_hash="1" * 64,
        schema_version="P-B4-PROMPTS-v0.2:INITIAL_OUTPUT_SCHEMA",
        input_hash="2" * 64,
        request_hash="3" * 64,
        request_payload={"model": "gpt-5.6-luna", "max_output_tokens": 4096},
    )


def _candidate():
    return SimpleNamespace(
        candidate_key="L1",
        model="gpt-5.6-luna",
        reasoning_effort="medium",
    )


def _case():
    return SimpleNamespace(
        case_id="E1",
        name="test case",
        lane=SimpleNamespace(value="BULL"),
        critical_safety=False,
    )


def _run(*, response_received: bool):
    return SimpleNamespace(
        effective_model="gpt-5.6-luna" if response_received else None,
        model_calls=1 if response_received else 0,
        passed=response_received,
        response_id="resp_test" if response_received else None,
        input_tokens=100,
        cached_tokens=10,
        output_tokens=20,
        reasoning_tokens=5,
        latency_ms=123,
        estimated_cost_usd=__import__("decimal").Decimal("0.001"),
        findings=() if response_received else ("RuntimeError: synthetic network failure",),
        output_hash="4" * 64 if response_received else None,
        result_hash="6" * 64,
    )


def test_dispatch_tracker_distinguishes_attempt_from_provider_response() -> None:
    module = _module()
    success = module.DispatchTrackingTransport(_SuccessTransport())
    assert success.post(payload={}, api_key="x") == {"id": "resp_test"}
    assert success.dispatch_attempts == 1
    assert success.provider_responses == 1

    failure = module.DispatchTrackingTransport(_FailureTransport())
    with pytest.raises(RuntimeError):
        failure.post(payload={}, api_key="x")
    assert failure.dispatch_attempts == 1
    assert failure.provider_responses == 0


def test_paid_receipt_marks_unknown_dispatch_cost_incomplete() -> None:
    module = _module()
    tracker = module.DispatchTrackingTransport(_FailureTransport())
    tracker.dispatch_attempts = 1
    receipt = module._build_paid_call_receipt(
        run_id="RUN1",
        dispatch_index=1,
        dispatch_started_at_utc="2026-08-29T19:10:00Z",
        dispatch_finished_at_utc="2026-08-29T19:10:01Z",
        authorization_artifact_hash="a" * 64,
        cost=_cost_artifact(),
        approved_ceiling=__import__("decimal").Decimal("4.6269612"),
        owner_approval_id="OWNER_APPROVAL_TEST",
        owner_approval_at_utc="2026-08-29T19:09:00Z",
        code_commit_sha="b" * 40,
        candidate=_candidate(),
        case=_case(),
        request=_request(),
        run=_run(response_received=False),
        tracker=tracker,
    )
    assert receipt["dispatch_attempted"] is True
    assert receipt["provider_response_received"] is False
    assert receipt["actual_cost_usd"] is None
    assert receipt["cost_receipt_status"] == "INCOMPLETE"
    assert receipt["case_result"] == "BLOCKED_UNKNOWN_PROVIDER_DISPATCH"
    assert len(receipt["receipt_hash"]) == 64


def test_successful_paid_receipt_is_complete_and_durable(tmp_path: Path) -> None:
    module = _module()
    tracker = module.DispatchTrackingTransport(_SuccessTransport())
    tracker.dispatch_attempts = 1
    tracker.provider_responses = 1
    receipt = module._build_paid_call_receipt(
        run_id="RUN1",
        dispatch_index=1,
        dispatch_started_at_utc="2026-08-29T19:10:00Z",
        dispatch_finished_at_utc="2026-08-29T19:10:01Z",
        authorization_artifact_hash="a" * 64,
        cost=_cost_artifact(),
        approved_ceiling=__import__("decimal").Decimal("4.6269612"),
        owner_approval_id="OWNER_APPROVAL_TEST",
        owner_approval_at_utc="2026-08-29T19:09:00Z",
        code_commit_sha="b" * 40,
        candidate=_candidate(),
        case=_case(),
        request=_request(),
        run=_run(response_received=True),
        tracker=tracker,
    )
    assert receipt["provider_response_received"] is True
    assert receipt["actual_cost_usd"] == "0.001"
    assert receipt["cost_receipt_status"] == "COMPLETE"
    assert receipt["case_result"] == "PASS"

    journal = tmp_path / "receipts.jsonl"
    module._append_receipt(journal, receipt)
    saved = json.loads(journal.read_text(encoding="utf-8").strip())
    assert saved["receipt_hash"] == receipt["receipt_hash"]


def test_paid_secret_load_occurs_after_durable_authorization_and_receipt_contract() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    authorization_write = source.index("_write_durable(args.authorization_output, authorization)")
    secret_import = source.index(
        "from aic.research.runtime import StdlibResponsesTransport, load_openai_api_key"
    )
    secret_load = source.index("api_key = load_openai_api_key()")
    assert authorization_write < secret_import < secret_load
    assert "os.fsync" in source
    assert "COST_RECEIPT_INCOMPLETE_PROVIDER_DISPATCH_UNKNOWN" in source
    assert 'parser.add_argument("--owner-approval-id")' in source
    assert 'parser.add_argument("--owner-approval-at-utc")' in source
    assert "dispatch_attempts" in source
    assert "paid_call_receipt_hashes" in source
