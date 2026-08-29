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
from aic.research.mandate import (
    DEFAULT_LIFECYCLE_POLICY_PATH,
    DEFAULT_MANDATE_PATH,
    DEFAULT_OPTIONS_POLICY_PATH,
    load_competition_decision_lifecycle_policy,
    load_competition_investment_mandate,
    load_competition_options_policy,
)
from aic.research.model_policy import MODEL_CANDIDATE_LADDER
from aic.research.plan_freeze import load_frozen_planner_batch
from aic.research.promotion import (
    bind_mandate_version,
    build_model_run_receipt_from_synthesis_record,
    verify_record_hash,
)
from aic.research.synthesize import CandidateSynthesisDraft, build_synthesis_input, build_synthesis_request
from aic.research.validate import build_canonical_candidate_packet


DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_PLANS = Path(".aic-runtime/b3_planner_batch.json")
DEFAULT_RETRIEVAL = Path(".aic-runtime/b3_retrieval_batch.json")
DEFAULT_SYNTHESIS = Path(".aic-runtime/b3_synthesis_batch.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b3_candidate_packet_promotion.json")
MODEL_CANDIDATE = MODEL_CANDIDATE_LADDER[0]
PROMOTION_ARTIFACT_VERSION = "B3_CANDIDATE_PACKET_PROMOTION_ARTIFACT_v0_1"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Promote already validated B3 synthesis drafts after owner-approved mandate freeze. "
            "No OpenAI/provider/broker call is made by this command."
        )
    )
    parser.add_argument("--handoff", type=Path, default=DEFAULT_HANDOFF)
    parser.add_argument("--plans", type=Path, default=DEFAULT_PLANS)
    parser.add_argument("--retrieval", type=Path, default=DEFAULT_RETRIEVAL)
    parser.add_argument("--synthesis", type=Path, default=DEFAULT_SYNTHESIS)
    parser.add_argument("--mandate", type=Path, default=DEFAULT_MANDATE_PATH)
    parser.add_argument("--options-policy", type=Path, default=DEFAULT_OPTIONS_POLICY_PATH)
    parser.add_argument("--lifecycle-policy", type=Path, default=DEFAULT_LIFECYCLE_POLICY_PATH)
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
    receipts = candidate_payload.get("provider_receipts")
    if not isinstance(receipts, list):
        raise ValueError("retrieval candidate requires provider_receipts array")
    gaps: list[str] = []
    for receipt in receipts:
        if not isinstance(receipt, dict):
            raise ValueError("provider receipt must be an object")
        provider = receipt.get("provider")
        if provider == "ALPACA" and receipt.get("pagination_complete") is False and receipt.get("error") is None:
            gaps.append("ALPACA_NEWS_PAGINATION_INCOMPLETE")
        if receipt.get("error") is not None:
            gaps.append(f"PROVIDER_READ_ERROR:{provider}:{receipt.get('error')}")
    return tuple(dict.fromkeys(gaps))


def _candidate_map(payload: dict[str, Any], *, artifact_name: str) -> dict[str, dict[str, Any]]:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError(f"{artifact_name} requires candidates array")
    by_candidate = {
        item.get("candidate"): item
        for item in candidates
        if isinstance(item, dict) and isinstance(item.get("candidate"), str)
    }
    if tuple(by_candidate) != EXPECTED_TOP3:
        raise ValueError(f"{artifact_name} must contain exact frozen top-3 order")
    return by_candidate


def _verify_artifact_hash(payload: dict[str, Any], *, artifact_name: str) -> None:
    actual = payload.get("artifact_hash")
    if not isinstance(actual, str) or len(actual) != 64:
        raise ValueError(f"{artifact_name} artifact_hash missing")
    expected = canonical_sha256(payload, exclude_fields=("artifact_hash",))
    if actual != expected:
        raise ValueError(f"{artifact_name} artifact_hash mismatch")


