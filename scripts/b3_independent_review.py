from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from time import perf_counter_ns
from typing import Any, Mapping

from aic.domain.canonical import canonical_sha256
from aic.research.handoff import EXPECTED_TOP3, load_real_event_handoff
from aic.research.independent_review import (
    ATTACK_CLASSES,
    INDEPENDENT_REVIEW_VERSION,
    REVIEWER_CANDIDATE,
    bound_review_value,
    build_independent_review_request,
    build_static_safety_manifest,
    independent_review_prompt_hash,
    parse_independent_review_output,
)
from aic.research.mandate import load_competition_investment_mandate
from aic.research.model_selection import (
    DEFAULT_SELECTED_MODEL_AUTHORITY_PATH,
    load_selected_model_authority,
    verify_model_eval_artifact,
)
from aic.research.runtime import (
    StdlibResponsesTransport,
    load_openai_api_key,
    parse_responses_payload,
)


DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_RETRIEVAL = Path(".aic-runtime/b3_retrieval_batch.json")
DEFAULT_MODEL_EVAL = Path(".aic-runtime/b3_model_eval.json")
DEFAULT_RECONCILIATION = Path(".aic-runtime/b3_selected_model_reconciliation.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b3_independent_review.json")
ARTIFACT_VERSION = "B3_INDEPENDENT_REVIEW_ARTIFACT_v0_1"
RUN_CLASS = "B3_INDEPENDENT_READ_ONLY_ACCEPTANCE_REVIEW"

_SECRET_PATTERNS = {
    "OPENAI_API_KEY_NAME": re.compile(r"OPENAI_API_KEY"),
    "OPENAI_SECRET_PREFIX": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}"),
    "ALPACA_SECRET_NAME": re.compile(r"APCA(?:_API|-API)-SECRET(?:_KEY|-KEY)"),
    "AUTH_BEARER": re.compile(r"Authorization\s*:\s*Bearer\s+\S+", re.IGNORECASE),
}


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the final B3 independent read-only acceptance review once. "
            "The reviewer has no tools/provider/broker capability and no repair path."
        )
    )
    parser.add_argument("--handoff", type=Path, default=DEFAULT_HANDOFF)
    parser.add_argument("--retrieval", type=Path, default=DEFAULT_RETRIEVAL)
    parser.add_argument("--model-eval", type=Path, default=DEFAULT_MODEL_EVAL)
    parser.add_argument("--reconciliation", type=Path, default=DEFAULT_RECONCILIATION)
    parser.add_argument("--model-authority", type=Path, default=DEFAULT_SELECTED_MODEL_AUTHORITY_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read review input artifact: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"review input artifact root must be object: {path}")
    return value


def _verify_artifact_hash(payload: Mapping[str, Any], *, name: str) -> None:
    actual = payload.get("artifact_hash")
    if not isinstance(actual, str) or len(actual) != 64:
        raise ValueError(f"{name} artifact_hash missing")
    expected = canonical_sha256(payload, exclude_fields=("artifact_hash",))
    if actual != expected:
        raise ValueError(f"{name} artifact_hash mismatch")


def _candidate_map(payload: Mapping[str, Any], *, name: str) -> dict[str, dict[str, Any]]:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError(f"{name} candidates missing")
    by_candidate = {
        item.get("candidate"): item
        for item in candidates
        if isinstance(item, dict) and isinstance(item.get("candidate"), str)
    }
    if tuple(by_candidate) != EXPECTED_TOP3:
        raise ValueError(f"{name} must contain exact frozen top-3 order")
    return by_candidate


