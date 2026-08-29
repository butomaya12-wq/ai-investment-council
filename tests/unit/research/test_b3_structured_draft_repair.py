from __future__ import annotations

import json
from datetime import UTC, datetime

from aic.research.event_policy import build_event_research_policy
from aic.research.model_policy import MODEL_CANDIDATE_LADDER, MODEL_POLICY_VERSION
from aic.research.policy import RESEARCH_POLICY_VERSION
from aic.research.run import RawCandidateSynthesisDraft, execute_synthesis_runtime
from aic.research.synthesize import (
    CandidatePacketDraft,
    CandidateSynthesisDraft,
    MaterialClaimDraft,
    SynthesisEvidenceItem,
    SynthesisInputEnvelope,
    SynthesisQuestion,
    build_synthesis_request,
)


EVIDENCE_ID = "B3_SEC_META_N1_1"
SOURCE_GAP = "ALPACA_NEWS_PAGINATION_INCOMPLETE"


def _input() -> SynthesisInputEnvelope:
    cutoff = datetime(2026, 8, 28, 17, 34, tzinfo=UTC)
    return SynthesisInputEnvelope(
        candidate_id="META",
        symbol="META",
        issuer_id="SEC_CIK_0001326801",
        b2_snapshot_id="EVENT_HANDOFF_B2_20260828_011",
        research_snapshot_id="B3_RESEARCH_BUNDLE_META_test",
        mandate_version=None,
        deep_comparison_id="EVENT_HANDOFF_DEEP_COMPARISON_20260828_011",
        research_policy_version=RESEARCH_POLICY_VERSION,
        model_policy_version=MODEL_POLICY_VERSION,
        research_cutoff=cutoff,
        evidence_bundle_hash="a" * 64,
        evidence_status="PARTIAL",
        evidence_items=(
            SynthesisEvidenceItem(
                evidence_id=EVIDENCE_ID,
                provider="SEC",
                source_type="SEC_FILING_SECTION",
                field_or_claim="Business",
                normalized_value="Bounded filing evidence.",
                published_at=datetime(2026, 1, 30, 21, 0, tzinfo=UTC),
                observed_at=None,
                as_of=datetime(2026, 1, 30, 21, 0, tzinfo=UTC),
                authoritative_for=("B3_QUALITATIVE_SEC_RESEARCH",),
            ),
        ),
        computed_values=(),
        conflict_ids=(),
        research_questions=(
            SynthesisQuestion(
                question_id="Q1",
                category="business_model",
                question_text="What filing context is material?",
                why_material="Required research context.",
            ),
            SynthesisQuestion(
                question_id="Q2",
                category="recent_developments",
                question_text="What recent developments are material?",
                why_material="News pagination is incomplete.",
            ),
        ),
        application_source_gaps=(SOURCE_GAP,),
    )


def _valid_draft() -> CandidateSynthesisDraft:
    claim = MaterialClaimDraft(
        claim_id="CLM_META_BUSINESS_1",
        candidate_id="META",
        category="business_model",
        claim_text="The filing supplies bounded business context.",
        claim_kind="FACT",
        materiality="MATERIAL",
        evidence_ids=(EVIDENCE_ID,),
        computed_value_ids=(),
        conflict_ids=(),
        assumptions=(),
        support_status="SUPPORTED",
        uncertainty_note=None,
    )
    return CandidateSynthesisDraft(
        candidate_id="META",
        claims=(claim,),
        packet=CandidatePacketDraft(
            business_model_claim_ids=(claim.claim_id,),
            growth_quality_claim_ids=(),
            financial_quality_claim_ids=(),
            competitive_position_claim_ids=(),
            valuation_context_claim_ids=(),
            market_context_claim_ids=(),
            capital_allocation_claim_ids=(),
            catalyst_claim_ids=(),
            risk_claim_ids=(),
            portfolio_interaction_claim_ids=(),
            material_unknowns=("Recent-news evidence remains incomplete.",),
            material_conflicts=(),
            source_gaps=(SOURCE_GAP,),
            computed_value_ids=(),
            evidence_ids=(EVIDENCE_ID,),
            research_questions_resolved=("Q1",),
            research_questions_unresolved=("Q2",),
            research_status="INCOMPLETE",
        ),
    )


def _duplicate_claim_payload() -> dict:
    payload = _valid_draft().model_dump(mode="json")
    payload["claims"].append(dict(payload["claims"][0]))
    return payload


def _response(output: dict, *, response_id: str) -> dict:
    return {
        "id": response_id,
        "status": "completed",
        "model": "gpt-5.6-terra",
        "store": False,
        "tools": [],
        "error": None,
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(output, sort_keys=True),
                    }
                ],
            }
        ],
    }


class _Transport:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.payloads: list[dict] = []

    def post(self, *, payload, api_key):
        assert api_key == "sk-test-local-only"
        self.payloads.append(dict(payload))
        if not self.responses:
            raise AssertionError("unexpected extra Responses call")
        return self.responses.pop(0)


def test_duplicate_claim_ids_receive_one_bounded_same_evidence_repair() -> None:
    synthesis_input = _input()
    invalid_payload = _duplicate_claim_payload()
    valid_payload = _valid_draft().model_dump(mode="json")
    transport = _Transport(
        [
            _response(invalid_payload, response_id="resp_duplicate"),
            _response(valid_payload, response_id="resp_repair"),
        ]
    )
    request = build_synthesis_request(
        model_candidate=MODEL_CANDIDATE_LADDER[0],
        synthesis_input=synthesis_input,
    )

    result = execute_synthesis_runtime(
        request=request,
        synthesis_input=synthesis_input,
        research_policy=build_event_research_policy(),
        api_key="sk-test-local-only",
        transport=transport,
    )

    assert result.repair_attempts == 1
    assert isinstance(result.initial_draft, RawCandidateSynthesisDraft)
    assert result.initial_draft.model_dump(mode="json") == invalid_payload
    assert result.initial_validator_error is not None
    assert "claim_id values must be unique" in result.initial_validator_error
    assert result.draft == _valid_draft()
    assert len(transport.payloads) == 2
    repair_input = json.loads(transport.payloads[1]["input"])
    assert repair_input["previous_invalid_draft"] == invalid_payload
    assert repair_input["frozen_synthesis_input"]["evidence_bundle_hash"] == "a" * 64
    assert transport.payloads[1]["store"] is False
    assert transport.payloads[1]["tools"] == []
