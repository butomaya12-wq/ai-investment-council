from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from aic.domain.canonical import canonical_sha256
from aic.research.evidence_bundle import ResearchEvidenceFreezeResult
from aic.research.event_policy import build_event_research_policy
from aic.research.handoff import EXPECTED_TOP3, load_real_event_handoff
from aic.research.model_policy import MODEL_CANDIDATE_LADDER
from aic.research.plan_freeze import load_frozen_planner_batch
from aic.research.runtime import load_openai_api_key
from aic.research.run import execute_synthesis_runtime
from aic.research.synthesize import build_synthesis_input, build_synthesis_request


DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_PLANS = Path(".aic-runtime/b3_planner_batch.json")
DEFAULT_RETRIEVAL = Path(".aic-runtime/b3_retrieval_batch.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b3_synthesis_batch.json")
MODEL_CANDIDATE = MODEL_CANDIDATE_LADDER[0]
MANDATE_PERSISTENCE_BLOCKER = "MANDATE_VERSION_UNBOUND"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run B3 CandidatePacket synthesis drafts from the frozen real retrieval artifact. "
            "This command performs model synthesis + deterministic validation only; canonical "
            "CandidatePacket persistence remains fail-closed until exact mandate lineage exists."
        )
    )
    parser.add_argument("--handoff", type=Path, default=DEFAULT_HANDOFF)
    parser.add_argument("--plans", type=Path, default=DEFAULT_PLANS)
    parser.add_argument("--retrieval", type=Path, default=DEFAULT_RETRIEVAL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read runtime artifact: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"runtime artifact root must be an object: {path}")
    return value


def _source_gaps(candidate_payload: dict[str, Any]) -> tuple[str, ...]:
    gaps: list[str] = []
    receipts = candidate_payload.get("provider_receipts")
    if not isinstance(receipts, list):
        raise ValueError("retrieval candidate requires provider_receipts array")
    for receipt in receipts:
        if not isinstance(receipt, dict):
            raise ValueError("provider receipt must be an object")
        provider = receipt.get("provider")
        pagination_complete = receipt.get("pagination_complete")
        error = receipt.get("error")
        if provider == "ALPACA" and pagination_complete is False and error is None:
            gaps.append("ALPACA_NEWS_PAGINATION_INCOMPLETE")
        if error is not None:
            gaps.append(f"PROVIDER_READ_ERROR:{provider}:{error}")
    return tuple(dict.fromkeys(gaps))


