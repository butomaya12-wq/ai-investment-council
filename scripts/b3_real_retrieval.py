from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aic.data.providers import (
    load_alpaca_market_data_credentials,
    load_sec_user_agent,
)
from aic.domain.canonical import canonical_sha256
from aic.research.evidence_bundle import freeze_research_evidence_bundle
from aic.research.event_policy import build_event_research_policy
from aic.research.handoff import load_real_event_handoff
from aic.research.plan_freeze import load_frozen_planner_batch
from aic.research.provider_adapters import (
    AlpacaNewsRetrievalAdapter,
    SecFilingRetrievalAdapter,
)
from aic.research.retrieve import (
    RetrievalExecutionStatus,
    RetrievalProvider,
    execute_retrieval_plan,
)


DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_PLANS = Path(".aic-runtime/b3_planner_batch.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b3_retrieval_batch.json")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Execute the frozen B3 top-3 plans through approved read-only provider adapters."
    )
    parser.add_argument("--handoff", type=Path, default=DEFAULT_HANDOFF)
    parser.add_argument("--plans", type=Path, default=DEFAULT_PLANS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = _args()
    handoff = load_real_event_handoff(args.handoff)
    plans = load_frozen_planner_batch(args.plans)
    if plans.handoff_hash != handoff.handoff_hash:
        print("frozen planner artifact does not match B2 event handoff", file=sys.stderr)
        return 2

    policy = build_event_research_policy()
    sec_user_agent = load_sec_user_agent()
    alpaca_key_id, alpaca_secret = load_alpaca_market_data_credentials()
    adapters = {
        RetrievalProvider.SEC: SecFilingRetrievalAdapter(
            handoff=handoff,
            user_agent=sec_user_agent,
        ),
        RetrievalProvider.ALPACA: AlpacaNewsRetrievalAdapter(
            api_key_id=alpaca_key_id,
            api_secret_key=alpaca_secret,
        ),
    }

    candidate_results: list[dict[str, object]] = []
    fatal_failures = 0
    for frozen in plans.results:
        candidate = handoff.candidate(frozen.candidate)
        try:
            retrieval_results = execute_retrieval_plan(
                frozen.research_plan,
                policy=policy,
                adapters=adapters,
            )
            frozen_bundle = freeze_research_evidence_bundle(
                frozen.research_plan,
                retrieval_results,
                bundle_id=f"B3_RESEARCH_BUNDLE_{frozen.candidate}_{frozen.plan_hash[:16]}",
                base_b2_evidence_ids=(candidate.sec_evidence_id,),
                base_computed_value_ids=tuple(
                    metric.computed_value_id for metric in candidate.metrics
                ),
            )
            if any(
                result.status is RetrievalExecutionStatus.FAILED
                for result in retrieval_results
            ):
                fatal_failures += 1
            candidate_results.append(
                {
                    "status": frozen_bundle.bundle.status.value,
                    "candidate": frozen.candidate,
                    "plan_hash": frozen.plan_hash,
                    "bundle_hash": frozen_bundle.bundle.bundle_hash,
                    "retrieval_result_hashes": [
                        result.result_hash for result in retrieval_results
                    ],
                    "provider_receipts": [
                        result.receipt.model_dump(mode="json")
                        for result in retrieval_results
                    ],
                    "research_evidence": frozen_bundle.model_dump(mode="json"),
                }
            )
        except Exception as exc:
            fatal_failures += 1
            candidate_results.append(
                {
                    "status": "FAILED",
                    "candidate": frozen.candidate,
                    "plan_hash": frozen.plan_hash,
                    "error_class": type(exc).__name__,
                    "error": str(exc),
                }
            )

    artifact = {
        "run_class": "B3_REAL_READ_ONLY_RETRIEVAL_RUNTIME",
        "handoff_hash": handoff.handoff_hash,
        "planner_artifact_hash": plans.artifact_hash,
        "research_policy_version": policy.policy_version,
        "candidates": candidate_results,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps({**artifact, "output_path": str(args.output)}, indent=2, ensure_ascii=False))
    return 1 if fatal_failures else 0


if __name__ == "__main__":
    sys.exit(main())
