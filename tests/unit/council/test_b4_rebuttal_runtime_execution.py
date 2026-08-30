from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from aic.council import rebuttal_runtime_execution as execution
from aic.domain.canonical import canonical_sha256


def _raw_usage() -> dict:
    return {
        "id": "resp_test",
        "model": "gpt-5.6-sol",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "input_tokens_details": {
                "cached_tokens": 10,
                "cache_write_tokens": 5,
            },
            "output_tokens_details": {"reasoning_tokens": 4},
        },
    }


def _item(candidate_id: str = "NVDA"):
    request = SimpleNamespace(
        request_hash="a" * 64,
        request_payload={
            "model": "gpt-5.6-sol",
            "reasoning": {"effort": "medium"},
            "max_output_tokens": 6144,
        },
    )
    return SimpleNamespace(
        candidate_id=candidate_id,
        context_hash="b" * 64,
        request=request,
        bundle=SimpleNamespace(candidate_id=candidate_id),
        model_input={"candidate_model_input": {"material_claims": [], "computed_values": []}},
        required_unknown_refs=("GAP1",),
    )


class _Transport:
    def __init__(self, value=None, error: Exception | None = None):
        self.value = value
        self.error = error

    def post(self, *, payload, api_key):
        assert api_key == "key"
        if self.error is not None:
            raise self.error
        return self.value


def test_execute_runtime_item_persists_strict_cost_and_validated_record(monkeypatch) -> None:
    item = _item()
    proposal = SimpleNamespace(
        model_dump=lambda **_: {"candidate_id": "NVDA"}
    )
    fake_call = SimpleNamespace(
        response_id="resp_test",
        effective_model="gpt-5.6-sol",
        output_hash="c" * 64,
    )
    monkeypatch.setattr(
        execution,
        "parse_council_responses_payload",
        lambda raw, request, latency_ms: (fake_call, proposal),
    )
    monkeypatch.setattr(execution, "RebuttalBundleDraft", type(proposal))
    monkeypatch.setattr(
        execution,
        "_candidate_initial_records",
        lambda initial_freeze, candidate_id: ({}, {}, {}),
    )
    monkeypatch.setattr(
        execution,
        "promote_rebuttal_bundle",
        lambda *args, **kwargs: SimpleNamespace(),
    )
    processed = {
        "candidate_id": "NVDA",
        "record_hash": "d" * 64,
    }
    monkeypatch.setattr(execution, "_processed_record", lambda **kwargs: processed)
    monkeypatch.setattr(execution, "validate_rebuttal_processed_record", lambda raw: None)
    monkeypatch.setattr(
        execution,
        "actual_cost_usd",
        lambda raw, model, pricing: Decimal("0.0042"),
    )

    run = execution.execute_rebuttal_runtime_item_once(
        item,
        initial_freeze={},
        api_key="key",
        transport=_Transport(_raw_usage()),
        pricing={},
        frozen_at=datetime(2026, 8, 30, tzinfo=UTC),
    )
    assert run.model_calls == 1
    assert run.validation_status == "PASS"
    assert run.validation_error is None
    assert run.cost_receipt_status == "COMPLETE"
    assert run.actual_cost_usd == Decimal("0.0042")
    assert run.input_tokens == 100
    assert run.cached_tokens == 10
    assert run.cache_write_tokens == 5
    assert run.output_tokens == 20
    assert run.reasoning_tokens == 4
    assert run.processed_record == processed


def test_execute_runtime_item_unknown_dispatch_fails_closed_without_fake_model_call(monkeypatch) -> None:
    run = execution.execute_rebuttal_runtime_item_once(
        _item(),
        initial_freeze={},
        api_key="key",
        transport=_Transport(error=RuntimeError("network unknown")),
        pricing={},
        frozen_at=datetime(2026, 8, 30, tzinfo=UTC),
    )
    assert run.model_calls == 0
    assert run.validation_status == "FAIL"
    assert "network unknown" in (run.validation_error or "")
    assert run.cost_receipt_status == "INCOMPLETE"
    assert run.actual_cost_usd is None
    assert run.processed_record is None


