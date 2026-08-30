from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from aic.council.model_policy import REBUTTAL_MODEL_LADDER
from aic.council.models import (
    CouncilClaimKind,
    CouncilClaimType,
    CouncilLane,
    CouncilMateriality,
    CouncilSupportStatus,
    ProposedCouncilClaim,
)
from aic.council.proposal import RebuttalBundleDraft, RebuttalItemDraft, RebuttalResponseType
from aic.council.rebuttal_eval_preflight import REBUTTAL_EVAL_VERSION, build_rebuttal_eval_cases
from aic.council.rebuttal_eval_runtime import REBUTTAL_EVAL_RUNTIME_VERSION, score_rebuttal_eval_case
from aic.council.rebuttal_model_selection import (
    REBUTTAL_MODEL_EVAL_ARTIFACT_VERSION,
    REBUTTAL_PAID_CALL_RECEIPT_VERSION,
    RebuttalSelectedModelAuthorityError,
    build_rebuttal_selected_model_authority,
    verify_rebuttal_selected_model_authority,
)
from aic.domain.canonical import canonical_sha256


def _proposal(case, *, valid: bool = True) -> RebuttalBundleDraft:
    items = []
    for lane in (CouncilLane.BULL, CouncilLane.BEAR, CouncilLane.RED_TEAM):
        conflict_ids = ()
        if case.case_id == "E4" and valid:
            conflict_ids = (case.required_conflict_ref,)
        claim = ProposedCouncilClaim(
            claim_local_ref=f"{case.case_id}_{lane.value}_S",
            candidate_id=case.bundle.candidate_id,
            lane=lane,
            claim_type=CouncilClaimType.CHALLENGE,
            claim_text="Frozen evidence supports this bounded Rebuttal finding.",
            source_material_claim_ids=(case.bundle.allowed_material_claim_ids[0],),
            computed_value_ids=(),
            conflict_ids=tuple(ref for ref in conflict_ids if ref is not None),
            claim_kind=CouncilClaimKind.INFERENCE,
            support_status=CouncilSupportStatus.SUPPORTED,
            materiality=CouncilMateriality.SUPPORTING,
        )
        if case.case_id == "E16":
            targets = (
                case.required_decisive_opposing_by_lane[lane]
                if valid
                else tuple(
                    ref
                    for ref in case.opposing_claim_ids_by_lane[lane]
                    if ref not in set(case.required_decisive_opposing_by_lane[lane])
                )[:1]
            )
        else:
            targets = case.opposing_claim_ids_by_lane[lane][:1]
        items.append(
            RebuttalItemDraft(
                rebuttal_item_id=f"{case.case_id}_{lane.value}_ITEM",
                responding_lane=lane,
                opposing_finding_ids=targets,
                response_type=RebuttalResponseType.UNRESOLVED,
                response_proposed_claims=(claim,),
                remaining_uncertainty_refs=case.required_unknown_refs if valid else (),
            )
        )
    requires_reopen = bool(case.required_unknown_refs) and valid
    return RebuttalBundleDraft(
        rebuttal_bundle_id=f"{case.case_id}_BUNDLE",
        candidate_id=case.bundle.candidate_id,
        council_input_bundle_hash=case.bundle.bundle_hash,
        initial_opinion_ids=case.initial_opinion_ids,
        initial_opinion_hashes=case.initial_opinion_hashes,
        items=tuple(items),
        research_reopen_required=requires_reopen,
        research_reopen_reason_codes=("MATERIAL_GAP",) if requires_reopen else (),
    )


