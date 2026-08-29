from __future__ import annotations

from copy import deepcopy
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from aic.council.claim_promotion_authority import load_claim_promotion_normalization
from aic.council.model_input import build_initial_model_inputs
from aic.council.model_policy import INITIAL_MODEL_LADDER
from aic.council.models import CouncilInputFreezeArtifact, CouncilLane
from aic.council.request import (
    CouncilRequestStage,
    assert_request_invariants,
    build_initial_request,
)
from aic.domain.canonical import canonical_sha256
from aic.research.handoff import load_real_event_handoff


DEFAULT_FREEZE = Path(".aic-runtime/b4_council_input_freeze.json")
DEFAULT_RECONCILIATION = Path(".aic-runtime/b3_selected_model_reconciliation.json")
DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b4_initial_request_preflight.json")
ARTIFACT_VERSION = "B4_INITIAL_REQUEST_PREFLIGHT_ARTIFACT_v0_1"
RUN_CLASS = "B4_LOCAL_ZERO_CALL_REAL_INITIAL_REQUEST_PREFLIGHT"
SEMANTIC_SCHEMA_NORMALIZATION_VERSION = "B4_INITIAL_SCHEMA_SEMANTIC_NORMALIZATION_v0_1"
_MODEL_RUN_REF_SENTINEL = "__APPLICATION_MODEL_RUN_REF__"

_STAGE_LANE = (
    (CouncilRequestStage.BULL_INITIAL, CouncilLane.BULL),
    (CouncilRequestStage.BEAR_INITIAL, CouncilLane.BEAR),
    (CouncilRequestStage.RED_TEAM_INITIAL, CouncilLane.RED_TEAM),
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"artifact root must be object: {path}")
    return value


def _request_bytes(payload: Any) -> int:
    return len(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _semantic_schema_hash(schema: Mapping[str, Any]) -> str:
    """Hash initial-output semantics while normalizing only per-run lineage.

    ``model_run_ref`` is application-owned and must be unique for each model
    candidate evaluated for the same logical Council call. Its const therefore
    legitimately differs across L1..L4. No other schema difference is ignored.
    """

    normalized = deepcopy(dict(schema))
    matches = 0

    def visit(node: Any) -> None:
        nonlocal matches
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict) and "model_run_ref" in properties:
                prop = properties["model_run_ref"]
                if not isinstance(prop, dict):
                    raise ValueError("model_run_ref schema property must be an object")
                if prop.get("type") != "string":
                    raise ValueError("model_run_ref schema property must remain a string")
                const = prop.get("const")
                if not isinstance(const, str) or not const:
                    raise ValueError("model_run_ref schema property must have application const binding")
                prop["const"] = _MODEL_RUN_REF_SENTINEL
                matches += 1
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(normalized)
    if matches != 1:
        raise ValueError("initial schema must contain exactly one model_run_ref const binding")
    return canonical_sha256(
        {
            "normalization_version": SEMANTIC_SCHEMA_NORMALIZATION_VERSION,
            "schema": normalized,
        }
    )