def _public_summary(artifact: dict[str, Any], *, output_path: Path) -> dict[str, Any]:
    return {
        "artifact_version": artifact["artifact_version"],
        "run_class": artifact["run_class"],
        "source_synthesis_artifact_hash": artifact["source_synthesis_artifact_hash"],
        "mandate_version": artifact["mandate_version"],
        "mandate_hash": artifact["mandate_hash"],
        "options_policy_hash": artifact["options_policy_hash"],
        "decision_lifecycle_policy_hash": artifact["decision_lifecycle_policy_hash"],
        "candidates": [
            {
                "candidate": item["candidate"],
                "candidate_packet_id": item["candidate_packet"]["candidate_packet_id"],
                "packet_hash": item["candidate_packet"]["packet_hash"],
                "model_run_id": item["model_run_receipt"]["model_run_id"],
                "model_run_receipt_hash": item["model_run_receipt"]["receipt_hash"],
                "material_claim_count": len(item["material_claims"]),
                "status": item["status"],
            }
            for item in artifact["candidates"]
        ],
        "canonical_persistence": artifact["canonical_persistence"],
        "broker_writes": artifact["broker_writes"],
        "alpaca_orders": artifact["alpaca_orders"],
        "live_money": artifact["live_money"],
        "artifact_hash": artifact["artifact_hash"],
        "output_path": str(output_path),
    }


