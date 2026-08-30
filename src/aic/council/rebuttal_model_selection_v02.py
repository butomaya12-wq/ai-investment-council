from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

from aic.domain.canonical import canonical_sha256

from . import rebuttal_model_selection as v01
from .model_policy import (
    MODEL_POLICY_VERSION,
    REBUTTAL_MODEL_LADDER,
    CouncilModelStage,
    StageModelEvalResult,
    StageModelSelectionStatus,
    select_stage_model_from_eval,
)
from .proposal import RebuttalBundleDraft
from .rebuttal_eval_preflight import REBUTTAL_EVAL_VERSION, build_rebuttal_eval_cases
from .rebuttal_eval_runtime import REBUTTAL_EVAL_RUNTIME_VERSION, score_rebuttal_eval_case


REBUTTAL_SELECTED_MODEL_AUTHORITY_VERSION_V02 = "B4_REBUTTAL_SELECTED_MODEL_AUTHORITY_v0_2"
REPLAY_CONTRACT_VERSION = "B4_REBUTTAL_DURABLE_RESULT_HASH_REPLAY_v0_2"
EXPECTED_CASE_IDS = v01.EXPECTED_CASE_IDS
EXPECTED_RECEIPTS = v01.EXPECTED_RECEIPTS


RebuttalSelectedModelAuthorityError = v01.RebuttalSelectedModelAuthorityError


def _result_payload_from_receipt(
    receipt: Mapping[str, Any],
    *,
    passed: bool,
    findings: tuple[str, ...],
    structured_hash: str,
) -> dict[str, Any]:
    return {
        "eval_version": REBUTTAL_EVAL_VERSION,
        "runtime_version": REBUTTAL_EVAL_RUNTIME_VERSION,
        "case_id": receipt.get("case_id"),
        "name": receipt.get("case_name"),
        "critical_safety": receipt.get("critical_safety"),
        "passed": passed,
        "findings": list(findings),
        "response_id": receipt.get("response_id"),
        "requested_model": receipt.get("requested_model"),
        "effective_model": receipt.get("effective_model"),
        "model_calls": 1,
        "latency_ms": receipt.get("latency_ms"),
        "input_tokens": receipt.get("input_tokens"),
        "cached_tokens": receipt.get("cached_tokens"),
        "cache_write_tokens": receipt.get("cache_write_tokens"),
        "output_tokens": receipt.get("output_tokens"),
        "reasoning_tokens": receipt.get("reasoning_tokens"),
        "actual_cost_usd": receipt.get("actual_cost_usd"),
        "cost_receipt_status": "COMPLETE",
        "output_hash": receipt.get("output_hash"),
        "structured_output_hash": structured_hash,
    }


def _replay_receipt_v02(
    receipt: Mapping[str, Any],
    *,
    case: Any,
) -> tuple[bool, tuple[str, ...], str]:
    structured = receipt.get("structured_output")
    if not isinstance(structured, Mapping):
        raise RebuttalSelectedModelAuthorityError("receipt structured output missing")
    structured_hash = v01._require_sha(
        receipt.get("structured_output_hash"),
        field_name="structured output hash",
    )
    if structured_hash != canonical_sha256(structured):
        raise RebuttalSelectedModelAuthorityError("receipt structured output hash mismatch")
    try:
        proposal = RebuttalBundleDraft.model_validate(structured)
    except ValueError as exc:
        raise RebuttalSelectedModelAuthorityError("receipt structured output no longer validates") from exc

    passed, findings = score_rebuttal_eval_case(case, proposal)
    expected_result = "PASS" if passed else "FAIL"
    if receipt.get("case_result") != expected_result:
        raise RebuttalSelectedModelAuthorityError(
            "receipt case_result disagrees with zero-call semantic replay"
        )
    if tuple(receipt.get("findings", ())) != findings:
        raise RebuttalSelectedModelAuthorityError(
            "receipt findings disagree with zero-call semantic replay"
        )
    if receipt.get("cost_receipt_status") != "COMPLETE":
        raise RebuttalSelectedModelAuthorityError(
            "result-hash replay requires COMPLETE cost receipt"
        )

    result_hash = canonical_sha256(
        _result_payload_from_receipt(
            receipt,
            passed=passed,
            findings=findings,
            structured_hash=structured_hash,
        )
    )
    if "result_hash" in receipt and receipt.get("result_hash") != result_hash:
        raise RebuttalSelectedModelAuthorityError(
            "optional receipt result_hash disagrees with durable replay"
        )
    return passed, findings, result_hash


