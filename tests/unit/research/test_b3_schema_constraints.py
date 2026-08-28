import json
from datetime import UTC, datetime

import pytest

from aic.research.model_policy import MODEL_CANDIDATE_LADDER, MODEL_POLICY_VERSION
from aic.research.models import CurrentEvidenceStatus, ResearchNeedType
from aic.research.planner import PlannerContextItem, PlannerInputEnvelope, build_planner_request, parse_planner_output
from aic.research.policy import RESEARCH_POLICY_VERSION, ResearchPolicy, ResearchPolicyError


CUTOFF = datetime(2026, 8, 28, 17, 20, tzinfo=UTC)
ACCESSION = "0001045810-26-000021"


def _input() -> PlannerInputEnvelope:
    return PlannerInputEnvelope(
        candidate_id="NVDA",
        b2_snapshot_id="b2-real-event-handoff-v0-1",
        deep_comparison_id="b2-real-deep-comparison-v0-1",
        research_policy_version=RESEARCH_POLICY_VERSION,
        model_policy_version=MODEL_POLICY_VERSION,
        research_cutoff=CUTOFF,
        context_items=(
            PlannerContextItem(
                item_id="ctx",
                category="risk",
                evidence_status="PARTIAL",
                description="Bounded context.",
                evidence_refs=("B2_NVDA_SEC_SECURITY_PROOF",),
                computed_value_refs=("B2_NVDA_return_20s",),
            ),
        ),
        allowed_source_handles=(ACCESSION, "ALPACA_NEWS_WINDOW_NVDA"),
    )


def _policy() -> ResearchPolicy:
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


def _base_output() -> dict:
    return {
        "research_plan_id": "plan",
        "candidate_id": "NVDA",
        "b2_snapshot_id": "b2-real-event-handoff-v0-1",
        "deep_comparison_id": "b2-real-deep-comparison-v0-1",
        "research_policy_version": RESEARCH_POLICY_VERSION,
        "model_policy_version": MODEL_POLICY_VERSION,
        "research_cutoff": "2026-08-28T17:20:00Z",
        "material_questions": [
            {
                "question_id": "q1",
                "category": "risk",
                "question_text": "What material risk evidence remains unresolved?",
                "why_material": "Needed for bounded later analysis.",
                "current_evidence_status": CurrentEvidenceStatus.PARTIAL.value,
            }
        ],
        "requested_needs": [],
    }


def test_model_facing_schema_constrains_budget_and_application_owned_refs() -> None:
    request = build_planner_request(
        model_candidate=MODEL_CANDIDATE_LADDER[0],
        planner_input=_input(),
    )
    schema = request.request_payload["text"]["format"]["schema"]
    defs = schema["$defs"]
    assert defs["ResearchNeed"]["properties"]["max_items"]["enum"] == [1, 2, 3, 4, 5]
    assert defs["B2EvidenceDetailParameters"]["properties"]["evidence_ids"]["items"]["enum"] == [
        "B2_NVDA_SEC_SECURITY_PROOF"
    ]
    assert defs["B2ComputedValueDetailParameters"]["properties"]["computed_value_ids"]["items"]["enum"] == [
        "B2_NVDA_return_20s"
    ]
    assert defs["SecFilingSectionParameters"]["properties"]["filing_accession"]["enum"] == [ACCESSION]


def test_post_validator_rejects_invented_computed_value_ref() -> None:
    payload = _base_output()
    payload["requested_needs"] = [
        {
            "need_id": "n1",
            "question_id": "q1",
            "need_type": "NEED_B2_COMPUTED_VALUE_DETAIL",
            "parameters": {"computed_value_ids": ["B2_NVDA_INVENTED"]},
            "max_items": 1,
            "expected_evidence_role": "detail",
        }
    ]
    with pytest.raises(ResearchPolicyError, match="outside planner input refs"):
        parse_planner_output(json.dumps(payload), planner_input=_input(), research_policy=_policy())


def test_post_validator_rejects_invented_sec_accession() -> None:
    payload = _base_output()
    payload["requested_needs"] = [
        {
            "need_id": "n1",
            "question_id": "q1",
            "need_type": "NEED_SEC_FILING_SECTION",
            "parameters": {
                "filing_accession": "0000000000-26-000001",
                "sections": ["Risk Factors"],
            },
            "max_items": 1,
            "expected_evidence_role": "primary",
        }
    ]
    with pytest.raises(ResearchPolicyError, match="outside allowed source handles"):
        parse_planner_output(json.dumps(payload), planner_input=_input(), research_policy=_policy())
