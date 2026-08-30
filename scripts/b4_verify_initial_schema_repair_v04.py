from __future__ import annotations

import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator

from aic.council.initial_schema_repair_v03 import build_bounded_initial_request_v03
from aic.council.initial_schema_repair_v04 import (
    INITIAL_SCHEMA_REPAIR_VERSION,
    INITIAL_SCHEMA_VERSION,
    PROMOTION_SEMANTICS_CONTRACT_VERSION,
    build_bounded_initial_request_v04,
    promotion_contract_branch_count,
    prompt_authority_unchanged,
)
from aic.council.model_input import build_initial_model_inputs
from aic.council.model_selection import load_initial_selected_model_authority
from aic.council.models import CouncilInputFreezeArtifact, CouncilLane
from aic.council.request import CouncilRequestStage
from aic.domain.canonical import canonical_sha256
from aic.research.handoff import load_real_event_handoff


DEFAULT_BLOCKED = Path(".aic-runtime/b4_initial_council_freeze_v0_3.json")
DEFAULT_RECEIPTS = Path(".aic-runtime/b4_initial_runtime_paid_receipts_v0_3.jsonl")
DEFAULT_FREEZE = Path(".aic-runtime/b4_council_input_freeze.json")
DEFAULT_RECONCILIATION = Path(".aic-runtime/b3_selected_model_reconciliation.json")
DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b4_initial_schema_repair_proof_v0_4.json")

ARTIFACT_VERSION = "B4_INITIAL_SCHEMA_REPAIR_PROOF_ARTIFACT_v0_4"
RUN_CLASS = "B4_LOCAL_ZERO_CALL_RETAINED_V03_OUTPUT_SCHEMA_REPAIR_PROOF"
EXPECTED_BLOCKED_HASH = "e520f0ef665057d116b34a78cb2cf8242307ee53215c76d5480afee23dcbb506"
EXPECTED_RECEIPT_HASH = "491a20c7624c6c648d13eeb21b22d68ddea9b612e5910f69d8dd8ac194d92fd8"
EXPECTED_STRUCTURED_HASH = "64e3beafaf12986813bf06a869d39c1e9867df7ce86ebdaf8b25e254a7bd5b30"
EXPECTED_BLOCKED_REASON = (
    "CouncilPromotionError: B4 inference/process finding requires frozen provenance refs"
)


