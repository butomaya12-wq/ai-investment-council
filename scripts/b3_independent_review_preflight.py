from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping
from uuid import uuid4

from aic.domain.canonical import canonical_sha256


REVIEW_SCRIPT = Path(__file__).with_name("b3_independent_review.py")
DEFAULT_OUTPUT = Path(".aic-runtime/b3_independent_review_preflight.json")
ARTIFACT_VERSION = "B3_INDEPENDENT_REVIEW_PREFLIGHT_ARTIFACT_v0_1"
RUN_CLASS = "B3_INDEPENDENT_REVIEW_LOCAL_ZERO_CALL_PREFLIGHT"


def _load_review_module() -> ModuleType:
    module_name = f"b3_independent_review_preflight_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, REVIEW_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load independent-review runner for preflight")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def _candidate_metrics(record: Mapping[str, Any]) -> dict[str, Any]:
    evidence = record.get("referenced_evidence")
    claims = record.get("claims")
    computed = record.get("referenced_computed_values")
    if not isinstance(evidence, list) or not isinstance(claims, list) or not isinstance(computed, list):
        raise ValueError("preflight candidate review record is incomplete")

    original_chars = 0
    bounded_chars = 0
    truncated = 0
    for item in evidence:
        if not isinstance(item, Mapping):
            raise ValueError("preflight evidence record must be an object")
        original = item.get("original_char_count")
        review_value = item.get("review_value")
        was_truncated = item.get("review_value_truncated")
        if type(original) is not int or original < 0 or not isinstance(review_value, str) or type(was_truncated) is not bool:
            raise ValueError("preflight evidence bound metadata is malformed")
        original_chars += original
        bounded_chars += len(review_value)
        truncated += int(was_truncated)

    return {
        "candidate": record.get("candidate"),
        "claim_count": len(claims),
        "referenced_evidence_count": len(evidence),
        "referenced_computed_value_count": len(computed),
        "truncated_evidence_count": truncated,
        "evidence_original_char_count": original_chars,
        "evidence_review_char_count": bounded_chars,
    }


def _write_artifact(path: Path, artifact: dict[str, Any]) -> None:
    artifact["artifact_hash"] = canonical_sha256(artifact, exclude_fields=("artifact_hash",))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def main() -> int:
    module = _load_review_module()
    repo_root = Path(__file__).resolve().parents[1]
    try:
        handoff = module.load_real_event_handoff(module.DEFAULT_HANDOFF)
        retrieval = module._read_json(module.DEFAULT_RETRIEVAL)
        model_eval = module._read_json(module.DEFAULT_MODEL_EVAL)
        reconciliation = module._read_json(module.DEFAULT_RECONCILIATION)
        module._verify_artifact_hash(retrieval, name="retrieval")
        module._verify_artifact_hash(model_eval, name="model eval")

        authority = module.load_selected_model_authority(module.DEFAULT_SELECTED_MODEL_AUTHORITY_PATH)
        module.verify_model_eval_artifact(model_eval, authority=authority)
        module.load_competition_investment_mandate()
        module._preflight_reconciliation(
            reconciliation,
            retrieval=retrieval,
            authority=authority,
        )

        review_input = module._build_review_input(
            handoff=handoff,
            retrieval=retrieval,
            model_eval=model_eval,
            reconciliation=reconciliation,
            authority=authority,
            repo_root=repo_root,
        )
        secret_hits = module._secret_scan(review_input)
        if secret_hits:
            raise ValueError("review input secret scan failed: " + ",".join(secret_hits))

        request = module.build_independent_review_request(review_input)
        input_text = request.request_payload.get("input")
        if not isinstance(input_text, str):
            raise ValueError("independent review request input must be serialized text")
        request_body = json.dumps(
            request.request_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        candidates = review_input.get("candidates")
        if not isinstance(candidates, list):
            raise ValueError("review input candidates missing")
        candidate_metrics = [_candidate_metrics(item) for item in candidates if isinstance(item, Mapping)]
        if len(candidate_metrics) != 3:
            raise ValueError("preflight requires exact three candidate metrics")

        artifact: dict[str, Any] = {
            "artifact_version": ARTIFACT_VERSION,
            "run_class": RUN_CLASS,
            "status": "READY_FOR_PROVIDER_RETRY_DECISION",
            "reviewer": module.REVIEWER_CANDIDATE.model_dump(mode="json"),
            "review_input_hash": request.input_hash,
            "review_request_hash": request.request_hash,
            "review_input_char_count": len(input_text),
            "review_input_utf8_bytes": len(input_text.encode("utf-8")),
            "request_body_utf8_bytes": len(request_body),
            "candidate_metrics": candidate_metrics,
            "total_referenced_evidence_count": sum(item["referenced_evidence_count"] for item in candidate_metrics),
            "total_truncated_evidence_count": sum(item["truncated_evidence_count"] for item in candidate_metrics),
            "total_evidence_original_char_count": sum(item["evidence_original_char_count"] for item in candidate_metrics),
            "total_evidence_review_char_count": sum(item["evidence_review_char_count"] for item in candidate_metrics),
            "attack_class_count": len(module.ATTACK_CLASSES),
            "model_calls": 0,
            "provider_reads": 0,
            "broker_writes": 0,
            "alpaca_orders": 0,
            "live_money": "PROHIBITED",
            "output_path": str(DEFAULT_OUTPUT),
        }
        _write_artifact(DEFAULT_OUTPUT, artifact)
        print(json.dumps(artifact, indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"B3 independent review preflight failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
