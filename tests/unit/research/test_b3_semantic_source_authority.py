import pytest

from aic.research.model_eval import build_eval_cases
from aic.research.synthesize import (
    CandidatePacketDraft,
    CandidateSynthesisDraft,
    MaterialClaimDraft,
)
from aic.research.validate import (
    CandidatePacketValidationError,
    validate_synthesis_draft,
)


MANDATE_VERSION = "ALPACA_COMPETITION_V1_2026_08_29"


def _e7_input():
    case = next(
        case
        for case in build_eval_cases(MANDATE_VERSION)
        if case.case_id == "E7"
    )
    return case.build_input(MANDATE_VERSION)


def test_fact_cannot_use_cross_category_evidence_as_semantic_support() -> None:
    synthesis_input = _e7_input()
    evidence_id = synthesis_input.evidence_items[0].evidence_id
    question_id = synthesis_input.research_questions[0].question_id
    claim = MaterialClaimDraft(
        claim_id="E7_BAD_FACT",
        candidate_id=synthesis_input.candidate_id,
        category="competitive_position",
        claim_text="The announcement establishes durable competitive leadership.",
        claim_kind="FACT",
        materiality="SUPPORTING",
        evidence_ids=(evidence_id,),
        support_status="SUPPORTED",
    )
    draft = CandidateSynthesisDraft(
        candidate_id=synthesis_input.candidate_id,
        claims=(claim,),
        packet=CandidatePacketDraft(
            competitive_position_claim_ids=(claim.claim_id,),
            evidence_ids=(evidence_id,),
            research_questions_unresolved=(question_id,),
            research_status="INCOMPLETE",
        ),
    )

    with pytest.raises(
        CandidatePacketValidationError,
        match="category-authoritative evidence",
    ):
        validate_synthesis_draft(draft, synthesis_input=synthesis_input)


def test_material_inference_cannot_promote_cross_category_evidence() -> None:
    synthesis_input = _e7_input()
    evidence_id = synthesis_input.evidence_items[0].evidence_id
    question_id = synthesis_input.research_questions[0].question_id
    claim = MaterialClaimDraft(
        claim_id="E7_BAD_INFERENCE",
        candidate_id=synthesis_input.candidate_id,
        category="competitive_position",
        claim_text="The launch suggests durable competitive leadership.",
        claim_kind="INFERENCE",
        materiality="MATERIAL",
        evidence_ids=(evidence_id,),
        assumptions=("A product launch is treated as evidence of durable leadership.",),
        support_status="SUPPORTED",
        uncertainty_note="The supplied evidence does not establish durable leadership.",
    )
    draft = CandidateSynthesisDraft(
        candidate_id=synthesis_input.candidate_id,
        claims=(claim,),
        packet=CandidatePacketDraft(
            competitive_position_claim_ids=(claim.claim_id,),
            evidence_ids=(evidence_id,),
            research_questions_unresolved=(question_id,),
            research_status="INCOMPLETE",
        ),
    )

    with pytest.raises(
        CandidatePacketValidationError,
        match="category-authoritative evidence",
    ):
        validate_synthesis_draft(draft, synthesis_input=synthesis_input)


def test_complete_packet_cannot_hide_unresolved_material_question() -> None:
    synthesis_input = _e7_input()
    question_id = synthesis_input.research_questions[0].question_id
    draft = CandidateSynthesisDraft(
        candidate_id=synthesis_input.candidate_id,
        claims=(),
        packet=CandidatePacketDraft(
            research_questions_unresolved=(question_id,),
            research_status="COMPLETE",
        ),
    )

    with pytest.raises(
        CandidatePacketValidationError,
        match="COMPLETE CandidatePacket cannot contain unresolved research questions",
    ):
        validate_synthesis_draft(draft, synthesis_input=synthesis_input)
