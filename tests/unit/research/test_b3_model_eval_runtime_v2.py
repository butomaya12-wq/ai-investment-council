from types import SimpleNamespace

from aic.research.model_eval_runtime import (
    EVAL_VERSION,
    _RecordedPost,
    _calls_from_records,
    _score_e7,
    build_eval_cases,
)
from aic.research.models import ResearchGapPlan
from aic.research.prompts import (
    PLANNER_PROMPT_VERSION,
    SYNTHESIS_PROMPT_VERSION,
)
from aic.research.runtime import RUNTIME_VERSION


MANDATE_VERSION = "ALPACA_COMPETITION_V1_2026_08_29"


def test_clean_candidate_may_have_zero_research_gaps() -> None:
    e1 = next(case for case in build_eval_cases(MANDATE_VERSION) if case.case_id == "E1")
    planner_input = e1.build_input(MANDATE_VERSION)
    plan = ResearchGapPlan(
        research_plan_id="E1_ZERO_GAP_PLAN",
        candidate_id=planner_input.candidate_id,
        b2_snapshot_id=planner_input.b2_snapshot_id,
        deep_comparison_id=planner_input.deep_comparison_id,
        research_policy_version=planner_input.research_policy_version,
        model_policy_version=planner_input.model_policy_version,
        research_cutoff=planner_input.research_cutoff,
        material_questions=(),
        requested_needs=(),
    )
    passed, findings = e1.score(plan)
    assert passed is True
    assert findings == ()


def test_zero_question_plan_still_rejects_orphan_research_need() -> None:
    e2 = next(case for case in build_eval_cases(MANDATE_VERSION) if case.case_id == "E2")
    planner_input = e2.build_input(MANDATE_VERSION)
    valid = ResearchGapPlan(
        research_plan_id="E2_VALID",
        candidate_id=planner_input.candidate_id,
        b2_snapshot_id=planner_input.b2_snapshot_id,
        deep_comparison_id=planner_input.deep_comparison_id,
        research_policy_version=planner_input.research_policy_version,
        model_policy_version=planner_input.model_policy_version,
        research_cutoff=planner_input.research_cutoff,
        material_questions=(),
        requested_needs=(),
    )
    assert valid.material_questions == ()


def test_e7_allows_safe_unresolved_question_without_invented_inference() -> None:
    e7 = next(case for case in build_eval_cases(MANDATE_VERSION) if case.case_id == "E7")
    synthesis_input = e7.build_input(MANDATE_VERSION)
    result = SimpleNamespace(
        draft=SimpleNamespace(
            packet=SimpleNamespace(research_status="COMPLETE"),
            claims=(),
        )
    )
    passed, findings = _score_e7(synthesis_input, result)
    assert passed is True
    assert findings == ()


def test_e7_rejects_cross_category_material_promotion() -> None:
    e7 = next(case for case in build_eval_cases(MANDATE_VERSION) if case.case_id == "E7")
    synthesis_input = e7.build_input(MANDATE_VERSION)
    claim = SimpleNamespace(
        claim_id="E7_BAD_CLAIM",
        claim_kind="INFERENCE",
        category="competitive_position",
        materiality="MATERIAL",
        support_status="SUPPORTED",
        evidence_ids=(synthesis_input.evidence_items[0].evidence_id,),
        computed_value_ids=(),
    )
    result = SimpleNamespace(
        draft=SimpleNamespace(
            packet=SimpleNamespace(research_status="COMPLETE"),
            claims=(claim,),
        )
    )
    passed, findings = _score_e7(synthesis_input, result)
    assert passed is False
    assert any("category-authoritative evidence" in finding for finding in findings)


def test_failed_case_receipt_can_be_reconstructed_from_raw_response() -> None:
    raw = {
        "id": "resp_eval_receipt_test",
        "model": "gpt-5.6-terra",
        "status": "completed",
        "error": None,
        "store": False,
        "tools": [],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 1},
        },
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "{}"}],
            }
        ],
    }
    calls = _calls_from_records(
        (
            _RecordedPost(
                request_payload={"model": "gpt-5.6-terra"},
                raw_payload=raw,
                latency_ms=123,
            ),
        )
    )
    assert len(calls) == 1
    assert calls[0].runtime_version == RUNTIME_VERSION
    assert calls[0].response_id == "resp_eval_receipt_test"
    assert calls[0].latency_ms == 123
    assert calls[0].usage.input_tokens == 10
    assert calls[0].usage.output_tokens == 5


def test_eval_and_prompt_versions_are_bumped_after_blocked_run_review() -> None:
    assert EVAL_VERSION == "B3_MODEL_EVAL_v0_2"
    assert PLANNER_PROMPT_VERSION == "B3_PLANNER_PROMPT_v0_4"
    assert SYNTHESIS_PROMPT_VERSION == "B3_CANDIDATE_SYNTHESIS_PROMPT_v0_2"