def _case_record_matches_receipt_v02(
    case_record: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    replayed_result_hash: str,
) -> None:
    mapping = {
        "case_id": "case_id",
        "name": "case_name",
        "critical_safety": "critical_safety",
        "findings": "findings",
        "response_id": "response_id",
        "requested_model": "requested_model",
        "effective_model": "effective_model",
        "latency_ms": "latency_ms",
        "input_tokens": "input_tokens",
        "cached_tokens": "cached_tokens",
        "cache_write_tokens": "cache_write_tokens",
        "output_tokens": "output_tokens",
        "reasoning_tokens": "reasoning_tokens",
        "actual_cost_usd": "actual_cost_usd",
        "cost_receipt_status": "cost_receipt_status",
        "output_hash": "output_hash",
        "structured_output_hash": "structured_output_hash",
    }
    for case_field, receipt_field in mapping.items():
        if case_record.get(case_field) != receipt.get(receipt_field):
            raise RebuttalSelectedModelAuthorityError(
                f"candidate case record differs from receipt: {case_field}"
            )
    if case_record.get("passed") != (receipt.get("case_result") == "PASS"):
        raise RebuttalSelectedModelAuthorityError(
            "candidate case record differs from receipt: passed"
        )
    if case_record.get("model_calls") != 1:
        raise RebuttalSelectedModelAuthorityError(
            "candidate case record differs from durable one-call contract"
        )
    if case_record.get("result_hash") != replayed_result_hash:
        raise RebuttalSelectedModelAuthorityError(
            "candidate case result_hash differs from durable receipt replay"
        )


def _candidate_metrics_v02(
    candidate: Any,
    record: Mapping[str, Any],
    receipts: Sequence[Mapping[str, Any]],
    replayed_result_hashes: Sequence[str],
) -> tuple[dict[str, Any], StageModelEvalResult]:
    if record.get("candidate_key") != candidate.candidate_key:
        raise RebuttalSelectedModelAuthorityError("candidate record key mismatch")
    if (
        record.get("model") != candidate.model
        or record.get("reasoning_effort") != candidate.reasoning_effort
    ):
        raise RebuttalSelectedModelAuthorityError(
            "candidate record model configuration mismatch"
        )
    if record.get("ladder_position") != candidate.ladder_position:
        raise RebuttalSelectedModelAuthorityError(
            "candidate record ladder position mismatch"
        )
    cases = record.get("cases")
    if not isinstance(cases, list) or len(cases) != len(EXPECTED_CASE_IDS):
        raise RebuttalSelectedModelAuthorityError(
            "candidate record must contain four cases"
        )
    if tuple(
        item.get("case_id") for item in cases if isinstance(item, Mapping)
    ) != EXPECTED_CASE_IDS:
        raise RebuttalSelectedModelAuthorityError("candidate case order mismatch")

    for case_record, receipt, replayed_result_hash in zip(
        cases,
        receipts,
        replayed_result_hashes,
        strict=True,
    ):
        if not isinstance(case_record, Mapping):
            raise RebuttalSelectedModelAuthorityError(
                "candidate case record malformed"
            )
        _case_record_matches_receipt_v02(
            case_record,
            receipt,
            replayed_result_hash=replayed_result_hash,
        )

    passed_cases = sum(
        1 for receipt in receipts if receipt.get("case_result") == "PASS"
    )
    critical_failures = sum(
        1
        for receipt in receipts
        if receipt.get("critical_safety") is True
        and receipt.get("case_result") != "PASS"
    )
    cost = sum(
        (
            v01._decimal(
                receipt.get("actual_cost_usd"),
                field_name="receipt actual cost",
            )
            for receipt in receipts
        ),
        Decimal("0"),
    )
    latency = sum(int(receipt.get("latency_ms")) for receipt in receipts)
    total_tokens = sum(
        int(receipt.get("input_tokens")) + int(receipt.get("output_tokens"))
        for receipt in receipts
    )
    all_passed = passed_cases == len(EXPECTED_CASE_IDS)
    expected_fields = {
        "passed_cases": passed_cases,
        "required_cases": len(EXPECTED_CASE_IDS),
        "all_required_checks_passed": all_passed,
        "critical_safety_failures": critical_failures,
        "estimated_cost_usd": str(cost),
        "latency_ms": latency,
        "total_tokens": total_tokens,
    }
    for field, expected in expected_fields.items():
        if record.get(field) != expected:
            raise RebuttalSelectedModelAuthorityError(
                f"candidate record metric mismatch: {field}"
            )
    if record.get("record_hash") != canonical_sha256(
        record,
        exclude_fields=("record_hash",),
    ):
        raise RebuttalSelectedModelAuthorityError("candidate record hash mismatch")

    summary = {
        **expected_fields,
        "record_hash": record["record_hash"],
    }
    result = StageModelEvalResult(
        candidate_key=candidate.candidate_key,
        all_required_checks_passed=all_passed,
        critical_safety_failures=critical_failures,
        estimated_cost_usd=cost,
        latency_ms=latency,
        total_tokens=total_tokens,
    )
    return summary, result


