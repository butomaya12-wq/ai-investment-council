import json
from datetime import UTC, datetime

import pytest

from aic.research.model_policy import MODEL_CANDIDATE_LADDER, MODEL_POLICY_VERSION
from aic.research.models import CurrentEvidenceStatus, ResearchNeedType
from aic.research.planner import PlannerContextItem, PlannerInputEnvelope, build_planner_request
from aic.research.policy import RESEARCH_POLICY_VERSION, ResearchPolicy
from aic.research.runtime import (
    ResponsesCredentialError,
    ResponsesProtocolError,
    execute_planner_runtime,
    load_openai_api_key,
    parse_responses_payload,
)


CUTOFF = datetime(2026, 8, 28, 16, tzinfo=UTC)


def _policy():
    return ResearchPolicy(
        policy_version=RESEARCH_POLICY_VERSION,
        allowed_need_types=tuple(ResearchNeedType),
        max_needs_per_candidate=6,
        max_items_per_need=5,
        max_total_evidence_items_per_candidate=30,
        allowed_source_tiers=("B2", "SEC", "ALPACA_NEWS"),
        allowed_sec_forms=("10-K", "10-Q", "8-K"),
        allowed_sec_sections=("Business", "Risk Factors", "MD&A", "Material 8-K"),
        company_ir_policy_ref=None,
        news_window_policy_ref="NEWS_WINDOW_v1",
        material_claim_categories=("risk", "growth_quality"),
        inference_rule="Explicitly mark inference and bind supporting evidence.",
        unknown_rule="State material unknowns explicitly.",
        conflict_rule="Material conflicts remain visible.",
        numeric_claim_rule="No model arithmetic.",
        research_cutoff_rule="Exclude evidence after cutoff.",
        max_model_calls_per_candidate=3,
        repair_attempt_limit=1,
        failure_behavior="Bounded failure only.",
    )


def _input():
    return PlannerInputEnvelope(
        candidate_id="NVDA",
        b2_snapshot_id="b2-1",
        deep_comparison_id="cmp-1",
        research_policy_version=RESEARCH_POLICY_VERSION,
        model_policy_version=MODEL_POLICY_VERSION,
        research_cutoff=CUTOFF,
        context_items=(
            PlannerContextItem(
                item_id="ctx-1",
                category="risk",
                evidence_status="PARTIAL",
                description="Risk-factor filing evidence is present but requires bounded detail.",
                evidence_refs=("e1",),
            ),
        ),
        allowed_source_handles=("sec-accession-1", "alpaca-news-window"),
    )


def _plan_payload():
    return {
        "research_plan_id": "plan-1",
        "candidate_id": "NVDA",
        "b2_snapshot_id": "b2-1",
        "deep_comparison_id": "cmp-1",
        "research_policy_version": RESEARCH_POLICY_VERSION,
        "model_policy_version": MODEL_POLICY_VERSION,
        "research_cutoff": "2026-08-28T16:00:00Z",
        "material_questions": [
            {
                "question_id": "q1",
                "category": "risk",
                "question_text": "What material risk evidence remains unresolved?",
                "why_material": "Needed to make material uncertainty explicit before later Council analysis.",
                "current_evidence_status": CurrentEvidenceStatus.PARTIAL.value,
            }
        ],
        "requested_needs": [
            {
                "need_id": "n1",
                "question_id": "q1",
                "need_type": "NEED_B2_EVIDENCE_DETAIL",
                "parameters": {"evidence_ids": ["e1"]},
                "max_items": 1,
                "expected_evidence_role": "primary",
            }
        ],
    }


def _response(*, output=None, status="completed", model="gpt-5.6-terra-2026-08-01", store=False):
    if output is None:
        output = [
            {"type": "reasoning", "id": "rs_1", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(_plan_payload()),
                        "annotations": [],
                    }
                ],
            },
        ]
    return {
        "id": "resp_1",
        "object": "response",
        "status": status,
        "error": None,
        "model": model,
        "store": store,
        "tools": [],
        "output": output,
        "usage": {
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 20},
            "output_tokens": 50,
            "output_tokens_details": {"reasoning_tokens": 10},
            "total_tokens": 150,
        },
    }


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.seen_payload = None
        self.seen_key = None

    def post(self, *, payload, api_key):
        self.seen_payload = payload
        self.seen_key = api_key
        return self.response


def test_missing_runtime_credential_is_blocked() -> None:
    with pytest.raises(ResponsesCredentialError, match="not present"):
        load_openai_api_key({})


def test_parse_completed_reasoning_response_extracts_structured_text_and_usage() -> None:
    result = parse_responses_payload(
        _response(),
        requested_model="gpt-5.6-terra",
        latency_ms=12,
    )
    assert result.response_id == "resp_1"
    assert result.effective_model.startswith("gpt-5.6-terra")
    assert result.usage.cached_tokens == 20
    assert result.usage.reasoning_tokens == 10


def test_runtime_rejects_any_returned_tool_call_item() -> None:
    with pytest.raises(ResponsesProtocolError, match="unexpected executable"):
        parse_responses_payload(
            _response(
                output=[
                    {
                        "type": "web_search_call",
                        "id": "ws_1",
                        "status": "completed",
                    }
                ]
            ),
            requested_model="gpt-5.6-terra",
            latency_ms=1,
        )


def test_runtime_rejects_refusal_instead_of_structured_output() -> None:
    with pytest.raises(ResponsesProtocolError, match="refusal"):
        parse_responses_payload(
            _response(
                output=[
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "refusal", "refusal": "No"}],
                    }
                ]
            ),
            requested_model="gpt-5.6-terra",
            latency_ms=1,
        )


def test_runtime_rejects_incomplete_response() -> None:
    with pytest.raises(ResponsesProtocolError, match="status must be completed"):
        parse_responses_payload(
            _response(status="incomplete"),
            requested_model="gpt-5.6-terra",
            latency_ms=1,
        )


def test_runtime_rejects_store_true_or_effective_model_drift() -> None:
    with pytest.raises(ResponsesProtocolError, match="store=false"):
        parse_responses_payload(
            _response(store=True),
            requested_model="gpt-5.6-terra",
            latency_ms=1,
        )
    with pytest.raises(ResponsesProtocolError, match="effective model"):
        parse_responses_payload(
            _response(model="gpt-5.6-sol"),
            requested_model="gpt-5.6-terra",
            latency_ms=1,
        )


def test_execute_planner_runtime_validates_response_and_plan_lineage() -> None:
    planner_input = _input()
    request = build_planner_request(
        model_candidate=MODEL_CANDIDATE_LADDER[0],
        planner_input=planner_input,
    )
    fake = FakeTransport(_response())
    result = execute_planner_runtime(
        request=request,
        planner_input=planner_input,
        research_policy=_policy(),
        api_key="sk-test-sentinel",
        transport=fake,
    )
    assert result.plan.candidate_id == "NVDA"
    assert result.call.response_id == "resp_1"
    assert fake.seen_payload["store"] is False
    assert fake.seen_payload["tools"] == []
    assert fake.seen_key == "sk-test-sentinel"


def test_runtime_secret_is_not_persisted_in_result_repr() -> None:
    planner_input = _input()
    request = build_planner_request(
        model_candidate=MODEL_CANDIDATE_LADDER[0],
        planner_input=planner_input,
    )
    result = execute_planner_runtime(
        request=request,
        planner_input=planner_input,
        research_policy=_policy(),
        api_key="sk-secret-sentinel",
        transport=FakeTransport(_response()),
    )
    assert "sk-secret-sentinel" not in repr(result)
    assert "sk-secret-sentinel" not in result.model_dump_json()