def test_usage_requires_cache_write_counter_for_complete_receipt() -> None:
    raw = _raw_usage()
    del raw["usage"]["input_tokens_details"]["cache_write_tokens"]
    with pytest.raises(execution.RebuttalRuntimeExecutionError, match="cache_write_tokens"):
        execution._usage_counts(raw)


def test_build_freeze_requires_exact_three_records_and_sets_hard_barrier(monkeypatch) -> None:
    candidates = ("NVDA", "MSFT", "META")
    freeze = SimpleNamespace(
        artifact_hash="f" * 64,
        candidate_order=candidates,
    )
    runtime_preflight = {
        "artifact_hash": "1" * 64,
        "code_commit_sha": "2" * 40,
        "initial_council_freeze_artifact_hash": execution.EXPECTED_INITIAL_FREEZE_HASH,
        "source_request_preflight_artifact_hash": "3" * 64,
        "request_manifest_hash": "4" * 64,
        "selected_model_authority_selection_hash": execution.EXPECTED_SELECTION_HASH,
        "candidate_order": list(candidates),
    }
    cost_preflight = {
        "artifact_hash": "5" * 64,
        "runtime_request_preflight_artifact_hash": runtime_preflight["artifact_hash"],
    }
    selection = {
        "selection_hash": execution.EXPECTED_SELECTION_HASH,
        "selected_candidate": dict(execution.EXPECTED_SELECTED),
    }
    records = []
    for index, candidate in enumerate(candidates, start=1):
        records.append(
            {
                "candidate_id": candidate,
                "rebuttal_bundle_id": f"REB_{candidate}",
                "rebuttal_bundle_hash": str(index) * 64,
                "research_reopen_required": candidate == "MSFT",
            }
        )

    monkeypatch.setattr(
        execution,
        "verify_rebuttal_runtime_request_preflight",
        lambda _: runtime_preflight["artifact_hash"],
    )
    monkeypatch.setattr(
        execution,
        "verify_rebuttal_runtime_cost_preflight",
        lambda _: cost_preflight["artifact_hash"],
    )
    monkeypatch.setattr(
        execution,
        "verify_rebuttal_selected_model_authority_v02",
        lambda _: execution.EXPECTED_SELECTION_HASH,
    )
    monkeypatch.setattr(execution, "validate_rebuttal_processed_record", lambda _: None)

    artifact = execution.build_rebuttal_council_freeze_artifact(
        processed_records=tuple(records),
        freeze=freeze,
        runtime_preflight=runtime_preflight,
        cost_preflight=cost_preflight,
        selection_authority=selection,
        run_id="RUN1",
        paid_authorization_artifact_hash="6" * 64,
        receipt_manifest_hash="7" * 64,
        actual_cost_usd_total=Decimal("0.12"),
    )
    assert artifact["status"] == execution.REBUTTAL_COUNCIL_FROZEN_STATUS
    assert artifact["rebuttal_freeze_barrier"] is True
    assert artifact["rebuttal_bundle_count"] == 3
    assert artifact["candidate_order"] == ["NVDA", "MSFT", "META"]
    assert artifact["research_reopen_required_candidates"] == ["MSFT"]
    assert artifact["dispatch_attempts"] == 3
    assert artifact["model_calls"] == 3
    assert artifact["automatic_repair_calls"] == 0
    assert artifact["judge_authorized"] is False
    assert artifact["rerun_authorized"] is False
    assert artifact["broker_writes"] == 0
    assert artifact["alpaca_orders"] == 0
    assert artifact["live_money"] == "PROHIBITED"
    assert artifact["artifact_hash"] == canonical_sha256(
        artifact, exclude_fields=("artifact_hash",)
    )

    with pytest.raises(
        execution.RebuttalRuntimeExecutionError,
        match="exactly three processed bundles",
    ):
        execution.build_rebuttal_council_freeze_artifact(
            processed_records=tuple(records[:2]),
            freeze=freeze,
            runtime_preflight=runtime_preflight,
            cost_preflight=cost_preflight,
            selection_authority=selection,
            run_id="RUN2",
            paid_authorization_artifact_hash="6" * 64,
            receipt_manifest_hash="7" * 64,
            actual_cost_usd_total=Decimal("0.08"),
        )
