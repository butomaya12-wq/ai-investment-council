from datetime import UTC, datetime, timedelta

import pytest

from aic.research.models import (
    AlpacaNewsWindowParameters,
    B2ComputedValueDetailParameters,
    B2EvidenceDetailParameters,
    CompanyIRDocumentParameters,
    CorporateActionDetailParameters,
    CurrentEvidenceStatus,
    ResearchGapPlan,
    ResearchNeed,
    ResearchNeedType,
    ResearchQuestion,
    SecFilingSectionParameters,
)
from aic.research.policy import RESEARCH_POLICY_VERSION, ResearchPolicy, ResearchPolicyError
from aic.research.retrieve import RetrievalAction, RetrievalProvider, compile_retrieval_requests


CUTOFF = datetime(2026, 8, 28, 16, tzinfo=UTC)


def _policy(company_ir_policy_ref="IR_REGISTRY_v1"):
    return ResearchPolicy(
        policy_version=RESEARCH_POLICY_VERSION,
        allowed_need_types=tuple(ResearchNeedType),
        max_needs_per_candidate=6,
        max_items_per_need=5,
        max_total_evidence_items_per_candidate=30,
        allowed_source_tiers=("B2", "SEC", "ALPACA_NEWS", "ALPACA_CORPORATE_ACTIONS", "IR_REGISTRY"),
        allowed_sec_forms=("10-K", "10-Q", "8-K"),
        allowed_sec_sections=("Business", "Risk Factors", "MD&A", "Material 8-K"),
        company_ir_policy_ref=company_ir_policy_ref,
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


def _question():
    return ResearchQuestion(
        question_id="q1",
        category="risk",
        question_text="What material risk evidence remains unresolved?",
        why_material="Required before later Council analysis.",
        current_evidence_status=CurrentEvidenceStatus.PARTIAL,
    )


def _plan(needs):
    return ResearchGapPlan(
        research_plan_id="plan-1",
        candidate_id="NVDA",
        b2_snapshot_id="b2-1",
        deep_comparison_id="cmp-1",
        research_policy_version=RESEARCH_POLICY_VERSION,
        model_policy_version="MODEL_POLICY_vB3_0_1",
        research_cutoff=CUTOFF,
        material_questions=(_question(),),
        requested_needs=tuple(needs),
    )


def test_dispatch_maps_all_six_need_types_without_url_or_query_escape() -> None:
    needs = (
        ResearchNeed(need_id="n1", question_id="q1", need_type=ResearchNeedType.NEED_B2_EVIDENCE_DETAIL, parameters=B2EvidenceDetailParameters(evidence_ids=("e1",)), max_items=1, expected_evidence_role="primary"),
        ResearchNeed(need_id="n2", question_id="q1", need_type=ResearchNeedType.NEED_B2_COMPUTED_VALUE_DETAIL, parameters=B2ComputedValueDetailParameters(computed_value_ids=("c1",)), max_items=1, expected_evidence_role="numeric"),
        ResearchNeed(need_id="n3", question_id="q1", need_type=ResearchNeedType.NEED_SEC_FILING_SECTION, parameters=SecFilingSectionParameters(filing_accession="0001", sections=("Risk Factors",)), max_items=2, expected_evidence_role="primary"),
        ResearchNeed(need_id="n4", question_id="q1", need_type=ResearchNeedType.NEED_ALPACA_NEWS_WINDOW, parameters=AlpacaNewsWindowParameters(window_start=CUTOFF - timedelta(days=7), window_end=CUTOFF), max_items=5, expected_evidence_role="secondary"),
        ResearchNeed(need_id="n5", question_id="q1", need_type=ResearchNeedType.NEED_CORPORATE_ACTION_DETAIL, parameters=CorporateActionDetailParameters(action_ids=("ca1",)), max_items=1, expected_evidence_role="primary"),
        ResearchNeed(need_id="n6", question_id="q1", need_type=ResearchNeedType.NEED_COMPANY_IR_DOCUMENT, parameters=CompanyIRDocumentParameters(registry_document_ids=("ir1",)), max_items=1, expected_evidence_role="secondary"),
    )
    requests = compile_retrieval_requests(_plan(needs), policy=_policy())
    assert [(row.provider, row.action) for row in requests] == [
        (RetrievalProvider.B2_STORE, RetrievalAction.GET_EVIDENCE_BY_IDS),
        (RetrievalProvider.B2_STORE, RetrievalAction.GET_COMPUTED_VALUES_BY_IDS),
        (RetrievalProvider.SEC, RetrievalAction.GET_FILING_SECTIONS),
        (RetrievalProvider.ALPACA, RetrievalAction.GET_NEWS_WINDOW),
        (RetrievalProvider.ALPACA, RetrievalAction.GET_CORPORATE_ACTIONS_BY_IDS),
        (RetrievalProvider.IR_REGISTRY, RetrievalAction.GET_IR_DOCUMENTS_BY_IDS),
    ]
    forbidden = {"url", "uri", "sql", "query", "api_key", "credential", "secret", "order", "broker"}
    assert all(not (set(key.lower() for key in row.parameters) & forbidden) for row in requests)
    assert all(len(row.request_hash) == 64 for row in requests)


def test_dispatch_validates_policy_before_compiling() -> None:
    need = ResearchNeed(
        need_id="n1",
        question_id="q1",
        need_type=ResearchNeedType.NEED_COMPANY_IR_DOCUMENT,
        parameters=CompanyIRDocumentParameters(registry_document_ids=("ir1",)),
        max_items=1,
        expected_evidence_role="secondary",
    )
    with pytest.raises(ResearchPolicyError, match="IR policy"):
        compile_retrieval_requests(_plan((need,)), policy=_policy(company_ir_policy_ref=None))
