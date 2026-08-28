from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from aic.domain.contracts_research import B3_RESEARCH_SNAPSHOT_V1, CANDIDATE_PACKET_V1, MATERIAL_CLAIM_V1, MODEL_RUN_RECEIPT_V1
from aic.research.models import (
    B2EvidenceDetailParameters,
    B3ResearchSnapshot,
    CandidatePacket,
    CompanyIRDocumentParameters,
    CurrentEvidenceStatus,
    MaterialClaim,
    ModelRunReceipt,
    ResearchBatchManifest,
    ResearchGapPlan,
    ResearchNeed,
    ResearchNeedType,
    ResearchQuestion,
    SecFilingSectionParameters,
)


def _question(text="What material business-model evidence is still missing?"):
    return ResearchQuestion(
        question_id="q1",
        category="business_model",
        question_text=text,
        why_material="Needed to establish the evidence-bounded business model context.",
        current_evidence_status=CurrentEvidenceStatus.PARTIAL,
    )


def test_b3_reuses_frozen_b1_canonical_contract_bindings() -> None:
    assert B3ResearchSnapshot is B3_RESEARCH_SNAPSHOT_V1
    assert MaterialClaim is MATERIAL_CLAIM_V1
    assert CandidatePacket is CANDIDATE_PACKET_V1
    assert ModelRunReceipt is MODEL_RUN_RECEIPT_V1


def test_unknown_need_type_is_rejected_by_schema() -> None:
    with pytest.raises(ValidationError):
        ResearchNeed.model_validate(
            {
                "need_id": "n1",
                "question_id": "q1",
                "need_type": "NEED_OPEN_WEB",
                "parameters": {"evidence_ids": ["e1"]},
                "max_items": 1,
                "expected_evidence_role": "support",
            }
        )


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "Read https://example.com and summarize it",
        "BUY the candidate if evidence is strong",
        "Submit an order after retrieval",
        "SELECT * FROM evidence WHERE candidate='NVDA'",
        "The margin equals 25%",
    ],
)
def test_plan_text_rejects_url_action_sql_and_arithmetic_result(unsafe_text: str) -> None:
    with pytest.raises(ValidationError):
        _question(unsafe_text)


def test_need_parameters_must_match_need_type() -> None:
    with pytest.raises(ValidationError, match="parameters do not match"):
        ResearchNeed(
            need_id="n1",
            question_id="q1",
            need_type=ResearchNeedType.NEED_B2_EVIDENCE_DETAIL,
            parameters=SecFilingSectionParameters(filing_accession="0001", sections=("Risk Factors",)),
            max_items=1,
            expected_evidence_role="support",
        )


def test_plan_requires_need_to_reference_existing_question() -> None:
    with pytest.raises(ValidationError, match="existing material question"):
        ResearchGapPlan(
            research_plan_id="plan-1",
            candidate_id="NVDA",
            b2_snapshot_id="b2-1",
            deep_comparison_id="cmp-1",
            research_policy_version="RESEARCH_POLICY_vB3_0_1",
            model_policy_version="MODEL_POLICY_vB3_0_1",
            research_cutoff=datetime(2026, 8, 28, 16, tzinfo=UTC),
            material_questions=(_question(),),
            requested_needs=(
                ResearchNeed(
                    need_id="n1",
                    question_id="missing",
                    need_type=ResearchNeedType.NEED_B2_EVIDENCE_DETAIL,
                    parameters=B2EvidenceDetailParameters(evidence_ids=("e1",)),
                    max_items=1,
                    expected_evidence_role="support",
                ),
            ),
        )


def test_research_cutoff_requires_timezone_awareness() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        ResearchGapPlan(
            research_plan_id="plan-1",
            candidate_id="NVDA",
            b2_snapshot_id="b2-1",
            deep_comparison_id="cmp-1",
            research_policy_version="RESEARCH_POLICY_vB3_0_1",
            model_policy_version="MODEL_POLICY_vB3_0_1",
            research_cutoff=datetime(2026, 8, 28, 16),
            material_questions=(_question(),),
            requested_needs=(),
        )


def test_research_batch_requires_exact_three_unique_candidates() -> None:
    with pytest.raises(ValidationError, match="three unique"):
        ResearchBatchManifest.build(
            batch_id="batch-1",
            b2_snapshot_id="b2-1",
            deep_comparison_id="cmp-1",
            research_policy_version="RESEARCH_POLICY_vB3_0_1",
            model_policy_version="MODEL_POLICY_vB3_0_1",
            candidate_ids=("NVDA", "NVDA", "META"),
        )
