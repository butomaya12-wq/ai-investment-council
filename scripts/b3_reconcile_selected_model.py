from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter_ns
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256
from aic.domain.contracts import MODEL_RUN_RECEIPT_V1
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
from aic.research.model_policy import MODEL_POLICY_VERSION
from aic.research.model_selection import (
    DEFAULT_SELECTED_MODEL_AUTHORITY_PATH,
    load_selected_model_authority,
    verify_model_eval_artifact,
)
from aic.research.plan_freeze import load_frozen_planner_batch
from aic.research.prompts import (
    SYNTHESIS_PROMPT_VERSION,
    SYNTHESIS_REPAIR_PROMPT_VERSION,
)
from aic.research.runtime import (
    ResponsesCallResult,
    StdlibResponsesTransport,
    load_openai_api_key,
    parse_responses_payload,
)
from aic.research.run import CandidateSynthesisRuntimeResult, execute_synthesis_runtime
from aic.research.synthesize import (
    SYNTHESIS_SCHEMA_NAME,
    build_synthesis_input,
    build_synthesis_request,
)
from aic.research.validate import build_canonical_candidate_packet


DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_PLANS = Path(".aic-runtime/b3_planner_batch.json")
DEFAULT_RETRIEVAL = Path(".aic-runtime/b3_retrieval_batch.json")
DEFAULT_MODEL_EVAL = Path(".aic-runtime/b3_model_eval.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b3_selected_model_reconciliation.json")
ARTIFACT_VERSION = "B3_SELECTED_MODEL_RECONCILIATION_ARTIFACT_v0_1"
RUN_CLASS = "B3_SELECTED_MODEL_REAL_CANDIDATE_RECONCILIATION"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Re-synthesize the frozen B3 top-3 once with the eval-selected model/current "
            "prompt, validate, and build canonical CandidatePackets. Planner/retrieval are "
            "not rerun and no broker API is available."
        )
    )
    parser.add_argument("--handoff", type=Path, default=DEFAULT_HANDOFF)
    parser.add_argument("--plans", type=Path, default=DEFAULT_PLANS)
    parser.add_argument("--retrieval", type=Path, default=DEFAULT_RETRIEVAL)
    parser.add_argument("--model-eval", type=Path, default=DEFAULT_MODEL_EVAL)
    parser.add_argument("--model-authority", type=Path, default=DEFAULT_SELECTED_MODEL_AUTHORITY_PATH)
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


def _verify_artifact_hash(payload: Mapping[str, Any], *, name: str) -> None:
    actual = payload.get("artifact_hash")
    if not isinstance(actual, str) or len(actual) != 64:
        raise ValueError(f"{name} artifact_hash missing")
    expected = canonical_sha256(payload, exclude_fields=("artifact_hash",))
    if actual != expected:
        raise ValueError(f"{name} artifact_hash mismatch")


def _candidate_map(payload: Mapping[str, Any], *, artifact_name: str) -> dict[str, dict[str, Any]]:
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


def _source_gaps(candidate_payload: Mapping[str, Any]) -> tuple[str, ...]:
    receipts = candidate_payload.get("provider_receipts")
    if not isinstance(receipts, list):
        raise ValueError("retrieval candidate requires provider_receipts array")
    gaps: list[str] = []
    for receipt in receipts:
        if not isinstance(receipt, Mapping):
            raise ValueError("provider receipt must be an object")
        provider = receipt.get("provider")
        if provider == "ALPACA" and receipt.get("pagination_complete") is False and receipt.get("error") is None:
            gaps.append("ALPACA_NEWS_PAGINATION_INCOMPLETE")
        if receipt.get("error") is not None:
            gaps.append(f"PROVIDER_READ_ERROR:{provider}:{receipt.get('error')}")
    return tuple(dict.fromkeys(gaps))


def _safe_call_receipt(call: ResponsesCallResult) -> dict[str, Any]:
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


@dataclass(frozen=True, slots=True)
class _RecordedPost:
    request_payload: Mapping[str, Any]
    raw_payload: Mapping[str, Any]
    latency_ms: int


