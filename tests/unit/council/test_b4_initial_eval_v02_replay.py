from __future__ import annotations

from decimal import Decimal
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sys
from types import SimpleNamespace


SCRIPT = Path("scripts/b4_initial_model_eval_v02.py")


def _module():
    name = "b4_initial_model_eval_v02_test_module"
    spec = spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(name, None)


def _cost_artifact():
    return {
        "artifact_hash": "c" * 64,
        "pricing_version": "OPENAI_TEXT_PRICING_2026_08_29",
        "pricing_hash": "5" * 64,
        "pricing_as_of_date": "2026-08-29",
    }


def _request():
    return SimpleNamespace(
        prompt_version="BEAR_INITIAL_vB4_0_2",
        prompt_hash="1" * 64,
        schema_version="P-B4-PROMPTS-v0.2:INITIAL_OUTPUT_SCHEMA",
        input_hash="2" * 64,
        request_hash="3" * 64,
        request_payload={"model": "gpt-5.6-terra", "max_output_tokens": 4096},
    )


def _candidate():
    return SimpleNamespace(
        candidate_key="L2",
        model="gpt-5.6-terra",
        reasoning_effort="low",
    )


def _case():
    return SimpleNamespace(
        case_id="E6",
        name="Bear narrative contradicted by stronger evidence",
        lane=SimpleNamespace(value="BEAR"),
        critical_safety=True,
    )


def _run():
    return SimpleNamespace(
        effective_model="gpt-5.6-terra",
        model_calls=1,
        passed=True,
        response_id="resp_test",
        input_tokens=100,
        cached_tokens=0,
        output_tokens=20,
        reasoning_tokens=5,
        latency_ms=123,
        estimated_cost_usd=Decimal("0.001"),
        findings=(),
        output_hash="4" * 64,
        result_hash="6" * 64,
    )


def test_v02_runner_declares_new_evidence_contract() -> None:
    module = _module()
    assert module.EVAL_VERSION == "B4_INITIAL_MODEL_EVAL_v0_2"
    assert module.ARTIFACT_VERSION == "B4_INITIAL_MODEL_EVAL_ARTIFACT_v0_3"
    assert module.PAID_CALL_RECEIPT_VERSION == "B4_INITIAL_PAID_CALL_RECEIPT_v0_2"
    assert module.legacy.INITIAL_EVAL_VERSION == module.EVAL_VERSION
    assert module.legacy.PAID_CALL_RECEIPT_VERSION == module.PAID_CALL_RECEIPT_VERSION
    assert module.legacy.DispatchTrackingTransport is module.ReplayableDispatchTrackingTransport


def test_paid_receipt_persists_replayable_structured_output(monkeypatch) -> None:
    module = _module()
    structured = {
        "opinion_id": "OP_E6",
        "candidate_id": "EVAL_E6",
        "lane": "BEAR",
        "proposed_claims": [],
    }
    monkeypatch.setattr(
        module,
        "parse_responses_payload",
        lambda raw, requested_model, latency_ms: SimpleNamespace(
            output_text=json.dumps(structured)
        ),
    )
    tracker = module.ReplayableDispatchTrackingTransport(SimpleNamespace())
    tracker.dispatch_attempts = 1
    tracker.provider_responses = 1
    tracker.last_provider_response = {"id": "resp_test"}

    receipt = module._build_paid_call_receipt_v02(
        run_id="RUN1",
        dispatch_index=1,
        dispatch_started_at_utc="2026-08-30T00:00:00Z",
        dispatch_finished_at_utc="2026-08-30T00:00:01Z",
        authorization_artifact_hash="a" * 64,
        cost=_cost_artifact(),
        approved_ceiling=Decimal("4.6269612"),
        owner_approval_id="OWNER_APPROVAL_TEST",
        owner_approval_at_utc="2026-08-29T23:59:00Z",
        code_commit_sha="b" * 40,
        candidate=_candidate(),
        case=_case(),
        request=_request(),
        run=_run(),
        tracker=tracker,
    )

    assert receipt["receipt_version"] == "B4_INITIAL_PAID_CALL_RECEIPT_v0_2"
    assert receipt["semantic_replay_status"] == "COMPLETE"
    assert receipt["structured_output"] == structured
    assert len(receipt["structured_output_hash"]) == 64
    assert receipt["raw_provider_response_persisted"] is False
    assert receipt["provider_output_text_persisted"] is False
    assert receipt["semantic_rescore_requires_new_model_call"] is False
    assert len(receipt["receipt_hash"]) == 64


def test_replay_capture_fails_closed_when_structured_output_is_not_json(monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(
        module,
        "parse_responses_payload",
        lambda raw, requested_model, latency_ms: SimpleNamespace(output_text="not-json"),
    )
    tracker = module.ReplayableDispatchTrackingTransport(SimpleNamespace())
    tracker.provider_responses = 1
    tracker.last_provider_response = {"id": "resp_test"}

    status, structured, output_hash = module._structured_output_evidence(
        tracker=tracker,
        candidate=_candidate(),
        run=_run(),
    )
    assert status.startswith("INCOMPLETE_")
    assert structured is None
    assert output_hash is None


def test_initial_prompts_make_claim_type_and_reference_list_semantics_explicit() -> None:
    from aic.council.prompts import (
        BEAR_INITIAL_INSTRUCTIONS,
        BEAR_INITIAL_PROMPT_VERSION,
        BULL_INITIAL_PROMPT_VERSION,
        RED_TEAM_INITIAL_INSTRUCTIONS,
        RED_TEAM_INITIAL_PROMPT_VERSION,
    )

    assert BULL_INITIAL_PROMPT_VERSION == "BULL_INITIAL_vB4_0_2"
    assert BEAR_INITIAL_PROMPT_VERSION == "BEAR_INITIAL_vB4_0_2"
    assert RED_TEAM_INITIAL_PROMPT_VERSION == "RED_TEAM_INITIAL_vB4_0_2"
    assert "downside/execution-risk thesis as CHALLENGE claims" in BEAR_INITIAL_INSTRUCTIONS
    assert "counterevidence" in BEAR_INITIAL_INSTRUCTIONS
    assert "FALSIFIER claims" in BEAR_INITIAL_INSTRUCTIONS
    assert "critical_assumption_claim_ids may reference only" in BEAR_INITIAL_INSTRUCTIONS
    assert "critical_assumption_claim_ids may reference only" in RED_TEAM_INITIAL_INSTRUCTIONS