def _read_json(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return raw


def _read_receipts(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        if not isinstance(raw, dict):
            raise ValueError("receipt journal line must be object")
        rows.append(raw)
    return rows


def _verify_hash(raw: dict, field: str, expected: str, label: str) -> None:
    if raw.get(field) != expected:
        raise ValueError(f"{label} recorded hash drift")
    if canonical_sha256(raw, exclude_fields=(field,)) != expected:
        raise ValueError(f"{label} canonical hash mismatch")


def _errors(schema: dict, structured: dict) -> list[dict]:
    validator = Draft202012Validator(schema)
    result = []
    for error in sorted(validator.iter_errors(structured), key=lambda item: list(item.absolute_path)):
        result.append(
            {
                "path": [str(item) for item in error.absolute_path],
                "schema_path": [str(item) for item in error.absolute_schema_path],
                "validator": error.validator,
                "message_hash": canonical_sha256({"message": error.message}),
            }
        )
    return result


def main() -> int:
    try:
        blocked = _read_json(DEFAULT_BLOCKED)
        receipts = _read_receipts(DEFAULT_RECEIPTS)
        if len(receipts) != 1:
            raise ValueError("v0.4 repair proof requires exact one retained v0.3 receipt")
        receipt = receipts[0]

        _verify_hash(blocked, "artifact_hash", EXPECTED_BLOCKED_HASH, "v0.3 blocked artifact")
        _verify_hash(receipt, "receipt_hash", EXPECTED_RECEIPT_HASH, "v0.3 paid receipt")
        if blocked.get("blocked_reason") != EXPECTED_BLOCKED_REASON:
            raise ValueError("v0.3 blocked reason drift")
        if receipt.get("receipt_version") != "B4_INITIAL_RUNTIME_PAID_CALL_RECEIPT_v0_3":
            raise ValueError("retained paid receipt is not v0.3")
        if receipt.get("provider_response_received") is not True:
            raise ValueError("retained v0.3 receipt lacks provider response")
        if receipt.get("semantic_replay_status") != "COMPLETE":
            raise ValueError("retained v0.3 receipt is not replay-complete")
        if receipt.get("automatic_repair_attempted") is not False:
            raise ValueError("retained v0.3 receipt indicates automatic repair")

        structured = receipt.get("structured_output")
        if not isinstance(structured, dict):
            raise ValueError("retained v0.3 structured output missing")
        if receipt.get("structured_output_hash") != EXPECTED_STRUCTURED_HASH:
            raise ValueError("retained v0.3 structured-output hash drift")
        if canonical_sha256(structured) != EXPECTED_STRUCTURED_HASH:
            raise ValueError("retained v0.3 structured-output canonical hash mismatch")

        freeze = CouncilInputFreezeArtifact.model_validate(_read_json(DEFAULT_FREEZE))
        reconciliation = _read_json(DEFAULT_RECONCILIATION)
        handoff = load_real_event_handoff(DEFAULT_HANDOFF)
        model_inputs = build_initial_model_inputs(freeze, reconciliation, handoff)
        authority = load_initial_selected_model_authority()

        dispatch_index = receipt.get("dispatch_index")
        candidate = receipt.get("candidate_id")
        lane = receipt.get("lane")
        if dispatch_index != 1 or candidate != "NVDA" or lane != "BULL":
            raise ValueError("retained v0.3 receipt identity drift")
        model_input = next(item for item in model_inputs if item.candidate_id == candidate)
        bundle = next(item for item in freeze.bundles if item.candidate_id == candidate)
        model_run_ref = structured.get("model_run_ref")
        if not isinstance(model_run_ref, str) or not model_run_ref:
            raise ValueError("retained v0.3 model_run_ref missing")

        model_input_payload = model_input.model_dump(mode="json", exclude_none=False)
        v03 = build_bounded_initial_request_v03(
            stage=CouncilRequestStage.BULL_INITIAL,
            model_candidate=authority.selected_candidate,
            bundle=bundle,
            model_run_ref=model_run_ref,
            model_input=model_input_payload,
            allowed_data_gap_refs=model_input.data_gap_refs,
        )
        v04 = build_bounded_initial_request_v04(
            stage=CouncilRequestStage.BULL_INITIAL,
            model_candidate=authority.selected_candidate,
            bundle=bundle,
            model_run_ref=model_run_ref,
            model_input=model_input_payload,
            allowed_data_gap_refs=model_input.data_gap_refs,
        )
        if receipt.get("request_hash") != v03.request_hash:
            raise ValueError("retained v0.3 receipt does not bind reconstructed v0.3 request")
        if not prompt_authority_unchanged(v03, v04):
            raise ValueError("v0.4 repair changes prompt or model input authority")

        schema03 = v03.request_payload["text"]["format"]["schema"]
        schema04 = v04.request_payload["text"]["format"]["schema"]
        errors03 = _errors(schema03, structured)
        errors04 = _errors(schema04, structured)
        if errors03:
            raise ValueError("retained provider output unexpectedly violates v0.3 strict schema")
        if not errors04:
            raise ValueError("v0.4 repaired schema unexpectedly accepts retained provenance defect")

        claims = structured.get("proposed_claims")
        if not isinstance(claims, list) or len(claims) != 11:
            raise ValueError("retained v0.3 proposal claim count drift")
        offender = [item for item in claims if isinstance(item, dict) and item.get("claim_local_ref") == "BULL_011"]
        if len(offender) != 1:
            raise ValueError("retained BULL_011 offender missing")
        bad = offender[0]
        if bad.get("claim_kind") != "PROCESS_FINDING":
            raise ValueError("retained offender claim kind drift")
        if any(bad.get(field) != [] for field in (
            "source_material_claim_ids", "computed_value_ids", "conflict_ids"
        )):
            raise ValueError("retained offender unexpectedly contains frozen provenance")

        artifact = {
            "artifact_version": ARTIFACT_VERSION,
            "run_class": RUN_CLASS,
            "status": "PASS_EXACT_RETAINED_V03_OUTPUT_REJECTED_BY_V04_PROMOTION_SCHEMA",
            "schema_repair_version": INITIAL_SCHEMA_REPAIR_VERSION,
            "schema_version": INITIAL_SCHEMA_VERSION,
            "promotion_semantics_contract_version": PROMOTION_SEMANTICS_CONTRACT_VERSION,
            "source_run_id": blocked.get("run_id"),
            "source_blocked_artifact_hash": EXPECTED_BLOCKED_HASH,
            "source_receipt_hash": EXPECTED_RECEIPT_HASH,
            "source_structured_output_hash": EXPECTED_STRUCTURED_HASH,
            "recorded_blocked_reason": EXPECTED_BLOCKED_REASON,
            "candidate_id": candidate,
            "lane": CouncilLane.BULL.value,
            "dispatch_index": dispatch_index,
            "selected_candidate": {
                "candidate_key": authority.selected_candidate.candidate_key,
                "model": authority.selected_candidate.model,
                "reasoning_effort": authority.selected_candidate.reasoning_effort,
            },
            "v03_request_hash": v03.request_hash,
            "v04_request_hash": v04.request_hash,
            "v03_schema_hash": canonical_sha256(schema03),
            "v04_schema_hash": canonical_sha256(schema04),
            "v03_schema_validation_error_count": len(errors03),
            "v04_schema_validation_error_count": len(errors04),
            "v04_schema_validation_errors": errors04,
            "promotion_contract_branch_count": promotion_contract_branch_count(schema04),
            "prompt_contract_version": v04.prompt_contract_version,
            "prompt_version": v04.prompt_version,
            "prompt_hash": v04.prompt_hash,
            "input_hash": v04.input_hash,
            "retained_offender_claim_local_ref": "BULL_011",
            "retained_offender_claim_kind": bad.get("claim_kind"),
            "retained_offender_source_material_claim_ids": bad.get("source_material_claim_ids"),
            "retained_offender_computed_value_ids": bad.get("computed_value_ids"),
            "retained_offender_conflict_ids": bad.get("conflict_ids"),
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
        print(json.dumps({**artifact, "output_path": str(DEFAULT_OUTPUT)}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(
            f"B4 Initial schema repair v0.4 proof failed closed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