class _RecordingTransport:
    def __init__(self) -> None:
        self._delegate = StdlibResponsesTransport()
        self.records: list[_RecordedPost] = []

    def post(self, *, payload: Mapping[str, Any], api_key: str) -> Mapping[str, Any]:
        started = perf_counter_ns()
        raw = self._delegate.post(payload=payload, api_key=api_key)
        latency_ms = max(0, (perf_counter_ns() - started) // 1_000_000)
        if isinstance(raw, Mapping):
            self.records.append(
                _RecordedPost(dict(payload), dict(raw), latency_ms)
            )
        return raw

    def safe_receipts(self) -> list[dict[str, Any]]:
        receipts: list[dict[str, Any]] = []
        for record in self.records:
            requested = record.request_payload.get("model")
            if not isinstance(requested, str) or not requested:
                continue
            try:
                call = parse_responses_payload(
                    record.raw_payload,
                    requested_model=requested,
                    latency_ms=record.latency_ms,
                )
            except RuntimeError:
                continue
            receipts.append(_safe_call_receipt(call))
        return receipts


def _runtime_record(
    *,
    candidate: str,
    plan_hash: str,
    bundle_hash: str,
    evidence_status: str,
    source_gaps: tuple[str, ...],
    synthesis_input_hash: str,
    synthesis_request_hash: str,
    runtime: CandidateSynthesisRuntimeResult,
    model_candidate_key: str,
) -> dict[str, Any]:
    initial_payload = runtime.initial_draft.model_dump(mode="json")
    validated_payload = runtime.draft.model_dump(mode="json")
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
        "model_candidate": model_candidate_key,
        "requested_model": final_call.requested_model,
        "effective_model": final_call.effective_model,
        "response_id": final_call.response_id,
        "output_hash": final_call.output_hash,
        "latency_ms": final_call.latency_ms,
        "usage": final_call.usage.model_dump(mode="json"),
        "repair_attempts": runtime.repair_attempts,
        "repair_request_hash": runtime.repair_request_hash,
        "initial_validator_error": runtime.initial_validator_error,
        "initial_draft_hash": canonical_sha256(runtime.initial_draft),
        "draft_hash": canonical_sha256(runtime.draft),
        "initial_draft": initial_payload,
        "validated_draft": validated_payload,
        "initial_call": _safe_call_receipt(runtime.initial_call),
        "repair_call": None if runtime.repair_call is None else _safe_call_receipt(runtime.repair_call),
        "claim_count": len(runtime.draft.claims),
        "research_status": runtime.draft.packet.research_status,
        "validator_results": [dict(item) for item in runtime.validator_results],
        "reconstructibility_status": "PASS",
    }
    record["record_hash"] = canonical_sha256(record)
    return record


def _build_model_run_receipt(
    *,
    candidate: str,
    run_id: str,
    record: Mapping[str, Any],
    model_candidate: object,
    research_policy_version: str,
    research_snapshot_hash: str,
):
    repair_attempts = record["repair_attempts"]
    stage = "SYNTHESIS" if repair_attempts == 0 else "REPAIR"
    prompt_version = SYNTHESIS_PROMPT_VERSION if repair_attempts == 0 else SYNTHESIS_REPAIR_PROMPT_VERSION
    reason = None if repair_attempts == 0 else record["initial_validator_error"]
    usage = record["usage"]
    model_run_id = f"B3_SELECTED_{stage}_{candidate}_{record['output_hash'][:16]}"
    return MODEL_RUN_RECEIPT_V1.from_unhashed(
        model_run_id=model_run_id,
        run_id=run_id,
        candidate_id=candidate,
        stage=stage,
        prompt_version=prompt_version,
        schema_version=SYNTHESIS_SCHEMA_NAME,
        research_policy_version=research_policy_version,
        model_policy_version=MODEL_POLICY_VERSION,
        requested_model=record["requested_model"],
        effective_model=record["effective_model"],
        reasoning_effort=getattr(model_candidate, "reasoning_effort"),
        openai_response_id=record["response_id"],
        input_bundle_hash=record["synthesis_input_hash"],
        research_snapshot_hash=research_snapshot_hash,
        output_hash=record["output_hash"],
        latency_ms=record["latency_ms"],
        usage_input_tokens=usage["input_tokens"],
        usage_output_tokens=usage["output_tokens"],
        usage_reasoning_tokens=usage["reasoning_tokens"],
        usage_cached_tokens=usage["cached_tokens"],
        validator_results=record["validator_results"],
        repair_or_escalation_reason=reason,
        final_status="DRAFT_VALIDATED",
        store=False,
        tools_enabled=False,
    )