def build_rebuttal_selected_model_authority_v02(
    eval_artifact: Mapping[str, Any],
    receipts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    eval_hash = v01._verify_eval_artifact(eval_artifact)
    if len(receipts) != EXPECTED_RECEIPTS:
        raise RebuttalSelectedModelAuthorityError(
            "selected-model authority requires exactly 12 receipts"
        )
    observed_order = tuple(
        (receipt.get("candidate_key"), receipt.get("case_id"))
        for receipt in receipts
    )
    if observed_order != v01._expected_receipt_order():
        raise RebuttalSelectedModelAuthorityError(
            "receipt candidate/case order differs from frozen ladder"
        )

    cases = {case.case_id: case for case in build_rebuttal_eval_cases()}
    receipt_hashes: list[str] = []
    replayed_result_hashes: list[str] = []
    replay_pass_count = 0
    for index, receipt in enumerate(receipts, start=1):
        receipt_hashes.append(
            v01._verify_receipt_common(
                receipt,
                dispatch_index=index,
                eval_artifact=eval_artifact,
            )
        )
        passed, _, result_hash = _replay_receipt_v02(
            receipt,
            case=cases[str(receipt["case_id"])],
        )
        replayed_result_hashes.append(result_hash)
        replay_pass_count += int(passed)

    if eval_artifact.get("paid_call_receipt_hashes") != receipt_hashes:
        raise RebuttalSelectedModelAuthorityError(
            "eval artifact receipt hashes differ from journal"
        )
    receipt_manifest_hash = canonical_sha256(
        {"receipt_hashes": receipt_hashes}
    )
    if eval_artifact.get("receipt_manifest_hash") != receipt_manifest_hash:
        raise RebuttalSelectedModelAuthorityError(
            "eval artifact receipt manifest mismatch"
        )

    candidate_records = eval_artifact.get("candidate_records")
    if not isinstance(candidate_records, list) or len(candidate_records) != len(
        REBUTTAL_MODEL_LADDER
    ):
        raise RebuttalSelectedModelAuthorityError(
            "eval artifact must contain exact R1-R3 records"
        )

    summaries: dict[str, dict[str, Any]] = {}
    eval_results: list[StageModelEvalResult] = []
    offset = 0
    for candidate, record in zip(
        REBUTTAL_MODEL_LADDER,
        candidate_records,
        strict=True,
    ):
        if not isinstance(record, Mapping):
            raise RebuttalSelectedModelAuthorityError(
                "candidate record malformed"
            )
        end = offset + len(EXPECTED_CASE_IDS)
        summary, result = _candidate_metrics_v02(
            candidate,
            record,
            receipts[offset:end],
            replayed_result_hashes[offset:end],
        )
        offset = end
        summaries[candidate.candidate_key] = summary
        eval_results.append(result)

    selection = select_stage_model_from_eval(
        CouncilModelStage.REBUTTAL,
        tuple(eval_results),
    )
    if (
        selection.status is not StageModelSelectionStatus.SELECTED
        or selection.selected_candidate is None
    ):
        raise RebuttalSelectedModelAuthorityError(
            "zero-call replay does not select a Rebuttal model"
        )
    expected_selected = {
        "candidate_key": selection.selected_candidate.candidate_key,
        "model": selection.selected_candidate.model,
        "reasoning_effort": selection.selected_candidate.reasoning_effort,
        "ladder_position": selection.selected_candidate.ladder_position,
    }
    stored_selection = eval_artifact.get("selection")
    if not isinstance(stored_selection, Mapping):
        raise RebuttalSelectedModelAuthorityError(
            "eval artifact selection missing"
        )
    if stored_selection.get("status") != selection.status.value:
        raise RebuttalSelectedModelAuthorityError(
            "stored selection status differs from zero-call replay"
        )
    if stored_selection.get("selected_candidate") != expected_selected:
        raise RebuttalSelectedModelAuthorityError(
            "stored selected candidate differs from zero-call replay"
        )
    if stored_selection.get("reason_code") != selection.reason_code:
        raise RebuttalSelectedModelAuthorityError(
            "stored selection reason differs from frozen rule"
        )

    actual_cost = sum(
        (result.estimated_cost_usd for result in eval_results),
        Decimal("0"),
    )
    if v01._decimal(
        eval_artifact.get("actual_cost_usd"),
        field_name="eval actual cost",
    ) != actual_cost:
        raise RebuttalSelectedModelAuthorityError(
            "eval actual cost differs from receipt/candidate sum"
        )

    selected_key = selection.selected_candidate.candidate_key
    authority: dict[str, Any] = {
        "artifact_version": REBUTTAL_SELECTED_MODEL_AUTHORITY_VERSION_V02,
        "replay_contract_version": REPLAY_CONTRACT_VERSION,
        "result_hash_replay_source": (
            "RECOMPUTED_FROM_DURABLE_RECEIPT_AND_MATCHED_TO_FINAL_CANDIDATE_RECORD"
        ),
        "stage": CouncilModelStage.REBUTTAL.value,
        "model_policy_version": MODEL_POLICY_VERSION,
        "model_eval_artifact_version": v01.REBUTTAL_MODEL_EVAL_ARTIFACT_VERSION,
        "model_eval_artifact_hash": eval_hash,
        "eval_version": REBUTTAL_EVAL_VERSION,
        "runtime_version": REBUTTAL_EVAL_RUNTIME_VERSION,
        "source_git_commit": eval_artifact["code_commit_sha"],
        "initial_council_freeze_artifact_hash": eval_artifact[
            "initial_council_freeze_artifact_hash"
        ],
        "request_preflight_artifact_hash": eval_artifact[
            "request_preflight_artifact_hash"
        ],
        "request_manifest_hash": eval_artifact["request_manifest_hash"],
        "cost_preflight_artifact_hash": eval_artifact[
            "cost_preflight_artifact_hash"
        ],
        "paid_run_id": eval_artifact["run_id"],
        "paid_authorization_artifact_hash": eval_artifact[
            "paid_authorization_artifact_hash"
        ],
        "receipt_manifest_hash": receipt_manifest_hash,
        "cost_receipt_status": "COMPLETE",
        "semantic_replay_receipts_complete": EXPECTED_RECEIPTS,
        "semantic_replay_passed_cases": replay_pass_count,
        "replayed_result_hash_count": len(replayed_result_hashes),
        "selection_status": selection.status.value,
        "selected_candidate": expected_selected,
        "selection_reason_code": selection.reason_code,
        "selected_eval_metrics": summaries[selected_key],
        "full_ladder_pass_summary": summaries,
        "actual_paid_eval_cost_usd": str(actual_cost),
        "production_rebuttal_authorized": False,
        "judge_authorized": False,
        "rerun_authorized": False,
        "model_calls": 0,
        "provider_reads": 0,
        "broker_writes": 0,
        "alpaca_orders": 0,
        "live_money": "PROHIBITED",
    }
    authority["selection_hash"] = canonical_sha256(authority)
    return authority


def verify_rebuttal_selected_model_authority_v02(
    payload: Mapping[str, Any],
) -> str:
    selection_hash = v01._require_sha(
        payload.get("selection_hash"),
        field_name="selection_hash",
    )
    if selection_hash != canonical_sha256(
        payload,
        exclude_fields=("selection_hash",),
    ):
        raise RebuttalSelectedModelAuthorityError(
            "selected-model authority hash mismatch"
        )
    if payload.get("artifact_version") != REBUTTAL_SELECTED_MODEL_AUTHORITY_VERSION_V02:
        raise RebuttalSelectedModelAuthorityError(
            "unexpected selected-model authority version"
        )
    if payload.get("replay_contract_version") != REPLAY_CONTRACT_VERSION:
        raise RebuttalSelectedModelAuthorityError(
            "selected-model replay contract mismatch"
        )
    if payload.get("replayed_result_hash_count") != EXPECTED_RECEIPTS:
        raise RebuttalSelectedModelAuthorityError(
            "selected-model authority lacks 12 reconstructed result hashes"
        )
    if payload.get("stage") != CouncilModelStage.REBUTTAL.value:
        raise RebuttalSelectedModelAuthorityError(
            "selected-model authority stage mismatch"
        )
    if payload.get("model_policy_version") != MODEL_POLICY_VERSION:
        raise RebuttalSelectedModelAuthorityError(
            "selected-model authority policy mismatch"
        )
    if payload.get("selection_status") != StageModelSelectionStatus.SELECTED.value:
        raise RebuttalSelectedModelAuthorityError(
            "selected-model authority is not SELECTED"
        )
    selected = payload.get("selected_candidate")
    expected_candidates = {
        candidate.candidate_key: {
            "candidate_key": candidate.candidate_key,
            "model": candidate.model,
            "reasoning_effort": candidate.reasoning_effort,
            "ladder_position": candidate.ladder_position,
        }
        for candidate in REBUTTAL_MODEL_LADDER
    }
    if not isinstance(selected, Mapping) or selected.get("candidate_key") not in expected_candidates:
        raise RebuttalSelectedModelAuthorityError(
            "selected candidate malformed"
        )
    if dict(selected) != expected_candidates[str(selected["candidate_key"])]:
        raise RebuttalSelectedModelAuthorityError(
            "selected candidate differs from frozen ladder"
        )
    for field in (
        "model_calls",
        "provider_reads",
        "broker_writes",
        "alpaca_orders",
    ):
        if payload.get(field) != 0:
            raise RebuttalSelectedModelAuthorityError(
                f"authority zero-call invariant violated: {field}"
            )
    if (
        payload.get("production_rebuttal_authorized") is not False
        or payload.get("judge_authorized") is not False
    ):
        raise RebuttalSelectedModelAuthorityError(
            "authority unexpectedly grants later-stage execution"
        )
    if (
        payload.get("rerun_authorized") is not False
        or payload.get("live_money") != "PROHIBITED"
    ):
        raise RebuttalSelectedModelAuthorityError(
            "authority rerun/live-money invariant drift"
        )
    return selection_hash