def main() -> int:
    try:
        freeze_raw = _read_json(DEFAULT_FREEZE)
        freeze = CouncilInputFreezeArtifact.model_validate(freeze_raw)
        reconciliation = _read_json(DEFAULT_RECONCILIATION)
        handoff = load_real_event_handoff(DEFAULT_HANDOFF)
        normalization = load_claim_promotion_normalization()
        model_inputs = build_initial_model_inputs(freeze, reconciliation, handoff)

        request_variants: list[dict[str, Any]] = []
        logical_calls: list[dict[str, Any]] = []
        for model_input in model_inputs:
            model_input_payload = model_input.model_dump(mode="json", exclude_none=False)
            for stage, lane in _STAGE_LANE:
                logical_key = f"{model_input.candidate_id}:{lane.value}"
                logical_hashes: list[str] = []
                schema_hashes: list[str] = []
                semantic_schema_hashes: list[str] = []
                for model_candidate in INITIAL_MODEL_LADDER:
                    model_run_ref = (
                        f"B4_INITIAL_{model_input.candidate_id}_{lane.value}_"
                        f"{model_candidate.candidate_key}_{model_input.model_input_hash[:12]}"
                    )
                    request = build_initial_request(
                        stage=stage,
                        model_candidate=model_candidate,
                        bundle=freeze.bundles[freeze.candidate_order.index(model_input.candidate_id)],
                        model_run_ref=model_run_ref,
                        model_input=model_input_payload,
                        allowed_data_gap_refs=model_input.data_gap_refs,
                    )
                    assert_request_invariants(request)
                    request_payload = request.request_payload
                    schema = request_payload["text"]["format"]["schema"]
                    schema_hash = canonical_sha256(schema)
                    semantic_schema_hash = _semantic_schema_hash(schema)
                    request_variants.append(
                        {
                            "logical_call": logical_key,
                            "candidate": model_input.candidate_id,
                            "lane": lane.value,
                            "stage": stage.value,
                            "model_candidate_key": model_candidate.candidate_key,
                            "model": model_candidate.model,
                            "reasoning_effort": model_candidate.reasoning_effort,
                            "model_run_ref": model_run_ref,
                            "model_input_hash": model_input.model_input_hash,
                            "request_hash": request.request_hash,
                            "schema_hash": schema_hash,
                            "semantic_schema_hash": semantic_schema_hash,
                            "request_body_utf8_bytes": _request_bytes(request_payload),
                            "store": request_payload["store"],
                            "tools": request_payload["tools"],
                            "parallel_tool_calls": request_payload["parallel_tool_calls"],
                            "truncation": request_payload["truncation"],
                            "strict_json_schema": request_payload["text"]["format"]["strict"],
                        }
                    )
                    logical_hashes.append(request.request_hash)
                    schema_hashes.append(schema_hash)
                    semantic_schema_hashes.append(semantic_schema_hash)
                logical_calls.append(
                    {
                        "logical_call": logical_key,
                        "candidate": model_input.candidate_id,
                        "lane": lane.value,
                        "model_input_hash": model_input.model_input_hash,
                        "request_variant_count": len(INITIAL_MODEL_LADDER),
                        "request_hashes": logical_hashes,
                        "schema_hashes": list(dict.fromkeys(schema_hashes)),
                        "semantic_schema_hashes": list(dict.fromkeys(semantic_schema_hashes)),
                        "allowed_schema_variation": "model_run_ref.const only",
                    }
                )

        if len(logical_calls) != 9:
            raise ValueError("B4 initial preflight requires exactly 9 logical calls")
        if len(request_variants) != 9 * len(INITIAL_MODEL_LADDER):
            raise ValueError("B4 initial preflight request-variant count mismatch")
        if any(len(item["semantic_schema_hashes"]) != 1 for item in logical_calls):
            raise ValueError(
                "B4 logical-call semantic schema varies beyond application-owned model_run_ref"
            )

        artifact: dict[str, Any] = {
            "artifact_version": ARTIFACT_VERSION,
            "run_class": RUN_CLASS,
            "status": "READY_FOR_INITIAL_STAGE_MODEL_EVAL_PREFLIGHT",
            "b4_input_freeze_artifact_hash": freeze.artifact_hash,
            "b3_reconciliation_artifact_hash": freeze.b3_reconciliation_artifact_hash,
            "b2_handoff_hash": handoff.handoff_hash,
            "mandate_version": freeze.mandate_version,
            "claim_promotion_normalization_version": normalization.normalization_version,
            "claim_promotion_normalization_hash": normalization.normalization_hash,
            "semantic_schema_normalization_version": SEMANTIC_SCHEMA_NORMALIZATION_VERSION,
            "semantic_schema_allowed_variation": "model_run_ref.const only",
            "candidate_order": list(freeze.candidate_order),
            "model_inputs": [
                {
                    "candidate": item.candidate_id,
                    "model_input_hash": item.model_input_hash,
                    "candidate_packet_hash": item.council_input_bundle["candidate_packet_hash"],
                    "material_claim_count": len(item.material_claims),
                    "computed_value_count": len(item.computed_values),
                    "data_gap_refs": list(item.data_gap_refs),
                }
                for item in model_inputs
            ],
            "logical_call_count": len(logical_calls),
            "request_variant_count": len(request_variants),
            "initial_model_ladder_count": len(INITIAL_MODEL_LADDER),
            "logical_calls": logical_calls,
            "request_variants": request_variants,
            "model_calls": 0,
            "provider_reads": 0,
            "broker_writes": 0,
            "alpaca_orders": 0,
            "live_money": "PROHIBITED",
        }
        artifact["artifact_hash"] = canonical_sha256(artifact)
        DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_OUTPUT.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(json.dumps(artifact, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"B4 initial request preflight failed closed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