def _public_summary(artifact: Mapping[str, Any], *, output_path: Path) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for item in artifact["candidates"]:
        if item["status"] != "CANONICAL_RECONCILED":
            candidates.append(
                {
                    "candidate": item["candidate"],
                    "status": item["status"],
                    "error_class": item.get("error_class"),
                    "error": item.get("error"),
                    "model_calls": len(item.get("safe_call_receipts", [])),
                }
            )
            continue
        candidates.append(
            {
                "candidate": item["candidate"],
                "status": item["status"],
                "research_status": item["research_status"],
                "claim_count": item["claim_count"],
                "repair_attempts": item["repair_attempts"],
                "response_id": item["response_id"],
                "draft_hash": item["draft_hash"],
                "packet_hash": item["candidate_packet"]["packet_hash"],
                "model_run_receipt_hash": item["model_run_receipt"]["receipt_hash"],
                "source_gaps": item["source_gaps"],
                "reconstructibility_status": item["reconstructibility_status"],
            }
        )
    return {
        "artifact_version": artifact["artifact_version"],
        "run_class": artifact["run_class"],
        "model_eval_artifact_hash": artifact["model_eval_artifact_hash"],
        "selected_model_authority_hash": artifact["selected_model_authority_hash"],
        "selected_candidate": artifact["selected_candidate"],
        "mandate_version": artifact["mandate_version"],
        "retrieval_artifact_hash": artifact["retrieval_artifact_hash"],
        "candidates": candidates,
        "reconstructibility_status": artifact["reconstructibility_status"],
        "canonical_reconciliation": artifact["canonical_reconciliation"],
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
        model_eval = _read_json(args.model_eval)
        _verify_artifact_hash(retrieval, name="retrieval")

        authority = load_selected_model_authority(args.model_authority)
        verify_model_eval_artifact(model_eval, authority=authority)
        model_candidate = authority.selected_candidate

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

        retrieval_by_candidate = _candidate_map(retrieval, artifact_name="retrieval")
        research_policy = build_event_research_policy()
        api_key = load_openai_api_key()
        run_seed = canonical_sha256(
            {
                "run_class": RUN_CLASS,
                "handoff_hash": handoff.handoff_hash,
                "planner_artifact_hash": plans.artifact_hash,
                "retrieval_artifact_hash": retrieval["artifact_hash"],
                "model_eval_artifact_hash": authority.model_eval_artifact_hash,
                "selected_model_authority_hash": authority.selection_hash,
                "mandate_hash": mandate.mandate_hash,
                "prompt_manifest": authority.prompt_manifest,
            }
        )
        run_id = f"B3_SELECTED_RECON_{run_seed[:16]}"

        candidate_records: list[dict[str, Any]] = []
        failures = 0
        for frozen in plans.results:
            candidate = frozen.candidate
            retrieval_record = retrieval_by_candidate[candidate]
            recorder = _RecordingTransport()
            try:
                if retrieval_record.get("plan_hash") != frozen.plan_hash:
                    raise ValueError(f"{candidate} retrieval plan_hash mismatch")
                if retrieval_record.get("status") == "FAILED":
                    raise ValueError(f"{candidate} retrieval candidate is FAILED")
                research_evidence_raw = retrieval_record.get("research_evidence")
                if not isinstance(research_evidence_raw, dict):
                    raise ValueError(f"{candidate} retrieval lacks research_evidence")
                frozen_evidence = ResearchEvidenceFreezeResult.model_validate(research_evidence_raw)
                if retrieval_record.get("bundle_hash") != frozen_evidence.bundle.bundle_hash:
                    raise ValueError(f"{candidate} bundle_hash mismatch")

                gaps = _source_gaps(retrieval_record)
                synthesis_input = build_synthesis_input(
                    handoff=handoff,
                    plan=frozen.research_plan,
                    frozen_evidence=frozen_evidence,
                    mandate_version=mandate.version,
                    application_source_gaps=gaps,
                )
                request = build_synthesis_request(
                    model_candidate=model_candidate,
                    synthesis_input=synthesis_input,
                )
                runtime = execute_synthesis_runtime(
                    request=request,
                    synthesis_input=synthesis_input,
                    research_policy=research_policy,
                    api_key=api_key,
                    transport=recorder,
                )
                record = _runtime_record(
                    candidate=candidate,
                    plan_hash=frozen.plan_hash,
                    bundle_hash=frozen_evidence.bundle.bundle_hash,
                    evidence_status=frozen_evidence.bundle.status.value,
                    source_gaps=gaps,
                    synthesis_input_hash=request.input_hash,
                    synthesis_request_hash=request.request_hash,
                    runtime=runtime,
                    model_candidate_key=model_candidate.candidate_key,
                )
                receipt = _build_model_run_receipt(
                    candidate=candidate,
                    run_id=run_id,
                    record=record,
                    model_candidate=model_candidate,
                    research_policy_version=research_policy.policy_version,
                    research_snapshot_hash=frozen_evidence.bundle.bundle_hash,
                )
                promoted = build_canonical_candidate_packet(
                    runtime.draft,
                    synthesis_input=synthesis_input,
                    research_policy=research_policy,
                    model_run_id=receipt.model_run_id,
                    model_output_hash=record["output_hash"],
                )
                record.update(
                    {
                        "status": "CANONICAL_RECONCILED",
                        "model_run_receipt": receipt.model_dump(mode="json", exclude_none=False),
                        "material_claims": [
                            claim.model_dump(mode="json", exclude_none=False)
                            for claim in promoted.material_claims
                        ],
                        "candidate_packet": promoted.candidate_packet.model_dump(
                            mode="json", exclude_none=False
                        ),
                        "canonical_validator_results": [
                            dict(item) for item in promoted.validator_results
                        ],
                    }
                )
                record["record_hash"] = canonical_sha256(record, exclude_fields=("record_hash",))
                candidate_records.append(record)
            except Exception as exc:
                failures += 1
                failed = {
                    "candidate": candidate,
                    "status": "FAILED",
                    "error_class": type(exc).__name__,
                    "error": str(exc),
                    "safe_call_receipts": recorder.safe_receipts(),
                    "reconstructibility_status": "FAILED",
                }
                failed["record_hash"] = canonical_sha256(failed)
                candidate_records.append(failed)

        batch_reconstructible = failures == 0 and all(
            item.get("reconstructibility_status") == "PASS" for item in candidate_records
        )
        artifact: dict[str, Any] = {
            "artifact_version": ARTIFACT_VERSION,
            "run_class": RUN_CLASS,
            "run_id": run_id,
            "handoff_hash": handoff.handoff_hash,
            "planner_artifact_hash": plans.artifact_hash,
            "retrieval_artifact_hash": retrieval["artifact_hash"],
            "model_eval_artifact_hash": authority.model_eval_artifact_hash,
            "selected_model_authority_hash": authority.selection_hash,
            "selected_candidate": model_candidate.model_dump(mode="json"),
            "prompt_manifest": authority.prompt_manifest.model_dump(mode="json"),
            "mandate_version": mandate.version,
            "mandate_hash": mandate.mandate_hash,
            "options_policy_hash": options_policy["policy_hash"],
            "decision_lifecycle_policy_hash": lifecycle_policy.policy_hash,
            "candidates": candidate_records,
            "reconstructibility_status": "PASS" if batch_reconstructible else "FAILED",
            "canonical_reconciliation": (
                "ALL_THREE_SELECTED_MODEL_CANDIDATE_PACKETS_RECONCILED"
                if failures == 0
                else "BLOCKED"
            ),
            "provider_reads": 0,
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
        return 1 if failures else 0
    except Exception as exc:
        print(f"B3 selected-model reconciliation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
