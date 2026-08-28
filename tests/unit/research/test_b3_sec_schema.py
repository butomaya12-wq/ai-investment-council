from datetime import UTC, datetime

from aic.research.model_policy import MODEL_CANDIDATE_LADDER, MODEL_POLICY_VERSION
from aic.research.planner import PlannerInputEnvelope, build_planner_request
from aic.research.policy import RESEARCH_POLICY_VERSION
from aic.research.sec_schema import ALLOWED_SEC_SECTION_VALUES


def test_planner_structured_output_constrains_sec_sections_to_frozen_allowlist() -> None:
    planner_input = PlannerInputEnvelope(
        candidate_id="NVDA",
        b2_snapshot_id="b2-real-event-handoff-v0-1",
        deep_comparison_id="b2-real-deep-comparison-v0-1",
        research_policy_version=RESEARCH_POLICY_VERSION,
        model_policy_version=MODEL_POLICY_VERSION,
        research_cutoff=datetime(2026, 8, 28, 17, 20, tzinfo=UTC),
        context_items=(),
        allowed_source_handles=(),
    )
    request = build_planner_request(
        model_candidate=MODEL_CANDIDATE_LADDER[0],
        planner_input=planner_input,
    )
    schema = request.request_payload["text"]["format"]["schema"]
    enum_values = (
        schema["$defs"]["SecFilingSectionParameters"]
        ["properties"]["sections"]["items"]["enum"]
    )
    assert enum_values == list(ALLOWED_SEC_SECTION_VALUES)
    assert enum_values == ["Business", "Risk Factors", "MD&A", "Material 8-K"]
