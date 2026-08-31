from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from aic.domain.canonical import canonical_sha256
from aic.council import reopen_rebuttal_runtime as rt


def _item(index: int, candidate: str, request_hash: str, context_hash: str):
    request = SimpleNamespace(
        request_hash=request_hash,
        request_payload={
            "model": "gpt-5.6-sol",
            "reasoning": {"effort": "medium"},
            "max_output_tokens": 6144,
        },
    )
    return SimpleNamespace(
        dispatch_index=index,
        candidate_id=candidate,
        context_hash=context_hash,
        request=request,
        request_body_utf8_bytes=1000 + index,
    )


def _bound():
    return rt.BoundReopenRebuttalRuntime(
        cost_preflight={"artifact_hash": rt.EXPECTED_COST_PREFLIGHT_HASH},
        selection_authority={"selection_hash": rt.EXPECTED_SELECTION_HASH},
        recovered_initial_freeze={},
        plan=(
            _item(1, "NVDA", "1" * 64, "a" * 64),
            _item(2, "MSFT", "2" * 64, "b" * 64),
            _item(3, "META", "3" * 64, "c" * 64),
        ),
        pricing={},
    )


def test_frozen_rebuttal_runtime_constants() -> None:
    assert rt.EXPECTED_CALLS == 3
    assert rt.EXPECTED_MAX_OUTPUT_TOKENS == 6144
    assert rt.EXPECTED_COST_CEILING_USD == Decimal("1.73851")
    assert rt.EXPECTED_SELECTION_HASH == "8db38779171e0dcfc2e0325581192116b17adf98a1140950ffcbe5ce4698a882"
    assert rt.EXPECTED_REQUEST_MANIFEST_HASH == "ff423f97dc2398befa25dd8bedbfd92bc46562e56c302caa67ddb2e1c8f50693"


def test_dry_artifact_is_zero_call_and_never_authorizes_paid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rt, "verify_cost_preflight", lambda _: rt.EXPECTED_COST_PREFLIGHT_HASH)
    monkeypatch.setattr(rt, "verify_selection_authority", lambda _: rt.EXPECTED_SELECTION_HASH)
    dry = rt.build_dry_artifact(code_commit_sha="d" * 40, bound=_bound())
    assert dry["status"] == rt.DRY_STATUS
    assert dry["planned_paid_calls_max"] == 3
    assert dry["max_output_tokens_per_call"] == 6144
    assert dry["cost_ceiling_usd"] == "1.73851"
    assert dry["paid_dispatch_authorized"] is False
    assert dry["model_calls"] == 0
    assert dry["provider_reads"] == 0
    assert dry["automatic_repair_calls_authorized"] == 0
    assert dry["automatic_retries"] == 0
    assert dry["judge_authorized"] is False
    assert dry["rebuttal_rerun_authorized"] is False
    assert dry["artifact_hash"] == canonical_sha256(dry, exclude_fields=("artifact_hash",))


def test_paid_authorization_rejects_future_owner_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    bound = _bound()
    monkeypatch.setattr(rt, "verify_dry_artifact", lambda *args, **kwargs: "e" * 64)
    with pytest.raises(rt.B4ReopenRebuttalRuntimeError, match="cannot postdate"):
        rt.build_paid_authorization(
            code_commit_sha="d" * 40,
            git_worktree_clean=True,
            created_at_utc="2026-08-30T21:00:00Z",
            run_id="RUN",
            owner_approval_id="OWNER-B4-REOPEN-REBUTTAL-V01",
            owner_approval_at_utc="2026-08-30T21:00:01Z",
            approve_cost_artifact_hash=rt.EXPECTED_COST_PREFLIGHT_HASH,
            approve_request_manifest_hash=rt.EXPECTED_REQUEST_MANIFEST_HASH,
            approve_dry_artifact_hash="e" * 64,
            approve_max_usd="1.73851",
            dry_artifact={},
            bound=bound,
            receipt_journal_path="journal.jsonl",
        )


