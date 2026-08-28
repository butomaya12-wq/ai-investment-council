import json
from datetime import UTC, datetime

import pytest

from aic.research.model_policy import MODEL_CANDIDATE_LADDER, MODEL_POLICY_VERSION
from aic.research.models import CurrentEvidenceStatus, ResearchNeedType
from aic.research.planner import PlannerContextItem, PlannerInputEnvelope, build_planner_request, parse_planner_output
from aic.research.policy import RESEARCH_POLICY_VERSION, ResearchPolicy


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


def _valid_output():
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


def test_request_uses_current_responses_structured_output_shape() -> None:
    request = build_planner_request(model_candidate=MODEL_CANDIDATE_LADDER[0], planner_input=_input())
    payload = request.request_payload
    assert payload["model"] == "gpt-5.6-terra"
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["store"] is False
    assert payload["tools"] == []
    assert payload["parallel_tool_calls"] is False
    assert payload["truncation"] == "disabled"
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["text"]["format"]["strict"] is True
    assert payload["text"]["format"]["schema"]["additionalProperties"] is False
    assert "json_schema" not in payload["instructions"].lower()


def test_request_hash_is_stable_for_same_inputs() -> None:
    first = build_planner_request(model_candidate=MODEL_CANDIDATE_LADDER[0], planner_input=_input())
    second = build_planner_request(model_candidate=MODEL_CANDIDATE_LADDER[0], planner_input=_input())
    assert first == second
    assert first.request_hash == second.request_hash


def test_planner_parser_accepts_valid_strict_plan() -> None:
    plan = parse_planner_output(json.dumps(_valid_output()), planner_input=_input(), research_policy=_policy())
    assert plan.candidate_id == "NVDA"
    assert len(plan.requested_needs) == 1


def test_planner_parser_rejects_lineage_rewrite() -> None:
    payload = _valid_output()
    payload["candidate_id"] = "META"
    with pytest.raises(ValueError, match="lineage"):
        parse_planner_output(json.dumps(payload), planner_input=_input(), research_policy=_policy())


def test_planner_parser_rejects_non_json() -> None:
    with pytest.raises(ValueError, match="valid JSON"):
        parse_planner_output("not-json", planner_input=_input(), research_policy=_policy())
