from __future__ import annotations

import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator

from aic.council.initial_schema_repair_v03 import (
    INITIAL_SCHEMA_REPAIR_VERSION,
    INITIAL_SCHEMA_VERSION,
    assert_initial_schema_repair,
    build_bounded_initial_request_v03,
)
from aic.council.model_input import build_initial_model_inputs
from aic.council.model_selection import load_initial_selected_model_authority
from aic.council.models import CouncilInputFreezeArtifact, CouncilLane
from aic.council.request import CouncilRequestStage
from aic.domain.canonical import canonical_sha256
from aic.research.handoff import load_real_event_handoff


DEFAULT_RECEIPTS = Path(".aic-runtime/b4_initial_runtime_paid_receipts_v0_2.jsonl")
DEFAULT_FREEZE = Path(".aic-runtime/b4_council_input_freeze.json")
DEFAULT_RECONCILIATION = Path(".aic-runtime/b3_selected_model_reconciliation.json")
DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b4_initial_schema_repair_proof_v0_3.json")

_STAGE_BY_LANE = {
    "BULL": CouncilRequestStage.BULL_INITIAL,
    "BEAR": CouncilRequestStage.BEAR_INITIAL,
    "RED_TEAM": CouncilRequestStage.RED_TEAM_INITIAL,
}


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"artifact root must be object: {path}")
    return value


def _read_receipts(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError("retained paid receipt journal missing object records")
    for row in rows:
        expected = canonical_sha256(row, exclude_fields=("receipt_hash",))
        if row.get("receipt_hash") != expected:
            raise ValueError("retained paid receipt hash mismatch")
    return rows


def _material_non_supported(structured: dict) -> list[dict]:
    claims = structured.get("proposed_claims")
    if not isinstance(claims, list):
        raise ValueError("retained structured output proposed_claims missing")
    return [
        dict(claim)
        for claim in claims
        if isinstance(claim, dict)
        and claim.get("materiality") == "MATERIAL"
        and claim.get("support_status") != "SUPPORTED"
    ]


def main() -> int:
    receipts = _read_receipts(DEFAULT_RECEIPTS)
    receipt = receipts[0]
    if receipt.get("dispatch_index") != 1:
        raise ValueError("schema repair proof requires first retained production receipt")
    if receipt.get("candidate_id") != "NVDA" or receipt.get("lane") != "BULL":
        raise ValueError("schema repair proof retained receipt identity drift")
    if receipt.get("validation_status") != "FAIL":
        raise ValueError("schema repair proof requires retained blocked receipt")
    structured = receipt.get("structured_output")
    if not isinstance(structured, dict):
        raise ValueError("retained structured output missing")
    if receipt.get("structured_output_hash") != canonical_sha256(structured):
        raise ValueError("retained structured output hash mismatch")

    freeze = CouncilInputFreezeArtifact.model_validate(_read_json(DEFAULT_FREEZE))
    reconciliation = _read_json(DEFAULT_RECONCILIATION)
    handoff = load_real_event_handoff(DEFAULT_HANDOFF)
    authority = load_initial_selected_model_authority()
    model_inputs = build_initial_model_inputs(freeze, reconciliation, handoff)
    by_candidate = {item.candidate_id: item for item in model_inputs}
    model_input = by_candidate["NVDA"]
    bundle = freeze.bundles[freeze.candidate_order.index("NVDA")]

    request = build_bounded_initial_request_v03(
        stage=_STAGE_BY_LANE[receipt["lane"]],
        model_candidate=authority.selected_candidate,
        bundle=bundle,
        model_run_ref=receipt["model_run_ref"],
        model_input=model_input.model_dump(mode="json", exclude_none=False),
        allowed_data_gap_refs=model_input.data_gap_refs,
    )
    schema = request.request_payload["text"]["format"]["schema"]
    assert_initial_schema_repair(schema)
    errors = list(Draft202012Validator(schema).iter_errors(structured))
    if not errors:
        raise ValueError("repaired strict schema unexpectedly accepts retained blocked output")

    offenders = _material_non_supported(structured)
    if not offenders:
        raise ValueError("retained blocked output lacks diagnosed material/non-supported claims")

    artifact = {
        "artifact_version": "B4_INITIAL_SCHEMA_REPAIR_PROOF_ARTIFACT_v0_3",
        "status": "PASS_EXACT_RETAINED_OUTPUT_REJECTED_BY_REPAIRED_SCHEMA",
        "schema_repair_version": INITIAL_SCHEMA_REPAIR_VERSION,
        "schema_version": INITIAL_SCHEMA_VERSION,
        "candidate_id": receipt["candidate_id"],
        "lane": receipt["lane"],
        "source_receipt_hash": receipt["receipt_hash"],
        "source_structured_output_hash": receipt["structured_output_hash"],
        "repaired_request_hash": request.request_hash,
        "repaired_schema_hash": canonical_sha256(schema),
        "prompt_contract_version": request.prompt_contract_version,
        "prompt_version": request.prompt_version,
        "prompt_hash": request.prompt_hash,
        "material_non_supported_claim_count": len(offenders),
        "repaired_schema_validation_error_count": len(errors),
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
        "rerun_authorized": False,
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUT.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(artifact, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"B4 Initial schema repair proof failed closed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(2)
