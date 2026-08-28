from datetime import UTC, datetime, timedelta

import pytest

from aic.research.models import (
    AlpacaNewsWindowParameters,
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
from aic.research.policy import RESEARCH_POLICY_VERSION, ResearchPolicy, ResearchPolicyError, validate_research_plan


CUTOFF = datetime(2026, 8, 28, 16, tzinfo=UTC)


def _policy(*, company_ir_policy_ref=None):
    return ResearchPolicy(
        policy_version=RESEARCH_POLICY_VERSION,
        allowed_need_types=tuple(ResearchNeedType),
        max_needs_per_candidate=6,
        max_items_per_need=5,
        max_total_evidence_items_per_candidate=30,
        allowed_source_tiers=("B2", "SEC", "ALPACA_NEWS"),
        allowed_sec_forms=("10-K", "10-Q", "8-K"),
        allowed_sec_sections=("Business", "Risk Factors", "MD&A", "Material 8-K"),
        company_ir_policy_ref=company_ir_policy_ref,
        news_window_policy_ref="NEWS_WINDOW_v1",
        material_claim_categories=("business_model", "growth_quality", "financial_quality", "competitive_position", "valuation_context", "market_context", "capital_allocation", "catalyst", "risk", "portfolio_interaction"),
        inference_rule="Explicitly mark inference and bind supporting evidence.",
        unknown_rule="State material unknowns explicitly.",
        conflict_rule="Material conflicts remain visible and unresolved unless deterministically resolved.",
        numeric_claim_rule="No model arithmetic; bind numbers to EvidenceItem or ComputedValue.",
        research_cutoff_rule="Exclude evidence not knowable by research_cutoff.",
        max_model_calls_per_candidate=3,
        repair_attempt_limit=1,
        failure_behavior="After bounded repair, return INCOMPLETE/FAIL; never unbounded loop.",
    )


def _question():
    return ResearchQuestion(
        question_id="q1",
        category="risk",
        question_text="What material risk evidence remains unresolved?",
        why_material="Required to make research gaps explicit before Council analysis.",
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


def test_frozen_resource_bounds_are_enforced() -> None:
    payload = _policy().model_dump(mode="python")
    payload["max_needs_per_candidate"] = 7
    with pytest.raises(ValueError, match="max_needs"):
        ResearchPolicy.model_validate(payload)


def test_more_than_six_needs_is_rejected() -> None:
    needs = [
        ResearchNeed(
            need_id=f"n{i}",
            question_id="q1",
            need_type=ResearchNeedType.NEED_B2_EVIDENCE_DETAIL,
            parameters=B2EvidenceDetailParameters(evidence_ids=(f"e{i}",)),
            max_items=1,
            expected_evidence_role="support",
        )
        for i in range(7)
    ]
    with pytest.raises(ResearchPolicyError, match="max_needs"):
        validate_research_plan(_plan(needs), _policy())


def test_max_items_per_need_is_rejected() -> None:
    need = ResearchNeed(
        need_id="n1",
        question_id="q1",
        need_type=ResearchNeedType.NEED_B2_EVIDENCE_DETAIL,
        parameters=B2EvidenceDetailParameters(evidence_ids=("e1",)),
        max_items=6,
        expected_evidence_role="support",
    )
    with pytest.raises(ResearchPolicyError, match="max_items"):
        validate_research_plan(_plan([need]), _policy())


def test_exact_thirty_item_budget_is_allowed() -> None:
    needs = [
        ResearchNeed(
            need_id=f"n{i}",
            question_id="q1",
            need_type=ResearchNeedType.NEED_B2_EVIDENCE_DETAIL,
            parameters=B2EvidenceDetailParameters(evidence_ids=(f"e{i}",)),
            max_items=5,
            expected_evidence_role="support",
        )
        for i in range(6)
    ]
    validate_research_plan(_plan(needs), _policy())


def test_sec_section_outside_allowlist_is_rejected() -> None:
    need = ResearchNeed(
        need_id="n1",
        question_id="q1",
        need_type=ResearchNeedType.NEED_SEC_FILING_SECTION,
        parameters=SecFilingSectionParameters(filing_accession="0001", sections=("Compensation",)),
        max_items=1,
        expected_evidence_role="primary",
    )
    with pytest.raises(ResearchPolicyError, match="SEC section"):
        validate_research_plan(_plan([need]), _policy())


def test_news_window_after_cutoff_is_rejected() -> None:
    need = ResearchNeed(
        need_id="n1",
        question_id="q1",
        need_type=ResearchNeedType.NEED_ALPACA_NEWS_WINDOW,
        parameters=AlpacaNewsWindowParameters(window_start=CUTOFF - timedelta(days=7), window_end=CUTOFF + timedelta(seconds=1)),
        max_items=5,
        expected_evidence_role="secondary",
    )
    with pytest.raises(ResearchPolicyError, match="cutoff"):
        validate_research_plan(_plan([need]), _policy())


def test_ir_need_requires_approved_ir_policy_ref() -> None:
    need = ResearchNeed(
        need_id="n1",
        question_id="q1",
        need_type=ResearchNeedType.NEED_COMPANY_IR_DOCUMENT,
        parameters=CompanyIRDocumentParameters(registry_document_ids=("ir-doc-1",)),
        max_items=1,
        expected_evidence_role="secondary",
    )
    with pytest.raises(ResearchPolicyError, match="source tier|IR policy"):
        validate_research_plan(_plan([need]), _policy(company_ir_policy_ref=None))


def test_need_source_tier_must_be_explicitly_authorized() -> None:
    need = ResearchNeed(
        need_id="n1",
        question_id="q1",
        need_type=ResearchNeedType.NEED_CORPORATE_ACTION_DETAIL,
        parameters=CorporateActionDetailParameters(action_ids=("ca-1",)),
        max_items=1,
        expected_evidence_role="primary",
    )
    with pytest.raises(ResearchPolicyError, match="source tier"):
        validate_research_plan(_plan([need]), _policy())
