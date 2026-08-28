from __future__ import annotations

import json
from datetime import UTC, datetime

from aic.research.model_policy import MODEL_CANDIDATE_LADDER, MODEL_POLICY_VERSION
from aic.research.models import ResearchNeedType
from aic.research.planner import PlannerContextItem, PlannerInputEnvelope, build_planner_request
from aic.research.policy import RESEARCH_POLICY_VERSION, ResearchPolicy
from aic.research.runtime import execute_planner_runtime, load_openai_api_key


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
        material_claim_categories=(
            "business_model",
            "growth_quality",
            "financial_quality",
            "competitive_position",
            "valuation_context",
            "market_context",
            "capital_allocation",
            "catalyst",
            "risk",
            "portfolio_interaction",
        ),
        inference_rule="Explicitly mark inference and bind supporting evidence.",
        unknown_rule="State material unknowns explicitly.",
        conflict_rule="Material conflicts remain visible.",
        numeric_claim_rule="No model arithmetic.",
        research_cutoff_rule="Exclude evidence after cutoff.",
        max_model_calls_per_candidate=3,
        repair_attempt_limit=1,
        failure_behavior="Bounded failure only.",
    )


def _planner_input() -> PlannerInputEnvelope:
    return PlannerInputEnvelope(
        candidate_id="NVDA",
        b2_snapshot_id="B3_CANARY_B2_SNAPSHOT",
        deep_comparison_id="B3_CANARY_DEEP_COMPARISON",
        research_policy_version=RESEARCH_POLICY_VERSION,
        model_policy_version=MODEL_POLICY_VERSION,
        research_cutoff=datetime(2026, 8, 28, 17, 20, tzinfo=UTC),
        context_items=(
            PlannerContextItem(
                item_id="CANARY_CONTEXT_1",
                category="risk",
                evidence_status="PARTIAL",
                description=(
                    "This runtime canary has one bounded B2 evidence item whose exact detail "
                    "remains to be inspected before later research."
                ),
                evidence_refs=("CANARY_EVIDENCE_1",),
            ),
        ),
        allowed_source_handles=("B2:CANARY_EVIDENCE_1",),
    )


def main() -> int:
    api_key = load_openai_api_key()
    policy = _policy()
    planner_input = _planner_input()
    model_candidate = MODEL_CANDIDATE_LADDER[0]
    request = build_planner_request(
        model_candidate=model_candidate,
        planner_input=planner_input,
    )
    result = execute_planner_runtime(
        request=request,
        planner_input=planner_input,
        research_policy=policy,
        api_key=api_key,
    )

    safe_summary = {
        "status": "PASS",
        "candidate": planner_input.candidate_id,
        "response_id": result.call.response_id,
        "requested_model": result.call.requested_model,
        "effective_model": result.call.effective_model,
        "latency_ms": result.call.latency_ms,
        "usage": result.call.usage.model_dump(mode="json"),
        "plan_hash": result.plan_hash,
        "research_plan": result.plan.model_dump(mode="json"),
    }
    print(json.dumps(safe_summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