def _safe_source_uri_ref(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return canonical_sha256({"source_uri": value})


def _review_evidence_item(item: Mapping[str, Any]) -> dict[str, Any]:
    evidence_id = item.get("evidence_id")
    if not isinstance(evidence_id, str) or not evidence_id:
        raise ValueError("review evidence item requires evidence_id")
    bounded = bound_review_value(item.get("normalized_value"))
    return {
        "review_ref": f"EVIDENCE:{evidence_id}",
        "evidence_id": evidence_id,
        "provider": item.get("provider"),
        "source_type": item.get("source_type"),
        "source_uri_hash": _safe_source_uri_ref(item.get("source_uri")),
        "entity_id": item.get("entity_id"),
        "field_or_claim": item.get("field_or_claim"),
        "published_at": item.get("published_at"),
        "observed_at": item.get("observed_at"),
        "retrieved_at": item.get("retrieved_at"),
        "as_of": item.get("as_of"),
        "knowable_at_cutoff": item.get("knowable_at_cutoff"),
        "authoritative_for": item.get("authoritative_for"),
        "raw_content_hash": item.get("raw_content_hash"),
        "normalization_version": item.get("normalization_version"),
        **bounded,
    }


def _review_computed_value(item: Mapping[str, Any]) -> dict[str, Any]:
    computed_id = item.get("computed_value_id")
    if not isinstance(computed_id, str) or not computed_id:
        raise ValueError("review computed value requires computed_value_id")
    return {
        "review_ref": f"COMPUTED:{computed_id}",
        "computed_value_id": computed_id,
        "metric_id": item.get("metric_id"),
        "value": item.get("value"),
        "unit": item.get("unit"),
        "as_of": item.get("as_of"),
        "input_refs": item.get("input_refs"),
        "formula_version": item.get("formula_version"),
    }


def _index_by_id(items: Any, *, key: str) -> dict[str, Mapping[str, Any]]:
    if not isinstance(items, list):
        return {}
    out: dict[str, Mapping[str, Any]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        identity = item.get(key)
        if not isinstance(identity, str) or not identity:
            continue
        previous = out.get(identity)
        if previous is not None and canonical_sha256(previous) != canonical_sha256(item):
            raise ValueError(f"duplicate {key} has inconsistent payload: {identity}")
        out[identity] = item
    return out


def _handoff_computed_index(handoff: object, candidate: str) -> dict[str, dict[str, Any]]:
    record = handoff.candidate(candidate)
    return {
        metric.computed_value_id: {
            "computed_value_id": metric.computed_value_id,
            "metric_id": metric.metric_id,
            "value": metric.value,
            "unit": metric.unit,
            "as_of": None,
            "input_refs": None,
            "formula_version": "B2_FROZEN_HANDOFF_METRIC",
        }
        for metric in record.metrics
    }


def _safe_call_review(item: Any, *, label: str) -> dict[str, Any] | None:
    if item is None:
        return None
    if not isinstance(item, Mapping):
        raise ValueError(f"{label} call receipt must be object or null")
    return {
        "review_ref": f"CALL:{label}:{item.get('response_id')}",
        "response_id": item.get("response_id"),
        "requested_model": item.get("requested_model"),
        "effective_model": item.get("effective_model"),
        "output_hash": item.get("output_hash"),
        "usage": item.get("usage"),
        "latency_ms": item.get("latency_ms"),
        "store": item.get("store"),
        "tools_enabled": item.get("tools_enabled"),
    }


def _candidate_review_record(
    *,
    candidate: str,
    retrieval_record: Mapping[str, Any],
    reconciliation_record: Mapping[str, Any],
    handoff: object,
) -> dict[str, Any]:
    if reconciliation_record.get("status") != "CANONICAL_RECONCILED":
        raise ValueError(f"{candidate} is not CANONICAL_RECONCILED")
    if reconciliation_record.get("reconstructibility_status") != "PASS":
        raise ValueError(f"{candidate} reconciliation is not reconstructible")

    research_evidence = retrieval_record.get("research_evidence")
    if not isinstance(research_evidence, Mapping):
        raise ValueError(f"{candidate} retrieval research_evidence missing")
    bundle = research_evidence.get("bundle")
    if not isinstance(bundle, Mapping):
        raise ValueError(f"{candidate} research bundle missing")
    evidence_by_id = _index_by_id(research_evidence.get("evidence_items"), key="evidence_id")
    computed_by_id = _index_by_id(research_evidence.get("computed_values"), key="computed_value_id")
    computed_by_id = {**_handoff_computed_index(handoff, candidate), **computed_by_id}

    claims = reconciliation_record.get("material_claims")
    packet = reconciliation_record.get("candidate_packet")
    if not isinstance(claims, list) or not isinstance(packet, Mapping):
        raise ValueError(f"{candidate} canonical claims/packet missing")

    referenced_evidence: list[str] = []
    referenced_computed: list[str] = []
    review_claims: list[dict[str, Any]] = []
    for claim in claims:
        if not isinstance(claim, Mapping):
            raise ValueError(f"{candidate} material claim must be object")
        claim_id = claim.get("claim_id")
        if not isinstance(claim_id, str) or not claim_id:
            raise ValueError(f"{candidate} material claim_id missing")
        evidence_ids = claim.get("evidence_ids") or []
        computed_ids = claim.get("computed_value_ids") or []
        if not isinstance(evidence_ids, (list, tuple)) or not isinstance(computed_ids, (list, tuple)):
            raise ValueError(f"{candidate} claim refs malformed")
        referenced_evidence.extend(str(value) for value in evidence_ids)
        referenced_computed.extend(str(value) for value in computed_ids)
        review_claims.append({"review_ref": f"CLAIM:{candidate}:{claim_id}", **dict(claim)})

    evidence_ids = tuple(dict.fromkeys(referenced_evidence))
    computed_ids = tuple(dict.fromkeys(referenced_computed))
    missing_evidence = tuple(value for value in evidence_ids if value not in evidence_by_id)
    missing_computed = tuple(value for value in computed_ids if value not in computed_by_id)
    if missing_evidence:
        raise ValueError(f"{candidate} cited evidence unavailable to independent review: {missing_evidence}")
    if missing_computed:
        raise ValueError(f"{candidate} cited computed values unavailable to independent review: {missing_computed}")

    reviewed_evidence = [_review_evidence_item(evidence_by_id[value]) for value in evidence_ids]
    reviewed_computed = [_review_computed_value(computed_by_id[value]) for value in computed_ids]
    packet_hash = packet.get("packet_hash")
    receipt = reconciliation_record.get("model_run_receipt")
    if not isinstance(packet_hash, str) or not isinstance(receipt, Mapping):
        raise ValueError(f"{candidate} packet/receipt hash missing")

    return {
        "candidate": candidate,
        "bundle_review_ref": f"BUNDLE:{candidate}:{bundle.get('bundle_hash')}",
        "research_bundle": dict(bundle),
        "research_status": reconciliation_record.get("research_status"),
        "source_gaps": reconciliation_record.get("source_gaps"),
        "repair_attempts": reconciliation_record.get("repair_attempts"),
        "initial_validator_error": reconciliation_record.get("initial_validator_error"),
        "initial_draft_hash": reconciliation_record.get("initial_draft_hash"),
        "validated_draft_hash": reconciliation_record.get("draft_hash"),
        "claims": review_claims,
        "candidate_packet": {"review_ref": f"PACKET:{candidate}:{packet_hash}", **dict(packet)},
        "referenced_evidence": reviewed_evidence,
        "referenced_computed_values": reviewed_computed,
        "model_run_receipt": {"review_ref": f"RECEIPT:{candidate}:{receipt.get('receipt_hash')}", **dict(receipt)},
        "initial_call": _safe_call_review(reconciliation_record.get("initial_call"), label=f"{candidate}:INITIAL"),
        "repair_call": _safe_call_review(reconciliation_record.get("repair_call"), label=f"{candidate}:REPAIR"),
        "validator_results": reconciliation_record.get("validator_results"),
        "canonical_validator_results": reconciliation_record.get("canonical_validator_results"),
        "reconstructibility_status": reconciliation_record.get("reconstructibility_status"),
    }


def _model_eval_review_record(model_eval: Mapping[str, Any]) -> dict[str, Any]:
    candidates = model_eval.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("model eval candidates missing")
    review_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise ValueError("model eval candidate must be object")
        key = candidate.get("candidate_key")
        cases = candidate.get("cases")
        if not isinstance(key, str) or not isinstance(cases, list):
            raise ValueError("model eval full candidate record incomplete")
        review_cases: list[dict[str, Any]] = []
        for case in cases:
            if not isinstance(case, Mapping):
                raise ValueError("model eval case must be object")
            case_id = case.get("case_id")
            review_cases.append(
                {
                    "review_ref": f"EVAL:{key}:{case_id}:{case.get('result_hash')}",
                    "case_id": case_id,
                    "name": case.get("name"),
                    "stage": case.get("stage"),
                    "critical_safety": case.get("critical_safety"),
                    "passed": case.get("passed"),
                    "findings": case.get("findings"),
                    "model_calls": case.get("model_calls"),
                    "repair_attempts": case.get("repair_attempts"),
                    "latency_ms": case.get("latency_ms"),
                    "estimated_cost_usd": case.get("estimated_cost_usd"),
                    "result_hash": case.get("result_hash"),
                }
            )
        review_candidates.append(
            {
                "candidate_key": key,
                "model": candidate.get("model"),
                "reasoning_effort": candidate.get("reasoning_effort"),
                "all_required_checks_passed": candidate.get("all_required_checks_passed"),
                "critical_safety_failures": candidate.get("critical_safety_failures"),
                "estimated_cost_usd": candidate.get("estimated_cost_usd"),
                "latency_ms": candidate.get("latency_ms"),
                "total_tokens": candidate.get("total_tokens"),
                "record_hash": candidate.get("record_hash"),
                "cases": review_cases,
            }
        )
    return {
        "review_ref": f"MODEL_EVAL:{model_eval.get('artifact_hash')}",
        "case_ids": model_eval.get("case_ids"),
        "selection_rule": model_eval.get("selection_rule"),
        "prompt_manifest": model_eval.get("prompt_manifest"),
        "network_manifest": model_eval.get("network_manifest"),
        "candidates": review_candidates,
        "selection": model_eval.get("selection"),
    }


def _static_manifest_with_refs(repo_root: Path) -> dict[str, Any]:
    manifest = build_static_safety_manifest(repo_root)
    checks = manifest["checks"]
    return {
        **manifest,
        "checks": {
            key: {"review_ref": f"STATIC:{key}", "passed": value}
            for key, value in checks.items()
        },
    }


def _secret_scan(value: Mapping[str, Any]) -> tuple[str, ...]:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return tuple(name for name, pattern in _SECRET_PATTERNS.items() if pattern.search(text))


def _preflight_reconciliation(
    reconciliation: Mapping[str, Any],
    *,
    retrieval: Mapping[str, Any],
    authority: object,
) -> None:
    _verify_artifact_hash(reconciliation, name="selected-model reconciliation")
    if reconciliation.get("run_class") != "B3_SELECTED_MODEL_REAL_CANDIDATE_RECONCILIATION":
        raise ValueError("unexpected reconciliation run class")
    if reconciliation.get("canonical_reconciliation") != "ALL_THREE_SELECTED_MODEL_CANDIDATE_PACKETS_RECONCILED":
        raise ValueError("reconciliation is not complete")
    if reconciliation.get("reconstructibility_status") != "PASS":
        raise ValueError("reconciliation is not reconstructible")
    if reconciliation.get("retrieval_artifact_hash") != retrieval.get("artifact_hash"):
        raise ValueError("reconciliation/retrieval artifact mismatch")
    if reconciliation.get("model_eval_artifact_hash") != authority.model_eval_artifact_hash:
        raise ValueError("reconciliation/model-eval authority mismatch")
    if reconciliation.get("selected_model_authority_hash") != authority.selection_hash:
        raise ValueError("reconciliation selected-model authority mismatch")
    if reconciliation.get("selected_candidate") != authority.selected_candidate.model_dump(mode="json"):
        raise ValueError("reconciliation selected model mismatch")
    if reconciliation.get("broker_writes") != 0 or reconciliation.get("alpaca_orders") != 0:
        raise ValueError("reconciliation contains broker/order writes")
    if reconciliation.get("live_money") != "PROHIBITED":
        raise ValueError("reconciliation live-money invariant drift")


def _build_review_input(
    *,
    handoff: object,
    retrieval: Mapping[str, Any],
    model_eval: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    authority: object,
    repo_root: Path,
) -> dict[str, Any]:
    retrieval_by_candidate = _candidate_map(retrieval, name="retrieval")
    reconciliation_by_candidate = _candidate_map(reconciliation, name="reconciliation")
    static_manifest = _static_manifest_with_refs(repo_root)
    if static_manifest.get("all_checks_pass") is not True:
        raise ValueError("static B3 safety manifest is not fully green")

    candidate_records = [
        _candidate_review_record(
            candidate=candidate,
            retrieval_record=retrieval_by_candidate[candidate],
            reconciliation_record=reconciliation_by_candidate[candidate],
            handoff=handoff,
        )
        for candidate in EXPECTED_TOP3
    ]
    return {
        "review_input_version": "B3_INDEPENDENT_REVIEW_INPUT_v0_1",
        "review_contract": {
            "attack_classes": list(ATTACK_CLASSES),
            "reviewer_must_not_repair": True,
            "pass_requires_no_material_acceptance_gap": True,
            "inconclusive_when_proof_insufficient": True,
        },
        "artifact_lineage": {
            "handoff_hash": handoff.handoff_hash,
            "retrieval_artifact_hash": retrieval.get("artifact_hash"),
            "model_eval_artifact_hash": model_eval.get("artifact_hash"),
            "reconciliation_artifact_hash": reconciliation.get("artifact_hash"),
            "selected_model_authority_hash": authority.selection_hash,
        },
        "production_model_authority": {
            "review_ref": f"MODEL_AUTHORITY:{authority.selection_hash}",
            "selected_candidate": authority.selected_candidate.model_dump(mode="json"),
            "selection_reason_code": authority.selection_reason_code,
            "selected_eval_metrics": authority.selected_eval_metrics.model_dump(mode="json"),
            "full_ladder_pass_summary": {
                key: value.model_dump(mode="json")
                for key, value in authority.full_ladder_pass_summary.items()
            },
            "prompt_manifest": authority.prompt_manifest.model_dump(mode="json"),
        },
        "model_eval_evidence": _model_eval_review_record(model_eval),
        "static_safety_manifest": static_manifest,
        "candidates": candidate_records,
        "known_global_gap": "ALPACA_NEWS_PAGINATION_INCOMPLETE",
        "reviewer_capabilities": {
            "model": REVIEWER_CANDIDATE.model,
            "reasoning_effort": REVIEWER_CANDIDATE.reasoning_effort,
            "store": False,
            "tools": [],
            "provider_credentials": False,
            "broker_credentials": False,
            "repair_attempt_limit": 0,
        },
    }


def _public_summary(artifact: Mapping[str, Any], *, output_path: Path) -> dict[str, Any]:
    review = artifact.get("review")
    attacks = []
    if isinstance(review, Mapping):
        raw_attacks = review.get("attack_results")
        if isinstance(raw_attacks, list):
            attacks = [
                {
                    "attack_class": item.get("attack_class"),
                    "status": item.get("status"),
                    "finding": item.get("finding"),
                    "evidence_refs": item.get("evidence_refs"),
                }
                for item in raw_attacks
                if isinstance(item, Mapping)
            ]
    return {
        "artifact_version": artifact.get("artifact_version"),
        "run_class": artifact.get("run_class"),
        "review_status": artifact.get("review_status"),
        "reviewer": artifact.get("reviewer"),
        "reconciliation_artifact_hash": artifact.get("reconciliation_artifact_hash"),
        "model_eval_artifact_hash": artifact.get("model_eval_artifact_hash"),
        "review_input_hash": artifact.get("review_input_hash"),
        "response_id": None if not isinstance(artifact.get("model_call"), Mapping) else artifact["model_call"].get("response_id"),
        "repair_attempts": artifact.get("repair_attempts"),
        "attack_results": attacks,
        "material_gap_summary": None if not isinstance(review, Mapping) else review.get("material_gap_summary"),
        "inconclusive_summary": None if not isinstance(review, Mapping) else review.get("inconclusive_summary"),
        "provider_reads": artifact.get("provider_reads"),
        "broker_writes": artifact.get("broker_writes"),
        "alpaca_orders": artifact.get("alpaca_orders"),
        "live_money": artifact.get("live_money"),
        "artifact_hash": artifact.get("artifact_hash"),
        "output_path": str(output_path),
    }


def _write_artifact(path: Path, artifact: dict[str, Any]) -> None:
    artifact["artifact_hash"] = canonical_sha256(artifact, exclude_fields=("artifact_hash",))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def main() -> int:
    args = _args()
    repo_root = Path(__file__).resolve().parents[1]
    base_artifact: dict[str, Any] = {
        "artifact_version": ARTIFACT_VERSION,
        "run_class": RUN_CLASS,
        "review_version": INDEPENDENT_REVIEW_VERSION,
        "review_prompt_hash": independent_review_prompt_hash(),
        "reviewer": REVIEWER_CANDIDATE.model_dump(mode="json"),
        "repair_attempts": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    try:
        handoff = load_real_event_handoff(args.handoff)
        retrieval = _read_json(args.retrieval)
        model_eval = _read_json(args.model_eval)
        reconciliation = _read_json(args.reconciliation)
        _verify_artifact_hash(retrieval, name="retrieval")
        _verify_artifact_hash(model_eval, name="model eval")

        authority = load_selected_model_authority(args.model_authority)
        verify_model_eval_artifact(model_eval, authority=authority)
        load_competition_investment_mandate()
        _preflight_reconciliation(
            reconciliation,
            retrieval=retrieval,
            authority=authority,
        )

        review_input = _build_review_input(
            handoff=handoff,
            retrieval=retrieval,
            model_eval=model_eval,
            reconciliation=reconciliation,
            authority=authority,
            repo_root=repo_root,
        )
        secret_hits = _secret_scan(review_input)
        if secret_hits:
            raise ValueError("review input secret scan failed: " + ",".join(secret_hits))

        request = build_independent_review_request(review_input)
        api_key = load_openai_api_key()
        transport = StdlibResponsesTransport(timeout_seconds=120)
        started = perf_counter_ns()
        raw = transport.post(payload=request.request_payload, api_key=api_key)
        latency_ms = max(0, (perf_counter_ns() - started) // 1_000_000)
        call = parse_responses_payload(
            raw,
            requested_model=REVIEWER_CANDIDATE.model,
            latency_ms=latency_ms,
        )
        review = parse_independent_review_output(call.output_text)

        artifact = {
            **base_artifact,
            "handoff_hash": handoff.handoff_hash,
            "retrieval_artifact_hash": retrieval["artifact_hash"],
            "model_eval_artifact_hash": model_eval["artifact_hash"],
            "reconciliation_artifact_hash": reconciliation["artifact_hash"],
            "selected_model_authority_hash": authority.selection_hash,
            "review_input_hash": request.input_hash,
            "review_request_hash": request.request_hash,
            "review_input": review_input,
            "model_call": {
                "response_id": call.response_id,
                "requested_model": call.requested_model,
                "effective_model": call.effective_model,
                "output_hash": call.output_hash,
                "usage": call.usage.model_dump(mode="json"),
                "latency_ms": call.latency_ms,
                "store": False,
                "tools_enabled": False,
            },
            "review": review.model_dump(mode="json", exclude_none=False),
            "review_status": review.review_status,
            "review_reconstructibility_status": "PASS",
            "external_writes": 0,
        }
        _write_artifact(args.output, artifact)
        print(json.dumps(_public_summary(artifact, output_path=args.output), indent=2, ensure_ascii=False))
        if review.review_status == "PASS":
            return 0
        if review.review_status == "MATERIAL_GAP_FOUND":
            return 1
        return 2
    except Exception as exc:
        failure = {
            **base_artifact,
            "review_status": "INCONCLUSIVE",
            "review_reconstructibility_status": "FAILED",
            "error_class": type(exc).__name__,
            "error": str(exc),
            "external_writes": 0,
        }
        _write_artifact(args.output, failure)
        print(f"B3 independent review failed closed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(json.dumps(_public_summary(failure, output_path=args.output), indent=2, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
