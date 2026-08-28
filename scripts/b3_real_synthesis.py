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
from aic.research.runtime import ResponsesCallResult, load_openai_api_key
from aic.research.run import CandidateSynthesisRuntimeResult, execute_synthesis_runtime
from aic.research.synthesize import build_synthesis_input, build_synthesis_request


DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_PLANS = Path(".aic-runtime/b3_planner_batch.json")
DEFAULT_RETRIEVAL = Path(".aic-runtime/b3_retrieval_batch.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b3_synthesis_batch.json")
MODEL_CANDIDATE = MODEL_CANDIDATE_LADDER[0]
MANDATE_PERSISTENCE_BLOCKER = "MANDATE_VERSION_UNBOUND"
SYNTHESIS_BATCH_ARTIFACT_VERSION = "B3_SYNTHESIS_BATCH_ARTIFACT_v0_2"


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


def _safe_call_receipt(call: ResponsesCallResult) -> dict[str, Any]:
    """Persist model-call evidence without duplicating raw model prose into logs."""
    return {
        "runtime_version": call.runtime_version,
        "response_id": call.response_id,
        "requested_model": call.requested_model,
        "effective_model": call.effective_model,
        "output_hash": call.output_hash,
        "usage": call.usage.model_dump(mode="json"),
        "latency_ms": call.latency_ms,
        "store": False,
        "tools_enabled": False,
    }


def _validated_candidate_record(
    *,
    candidate: str,
    plan_hash: str,
    bundle_hash: str,
    evidence_status: str,
    source_gaps: tuple[str, ...],
    synthesis_input_hash: str,
    synthesis_request_hash: str,
    runtime: CandidateSynthesisRuntimeResult,
) -> dict[str, Any]:
    initial_draft = runtime.initial_draft.model_dump(mode="json")
    validated_draft = runtime.draft.model_dump(mode="json")
    initial_draft_hash = canonical_sha256(runtime.initial_draft)
    draft_hash = canonical_sha256(runtime.draft)
    final_call = runtime.repair_call or runtime.initial_call

    record: dict[str, Any] = {
        "status": "DRAFT_VALIDATED",
        "candidate": candidate,
        "plan_hash": plan_hash,
        "bundle_hash": bundle_hash,
        "evidence_status": evidence_status,
        "source_gaps": list(source_gaps),
        "synthesis_input_hash": synthesis_input_hash,
        "synthesis_request_hash": synthesis_request_hash,
        "model_candidate": MODEL_CANDIDATE.candidate_key,
        "requested_model": final_call.requested_model,
        "effective_model": final_call.effective_model,
        "response_id": final_call.response_id,
        "output_hash": final_call.output_hash,
        "latency_ms": final_call.latency_ms,
        "usage": final_call.usage.model_dump(mode="json"),
        "repair_attempts": runtime.repair_attempts,
        "repair_request_hash": runtime.repair_request_hash,
        "initial_validator_error": runtime.initial_validator_error,
        "initial_draft_hash": initial_draft_hash,
        "draft_hash": draft_hash,
        "initial_draft": initial_draft,
        "validated_draft": validated_draft,
        "initial_call": _safe_call_receipt(runtime.initial_call),
        "repair_call": (
            None if runtime.repair_call is None else _safe_call_receipt(runtime.repair_call)
        ),
        "claim_count": len(runtime.draft.claims),
        "research_status": runtime.draft.packet.research_status,
        "validator_results": [dict(item) for item in runtime.validator_results],
        "reconstructibility_status": "PASS",
        "canonical_persistence": "BLOCKED",
        "persistence_blocker": MANDATE_PERSISTENCE_BLOCKER,
    }
    record["record_hash"] = canonical_sha256(record)
    return record


def _public_summary(artifact: dict[str, Any], *, output_path: Path) -> dict[str, Any]:
    candidate_summaries: list[dict[str, Any]] = []
    for candidate in artifact["candidates"]:
        if candidate["status"] != "DRAFT_VALIDATED":
            candidate_summaries.append(
                {
                    "candidate": candidate["candidate"],
                    "status": candidate["status"],
                    "error_class": candidate.get("error_class"),
                    "error": candidate.get("error"),
                    "record_hash": candidate.get("record_hash"),
                }
            )
            continue
        candidate_summaries.append(
            {
                "candidate": candidate["candidate"],
                "status": candidate["status"],
                "evidence_status": candidate["evidence_status"],
                "research_status": candidate["research_status"],
                "claim_count": candidate["claim_count"],
                "repair_attempts": candidate["repair_attempts"],
                "response_id": candidate["response_id"],
                "initial_draft_hash": candidate["initial_draft_hash"],
                "draft_hash": candidate["draft_hash"],
                "record_hash": candidate["record_hash"],
                "source_gaps": candidate["source_gaps"],
                "reconstructibility_status": candidate["reconstructibility_status"],
                "canonical_persistence": candidate["canonical_persistence"],
                "persistence_blocker": candidate["persistence_blocker"],
            }
        )

    return {
        "artifact_version": artifact["artifact_version"],
        "run_class": artifact["run_class"],
        "handoff_hash": artifact["handoff_hash"],
        "planner_artifact_hash": artifact["planner_artifact_hash"],
        "retrieval_artifact_hash": artifact["retrieval_artifact_hash"],
        "research_policy_version": artifact["research_policy_version"],
        "model_candidate": artifact["model_candidate"],
        "candidates": candidate_summaries,
        "reconstructibility_status": artifact["reconstructibility_status"],
        "canonical_persistence": artifact["canonical_persistence"],
        "persistence_blocker": artifact["persistence_blocker"],
        "broker_writes": artifact["broker_writes"],
        "alpaca_orders": artifact["alpaca_orders"],
        "live_money": artifact["live_money"],
        "artifact_hash": artifact["artifact_hash"],
        "output_path": str(output_path),
    }


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
        request = None
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
            results.append(
                _validated_candidate_record(
                    candidate=frozen.candidate,
                    plan_hash=frozen.plan_hash,
                    bundle_hash=frozen_evidence.bundle.bundle_hash,
                    evidence_status=frozen_evidence.bundle.status.value,
                    source_gaps=gaps,
                    synthesis_input_hash=request.input_hash,
                    synthesis_request_hash=request.request_hash,
                    runtime=runtime,
                )
            )
        except Exception as exc:
            fatal_failures += 1
            failure_record: dict[str, Any] = {
                "status": "FAILED",
                "candidate": frozen.candidate,
                "plan_hash": frozen.plan_hash,
                "error_class": type(exc).__name__,
                "error": str(exc),
            }
            if request is not None:
                failure_record["synthesis_input_hash"] = request.input_hash
                failure_record["synthesis_request_hash"] = request.request_hash
            failure_record["record_hash"] = canonical_sha256(failure_record)
            results.append(failure_record)

    artifact = {
        "artifact_version": SYNTHESIS_BATCH_ARTIFACT_VERSION,
        "run_class": "B3_REAL_CANDIDATE_SYNTHESIS_RUNTIME",
        "handoff_hash": handoff.handoff_hash,
        "planner_artifact_hash": plans.artifact_hash,
        "retrieval_artifact_hash": retrieval.get("artifact_hash"),
        "research_policy_version": policy.policy_version,
        "model_candidate": MODEL_CANDIDATE.candidate_key,
        "candidates": results,
        "reconstructibility_status": "PASS" if fatal_failures == 0 else "FAILED",
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
            _public_summary(artifact, output_path=args.output),
            indent=2,
            ensure_ascii=False,
        )
    )
    return 1 if fatal_failures else 0


if __name__ == "__main__":
    sys.exit(main())
