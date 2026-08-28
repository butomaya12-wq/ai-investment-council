import json
from datetime import UTC, datetime

import pytest

from aic.research.model_policy import MODEL_POLICY_VERSION
from aic.research.models import CurrentEvidenceStatus, ResearchGapPlan, ResearchNeed, ResearchNeedType, ResearchQuestion, SecFilingSectionParameters
from aic.research.planner import PlannerInputEnvelope, parse_planner_output
from aic.research.policy import RESEARCH_POLICY_VERSION, ResearchPolicy, ResearchPolicyError
from aic.research.retrieve import compile_retrieval_requests
from aic.research.sec_schema import RUNTIME_RETRIEVABLE_SEC_SECTION_VALUES


CUTOFF = datetime(2026, 8, 28, 17, 34, tzinfo=UTC)
ACCESSION = "0001045810-26-000021"


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
        material_claim_categories=("risk",),
        inference_rule="Bind inference to evidence.",
        unknown_rule="Keep unknowns explicit.",
        conflict_rule="Keep conflicts explicit.",
        numeric_claim_rule="No model arithmetic.",
        research_cutoff_rule="No future evidence.",
        max_model_calls_per_candidate=3,
        repair_attempt_limit=1,
        failure_behavior="Bounded failure.",
    )


def _input() -> PlannerInputEnvelope:
    return PlannerInputEnvelope(
        candidate_id="NVDA",
        b2_snapshot_id="EVENT_HANDOFF_B2_20260828_011",
        deep_comparison_id="EVENT_HANDOFF_DEEP_COMPARISON_20260828_011",
        research_policy_version=RESEARCH_POLICY_VERSION,
        model_policy_version=MODEL_POLICY_VERSION,
        research_cutoff=CUTOFF,
        context_items=(),
        allowed_source_handles=(ACCESSION, "ALPACA_NEWS_WINDOW_NVDA"),
    )


def _plan(section: str) -> ResearchGapPlan:
    return ResearchGapPlan(
        research_plan_id="plan-1",
        candidate_id="NVDA",
        b2_snapshot_id="EVENT_HANDOFF_B2_20260828_011",
        deep_comparison_id="EVENT_HANDOFF_DEEP_COMPARISON_20260828_011",
        research_policy_version=RESEARCH_POLICY_VERSION,
        model_policy_version=MODEL_POLICY_VERSION,
        research_cutoff=CUTOFF,
        material_questions=(
            ResearchQuestion(
                question_id="q1",
                category="risk",
                question_text="What material risk evidence remains unresolved?",
                why_material="Required before later Council analysis.",
                current_evidence_status=CurrentEvidenceStatus.MISSING,
            ),
        ),
        requested_needs=(
            ResearchNeed(
                need_id="n1",
                question_id="q1",
                need_type=ResearchNeedType.NEED_SEC_FILING_SECTION,
                parameters=SecFilingSectionParameters(
                    filing_accession=ACCESSION,
                    sections=(section,),
                ),
                max_items=1,
                expected_evidence_role="primary",
            ),
        ),
    )


def test_runtime_sec_capability_is_narrower_than_generic_policy() -> None:
    assert RUNTIME_RETRIEVABLE_SEC_SECTION_VALUES == ("Business", "Risk Factors", "MD&A")
    assert "Material 8-K" not in RUNTIME_RETRIEVABLE_SEC_SECTION_VALUES


def test_parser_rejects_material_8k_as_section_of_supplied_annual_filing() -> None:
    payload = _plan("Material 8-K").model_dump(mode="json")
    with pytest.raises(ResearchPolicyError, match="retrieval capability"):
        parse_planner_output(
            json.dumps(payload),
            planner_input=_input(),
            research_policy=_policy(),
        )


def test_retrieval_compiler_rejects_material_8k_section_even_if_policy_allows_it() -> None:
    with pytest.raises(ResearchPolicyError, match="retrieval capability"):
        compile_retrieval_requests(_plan("Material 8-K"), policy=_policy())


def test_supported_annual_filing_section_still_compiles() -> None:
    requests = compile_retrieval_requests(_plan("Risk Factors"), policy=_policy())
    assert len(requests) == 1
    assert requests[0].parameters["sections"] == ("Risk Factors",)
