from __future__ import annotations

import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator

from aic.council.initial_schema_repair_v04 import build_bounded_initial_request_v04
from aic.council.initial_schema_repair_v05 import (
    INITIAL_ALLOWED_CLAIM_TYPES,
    INITIAL_SCHEMA_REPAIR_VERSION,
    INITIAL_SCHEMA_VERSION,
    INITIAL_STAGE_CLAIM_TYPE_CONTRACT_VERSION,
    JUDGE_ONLY_CLAIM_TYPES,
    PROMOTION_SEMANTICS_CONTRACT_VERSION,
    build_bounded_initial_request_v05,
    initial_stage_claim_type_contract_satisfied,
    prompt_authority_unchanged,
)
from aic.council.model_input import build_initial_model_inputs
from aic.council.model_selection import load_initial_selected_model_authority
from aic.council.models import CouncilInputFreezeArtifact, CouncilLane
from aic.council.request import CouncilRequestStage
from aic.domain.canonical import canonical_sha256
from aic.research.handoff import load_real_event_handoff


DEFAULT_BLOCKED = Path(".aic-runtime/b4_initial_council_freeze_v0_4.json")
DEFAULT_RECEIPTS = Path(".aic-runtime/b4_initial_runtime_paid_receipts_v0_4.jsonl")
DEFAULT_DIAGNOSIS = Path(".aic-runtime/b4_initial_runtime_block_diagnosis_v0_4.json")
DEFAULT_FREEZE = Path(".aic-runtime/b4_council_input_freeze.json")
DEFAULT_RECONCILIATION = Path(".aic-runtime/b3_selected_model_reconciliation.json")
DEFAULT_HANDOFF = Path("config/event/b2_real_event_handoff_v0_1.json")
DEFAULT_OUTPUT = Path(".aic-runtime/b4_initial_schema_repair_proof_v0_5.json")