def _fixture() -> tuple[dict, list[dict]]:
    cases = build_rebuttal_eval_cases()
    request_hash = "1" * 64
    manifest_hash = "2" * 64
    cost_hash = "3" * 64
    auth_hash = "4" * 64
    pricing_hash = "5" * 64
    run_id = "AIC-B4-REBUTTAL-EVAL-TEST"
    head = "a" * 40
    receipts: list[dict] = []
    candidate_records: list[dict] = []
    all_cost = Decimal("0")
    dispatch_index = 0

    for candidate in REBUTTAL_MODEL_LADDER:
        case_records = []
        candidate_cost = Decimal("0")
        candidate_latency = 0
        candidate_tokens = 0
        passed_cases = 0
        critical_failures = 0
        for case in cases:
            dispatch_index += 1
            valid = not (
                (candidate.candidate_key == "R1" and case.case_id == "E4")
                or (candidate.candidate_key == "R2" and case.case_id == "E13")
            )
            proposal = _proposal(case, valid=valid)
            passed, findings = score_rebuttal_eval_case(case, proposal)
            assert passed is valid
            structured = proposal.model_dump(mode="json", exclude_none=False, warnings=False)
            structured_hash = canonical_sha256(structured)
            cost = Decimal({"R1": "0.0100000", "R2": "0.0200000", "R3": "0.0300000"}[candidate.candidate_key])
            latency = 100 + dispatch_index
            input_tokens = 1000 + dispatch_index
            output_tokens = 200 + dispatch_index
            result_payload = {
                "eval_version": REBUTTAL_EVAL_VERSION,
                "runtime_version": REBUTTAL_EVAL_RUNTIME_VERSION,
                "case_id": case.case_id,
                "name": case.name,
                "critical_safety": case.critical_safety,
                "passed": passed,
                "findings": list(findings),
                "response_id": f"resp_{dispatch_index}",
                "requested_model": candidate.model,
                "effective_model": candidate.model,
                "model_calls": 1,
                "latency_ms": latency,
                "input_tokens": input_tokens,
                "cached_tokens": 0,
                "cache_write_tokens": 0,
                "output_tokens": output_tokens,
                "reasoning_tokens": 10,
                "actual_cost_usd": str(cost),
                "cost_receipt_status": "COMPLETE",
                "output_hash": canonical_sha256({"output": dispatch_index}),
                "structured_output_hash": structured_hash,
            }
            result_hash = canonical_sha256(result_payload)
            receipt = {
                "receipt_version": REBUTTAL_PAID_CALL_RECEIPT_VERSION,
                "run_id": run_id,
                "dispatch_index": dispatch_index,
                "code_commit_sha": head,
                "stage": "REBUTTAL",
                "run_class": "MODEL_EVAL",
                "candidate_key": candidate.candidate_key,
                "case_id": case.case_id,
                "case_name": case.name,
                "critical_safety": case.critical_safety,
                "requested_model": candidate.model,
                "effective_model": candidate.model,
                "reasoning_effort": candidate.reasoning_effort,
                "request_preflight_artifact_hash": request_hash,
                "request_manifest_hash": manifest_hash,
                "cost_preflight_artifact_hash": cost_hash,
                "paid_authorization_artifact_hash": auth_hash,
                "pricing_version": "TEST_PRICING",
                "pricing_hash": pricing_hash,
                "dispatch_attempted": True,
                "provider_response_received": True,
                "response_id": result_payload["response_id"],
                "input_tokens": input_tokens,
                "cached_tokens": 0,
                "cache_write_tokens": 0,
                "output_tokens": output_tokens,
                "reasoning_tokens": 10,
                "latency_ms": latency,
                "actual_cost_usd": str(cost),
                "cost_receipt_status": "COMPLETE",
                "case_result": "PASS" if passed else "FAIL",
                "findings": list(findings),
                "output_hash": result_payload["output_hash"],
                "semantic_replay_status": "COMPLETE",
                "structured_output": structured,
                "structured_output_hash": structured_hash,
                "result_hash": result_hash,
                "automatic_repair_attempted": False,
                "production_rebuttal_authorized": False,
                "judge_authorized": False,
                "rerun_authorized": False,
                "broker_writes": 0,
                "alpaca_orders": 0,
                "live_money": "PROHIBITED",
            }
            receipt["receipt_hash"] = canonical_sha256(receipt)
            receipts.append(receipt)
            case_record = {
                "case_id": case.case_id,
                "name": case.name,
                "critical_safety": case.critical_safety,
                "passed": passed,
                "findings": list(findings),
                "response_id": result_payload["response_id"],
                "requested_model": candidate.model,
                "effective_model": candidate.model,
                "model_calls": 1,
                "latency_ms": latency,
                "input_tokens": input_tokens,
                "cached_tokens": 0,
                "cache_write_tokens": 0,
                "output_tokens": output_tokens,
                "reasoning_tokens": 10,
                "actual_cost_usd": str(cost),
                "cost_receipt_status": "COMPLETE",
                "output_hash": result_payload["output_hash"],
                "structured_output_hash": structured_hash,
                "result_hash": result_hash,
            }
            case_records.append(case_record)
            candidate_cost += cost
            all_cost += cost
            candidate_latency += latency
            candidate_tokens += input_tokens + output_tokens
            passed_cases += int(passed)
            critical_failures += int(case.critical_safety and not passed)

        record = {
            "candidate_key": candidate.candidate_key,
            "model": candidate.model,
            "reasoning_effort": candidate.reasoning_effort,
            "ladder_position": candidate.ladder_position,
            "cases": case_records,
            "passed_cases": passed_cases,
            "required_cases": 4,
            "all_required_checks_passed": passed_cases == 4,
            "critical_safety_failures": critical_failures,
            "estimated_cost_usd": str(candidate_cost),
            "latency_ms": candidate_latency,
            "total_tokens": candidate_tokens,
        }
        record["record_hash"] = canonical_sha256(record)
        candidate_records.append(record)

    receipt_hashes = [row["receipt_hash"] for row in receipts]
    artifact = {
        "artifact_version": REBUTTAL_MODEL_EVAL_ARTIFACT_VERSION,
        "status": "PASS_SELECTED",
        "run_id": run_id,
        "code_commit_sha": head,
        "eval_version": REBUTTAL_EVAL_VERSION,
        "runtime_version": REBUTTAL_EVAL_RUNTIME_VERSION,
        "model_policy_version": "MODEL_POLICY_vB4_0_1",
        "request_preflight_artifact_hash": request_hash,
        "request_manifest_hash": manifest_hash,
        "cost_preflight_artifact_hash": cost_hash,
        "paid_authorization_artifact_hash": auth_hash,
        "initial_council_freeze_artifact_hash": "6" * 64,
        "pricing_version": "TEST_PRICING",
        "pricing_hash": pricing_hash,
        "case_ids": ["E4", "E8", "E13", "E16"],
        "candidate_records": candidate_records,
        "selection": {
            "status": "SELECTED",
            "selected_candidate": {
                "candidate_key": "R3",
                "model": "gpt-5.6-sol",
                "reasoning_effort": "medium",
                "ladder_position": 3,
            },
            "reason_code": "LOWEST_COST_PASSING_CONFIG_THEN_LATENCY_THEN_TOKENS",
        },
        "dispatch_attempts": 12,
        "model_calls": 12,
        "actual_cost_usd": str(all_cost),
        "cost_receipt_status": "COMPLETE",
        "semantic_replay_receipts_complete": 12,
        "paid_call_receipt_hashes": receipt_hashes,
        "receipt_manifest_hash": canonical_sha256({"receipt_hashes": receipt_hashes}),
        "automatic_repair_calls": 0,
        "production_rebuttal_authorized": False,
        "judge_authorized": False,
        "rerun_authorized": False,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    artifact["artifact_hash"] = canonical_sha256(artifact)
    return artifact, receipts


def test_zero_call_replay_selects_only_full_passing_r3_and_binds_receipts() -> None:
    artifact, receipts = _fixture()
    authority = build_rebuttal_selected_model_authority(artifact, receipts)
    assert authority["selected_candidate"] == {
        "candidate_key": "R3",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "medium",
        "ladder_position": 3,
    }
    assert authority["full_ladder_pass_summary"]["R1"]["passed_cases"] == 3
    assert authority["full_ladder_pass_summary"]["R1"]["critical_safety_failures"] == 1
    assert authority["full_ladder_pass_summary"]["R2"]["passed_cases"] == 3
    assert authority["full_ladder_pass_summary"]["R3"]["passed_cases"] == 4
    assert authority["full_ladder_pass_summary"]["R3"]["critical_safety_failures"] == 0
    assert authority["semantic_replay_receipts_complete"] == 12
    assert authority["semantic_replay_passed_cases"] == 10
    assert verify_rebuttal_selected_model_authority(authority) == authority["selection_hash"]
    assert authority["model_calls"] == 0
    assert authority["production_rebuttal_authorized"] is False
    assert authority["judge_authorized"] is False
    assert authority["live_money"] == "PROHIBITED"


def test_zero_call_selection_rejects_tampered_replayable_output() -> None:
    artifact, receipts = _fixture()
    receipts[0]["structured_output"]["candidate_id"] = "TAMPERED"
    receipts[0]["receipt_hash"] = canonical_sha256(receipts[0], exclude_fields=("receipt_hash",))
    with pytest.raises(RebuttalSelectedModelAuthorityError):
        build_rebuttal_selected_model_authority(artifact, receipts)


def test_selection_freeze_script_is_zero_call_and_binds_exact_paid_evidence() -> None:
    text = Path("scripts/b4_freeze_rebuttal_selected_model_v01.py").read_text(encoding="utf-8")
    assert "1533a224f9a0c85abb77f42526aeed24e76c7e0453bc85cc5c8f8881669ae414" in text
    assert "c45bf9cfcdcc4c91513a710f50d94bd0d260de83e814fe93931246efbc73b202" in text
    assert "5a34f22d00af8d0377b7cbe7b5dbb77669e0528abebfca23dc9fee0b1c9296df" in text
    assert "build_rebuttal_selected_model_authority" in text
    assert "OPENAI_API_KEY" not in text
    assert "StdlibResponsesTransport" not in text
    assert '"model_calls": 0' in text
    assert '"production_rebuttal_authorized": False' in text
    assert '"judge_authorized": False' in text
