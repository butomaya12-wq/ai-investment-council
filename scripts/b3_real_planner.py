from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aic.research.handoff import EXPECTED_TOP3, build_planner_input_from_handoff, load_real_event_handoff
from aic.research.model_policy import MODEL_CANDIDATE_LADDER
from aic.research.models import ResearchNeedType
from aic.research.plan_freeze import (
    FrozenPlannerBatch,
    FrozenPlannerResult,
    save_frozen_planner_batch,
)
from aic.research.planner import build_planner_request
from aic.research.policy import RESEARCH_POLICY_VERSION, ResearchPolicy
from aic.research.runtime import execute_planner_runtime, load_openai_api_key


DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")


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
        numeric_claim_rule="No model arithmetic; use existing evidence or computed-value IDs.",
        research_cutoff_rule="Exclude evidence after the frozen research cutoff.",
        max_model_calls_per_candidate=3,
        repair_attempt_limit=1,
        failure_behavior="Bounded failure only; no silent tool or model expansion.",
    )


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run independent real-input B3 planner calls.")
    parser.add_argument(
        "--candidate",
        choices=("ALL",) + EXPECTED_TOP3,
        default="ALL",
        help="Run one frozen top-3 candidate or all three independently.",
    )
    parser.add_argument("--handoff", type=Path, default=DEFAULT_HANDOFF)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional local path for a hash-bound three-candidate planner artifact; requires --candidate ALL.",
    )
    return parser.parse_args()


def main() -> int:
    args = _args()
    if args.output is not None and args.candidate != "ALL":
        print("--output requires --candidate ALL", file=sys.stderr)
        return 2

    handoff = load_real_event_handoff(args.handoff)
    api_key = load_openai_api_key()
    policy = _policy()
    model_candidate = MODEL_CANDIDATE_LADDER[0]
    symbols = handoff.top3 if args.candidate == "ALL" else (args.candidate,)

    results: list[dict[str, object]] = []
    frozen_results: list[FrozenPlannerResult] = []
    failures = 0
    for symbol in symbols:
        try:
            planner_input = build_planner_input_from_handoff(handoff, symbol=symbol)
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
            results.append(
                {
                    "status": "PASS",
                    "candidate": symbol,
                    "handoff_hash": handoff.handoff_hash,
                    "response_id": result.call.response_id,
                    "requested_model": result.call.requested_model,
                    "effective_model": result.call.effective_model,
                    "latency_ms": result.call.latency_ms,
                    "usage": result.call.usage.model_dump(mode="json"),
                    "plan_hash": result.plan_hash,
                    "research_plan": result.plan.model_dump(mode="json"),
                }
            )
            frozen_results.append(
                FrozenPlannerResult(
                    candidate=symbol,
                    handoff_hash=handoff.handoff_hash,
                    response_id=result.call.response_id,
                    requested_model=result.call.requested_model,
                    effective_model=result.call.effective_model,
                    latency_ms=result.call.latency_ms,
                    usage=result.call.usage,
                    plan_hash=result.plan_hash,
                    research_plan=result.plan,
                )
            )
        except Exception as exc:
            failures += 1
            results.append(
                {
                    "status": "FAIL",
                    "candidate": symbol,
                    "handoff_hash": handoff.handoff_hash,
                    "error_class": type(exc).__name__,
                    "error": str(exc),
                }
            )

    artifact_info: dict[str, object] | None = None
    if failures == 0 and args.output is not None:
        batch = FrozenPlannerBatch.build(
            model_candidate=model_candidate.candidate_key,
            handoff_hash=handoff.handoff_hash,
            results=tuple(frozen_results),
        )
        save_frozen_planner_batch(batch, args.output)
        artifact_info = {
            "path": str(args.output),
            "artifact_hash": batch.artifact_hash,
            "artifact_version": batch.artifact_version,
        }

    print(
        json.dumps(
            {
                "run_class": "B3_REAL_INPUT_PLANNER_RUNTIME",
                "model_candidate": model_candidate.candidate_key,
                "results": results,
                "frozen_artifact": artifact_info,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