ARTIFACT_VERSION = "B4_INITIAL_SCHEMA_REPAIR_PROOF_ARTIFACT_v0_5"
RUN_CLASS = "B4_LOCAL_ZERO_CALL_RETAINED_V04_STAGE_CONTRACT_SCHEMA_REPAIR_PROOF"
EXPECTED_BLOCKED_HASH = "677a6bbdc940124356dceca5cf000a5b6abb20af24c84ab69c1b2c0d601dab66"
EXPECTED_DIAGNOSIS_HASH = "e908399c99b0add031d7a6fc33e699d60d06f73897df826dcf0487c1f36cc10c"
EXPECTED_RECEIPT_HASHES = (
    "a30a32488b85e60d6af1dcffc666d5c777189a82bb040ccf9035a729622fef08",
    "98f5c83da7979e028e9c397081b4c6acd492f2804e56f3fcf3734f41ad0564dc",
    "8eb8766c95c435f256aae89f46ca2091a0de7878685bed7c54d08a1976706fb8",
    "6996c21338eb93f593fd86819c5c57f27202dbe5281973c1b52c93b0ac3f6581",
    "e8d1da99663d6a855eed52f3dbae75701dbd503f4095dfd65a19dc8385d519f9",
    "eee7b7e4ae494110bb5613d371d88563ac3d4191614db41746f71e91cdb37b82",
    "29eb7866b320dfabafc7eadd2c1287775cbf4f1448808d4a271efc633ee8ffdd",
)
EXPECTED_STRUCTURED_HASH = "4c7ae2f37ba8f4f1713ff9bb1314a438c6525b1df3f42f44e39b7f81654c86a4"
EXPECTED_V04_REQUEST_HASH = "dde555267f42c3f790395330f0209612a75efd7d3c7d1740454f48bf95e81bc2"
EXPECTED_BLOCKED_REASON = (
    "CouncilPromotionError: DECISION_BASIS is Judge-only and forbidden in initial opinions"
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
        diagnosis = _read_json(DEFAULT_DIAGNOSIS)
        receipts = _read_receipts(DEFAULT_RECEIPTS)
        if len(receipts) != 7:
            raise ValueError("v0.5 repair proof requires exact seven retained v0.4 receipts")

        _verify_hash(blocked, "artifact_hash", EXPECTED_BLOCKED_HASH, "v0.4 blocked artifact")
        _verify_hash(diagnosis, "artifact_hash", EXPECTED_DIAGNOSIS_HASH, "v0.4 diagnosis artifact")
        if blocked.get("blocked_reason") != EXPECTED_BLOCKED_REASON:
            raise ValueError("v0.4 blocked reason drift")
        if blocked.get("dispatch_attempts") != 7 or blocked.get("model_calls") != 7:
            raise ValueError("v0.4 blocked counters drift")
        if blocked.get("processed_opinion_count") != 6:
            raise ValueError("v0.4 processed-opinion count drift")
        if diagnosis.get("status") != "PASS_ZERO_CALL_V04_STAGE_CONTRACT_REPLAY_DIAGNOSIS":
            raise ValueError("v0.4 diagnosis status drift")
        if diagnosis.get("successful_replay_count") != 6:
            raise ValueError("v0.4 diagnosis successful replay count drift")
        if diagnosis.get("blocked_replay_count") != 1:
            raise ValueError("v0.4 diagnosis blocked replay count drift")
        if diagnosis.get("exact_promotion_error_reproduced") is not True:
            raise ValueError("v0.4 exact promotion error no longer reproduces")
        if diagnosis.get("initial_stage_forbidden_claim_type_count") != 1:
            raise ValueError("v0.4 diagnosis Judge-only offender count drift")
        if diagnosis.get("non_stage_promotion_rule_failure_count") != 0:
            raise ValueError("v0.4 retained output contains non-stage promotion blocker")
        if diagnosis.get("contract_gap_signal") is not True:
            raise ValueError("v0.4 diagnosis contract-gap signal missing")

        for index, (receipt, expected_hash) in enumerate(
            zip(receipts, EXPECTED_RECEIPT_HASHES, strict=True), start=1
        ):
            _verify_hash(receipt, "receipt_hash", expected_hash, f"v0.4 receipt {index}")
            if receipt.get("receipt_version") != "B4_INITIAL_RUNTIME_PAID_CALL_RECEIPT_v0_4":
                raise ValueError(f"retained receipt {index} is not v0.4")
            if receipt.get("provider_response_received") is not True:
                raise ValueError(f"retained receipt {index} lacks provider response")
            if receipt.get("semantic_replay_status") != "COMPLETE":
                raise ValueError(f"retained receipt {index} is not replay-complete")
            if receipt.get("automatic_repair_attempted") is not False:
                raise ValueError(f"retained receipt {index} indicates automatic repair")
            expected_validation = "PASS" if index <= 6 else "FAIL"
            if receipt.get("validation_status") != expected_validation:
                raise ValueError(f"retained receipt {index} validation-status drift")

        receipt = receipts[-1]
        if receipt.get("dispatch_index") != 7:
            raise ValueError("retained v0.4 offender dispatch index drift")
        if receipt.get("candidate_id") != "META" or receipt.get("lane") != "BULL":
            raise ValueError("retained v0.4 offender identity drift")
        if receipt.get("request_hash") != EXPECTED_V04_REQUEST_HASH:
            raise ValueError("retained v0.4 offender request hash drift")

        structured = receipt.get("structured_output")
        if not isinstance(structured, dict):
            raise ValueError("retained v0.4 structured output missing")
        if receipt.get("structured_output_hash") != EXPECTED_STRUCTURED_HASH:
            raise ValueError("retained v0.4 structured-output hash drift")
        if canonical_sha256(structured) != EXPECTED_STRUCTURED_HASH:
            raise ValueError("retained v0.4 structured-output canonical hash mismatch")

        claims = structured.get("proposed_claims")
        if not isinstance(claims, list):
            raise ValueError("retained v0.4 proposed_claims missing")
        offenders = [
            item
            for item in claims
            if isinstance(item, dict) and item.get("claim_type") == "DECISION_BASIS"
        ]
        if len(offenders) != 1:
            raise ValueError("retained v0.4 DECISION_BASIS offender count drift")
        offender = offenders[0]
        if offender.get("claim_local_ref") != "BULL_S1":
            raise ValueError("retained v0.4 DECISION_BASIS local ref drift")

        freeze = CouncilInputFreezeArtifact.model_validate(_read_json(DEFAULT_FREEZE))
        reconciliation = _read_json(DEFAULT_RECONCILIATION)
        handoff = load_real_event_handoff(DEFAULT_HANDOFF)
        model_inputs = build_initial_model_inputs(freeze, reconciliation, handoff)
        authority = load_initial_selected_model_authority()
        model_input = next(item for item in model_inputs if item.candidate_id == "META")
        bundle = next(item for item in freeze.bundles if item.candidate_id == "META")
        model_run_ref = structured.get("model_run_ref")
        if not isinstance(model_run_ref, str) or not model_run_ref:
            raise ValueError("retained v0.4 model_run_ref missing")
        payload = model_input.model_dump(mode="json", exclude_none=False)

        v04 = build_bounded_initial_request_v04(
            stage=CouncilRequestStage.BULL_INITIAL,
            model_candidate=authority.selected_candidate,
            bundle=bundle,
            model_run_ref=model_run_ref,
            model_input=payload,
            allowed_data_gap_refs=model_input.data_gap_refs,
        )
        v05 = build_bounded_initial_request_v05(
            stage=CouncilRequestStage.BULL_INITIAL,
            model_candidate=authority.selected_candidate,
            bundle=bundle,
            model_run_ref=model_run_ref,
            model_input=payload,
            allowed_data_gap_refs=model_input.data_gap_refs,
        )
        if v04.request_hash != EXPECTED_V04_REQUEST_HASH:
            raise ValueError("reconstructed v0.4 request no longer matches retained receipt")
        if not prompt_authority_unchanged(v04, v05):
            raise ValueError("v0.5 repair changes prompt or model input authority")

        schema04 = v04.request_payload["text"]["format"]["schema"]
        schema05 = v05.request_payload["text"]["format"]["schema"]
        errors04 = _errors(schema04, structured)
        errors05 = _errors(schema05, structured)
        if errors04:
            raise ValueError("retained provider output unexpectedly violates v0.4 strict schema")
        if not errors05:
            raise ValueError("v0.5 repaired schema unexpectedly accepts retained Judge-only claim")
        if not initial_stage_claim_type_contract_satisfied(schema05):
            raise ValueError("v0.5 Initial stage claim-type schema contract not satisfied")

        artifact = {
            "artifact_version": ARTIFACT_VERSION,
            "run_class": RUN_CLASS,
            "status": "PASS_EXACT_RETAINED_V04_OUTPUT_REJECTED_BY_V05_STAGE_SCHEMA",
            "schema_repair_version": INITIAL_SCHEMA_REPAIR_VERSION,
            "schema_version": INITIAL_SCHEMA_VERSION,
            "promotion_semantics_contract_version": PROMOTION_SEMANTICS_CONTRACT_VERSION,
            "initial_stage_claim_type_contract_version": INITIAL_STAGE_CLAIM_TYPE_CONTRACT_VERSION,
            "initial_allowed_claim_types": list(INITIAL_ALLOWED_CLAIM_TYPES),
            "judge_only_claim_types": list(JUDGE_ONLY_CLAIM_TYPES),
            "source_run_id": blocked.get("run_id"),
            "source_blocked_artifact_hash": EXPECTED_BLOCKED_HASH,
            "source_diagnosis_artifact_hash": EXPECTED_DIAGNOSIS_HASH,
            "source_receipt_hash": EXPECTED_RECEIPT_HASHES[-1],
            "source_structured_output_hash": EXPECTED_STRUCTURED_HASH,
            "recorded_blocked_reason": EXPECTED_BLOCKED_REASON,
            "candidate_id": "META",
            "lane": CouncilLane.BULL.value,
            "dispatch_index": 7,
            "selected_candidate": {
                "candidate_key": authority.selected_candidate.candidate_key,
                "model": authority.selected_candidate.model,
                "reasoning_effort": authority.selected_candidate.reasoning_effort,
            },
            "v04_request_hash": v04.request_hash,
            "v05_request_hash": v05.request_hash,
            "v04_schema_hash": canonical_sha256(schema04),
            "v05_schema_hash": canonical_sha256(schema05),
            "v04_schema_validation_error_count": len(errors04),
            "v05_schema_validation_error_count": len(errors05),
            "v05_schema_validation_errors": errors05,
            "initial_stage_claim_type_contract_satisfied": True,
            "successful_retained_promotion_replays": diagnosis["successful_replay_count"],
            "blocked_retained_promotion_replays": diagnosis["blocked_replay_count"],
            "non_stage_promotion_rule_failure_count": diagnosis[
                "non_stage_promotion_rule_failure_count"
            ],
            "retained_offender_claim_local_ref": offender.get("claim_local_ref"),
            "retained_offender_claim_type": offender.get("claim_type"),
            "retained_offender_claim_kind": offender.get("claim_kind"),
            "prompt_contract_version": v05.prompt_contract_version,
            "prompt_version": v05.prompt_version,
            "prompt_hash": v05.prompt_hash,
            "input_hash": v05.input_hash,
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
        print(
            json.dumps(
                {**artifact, "output_path": str(DEFAULT_OUTPUT)},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        print(
            f"B4 Initial schema repair v0.5 proof failed closed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