def test_attempt_event_is_hash_bound_and_consumes_authority() -> None:
    item = _bound().plan[0]
    event = rt.build_attempt_event(
        run_id="RUN",
        item=item,
        authorization_hash="f" * 64,
        started_at_utc="2026-08-30T21:00:00Z",
    )
    assert event["event_type"] == "REBUTTAL_PROVIDER_DISPATCH_ATTEMPT"
    assert event["authorization_consumed_by_this_attempt"] is True
    assert event["dispatch_index"] == 1
    assert event["request_hash"] == "1" * 64
    assert event["max_output_tokens"] == 6144
    assert event["event_hash"] == canonical_sha256(event, exclude_fields=("event_hash",))


def test_result_receipt_persists_validated_processed_record_for_local_finalize() -> None:
    item = _bound().plan[0]
    record = {"record_hash": "9" * 64, "candidate_id": "NVDA", "request_hash": "1" * 64, "context_hash": "a" * 64}
    run = SimpleNamespace(
        processed_record=record,
        actual_cost_usd=Decimal("0.12"),
        response_id="resp_1",
        effective_model="gpt-5.6-sol",
        input_tokens=100,
        cached_tokens=0,
        cache_write_tokens=0,
        output_tokens=50,
        reasoning_tokens=10,
        latency_ms=12,
        cost_receipt_status="COMPLETE",
        validation_status="PASS",
        validation_error=None,
        output_hash="8" * 64,
        structured_output_hash="7" * 64,
    )
    receipt = rt.build_result_receipt(
        run_id="RUN",
        item=item,
        authorization_hash="f" * 64,
        attempt_hash="6" * 64,
        started_at_utc="2026-08-30T21:00:00Z",
        finished_at_utc="2026-08-30T21:00:01Z",
        provider_response_received=True,
        run=run,
    )
    assert receipt["validation_status"] == "PASS"
    assert receipt["validated_processed_record_persisted"] is True
    assert receipt["local_finalize_replayable"] is True
    assert receipt["processed_record"] == record
    assert receipt["raw_provider_response_persisted"] is False
    assert receipt["provider_output_text_persisted"] is False
    assert receipt["receipt_hash"] == canonical_sha256(receipt, exclude_fields=("receipt_hash",))


def test_durable_finalize_requires_exact_three_pass_receipts(monkeypatch: pytest.MonkeyPatch) -> None:
    bound = _bound()
    monkeypatch.setattr(rt, "validate_rebuttal_processed_record", lambda _: None)
    auth_hash = "f" * 64
    events = []
    for item in bound.plan:
        attempt = rt.build_attempt_event(
            run_id="RUN",
            item=item,
            authorization_hash=auth_hash,
            started_at_utc=f"2026-08-30T21:00:0{item.dispatch_index}Z",
        )
        events.append(attempt)
        record = {
            "record_hash": f"{item.dispatch_index}" * 64,
            "candidate_id": item.candidate_id,
            "request_hash": item.request.request_hash,
            "context_hash": item.context_hash,
        }
        run = SimpleNamespace(
            processed_record=record,
            actual_cost_usd=Decimal("0.1"),
            response_id=f"resp_{item.dispatch_index}",
            effective_model="gpt-5.6-sol",
            input_tokens=100,
            cached_tokens=0,
            cache_write_tokens=0,
            output_tokens=50,
            reasoning_tokens=10,
            latency_ms=12,
            cost_receipt_status="COMPLETE",
            validation_status="PASS",
            validation_error=None,
            output_hash="8" * 64,
            structured_output_hash="7" * 64,
        )
        receipt = rt.build_result_receipt(
            run_id="RUN",
            item=item,
            authorization_hash=auth_hash,
            attempt_hash=attempt["event_hash"],
            started_at_utc=f"2026-08-30T21:00:0{item.dispatch_index}Z",
            finished_at_utc=f"2026-08-30T21:00:1{item.dispatch_index}Z",
            provider_response_received=True,
            run=run,
        )
        events.append(receipt)
    finalize = rt.durable_finalize_inputs_from_journal(
        events=events,
        bound=bound,
        authorization_hash=auth_hash,
    )
    assert finalize is not None
    receipt_hashes, records, total = finalize
    assert len(receipt_hashes) == 3
    assert len(records) == 3
    assert total == Decimal("0.3")
    assert rt.durable_finalize_inputs_from_journal(
        events=events[:-2],
        bound=bound,
        authorization_hash=auth_hash,
    ) is None