def main() -> int:
    args = _args()
    try:
        handoff = load_real_event_handoff(args.handoff)
        plans = load_frozen_planner_batch(args.plans)
        retrieval = _read_json(args.retrieval)
        synthesis = _read_json(args.synthesis)
        _verify_artifact_hash(retrieval, artifact_name="retrieval")
        _verify_artifact_hash(synthesis, artifact_name="synthesis")

        mandate = load_competition_investment_mandate(
            args.mandate,
            options_policy_path=args.options_policy,
        )
        options_policy = load_competition_options_policy(args.options_policy)
        lifecycle_policy = load_competition_decision_lifecycle_policy(args.lifecycle_policy)

        if plans.handoff_hash != handoff.handoff_hash:
            raise ValueError("planner artifact does not match frozen B2 handoff")
        if retrieval.get("handoff_hash") != handoff.handoff_hash:
            raise ValueError("retrieval artifact does not match frozen B2 handoff")
        if retrieval.get("planner_artifact_hash") != plans.artifact_hash:
            raise ValueError("retrieval artifact does not match frozen planner batch")
        if synthesis.get("handoff_hash") != handoff.handoff_hash:
            raise ValueError("synthesis artifact does not match frozen B2 handoff")
        if synthesis.get("planner_artifact_hash") != plans.artifact_hash:
            raise ValueError("synthesis artifact does not match frozen planner batch")
        if synthesis.get("retrieval_artifact_hash") != retrieval.get("artifact_hash"):
            raise ValueError("synthesis artifact does not match frozen retrieval batch")
        if synthesis.get("reconstructibility_status") != "PASS":
            raise ValueError("synthesis batch is not reconstructible")

        retrieval_by_candidate = _candidate_map(retrieval, artifact_name="retrieval")
        synthesis_by_candidate = _candidate_map(synthesis, artifact_name="synthesis")
        research_policy = build_event_research_policy()
        promoted_records: list[dict[str, Any]] = []

        for frozen in plans.results:
            candidate = frozen.candidate
            retrieval_record = retrieval_by_candidate[candidate]
            synthesis_record = synthesis_by_candidate[candidate]
            verify_record_hash(synthesis_record)
            if synthesis_record.get("status") != "DRAFT_VALIDATED":
                raise ValueError(f"{candidate} synthesis record is not DRAFT_VALIDATED")
            if retrieval_record.get("plan_hash") != frozen.plan_hash:
                raise ValueError(f"{candidate} retrieval plan_hash mismatch")
            if synthesis_record.get("plan_hash") != frozen.plan_hash:
                raise ValueError(f"{candidate} synthesis plan_hash mismatch")

            research_evidence_raw = retrieval_record.get("research_evidence")
            if not isinstance(research_evidence_raw, dict):
                raise ValueError(f"{candidate} retrieval lacks research_evidence")
            frozen_evidence = ResearchEvidenceFreezeResult.model_validate(research_evidence_raw)
            if retrieval_record.get("bundle_hash") != frozen_evidence.bundle.bundle_hash:
                raise ValueError(f"{candidate} bundle_hash mismatch")
            if synthesis_record.get("bundle_hash") != frozen_evidence.bundle.bundle_hash:
                raise ValueError(f"{candidate} synthesis bundle_hash mismatch")

            gaps = _source_gaps(retrieval_record)
            legacy_input = build_synthesis_input(
                handoff=handoff,
                plan=frozen.research_plan,
                frozen_evidence=frozen_evidence,
                mandate_version=None,
                application_source_gaps=gaps,
            )
            legacy_request = build_synthesis_request(
                model_candidate=MODEL_CANDIDATE,
                synthesis_input=legacy_input,
            )
            if synthesis_record.get("synthesis_input_hash") != legacy_request.input_hash:
                raise ValueError(f"{candidate} legacy synthesis input hash mismatch")
            if synthesis_record.get("synthesis_request_hash") != legacy_request.request_hash:
                raise ValueError(f"{candidate} legacy synthesis request hash mismatch")

            draft_raw = synthesis_record.get("validated_draft")
            if not isinstance(draft_raw, dict):
                raise ValueError(f"{candidate} validated_draft missing")
            draft = CandidateSynthesisDraft.model_validate(draft_raw)
            if synthesis_record.get("draft_hash") != canonical_sha256(draft):
                raise ValueError(f"{candidate} validated draft hash mismatch")

            promoted_input = bind_mandate_version(
                legacy_input,
                mandate_version=mandate.version,
            )
            model_receipt = build_model_run_receipt_from_synthesis_record(
                candidate=candidate,
                record=synthesis_record,
                model_candidate=MODEL_CANDIDATE,
                research_policy_version=research_policy.policy_version,
                research_snapshot_hash=frozen_evidence.bundle.bundle_hash,
                synthesis_artifact_hash=synthesis["artifact_hash"],
            )
            promoted = build_canonical_candidate_packet(
                draft,
                synthesis_input=promoted_input,
                research_policy=research_policy,
                model_run_id=model_receipt.model_run_id,
                model_output_hash=synthesis_record["output_hash"],
            )
            promoted_records.append(
                {
                    "candidate": candidate,
                    "status": "CANONICAL_PROMOTED",
                    "legacy_synthesis_input_hash": legacy_request.input_hash,
                    "promoted_synthesis_input_hash": canonical_sha256(promoted_input),
                    "validated_draft_hash": synthesis_record["draft_hash"],
                    "model_run_receipt": model_receipt.model_dump(mode="json", exclude_none=False),
                    "material_claims": [
                        claim.model_dump(mode="json", exclude_none=False)
                        for claim in promoted.material_claims
                    ],
                    "candidate_packet": promoted.candidate_packet.model_dump(
                        mode="json", exclude_none=False
                    ),
                    "validator_results": [dict(item) for item in promoted.validator_results],
                }
            )

        artifact: dict[str, Any] = {
            "artifact_version": PROMOTION_ARTIFACT_VERSION,
            "run_class": "B3_CANONICAL_CANDIDATE_PACKET_PROMOTION",
            "handoff_hash": handoff.handoff_hash,
            "planner_artifact_hash": plans.artifact_hash,
            "retrieval_artifact_hash": retrieval["artifact_hash"],
            "source_synthesis_artifact_hash": synthesis["artifact_hash"],
            "mandate_version": mandate.version,
            "mandate_hash": mandate.mandate_hash,
            "investment_mandate": mandate.model_dump(mode="json", exclude_none=False),
            "options_policy_hash": options_policy["policy_hash"],
            "decision_lifecycle_policy_hash": lifecycle_policy.policy_hash,
            "decision_lifecycle_policy": lifecycle_policy.model_dump(mode="json", exclude_none=False),
            "candidates": promoted_records,
            "canonical_persistence": "CANONICAL_OBJECTS_PROMOTED_TO_HASH_BOUND_RUNTIME_ARTIFACT",
            "external_writes": 0,
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
        print(json.dumps(_public_summary(artifact, output_path=args.output), indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"B3 canonical promotion failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