def main() -> int:
    args = _args()
    handoff = load_real_event_handoff(args.handoff)
    plans = load_frozen_planner_batch(args.plans)
    retrieval = _read_json(args.retrieval)

    if plans.handoff_hash != handoff.handoff_hash:
        print("planner artifact does not match frozen B2 handoff", file=sys.stderr)
        return 2
    if retrieval.get("handoff_hash") != handoff.handoff_hash:
        print("retrieval artifact does not match frozen B2 handoff", file=sys.stderr)
        return 2
    if retrieval.get("planner_artifact_hash") != plans.artifact_hash:
        print("retrieval artifact does not match frozen planner batch", file=sys.stderr)
        return 2

    raw_candidates = retrieval.get("candidates")
    if not isinstance(raw_candidates, list):
        print("retrieval artifact requires candidates array", file=sys.stderr)
        return 2
    by_candidate = {
        item.get("candidate"): item
        for item in raw_candidates
        if isinstance(item, dict) and isinstance(item.get("candidate"), str)
    }
    if tuple(by_candidate) != EXPECTED_TOP3:
        print("retrieval artifact must contain exact frozen top-3 order", file=sys.stderr)
        return 2

    policy = build_event_research_policy()
    api_key = load_openai_api_key()
    results: list[dict[str, Any]] = []
    fatal_failures = 0

    for frozen in plans.results:
        candidate_payload = by_candidate[frozen.candidate]
        try:
            if candidate_payload.get("plan_hash") != frozen.plan_hash:
                raise ValueError("candidate retrieval plan_hash mismatch")
            if candidate_payload.get("status") == "FAILED":
                raise ValueError("cannot synthesize a FAILED retrieval candidate")
            research_evidence_raw = candidate_payload.get("research_evidence")
            if not isinstance(research_evidence_raw, dict):
                raise ValueError("candidate retrieval artifact lacks research_evidence")
            frozen_evidence = ResearchEvidenceFreezeResult.model_validate(
                research_evidence_raw
            )
            if candidate_payload.get("bundle_hash") != frozen_evidence.bundle.bundle_hash:
                raise ValueError("candidate bundle_hash mismatch")

            gaps = _source_gaps(candidate_payload)
            if frozen_evidence.bundle.status.value != "COMPLETE" and not gaps:
                raise ValueError(
                    "non-COMPLETE retrieval bundle requires application-owned source gap"
                )
            synthesis_input = build_synthesis_input(
                handoff=handoff,
                plan=frozen.research_plan,
                frozen_evidence=frozen_evidence,
                mandate_version=None,
                application_source_gaps=gaps,
            )
            request = build_synthesis_request(
                model_candidate=MODEL_CANDIDATE,
                synthesis_input=synthesis_input,
            )
            runtime = execute_synthesis_runtime(
                request=request,
                synthesis_input=synthesis_input,
                research_policy=policy,
                api_key=api_key,
            )
            draft_hash = canonical_sha256(runtime.draft)
            final_call = runtime.repair_call or runtime.initial_call
            results.append(
                {
                    "status": "DRAFT_VALIDATED",
                    "candidate": frozen.candidate,
                    "plan_hash": frozen.plan_hash,
                    "bundle_hash": frozen_evidence.bundle.bundle_hash,
                    "evidence_status": frozen_evidence.bundle.status.value,
                    "source_gaps": list(gaps),
                    "synthesis_input_hash": request.input_hash,
                    "synthesis_request_hash": request.request_hash,
                    "model_candidate": MODEL_CANDIDATE.candidate_key,
                    "requested_model": final_call.requested_model,
                    "effective_model": final_call.effective_model,
                    "response_id": final_call.response_id,
                    "output_hash": final_call.output_hash,
                    "latency_ms": final_call.latency_ms,
                    "usage": final_call.usage.model_dump(mode="json"),
                    "repair_attempts": runtime.repair_attempts,
                    "draft_hash": draft_hash,
                    "claim_count": len(runtime.draft.claims),
                    "research_status": runtime.draft.packet.research_status,
                    "validator_results": [dict(item) for item in runtime.validator_results],
                    "canonical_persistence": "BLOCKED",
                    "persistence_blocker": MANDATE_PERSISTENCE_BLOCKER,
                }
            )
        except Exception as exc:
            fatal_failures += 1
            results.append(
                {
                    "status": "FAILED",
                    "candidate": frozen.candidate,
                    "plan_hash": frozen.plan_hash,
                    "error_class": type(exc).__name__,
                    "error": str(exc),
                }
            )

    artifact = {
        "run_class": "B3_REAL_CANDIDATE_SYNTHESIS_RUNTIME",
        "handoff_hash": handoff.handoff_hash,
        "planner_artifact_hash": plans.artifact_hash,
        "retrieval_artifact_hash": retrieval.get("artifact_hash"),
        "research_policy_version": policy.policy_version,
        "model_candidate": MODEL_CANDIDATE.candidate_key,
        "candidates": results,
        "canonical_persistence": "BLOCKED_MANDATE_LINEAGE",
        "persistence_blocker": MANDATE_PERSISTENCE_BLOCKER,
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
    print(
        json.dumps(
            {**artifact, "output_path": str(args.output)},
            indent=2,
            ensure_ascii=False,
        )
    )
    return 1 if fatal_failures else 0


if __name__ == "__main__":
    sys.exit(main())
